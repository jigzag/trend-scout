// LINE の返信を受けて GitHub Actions を起動する中継（Cloudflare Workers）。
// 設定（Cloudflare の Worker → 設定 → 変数とシークレット）:
//   LINE_CHANNEL_SECRET        … LINE Developers「チャネル基本設定」のチャネルシークレット（署名の確認用）
//   LINE_CHANNEL_ACCESS_TOKEN  … 「Messaging API設定」の長期トークン（受け付けた旨の返信用）
//   LINE_USER_ID               … 運営者のユーザーID（この人以外のメッセージは無視する）
//   GITHUB_TOKEN               … GitHub の Fine-grained token（trend-scout の Contents: Read and write）
//   GITHUB_REPO                … jigzag/trend-scout
//
// 返信の書き方:
//   1 / 1 無料版は3回まで   … 候補1で記事を作る（メモは任意）
//   公開 / 公開 23          … いちばん新しい下書き（または投稿ID 23）を公開
//   削除 / 削除 23          … 下書きをゴミ箱へ
//   ヘルプ                  … 使い方

const HELP = [
  "【使い方】",
  "1〜3 … その番号の候補で記事を作る",
  "1 メモ … 感想などのメモ付きで作る（例: 1 無料版は3回まで）",
  "公開 … いちばん新しい下書きを公開",
  "削除 … いちばん新しい下書きをゴミ箱へ",
  "（公開 23 のように投稿IDも指定できます）",
].join("\n");

export function parseCommand(raw) {
  const text = String(raw || "")
    .replace(/[０-９]/g, (c) => String.fromCharCode(c.charCodeAt(0) - 0xfee0))
    .trim();
  let m = text.match(/^([1-3])(?:[\s　:：、,.。]+([\s\S]*))?$/);
  if (m) return { type: "write-article", payload: { n: m[1], memo: (m[2] || "").trim().slice(0, 1000), date: "" } };
  m = text.match(/^公開(?:[\s　]*(\d+))?$/);
  if (m) return { type: "wp-command", payload: { action: "publish", id: m[1] || "" } };
  m = text.match(/^(?:削除|ボツ)(?:[\s　]*(\d+))?$/);
  if (m) return { type: "wp-command", payload: { action: "trash", id: m[1] || "" } };
  return { type: "help" };
}

const REPLY = {
  "write-article": (p) => `受け付けました。候補${p.n}で記事を作ります${p.memo ? "（メモあり）" : ""}。数分お待ちください。`,
  "wp-command": (p) => (p.action === "publish" ? "公開します。少しお待ちください。" : "ゴミ箱へ移します。少しお待ちください。"),
};

export async function verify(body, signature, secret) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  return expected === signature;
}

async function reply(env, token, text) {
  await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` },
    body: JSON.stringify({ replyToken: token, messages: [{ type: "text", text }] }),
  });
}

async function dispatch(env, type, payload) {
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "trend-scout-line-webhook",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ event_type: type, client_payload: payload }),
  });
  return r.status; // 成功は 204
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok");
    const body = await request.text();
    if (!(await verify(body, request.headers.get("x-line-signature") || "", env.LINE_CHANNEL_SECRET))) {
      return new Response("bad signature", { status: 401 });
    }
    const events = JSON.parse(body).events || []; // LINE の「検証」ボタンは events が空
    for (const ev of events) {
      if (ev.type !== "message" || ev.message?.type !== "text") continue;
      if (ev.source?.userId !== env.LINE_USER_ID) continue; // 運営者以外は無視
      const cmd = parseCommand(ev.message.text);
      if (cmd.type === "help") {
        await reply(env, ev.replyToken, HELP);
        continue;
      }
      const status = await dispatch(env, cmd.type, cmd.payload);
      await reply(env, ev.replyToken, status === 204
        ? REPLY[cmd.type](cmd.payload)
        : `GitHub の起動に失敗しました（${status}）。GITHUB_TOKEN の権限か期限を確認してください。`);
    }
    return new Response("ok");
  },
};
