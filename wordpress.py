"""WordPress への下書き投稿（Ver.2）。REST API + アプリケーションパスワード。
Secrets: WP_URL（https://nocode-ai.net）/ WP_USER / WP_APP_PASSWORD
"""
from __future__ import annotations

import os

import requests


def configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"))


def post_draft(title: str, content: str, slug: str = "", excerpt: str = "") -> dict:
    """下書きとして投稿し {id, link, edit_url} を返す。公開はしない。"""
    base = os.environ["WP_URL"].strip().rstrip("/")
    auth = (os.environ["WP_USER"].strip(), os.environ["WP_APP_PASSWORD"].strip())
    body = {"title": title, "content": content, "status": "draft"}
    if slug:
        body["slug"] = slug
    if excerpt:
        body["excerpt"] = excerpt
    r = requests.post(f"{base}/wp-json/wp/v2/posts", auth=auth, json=body, timeout=60)
    if r.status_code == 403:
        raise RuntimeError("WordPress 403: エックスサーバーの『REST API アクセス制限（国外）』がONの可能性"
                           f"（SITE_SETUP 手順2）: {r.text[:200]}")
    if r.status_code == 401:
        raise RuntimeError(f"WordPress 401: WP_USER / WP_APP_PASSWORD を確認: {r.text[:200]}")
    if r.status_code >= 300:
        raise RuntimeError(f"WordPress API error {r.status_code}: {r.text[:300]}")
    js = r.json()
    return {"id": js["id"], "link": js.get("link", ""),
            "edit_url": f"{base}/wp-admin/post.php?post={js['id']}&action=edit"}
