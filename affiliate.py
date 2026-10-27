"""アフィリエイトリンク（Ver.3）。
  台帳 data/affiliates.yaml に「ツール名・別名・リンク」を持ち、記事で扱うツールが台帳にあればリンクの囲みを入れる。
  台帳は LINE の「リンク ツール名 URL」から登録する（affiliate.yml 経由でこのファイルを実行）。

  python affiliate.py --add "Clips by Plastic/Clips" "https://px.a8.net/..."   # 登録（別名は / で区切る）
  python affiliate.py --add "Clips" '<a href="...">...</a><img ...>'           # ASP のHTMLをそのまま登録してもよい
  python affiliate.py --remove "Clips by Plastic"
  python affiliate.py --category "ココナラ" "画像、動画"   # カテゴリで出すリンク（ツール名が本文になくても、そのカテゴリの記事の最後に1行入れる）
  python affiliate.py --list
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import traceback
from datetime import datetime

import yaml

from main import BASE, JST
from notifier import send_line

PATH = BASE / "data" / "affiliates.yaml"
BOX_CLASS = "affiliate-box"
MAX_LINKS = 2  # 1記事に入れるツールの数
PITCH = "自分で作るのが難しければ、プロに頼む方法もあります"  # カテゴリで出すリンクの前置き（台帳の pitch で変えられる）


def load(path=None) -> list[dict]:
    path = path or PATH
    if not path.exists():
        return []
    return yaml.safe_load(path.read_text(encoding="utf-8")) or []


def save(entries: list[dict], path=None) -> None:
    path = path or PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "# アフィリエイトリンク台帳（LINE の「リンク ツール名 URL」で登録。手で編集してもよい）\n"
    path.write_text(head + yaml.safe_dump(entries, allow_unicode=True, sort_keys=False), encoding="utf-8")


def names(e: dict) -> list[str]:
    return [e["name"], *(e.get("aliases") or [])]


def find(entries: list[dict], name: str) -> dict | None:
    key = name.strip().lower()
    return next((e for e in entries if key in (n.lower() for n in names(e))), None)


def add(entries: list[dict], name_part: str, link: str, today: str = "") -> tuple[dict, bool]:
    """name_part は「名前/別名/別名」。登録済みのツール（別名でも可）ならリンクを差し替え、別名は足す。
    返り値: (登録内容, 上書きか)"""
    parts = [p.strip() for p in re.split(r"[/／]", name_part) if p.strip()]
    if not parts:
        raise ValueError("ツール名がありません")
    link = link.strip()
    if not (link.startswith("http") or link.startswith("<")):
        raise ValueError("リンクは https:// で始まるURLか、ASP の HTML（<a ...>）を送ってください")
    old = next((o for o in (find(entries, n) for n in parts) if o), None)
    if old:
        known = [n.lower() for n in names(old)]
        aliases = (old.get("aliases") or []) + [n for n in parts if n.lower() not in known]
        entry = {**old, "aliases": aliases, "link": link, "added": today}  # カテゴリ設定は残す
        entries[entries.index(old)] = entry
    else:
        entry = {"name": parts[0], "aliases": parts[1:], "link": link, "added": today}
        entries.append(entry)
    return entry, bool(old)


def remove(entries: list[dict], name: str) -> dict | None:
    e = find(entries, name)
    if e:
        entries.remove(e)
    return e


def _pattern(name: str) -> re.Pattern:
    # 英字の名前は単語の途中に一致させない（Clef が Clefairy に一致しないように）
    esc = re.escape(name)
    if re.fullmatch(r"[\x20-\x7e]+", name):
        return re.compile(rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])", re.I)
    return re.compile(esc, re.I)


def match(entries: list[dict], title: str, text: str, limit: int = MAX_LINKS) -> list[dict]:
    """タイトルと本文に出てくるツールを、出てくる回数の多い順に返す。"""
    found = []
    for e in entries:
        count = sum(len(_pattern(n).findall(title + "\n" + text)) for n in names(e))
        if count:
            found.append((count, e))
    found.sort(key=lambda x: -x[0])
    return [e for _, e in found[:limit]]


def by_category(entries: list[dict], category: str, exclude: list[dict] = ()) -> list[dict]:
    """そのカテゴリに割り当てたリンク（本文で一致したものは除く）。"""
    if not category:
        return []
    return [e for e in entries if category in (e.get("categories") or []) and e not in exclude]


def resolve_categories(text: str, categories: list[str]) -> list[str]:
    """「画像、動画」のような短い書き方を、config のカテゴリ名に直す。「なし」で解除。"""
    out = []
    for word in (w.strip() for w in re.split(r"[、,，/／\s　]+", text) if w.strip()):
        if word in ("なし", "解除"):
            return []
        hits = [c for c in categories if word in c]
        if not hits:
            raise ValueError(f"「{word}」に当たるカテゴリがありません。使えるのは: " + "、".join(categories))
        out += [c for c in hits if c not in out]
    return out


def link_html(e: dict) -> str:
    link = e["link"]
    if link.startswith("<"):
        return link  # ASP の HTML はそのまま（計測用の画像タグを壊さない）
    label = html.escape(f"{e['name']} の公式サイトを見る")
    return f'<a href="{html.escape(link)}" target="_blank" rel="sponsored noopener">{label}</a>'


def box(found: list[dict]) -> str:
    items = "".join(f"<li>{link_html(e)}</li>" for e in found)
    return (f'<div class="{BOX_CLASS} blank-box"><p><strong>この記事で紹介したツール</strong></p>'
            f"<ul>{items}</ul></div>")


def category_html(cat_links: list[dict]) -> str:
    lines = []
    for e in cat_links:
        link = e["link"] if e["link"].startswith("<") else (
            f'<a href="{html.escape(e["link"])}" target="_blank" rel="sponsored noopener">{html.escape(e["name"])}</a>')
        lines.append(f"<p>{html.escape(e.get('pitch') or PITCH)} → {link}</p>")
    return f'<div class="{BOX_CLASS} blank-box">' + "".join(lines) + "</div>"


def insert(body: str, found: list[dict], cat_links: list[dict] = ()) -> str:
    """想定シナリオの章の終わり（次の h2 の前）と、本文の最後にリンクの囲みを入れる。
    カテゴリで出すリンクは本文の最後に1回だけ（ツールの囲みより後）。"""
    if found:
        b = box(found)
        i = body.find("想定シナリオ")
        if i != -1:
            j = body.find("<h2", body.find("</h2>", i))
            if j != -1:
                body = body[:j] + b + "\n" + body[j:]
        body = body + "\n" + b
    if cat_links:
        body = body + "\n" + category_html(cat_links)
    return body


def _load_categories() -> list[str]:
    cfg = yaml.safe_load((BASE / "config.yaml").read_text(encoding="utf-8"))
    return cfg.get("categories") or []


def command(action: str, name: str = "", link: str = "", path=None, categories: list[str] | None = None) -> str:
    entries = load(path)
    if action == "list":
        if not entries:
            return "【リンク台帳】まだ登録がありません\n登録: リンク ツール名 URL"
        return "【リンク台帳】\n" + "\n".join(
            f"・{'／'.join(names(e))}" + (f"（カテゴリ: {'、'.join(e['categories'])}）" if e.get("categories") else "")
            for e in entries) + f"\n（{len(entries)}件）"
    if action == "add":
        today = datetime.now(JST).strftime("%Y-%m-%d")
        e, replaced = add(entries, name, link, today)
        save(entries, path)
        return (f"【リンクを{'更新' if replaced else '登録'}しました】\n{'／'.join(names(e))}\n"
                "これから作る記事でこのツールが出てきたら、リンクを入れます")
    if action == "remove":
        e = remove(entries, name)
        if not e:
            return f"「{name}」は台帳にありません"
        save(entries, path)
        return f"【リンクを削除しました】\n{e['name']}"
    if action == "category":
        e = find(entries, name)
        if not e:
            return f"「{name}」は台帳にありません。先に「リンク {name} URL」で登録してください"
        e["categories"] = resolve_categories(link, categories if categories is not None else _load_categories())
        save(entries, path)
        if not e["categories"]:
            return f"【カテゴリを解除しました】\n{e['name']}"
        return (f"【カテゴリを設定しました】\n{e['name']} → {'、'.join(e['categories'])}\n"
                f"これらのカテゴリの記事の最後に「{e.get('pitch') or PITCH} → {e['name']}」を入れます")
    raise ValueError(f"unknown action: {action}")


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--add", nargs=2, metavar=("NAME", "LINK"))
    g.add_argument("--remove", metavar="NAME")
    g.add_argument("--category", nargs=2, metavar=("NAME", "CATEGORIES"))
    g.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        if args.add:
            text = command("add", *args.add)
        elif args.category:
            text = command("category", *args.category)
        elif args.remove:
            text = command("remove", args.remove)
        else:
            text = command("list")
        print(text)
        if not args.dry_run:
            send_line(text)
    except Exception as e:  # noqa: BLE001
        print(traceback.format_exc(), file=sys.stderr)
        if not args.dry_run:
            try:
                send_line(f"【リンク登録】エラー\n{str(e)[:500]}")
            except Exception as e2:  # noqa: BLE001
                print(f"[notify] LINEへのエラー通知も失敗: {e2}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
