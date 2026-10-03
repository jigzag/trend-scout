"""話題ソースの取得。各関数は Item のリストを返す。失敗時は例外を投げる（main 側で拾う）。"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict

import requests

UA = {"User-Agent": "Mozilla/5.0 (trend-scout; personal use)"}
TIMEOUT = 20


@dataclass
class Item:
    source: str
    title: str
    url: str
    summary: str = ""
    signal: int | None = None      # はてブ数 / いいね数 / ポイントなど
    signal_label: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------- 共通 ----------
def _get(url: str) -> requests.Response:
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r


def clean_text(s: str | None, limit: int = 200) -> str:
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(xml_text: str) -> list[dict]:
    """RSS2.0 / RSS1.0(RDF) / Atom をまとめて扱う簡易パーサ。"""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    out = []
    for el in root.iter():
        name = _local(el.tag)
        if name not in ("item", "entry"):
            continue
        d = {"title": "", "link": "", "summary": "", "extra": {}}
        for c in el:
            cn = _local(c.tag)
            if cn == "title":
                d["title"] = (c.text or "").strip()
            elif cn == "link":
                href = c.attrib.get("href")
                rel = c.attrib.get("rel", "alternate")
                if href and rel == "alternate":
                    d["link"] = href
                elif c.text and not d["link"]:
                    d["link"] = c.text.strip()
            elif cn in ("description", "summary", "content") and not d["summary"]:
                d["summary"] = c.text or ""
            elif cn == "bookmarkcount":
                try:
                    d["extra"]["bookmarks"] = int(c.text)
                except (TypeError, ValueError):
                    pass
        if not d["link"]:
            about = el.attrib.get("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}about")
            if about:
                d["link"] = about
        if d["title"] and d["link"]:
            out.append(d)
    return out


# ---------- 各ソース ----------
def fetch_hatena(limit: int) -> list[Item]:
    feed = parse_feed(_get("https://b.hatena.ne.jp/hotentry/it.rss").text)
    items = []
    for d in feed[:limit]:
        bm = d["extra"].get("bookmarks")
        items.append(Item("はてブ", d["title"], d["link"], clean_text(d["summary"]),
                          bm, f"{bm}users" if bm is not None else ""))
    return items


def fetch_zenn(limit: int) -> list[Item]:
    data = _get(f"https://zenn.dev/api/articles?order=daily&count={limit}").json()
    items = []
    for a in data.get("articles", [])[:limit]:
        likes = a.get("liked_count")
        items.append(Item("Zenn", a.get("title", ""), "https://zenn.dev" + a.get("path", ""),
                          "", likes, f"{likes}いいね" if likes is not None else ""))
    return items


def fetch_qiita(limit: int) -> list[Item]:
    feed = parse_feed(_get("https://qiita.com/popular-items/feed").text)
    return [Item("Qiita", d["title"], d["link"], clean_text(d["summary"])) for d in feed[:limit]]


def fetch_hackernews(limit: int) -> list[Item]:
    data = _get(f"https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage={limit}").json()
    items = []
    for h in data.get("hits", [])[:limit]:
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        pts = h.get("points")
        items.append(Item("HackerNews", h.get("title", ""), url, "", pts,
                          f"{pts}pt" if pts is not None else ""))
    return items


def fetch_producthunt(limit: int) -> list[Item]:
    feed = parse_feed(_get("https://www.producthunt.com/feed").text)
    return [Item("ProductHunt", d["title"], d["link"], clean_text(d["summary"])) for d in feed[:limit]]


def fetch_rss(url: str, label: str, limit: int) -> list[Item]:
    """config.yaml で url を指定した任意のRSS/Atom。"""
    feed = parse_feed(_get(url).text)
    return [Item(label, d["title"], d["link"], clean_text(d["summary"])) for d in feed[:limit]]


FETCHERS = {
    "hatena": fetch_hatena,
    "zenn": fetch_zenn,
    "qiita": fetch_qiita,
    "hackernews": fetch_hackernews,
    "producthunt": fetch_producthunt,
}
