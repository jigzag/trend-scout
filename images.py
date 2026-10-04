"""記事の画像（アイキャッチ＋本文1枚）を OpenAI の画像生成で作る。
実在のツール画面・ロゴ・人物に見える画像は作らない（読者の誤認を避ける）。本文の画像には「AI生成」と明記する。
"""
from __future__ import annotations

import base64
import html
import os

import requests

STYLE = ("Flat vector illustration for a Japanese tech blog, soft pastel colors, clean simple shapes, "
         "friendly and calm mood, plenty of empty space. Absolutely no text, letters, numbers, logos, "
         "brand marks, app screenshots or realistic user interfaces. No real or famous people.")
CAPTION = "イメージ画像（AI生成）"


def generate(prompt: str, opt: dict) -> bytes:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENAI_API_KEY が空です（画像生成）")
    body = {"model": opt.get("model", "gpt-image-2"), "prompt": f"{prompt}\n\nStyle: {STYLE}",
            "size": opt.get("size", "1536x1024"), "quality": opt.get("quality", "low"), "n": 1,
            "output_format": "jpeg", "output_compression": opt.get("compression", 80)}
    r = requests.post("https://api.openai.com/v1/images/generations",
                      headers={"Authorization": f"Bearer {key}"}, json=body, timeout=180)
    if r.status_code >= 300:
        hint = "（OpenAI の組織認証が未完了の可能性）" if "verif" in r.text.lower() else ""
        raise RuntimeError(f"画像生成 API error {r.status_code}{hint}: {r.text[:300]}")
    return base64.b64decode(r.json()["data"][0]["b64_json"])


def figure(url: str, alt: str) -> str:
    return (f'<figure class="wp-block-image size-large"><img src="{html.escape(url)}" alt="{html.escape(alt)}"/>'
            f"<figcaption>{CAPTION}</figcaption></figure>")


def insert_after_scenario(body: str, fig: str) -> str:
    """【想定シナリオ】の見出しの直後に画像を入れる（見出しがなければ最初の h2 の前）。"""
    i = body.find("想定シナリオ")
    if i != -1:
        end = body.find("</h2>", i)
        if end != -1:
            end += len("</h2>")
            return body[:end] + "\n" + fig + body[end:]
    j = body.find("<h2")
    return body[:j] + fig + "\n" + body[j:] if j != -1 else fig + body
