"""WordPress への下書き投稿（Ver.2）。REST API + アプリケーションパスワード。
Secrets: WP_URL（https://nocode-ai.net）/ WP_USER / WP_APP_PASSWORD
"""
from __future__ import annotations

import os

import requests


def configured() -> bool:
    return all(os.environ.get(k, "").strip() for k in ("WP_URL", "WP_USER", "WP_APP_PASSWORD"))


def _base() -> str:
    return os.environ["WP_URL"].strip().rstrip("/")


def _auth() -> tuple[str, str]:
    return os.environ["WP_USER"].strip(), os.environ["WP_APP_PASSWORD"].strip()


def _check(r: requests.Response) -> dict:
    if r.status_code == 403:
        raise RuntimeError("WordPress 403: エックスサーバーの『REST API アクセス制限（国外）』がONの可能性"
                           f"（SITE_SETUP 手順2）: {r.text[:200]}")
    if r.status_code == 401:
        raise RuntimeError(f"WordPress 401: WP_USER / WP_APP_PASSWORD を確認: {r.text[:200]}")
    if r.status_code >= 300:
        raise RuntimeError(f"WordPress API error {r.status_code}: {r.text[:300]}")
    return r.json()


def upload_media(data: bytes, filename: str, alt: str = "") -> dict:
    """画像をメディアライブラリに上げて {id, url} を返す。"""
    js = _check(requests.post(
        f"{_base()}/wp-json/wp/v2/media", auth=_auth(), data=data, timeout=120,
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Content-Type": "image/jpeg"}))
    if alt:
        requests.post(f"{_base()}/wp-json/wp/v2/media/{js['id']}", auth=_auth(),
                      json={"alt_text": alt}, timeout=60)
    return {"id": js["id"], "url": js.get("source_url", "")}


def _post_info(js: dict) -> dict:
    return {"id": js["id"], "status": js.get("status", ""), "link": js.get("link", ""),
            "title": (js.get("title") or {}).get("raw") or (js.get("title") or {}).get("rendered", "")}


def get_post(post_id: int) -> dict:
    return _post_info(_check(requests.get(f"{_base()}/wp-json/wp/v2/posts/{post_id}", auth=_auth(),
                                          params={"context": "edit"}, timeout=60)))


def set_status(post_id: int, status: str) -> dict:
    return _post_info(_check(requests.post(f"{_base()}/wp-json/wp/v2/posts/{post_id}", auth=_auth(),
                                           json={"status": status}, timeout=60)))


def trash(post_id: int) -> None:
    """ゴミ箱へ（完全削除はしない。管理画面のゴミ箱から戻せる）。"""
    _check(requests.delete(f"{_base()}/wp-json/wp/v2/posts/{post_id}", auth=_auth(), timeout=60))


def post_draft(title: str, content: str, slug: str = "", excerpt: str = "",
               featured_media: int | None = None) -> dict:
    """下書きとして投稿し {id, link, edit_url} を返す。公開はしない。"""
    body = {"title": title, "content": content, "status": "draft"}
    if slug:
        body["slug"] = slug
    if excerpt:
        body["excerpt"] = excerpt
    if featured_media:
        body["featured_media"] = featured_media
    js = _check(requests.post(f"{_base()}/wp-json/wp/v2/posts", auth=_auth(), json=body, timeout=60))
    return {"id": js["id"], "link": js.get("link", ""),
            "edit_url": f"{_base()}/wp-admin/post.php?post={js['id']}&action=edit"}
