"""LINE の「公開」「削除」から呼ばれる WordPress 操作。
  python wp_command.py --action publish [--id 23]   # 省略時は、いちばん新しく作った記事の下書き
  python wp_command.py --action trash   [--id 23]
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback

import wordpress
from main import BASE
from notifier import send_line


def latest_by_created() -> int:
    """作成日時（created_at）がいちばん新しい記事の投稿ID。git checkout でファイル時刻が揃うため、こちらを使う。"""
    best = None
    for f in (BASE / "data" / "articles").glob("*.json"):
        meta = json.loads(f.read_text(encoding="utf-8"))
        pid = (meta.get("wordpress") or {}).get("id")
        if pid and (best is None or meta.get("created_at", "") > best[0]):
            best = (meta.get("created_at", ""), int(pid))
    if not best:
        raise RuntimeError("下書きが見つかりません（data/articles/ に記事がない）")
    return best[1]


def run(action: str, post_id: str = "") -> str:
    pid = int(post_id) if str(post_id).strip() else latest_by_created()
    post = wordpress.get_post(pid)
    if action == "publish":
        if post["status"] == "publish":
            return f"すでに公開済みです\n{post['title']}\n{post['link']}"
        if post["status"] == "trash":
            return f"ゴミ箱にある記事は公開できません（投稿ID {pid}）"
        done = wordpress.set_status(pid, "publish")
        return f"【公開しました】\n{done['title']}\n{done['link']}"
    if action == "trash":
        if post["status"] == "publish":
            return f"公開済みの記事は LINE からは削除しません。管理画面で操作してください\n{post['link']}"
        wordpress.trash(pid)
        return f"【ゴミ箱へ移しました】\n{post['title']}（投稿ID {pid}）"
    raise ValueError(f"unknown action: {action}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", required=True, choices=["publish", "trash"])
    ap.add_argument("--id", default="")
    args = ap.parse_args()
    try:
        text = run(args.action, args.id)
        print(text)
        send_line(text)
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        try:
            send_line("【公開・削除】エラー\n" + err[-1000:])
        except Exception as e:  # noqa: BLE001
            print(f"[notify] LINEへのエラー通知も失敗: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
