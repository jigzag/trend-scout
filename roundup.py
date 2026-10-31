"""今週の新着AIツールまとめ（全自動・週1回。2026-10-31〜 SPEC §14）
  1週間分の候補 → 評価の高い順に最大N件 → 1件ずつ元記事から事実を取る（個人向けでないものは外す）
  → まとめ記事を書く → 分かりやすさチェック → 類似度チェック → 公式サイトのリンクを入れる
  → WordPress に公開（似すぎ・体験表現などの問題があれば下書き）→ LINE に通知

使い方:
  python roundup.py              # 本番
  python roundup.py --dry-run    # 投稿も通知もせず、画面とファイルに出す
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

import affiliate
import images
import research
import wordpress
import writer
from article import llm_cfg
from main import BASE, JST
from notifier import send_line
from scorer import BOOL, INT, STR, arr, call_llm, extract_json, obj_schema, topic_key

ITEM_PROMPT = """あなたは調査担当の編集者です。次のAIツール・新機能について、記事から「事実」だけを抜き出してください。

# 話題
{topic}

# 記事
{text}

# リンク候補（記事と、記事に貼られていたリンク）
{links}

# 出力（JSONオブジェクトだけ）
- name: ツール名・機能名（原文の表記のまま。例「Gamma」「ChatGPT の試着機能」）
- facts: 事実の一覧（日本語で言い換えた1文ずつ、最大8個）。何ができるか・料金（金額はそのまま）・対応環境・日本語対応・日本から使えるか・提供開始日を優先
- reader_fit: プログラミングをしない個人が、アカウントを作れば一人で使い始められるなら true。
  コードを書く前提・特定の業務システムが前提・法人契約が前提・まだ使えない（研究や発表だけ）なら false
- is_tool: 特定のツールや機能の話なら true。業界ニュース・まとめ記事・イベントの話なら false
- official_link: リンク候補のうち、このツールの公式サイト（トップページ・製品ページ）の番号。ニュースやブログは選ばない。なければ 0
文章をそのまま写さず、短く言い換えること。推測は入れないこと。
{{"name": "", "facts": [], "reader_fit": true, "is_tool": true, "official_link": 0}}
"""
ITEM_SCHEMA = obj_schema({"name": STR, "facts": arr(STR), "reader_fit": BOOL, "is_tool": BOOL, "official_link": INT})

WRITE_PROMPT = """あなたはブログライターです。「今週の新着AIツールまとめ」の記事を書いてください。

# ブログ
{genre}

# 今週のツール（事実メモ。番号順に紹介する）
{items}

{rules}

