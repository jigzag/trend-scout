"""LINE Messaging API でプッシュ通知（LINE Notify は2025年3月で終了済み）。"""
from __future__ import annotations

import os
from datetime import date

import requests
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_LEN = 5000  # テキストメッセージの上限


def clean_url(url: str) -> str:
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query) if not k.startswith("utm_")]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


def build_message(today: date, top: list[dict], stats: dict) -> str:
    wd = "月火水木金土日"[today.weekday()]
    lines = [f"【今日の記事候補】{today.month}/{today.day}({wd})", ""]
    if not top:
        lines.append("今日はジャンルに合う候補がありませんでした。")
    for n, r in enumerate(top, 1):
        it, d = r["item"], r.get("detail", {})
        src = it.source + (f" {it.signal_label}" if it.signal_label else "")
        if it.extra.get("also_on"):
            src += " / " + "・".join(it.extra["also_on"])
        lines += [
            f"■{n}. {it.title}",
            f"[{src}] スコア{r['total']}（関連{r['relevance']} 検証{r['testable']} 収益{r['monetizable']} 話題{r.get('buzz', '-')}）",
        ]
        if d.get("summary"):
            lines.append(f"概要: {d['summary']}")
        if d.get("why"):
            lines.append(f"理由: {d['why']}")
        if d.get("try"):
            lines.append(f"試すなら: {d['try']}")
        if d.get("angle"):
            lines.append(f"タイトル案: {d['angle']}")
        if d.get("products"):
            lines.append("紹介候補: " + "、".join(d["products"]))
        lines += [clean_url(it.url), ""]
    foot = f"取得{stats['fetched']}件 → 新規{stats['new']}件を採点 → 上位{len(top)}件"
    if stats.get("failed"):
        foot += f"\n取得失敗: {', '.join(stats['failed'])}"
    lines.append(foot)
    text = "\n".join(lines)
    return text if len(text) <= MAX_LEN else text[: MAX_LEN - 20] + "\n…(省略)"


def send_line(text: str) -> None:
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("LINE_USER_ID", "").strip()
    if not token or not user_id:
        raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN / LINE_USER_ID が未設定（Secretの名前を確認）")
    if not user_id.startswith("U"):
        raise RuntimeError(f"LINE_USER_ID が U で始まっていません（先頭: {user_id[:2]}…）。"
                           "チャネル基本設定の『あなたのユーザーID』を使ってください")
    r = requests.post(
        LINE_PUSH_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": user_id, "messages": [{"type": "text", "text": text}]},
        timeout=30,
    )
    if r.status_code >= 300:
        raise RuntimeError(f"LINE push failed: {r.status_code} {r.text}")
