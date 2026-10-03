"""話題検知 Ver.0
  取得 → 重複除去 → 既出除外 → AI採点 → 上位N件の詳細 → LINE通知 → 記録

使い方:
  python main.py            # 本番（LINEに送信し、履歴を更新）
  python main.py --dry-run  # 送信せず、通知文を画面に表示（履歴も更新しない）
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from history import History, dedupe
from notifier import build_message, send_line
from scorer import add_details, pick_diverse, score_all
from sources import FETCHERS, fetch_rss

BASE = Path(__file__).parent
JST = timezone(timedelta(hours=9))


def run(dry_run: bool = False, config_path: Path = BASE / "config.yaml") -> str:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    today = datetime.now(JST).date()

    # 1. 取得（1ソース落ちても続行）
    items, failed = [], []
    for name, opt in cfg["sources"].items():
        if not opt.get("enabled", True):
            continue
        try:
            if opt.get("url"):
                got = fetch_rss(opt["url"], opt.get("label", name), opt.get("limit", 20))
            else:
                got = FETCHERS[name](opt.get("limit", 20))
            for idx, it in enumerate(got):  # ソース内の順位 → 話題度 0〜10
                it.extra["buzz"] = round(10 * (1 - idx / max(len(got), 1)), 1)
            items += got
            print(f"[fetch] {name}: {len(got)}件")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"[fetch] {name}: 失敗 {e}", file=sys.stderr)
    fetched = len(items)

    # 2. 重複除去・既出除外
    history = History(BASE / "data" / "seen.json", cfg.get("recheck_days", 14))
    items = history.filter_new(dedupe(items), today)
    stats = {"fetched": fetched, "new": len(items), "failed": failed}
    print(f"[filter] 新規 {len(items)}件")

    # 3. 採点 → 上位の詳細
    top = []
    if items:
        scored = score_all(items, cfg["genre"], cfg["weights"], cfg["llm"])
        top = pick_diverse(scored, cfg.get("top_n", 3), cfg.get("min_relevance", 5))
        if top:
            top = add_details(top, cfg["genre"], cfg["llm"])

    # 4. 通知
    text = build_message(today, top, stats)
    if dry_run:
        print("\n" + text)
        return text
    send_line(text)
    print("[notify] LINE送信完了")

    # 5. 記録（次工程=記事生成の入力にもなる）
    history.mark([r["item"] for r in top], today)
    history.save()
    out = BASE / "data" / "candidates" / f"{today.isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        [{**{k: v for k, v in r.items() if k != "item"}, "item": r["item"].to_dict()} for r in top],
        ensure_ascii=False, indent=2), encoding="utf-8")
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-line", action="store_true", help="LINEにテスト送信だけ行う")
    args = ap.parse_args()
    if args.test_line:
        send_line("【話題検知】テスト送信です。これが届けばLINE設定はOKです。")
        print("[notify] テスト送信OK")
        return
    try:
        run(dry_run=args.dry_run)
    except Exception:  # 失敗したこと自体をLINEで知らせる
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        if not args.dry_run:
            try:
                send_line("【話題検知】実行エラー\n" + err[-1500:])
            except Exception as e:  # noqa: BLE001
                print(f"[notify] LINEへのエラー通知も失敗: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