# 書き方
- 1つのツールにつき本文 250〜400 字。「何ができるか → どんな場面で何がうれしいか → 料金・日本語・使える環境」の順に、2〜3段落の文章で書く
- body は HTML の p タグだけ（strong は使ってよい）。見出し・リンク・箇条書きは書かない（あとで自動で付ける）
- heading はツール名と、ひとことで何ができるか（例「Gamma：文章を貼るだけでスライドができる」。30字くらいまで）
- who は「こんな人に向いています」を1文で。price は料金を短く（事実メモに料金がなければ空文字）
- intro は記事の書き出し（p タグで2〜3文。今週どんな傾向のツールが出たか）。見出しは付けない
- easy は「ざっくり言うと」の箇条書き。ツールごとに1行（「ツール名：中学生でも分かる言葉で、何をしてくれるか」）
- closing は「まとめ」の本文（p タグで2〜3文。どんな人がどれから見るとよいか）
- title は 32 文字前後（例「今週の新着AIツール5選：スライド自動作成や音声の吹き替えなど」）。日付は入れない
- slug は英小文字とハイフン（例 weekly-ai-tools）。excerpt は検索結果に出る説明文（80〜120文字）
- image_featured はアイキャッチ画像の内容（英語1〜2文。いろいろな道具が並ぶ机などのイラスト。文字・ロゴ・実在のアプリ画面・人物は入れない）
{{"title": "", "slug": "", "excerpt": "", "intro": "", "easy": [], "items": [{{"n": 1, "heading": "", "body": "", "who": "", "price": ""}}], "closing": "", "image_featured": ""}}
"""
WRITE_SCHEMA = obj_schema({"title": STR, "slug": STR, "excerpt": STR, "intro": STR, "easy": arr(STR),
                           "items": arr(obj_schema({"n": INT, "heading": STR, "body": STR, "who": STR, "price": STR})),
                           "closing": STR, "image_featured": STR})


def pick(folder: Path, today: date, days: int, n: int) -> list[dict]:
    """直近 days 日の候補を、評価（total）の高い順に最大 n 件。同じURL・同じ話題は1件にする。"""
    cands = []
    for f in sorted(folder.glob("*.json")):
        try:
            d = date.fromisoformat(f.stem)
        except ValueError:
            continue
        if today - timedelta(days=days) < d <= today:
            cands += json.loads(f.read_text(encoding="utf-8"))
    out, urls, topics = [], set(), set()
    for c in sorted(cands, key=lambda c: c.get("total", 0), reverse=True):
        key = topic_key(c.get("topic", ""))
        if c["item"]["url"] in urls or (key and key in topics):
            continue
        urls.add(c["item"]["url"])
        topics.add(key)
        out.append(c)
        if len(out) >= n:
            break
    return out


def item_facts(cand: dict, opt: dict, lc: dict) -> dict | None:
    """1件分の事実。元記事の本文が取れない・個人向けでない・ツールの話でないなら None。"""
    item = cand["item"]
    text = research.extract([item["url"]]).get(item["url"], "")
    if len(text) < research.MIN_TEXT:
        print(f"[roundup] 本文が取れなかった: {item['url']}")
        return None
    links = [{"url": item["url"], "text": item["title"]}]
    try:
        links += research.page_links(item["url"])
    except Exception as e:  # noqa: BLE001
        print(f"[roundup] リンクを取れなかった: {e}")
    listed = "\n".join(f"{i}. {c['url']}（{c['text']}）" for i, c in enumerate(links, 1))
    topic = f"{item['title']}（{cand.get('detail', {}).get('summary', '')}）"
    text = text[:opt.get("max_chars", 6000)]
    f = extract_json(call_llm(ITEM_PROMPT.format(topic=topic, text=text, links=listed), lc, 6000, ITEM_SCHEMA, "item"))
    if not f.get("reader_fit", True) or not f.get("is_tool", True) or not f.get("facts"):
        print(f"[roundup] 外した（個人向けでない・ツールの話でない）: {item['title']}")
        return None
    k = f.get("official_link", 0)
    official = links[k - 1]["url"] if isinstance(k, int) and 2 <= k <= len(links) else ""  # 1=ニュース記事そのもの
    return {"name": f.get("name") or item["title"], "facts": f["facts"], "official_url": official,
            "source": {"title": item["title"], "url": item["url"], "text": text}}


def build_html(out: dict, n_items: int) -> str:
    easy = "".join(f"<li>{html.escape(x)}</li>" for x in out.get("easy") or [])
    parts = [out.get("intro", ""),
             f"{writer.EASY_DIV}<p><strong>ざっくり言うと</strong></p><ul>{easy}</ul></div>"]
    items = sorted(out.get("items") or [], key=lambda x: x.get("n", 0))[:n_items]
    for i, it in enumerate(items, 1):
        meta = "".join(f"<li><strong>{k}：</strong>{html.escape(v)}</li>"
                       for k, v in (("向いている人", it.get("who", "")), ("料金", it.get("price", ""))) if v)
        parts.append(f"<h2>{i}. {html.escape(it.get('heading', ''))}</h2>\n{it.get('body', '')}"
                     + (f"\n<ul>{meta}</ul>" if meta else ""))
    parts.append(f"<h2>まとめ</h2>\n{out.get('closing', '')}")
    return "\n".join(p for p in parts if p)


def add_links(body: str, urls: list[str]) -> str:
    """i 番目のツールの節の終わり（次の h2 の手前）に公式サイトのリンクを入れる。
    書き直しで見出しの数が変わっていたら、まとめの手前に一覧で入れる。"""
    heads = [m.start() for m in re.finditer(r"<h2[\s>]", body)]
    if len(heads) == len(urls) + 1:
        for i in reversed(range(len(urls))):
            if urls[i]:
                at = heads[i + 1]
                body = body[:at] + writer.link_p("公式サイト", urls[i]) + "\n" + body[at:]
        return body
    ps = "".join(writer.link_p(f"{i}の公式サイト", u) for i, u in enumerate(urls, 1) if u)
    j = body.rfind("<h2")
    return body[:j] + ps + "\n" + body[j:] if ps and j != -1 else body


def check(body: str) -> list[str]:
    """公開を止める問題（あれば下書きにする）。"""
    problems = []
    words = sorted(set(writer.EXPERIENCE_WORDS.findall(body)))
    if words:
        problems.append("体験したかのような表現: " + "、".join(words))
    leaked = sorted(set(re.findall(r"事実メモ|参考記事|運営者メモ", body)))
    if leaked:
        problems.append("指示の言葉が本文に出ている: " + "、".join(leaked))
    return problems


def message(art: dict, wp: dict | None, published: bool) -> str:
    head = "【今週のまとめを公開しました】" if published else "【今週のまとめ（下書き）】"
    lines = [head, art["title"], "", "紹介したツール:"] + [f"・{x}" for x in art["tools"]]
    if art["warnings"]:
        lines += ["", "確認してほしい点:"] + [f"・{w}" for w in art["warnings"]]
    lines.append("")
    if wp and published:
        lines += [wp["link"], "", f"取り下げるときは、管理画面で下書きに戻してください（投稿ID {wp['id']}）"]
    elif wp:
        base = wp["edit_url"].split("/wp-admin/")[0]
        lines += ["問題があったので公開せず下書きにしました。", "プレビュー:", f"{base}/?p={wp['id']}&preview=true", "",
                  f"よければ「公開 {wp['id']}」、ボツなら「削除 {wp['id']}」と返信"]
    else:
        lines.append("WordPress 未設定のため、GitHub の data/roundups/ に保存しました")
    return "\n".join(lines)


def run(dry_run: bool = False, config_path: Path = BASE / "config.yaml", today: date | None = None) -> dict | None:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    ro, opt, lc = cfg.get("roundup") or {}, cfg.get("article") or {}, llm_cfg(cfg)
    today = today or datetime.now(JST).date()
    picked = pick(BASE / "data" / "candidates", today, ro.get("days", 7), ro.get("max_items", 6) + 3)
    tools = []
    for c in picked:
        if len(tools) >= ro.get("max_items", 6):
            break
        try:
            f = item_facts(c, opt, lc)
        except Exception as e:  # noqa: BLE001  1件の失敗で全体を止めない
            print(f"[roundup] 事実を取れなかった {c['item']['url']}: {e}")
            continue
        if f:
            tools.append(f)
    print(f"[roundup] 候補 {len(picked)}件 → 紹介 {len(tools)}件")
    if len(tools) < ro.get("min_items", 3):
        text = f"【今週のまとめ】紹介できるツールが{len(tools)}件だけだったので、今週は作りませんでした"
        print(text) if dry_run else send_line(text)
        return None

    memo = [{"n": i, "name": t["name"], "facts": t["facts"]} for i, t in enumerate(tools, 1)]
    out = extract_json(call_llm(WRITE_PROMPT.format(genre=cfg["genre"].strip(), rules=writer.RULES,
                                                    items=json.dumps(memo, ensure_ascii=False, indent=1)),
                                lc, 16000, WRITE_SCHEMA, "roundup"))
    body = build_html(out, len(tools))
    body = writer.trim_hedges(body, lc)
    body, clarity = writer.clarify(body, lc)
    sources = [t["source"] for t in tools]
    body, sim = writer.fix_similarity(body, sources, opt, lc)
    body = add_links(body, [t["official_url"] for t in tools])
    problems = check(body)
    if not sim["ok"]:
        problems.append(f"元記事との類似が高い（一致率{sim['ratio']:.1%}、最長{sim['longest']}字）")
    title = out.get("title") or "今週の新着AIツールまとめ"
    art = {"title": title, "slug": f"{out.get('slug') or 'weekly-ai-tools'}-{today:%Y%m%d}",
           "excerpt": out.get("excerpt", ""), "tools": [t["name"] for t in tools], "warnings": problems,
           "similarity": {"ratio": sim["ratio"], "longest": sim["longest"], "ok": sim["ok"]}, "clarity": clarity,
           "sources": [{"title": s["title"], "url": s["url"]} for s in sources]}

    use_wp = not dry_run and wordpress.configured()
    featured = None
    img_opt = cfg.get("image") or {}
    if use_wp and img_opt.get("enabled"):
        try:
            data = images.generate(out.get("image_featured") or title, img_opt)
            featured = wordpress.upload_media(data, f"{art['slug']}.jpg", title)["id"]
        except Exception as e:  # noqa: BLE001
            art["warnings"].append(f"アイキャッチを作れなかった: {str(e)[:150]}")
    found = affiliate.match(affiliate.load(), title, writer.plain(body))
    body = affiliate.insert(body, found)
    art["affiliates"] = [e["name"] for e in found]
    art["content"] = writer.assemble(body, sources)

    out_dir = BASE / "data" / "roundups"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{today}.html").write_text(art["content"], encoding="utf-8")

    wp, published = None, False
    if use_wp:
        published = ro.get("auto_publish", True) and not problems
        cats = []
        try:
            cats = [wordpress.category_id(ro.get("category", "今週のAIツールまとめ"))]
        except Exception as e:  # noqa: BLE001
            art["warnings"].append(f"カテゴリを設定できなかった: {str(e)[:120]}")
        wp = wordpress.post_draft(title, art["content"], art["slug"], art["excerpt"], featured, cats,
                                  status="publish" if published else "draft")
        print(f"[roundup] WordPress {'公開' if published else '下書き'}: {wp['link'] or wp['edit_url']}")
    meta = {k: v for k, v in art.items() if k != "content"}
    meta.update(wordpress=wp, published=published, created_at=datetime.now(JST).isoformat(timespec="seconds"))
    (out_dir / f"{today}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    text = message(art, wp, published)
    print("\n" + text) if dry_run else send_line(text)
    return {**art, "wordpress": wp, "published": published}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        run(args.dry_run)
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        if not args.dry_run:
            try:
                send_line("【今週のまとめ】エラー\n" + err[-1500:])
            except Exception as e:  # noqa: BLE001
                print(f"[notify] LINEへのエラー通知も失敗: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
