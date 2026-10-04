"""記事生成 Ver.1（＋ Ver.2 の WordPress 下書き投稿）
  候補の読み込み → 参考記事の取得 → 事実抽出 → 構成 → 執筆 → 校閲 → 類似度チェック
  → data/articles/ に保存 → WordPress に下書き → LINE に通知

使い方:
  python article.py --n 1                      # 今日の候補1で記事を作る
  python article.py --n 2 --memo "無料版は3回まで"   # 運営者メモ付き
  python article.py --n 1 --date 2026-10-04 --dry-run   # 投稿も通知もせず、画面とファイルに出す
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import yaml

import research
import wordpress
import writer
from main import BASE, JST
from notifier import send_line


def load_candidate(n: int, day: str | None) -> tuple[str, dict]:
    folder = BASE / "data" / "candidates"
    if day:
        path = folder / f"{day}.json"
    else:
        files = sorted(folder.glob("*.json"))
        if not files:
            raise RuntimeError("data/candidates に候補ファイルがありません")
        path = files[-1]  # いちばん新しい日付
    cands = json.loads(path.read_text(encoding="utf-8"))
    if not 1 <= n <= len(cands):
        raise RuntimeError(f"{path.name} の候補は {len(cands)} 件です（--n {n} は範囲外）")
    return path.stem, cands[n - 1]


def llm_cfg(cfg: dict) -> dict:
    """記事用のモデル設定（article.llm で上書きできる）。"""
    return {**cfg["llm"], **(cfg.get("article", {}).get("llm") or {})}


def build(cfg: dict, cand: dict, memo: str) -> dict:
    opt = cfg.get("article", {})
    lc = llm_cfg(cfg)
    item = cand["item"]
    topic = f"{item['title']}（{cand.get('detail', {}).get('summary', '')}）"

    sources = research.gather(cand, opt)
    if not sources:
        raise RuntimeError(f"参考記事の本文を1件も取得できませんでした: {item['url']}")
    print(f"[article] 参考記事 {len(sources)}件: " + ", ".join(s["url"] for s in sources))

    facts = writer.extract_facts(topic, sources, lc)
    outline = writer.make_outline(cfg["genre"], facts, memo, lc)
    body = writer.write_body(cfg["genre"], outline, facts, memo, opt, lc)
    body = writer.review(body, facts, memo, lc)
    body, sim = writer.fix_similarity(body, sources, opt, lc)
    warnings = writer.check_rules(body, memo, opt.get("min_chars", 3000))
    if not sim["ok"]:
        warnings.append(f"参考記事との類似が高い（一致率{sim['ratio']:.1%}、最長{sim['longest']}字）")
    return {
        "title": outline.get("title") or item["title"], "slug": outline.get("slug", ""),
        "excerpt": outline.get("excerpt", ""), "persona": outline.get("persona", ""),
        "content": writer.assemble(body, sources), "chars": len(writer.plain(body)),
        "similarity": sim, "warnings": warnings, "memo": memo,
        "sources": [{"title": s["title"], "url": s["url"]} for s in sources],
        "candidate": {"title": item["title"], "url": item["url"]},
    }


def message(art: dict, wp: dict | None) -> str:
    lines = ["【下書きができました】", art["title"], "",
             f"本文 {art['chars']}字 / 参考記事 {len(art['sources'])}件 / "
             f"類似度 一致率{art['similarity']['ratio']:.1%}・最長{art['similarity']['longest']}字"]
    if art["warnings"]:
        lines += ["", "確認してほしい点:"] + [f"・{w}" for w in art["warnings"]]
    lines.append("")
    if wp:
        lines += ["編集画面:", wp["edit_url"]]
    else:
        lines.append("WordPress 未設定のため、GitHub の data/articles/ に保存しました")
    return "\n".join(lines)


def run(n: int, memo: str = "", day: str | None = None, dry_run: bool = False,
        config_path: Path = BASE / "config.yaml") -> dict:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cand_day, cand = load_candidate(n, day)
    print(f"[article] {cand_day} 候補{n}: {cand['item']['title']}")
    art = build(cfg, cand, memo.strip())

    out_dir = BASE / "data" / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{cand_day}-{n}"
    (out_dir / f"{stem}.html").write_text(art["content"], encoding="utf-8")

    wp = None
    if not dry_run and wordpress.configured():
        wp = wordpress.post_draft(art["title"], art["content"], art["slug"], art["excerpt"])
        print(f"[article] WordPress 下書き: {wp['edit_url']}")
    meta = {k: v for k, v in art.items() if k != "content"}
    meta["wordpress"] = wp
    meta["created_at"] = datetime.now(JST).isoformat(timespec="seconds")
    (out_dir / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    text = message(art, wp)
    if dry_run:
        print("\n" + text)
    else:
        send_line(text)
    return {**art, "wordpress": wp}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1, help="候補番号（1〜3）")
    ap.add_argument("--memo", default="", help="運営者メモ（任意）")
    ap.add_argument("--date", default=None, help="候補の日付 YYYY-MM-DD（省略時はいちばん新しい日）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        run(args.n, args.memo, args.date or None, args.dry_run)
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        if not args.dry_run:
            try:
                send_line("【記事生成】エラー\n" + err[-1500:])
            except Exception as e:  # noqa: BLE001
                print(f"[notify] LINEへのエラー通知も失敗: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
