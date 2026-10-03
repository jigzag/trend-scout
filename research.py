"""参考記事の取得（Ver.1）。Tavily で関連記事を探し、本文を取り出す。
Tavily が使えないときは、候補の元記事だけを requests で直接取得して続行する。
"""
from __future__ import annotations

import html
import os
import re
from urllib.parse import urlsplit

import requests

from sources import UA

TAVILY_SEARCH = "https://api.tavily.com/search"
TAVILY_EXTRACT = "https://api.tavily.com/extract"
MIN_TEXT = 600       # これより短い本文は参考記事として使わない（一覧ページやエラーページ対策）


def _domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def _tavily_key() -> str:
    return os.environ.get("TAVILY_API_KEY", "").strip()


def _tavily(url: str, body: dict) -> dict:
    r = requests.post(url, headers={"Authorization": f"Bearer {_tavily_key()}"}, json=body, timeout=60)
    if r.status_code >= 300:
        raise RuntimeError(f"Tavily API error {r.status_code}: {r.text[:300]}")
    return r.json()


def html_to_text(raw: str) -> str:
    """requests で取ったHTMLから本文らしきテキストを取り出す簡易版（Tavily が使えないときの予備）。"""
    raw = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form|noscript)[^>]*>.*?</\1>", " ", raw)
    m = re.search(r"(?is)<(article|main)[^>]*>(.*?)</\1>", raw)
    if m:
        raw = m.group(2)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if len(ln) >= 2)


def fetch_direct(url: str) -> str:
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return html_to_text(r.text)


def extract(urls: list[str]) -> dict[str, str]:
    """URL → 本文。Tavily Extract → 失敗分は requests で直接取得。"""
    out: dict[str, str] = {}
    if _tavily_key() and urls:
        try:
            js = _tavily(TAVILY_EXTRACT, {"urls": urls, "format": "text"})
            for r in js.get("results", []):
                out[r["url"]] = r.get("raw_content") or ""
        except Exception as e:  # noqa: BLE001
            print(f"[research] Tavily extract 失敗 {e}")
    for u in urls:
        if len(out.get(u, "")) < MIN_TEXT:
            try:
                out[u] = fetch_direct(u)
            except Exception as e:  # noqa: BLE001
                print(f"[research] 直接取得失敗 {u}: {e}")
    return out


def search(query: str, opt: dict) -> list[dict]:
    """関連記事を探す。[{title, url, text}]。キーがなければ空。"""
    if not _tavily_key():
        print("[research] TAVILY_API_KEY が空のため、関連記事の検索は省略（元記事だけで書く）")
        return []
    body = {"query": query, "search_depth": "basic", "max_results": opt.get("max_results", 8),
            "include_raw_content": "text", "time_range": opt.get("time_range", "month")}
    if opt.get("country"):
        body["country"] = opt["country"]
    js = _tavily(TAVILY_SEARCH, body)
    return [{"title": r.get("title", ""), "url": r["url"], "text": r.get("raw_content") or r.get("content") or ""}
            for r in js.get("results", [])]


def gather(candidate: dict, opt: dict) -> list[dict]:
    """候補の元記事 + 別ドメインの関連記事で、最大 max_sources 件の参考記事を返す。
    返り値: [{title, url, text}]（text は max_chars で切る）"""
    item = candidate["item"]
    max_sources, max_chars = opt.get("max_sources", 3), opt.get("max_chars", 6000)
    sources = []
    main_text = extract([item["url"]]).get(item["url"], "")
    if len(main_text) >= MIN_TEXT:
        sources.append({"title": item["title"], "url": item["url"], "text": main_text})
    else:
        print(f"[research] 元記事の本文が取れなかった: {item['url']}")

    query = candidate.get("topic") or item["title"]
    if candidate.get("topic") and candidate["topic"] not in item["title"]:
        query = f"{candidate['topic']} {item['title']}"
    try:
        found = search(query, opt)
    except Exception as e:  # noqa: BLE001  検索の失敗では止めない
        print(f"[research] 検索失敗 {e}")
        found = []
    used = {_domain(s["url"]) for s in sources}
    for r in found:
        if len(sources) >= max_sources:
            break
        if _domain(r["url"]) in used or len(r["text"]) < MIN_TEXT:
            continue
        sources.append(r)
        used.add(_domain(r["url"]))
    for s in sources:
        s["text"] = s["text"][:max_chars]
    return sources
