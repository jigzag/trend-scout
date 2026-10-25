"""重複除去と「過去に出した候補」の記録。"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from sources import Item

DROP_PARAMS = re.compile(r"^(utm_|ref$|ref_|source$|fbclid$|gclid$)")


def normalize_url(url: str) -> str:
    p = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query) if not DROP_PARAMS.match(k)]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower().removeprefix("www."), path, urlencode(q), ""))


def _norm_title(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", t.lower())


def dedupe(items: list[Item]) -> list[Item]:
    """同じURL / 同じタイトルを1件にまとめる（ソース名は extra.also_on に残す）。"""
    by_key: dict[str, Item] = {}
    title_index: dict[str, str] = {}
    for it in items:
        key = normalize_url(it.url)
        tkey = _norm_title(it.title)
        existing_key = key if key in by_key else title_index.get(tkey)
        if existing_key:
            by_key[existing_key].extra.setdefault("also_on", []).append(it.source)
            continue
        by_key[key] = it
        if tkey:
            title_index[tkey] = key
    return list(by_key.values())


class History:
    def __init__(self, path: Path, recheck_days: int):
        self.path = path
        self.recheck_days = recheck_days
        self.seen: dict[str, str] = {}
        if path.exists():
            self.seen = json.loads(path.read_text(encoding="utf-8"))
        self.topics_path = path.with_name("seen_topics.json")   # 話題ラベル → 最後に通知した日
        self.topics: dict[str, str] = {}
        if self.topics_path.exists():
            self.topics = json.loads(self.topics_path.read_text(encoding="utf-8"))

    def filter_new(self, items: list[Item], today: date) -> list[Item]:
        limit = today - timedelta(days=self.recheck_days)
        out = []
        for it in items:
            d = self.seen.get(normalize_url(it.url))
            if d and date.fromisoformat(d) >= limit:
                continue
            out.append(it)
        return out

    def mark(self, items: list[Item], today: date) -> None:
        for it in items:
            self.seen[normalize_url(it.url)] = today.isoformat()
        cutoff = today - timedelta(days=max(self.recheck_days * 2, 30))
        self.seen = {k: v for k, v in self.seen.items() if date.fromisoformat(v) >= cutoff}

    def recent_topics(self, today: date) -> set[str]:
        """最近（recheck_days 以内）に通知した話題ラベル。別のURLでも同じ話題なら再通知しないため。"""
        limit = today - timedelta(days=self.recheck_days)
        return {k for k, d in self.topics.items() if date.fromisoformat(d) >= limit}

    def mark_topics(self, keys: list[str], today: date) -> None:
        for k in keys:
            if k:
                self.topics[k] = today.isoformat()
        cutoff = today - timedelta(days=max(self.recheck_days * 2, 30))
        self.topics = {k: v for k, v in self.topics.items() if date.fromisoformat(v) >= cutoff}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.seen, ensure_ascii=False, indent=1), encoding="utf-8")
        self.topics_path.write_text(json.dumps(self.topics, ensure_ascii=False, indent=1), encoding="utf-8")
