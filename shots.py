"""運営者のスクショ（SPEC §13）。LINE で送った画像を data/shots/pending/ に貯め、次に作る記事の手順に入れる。
  LINE に画像を送る → Worker → Actions（line-shot）→ このファイルの --save で保存
  記事を作るとき → place() が画像を読み取り、どの見出しの画面かを選ばせて、その節に入れる

使い方:
  python shots.py --save LINEのメッセージID   # LINE から画像を取って保存（Actions から呼ぶ）
  python shots.py --list                    # 貯まっている枚数を LINE に返す
  python shots.py --clear                   # 貯まっている画像を全部消す
"""
from __future__ import annotations

import argparse
import html
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

from main import BASE, JST
from notifier import send_line
from scorer import BOOL, INT, STR, arr, call_llm, extract_json, obj_schema
from writer import LINK_CLASS

PENDING = BASE / "data" / "shots" / "pending"
KEEP_DAYS = 7          # これより古い画像は、記事に使われなくても消す
MAX_SHOTS = 8          # 1記事に入れる最大枚数
SHOT_CLASS = "operator-shot"
CONTENT_URL = "https://api-data.line.me/v2/bot/message/{id}/content"


def mime_of(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def pending(folder: Path | None = None) -> list[Path]:
    folder = folder or PENDING
    return sorted(p for p in folder.glob("*") if p.suffix.lower() in (".jpg", ".png")) if folder.exists() else []


def save(message_id: str, folder: Path | None = None) -> Path:
    """LINE のサーバーから画像を取って保存する。ファイル名は「受け取った日時-メッセージID」。"""
    if not re.fullmatch(r"\d+", message_id or ""):
        raise ValueError(f"メッセージIDが数字ではありません: {message_id!r}")
    folder = folder or PENDING
    r = requests.get(CONTENT_URL.format(id=message_id), timeout=60,
                     headers={"Authorization": f"Bearer {os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '').strip()}"})
    if r.status_code >= 300:
        raise RuntimeError(f"LINE から画像を取れませんでした（{r.status_code}）: {r.text[:200]}")
    folder.mkdir(parents=True, exist_ok=True)
    ext = ".png" if mime_of(r.content) == "image/png" else ".jpg"
    path = folder / f"{datetime.now(JST):%Y%m%d-%H%M%S}-{message_id}{ext}"
    path.write_bytes(r.content)
    return path


def prune(folder: Path | None = None, now: datetime | None = None) -> list[Path]:
    """KEEP_DAYS より前に受け取った画像を消す（ファイル名の日時で判断。git では更新日時が残らないため）。"""
    limit = ((now or datetime.now(JST)) - timedelta(days=KEEP_DAYS)).strftime("%Y%m%d")
    old = [p for p in pending(folder) if p.name[:8] < limit]
    for p in old:
        p.unlink()
    return old


def clear(folder: Path | None = None) -> int:
    files = pending(folder)
    for p in files:
        p.unlink()
    return len(files)


# ---------- 記事に入れる ----------
PLACE_PROMPT = """次の画像は、ブログ運営者が「{title}」を自分のパソコンで実際に操作して撮ったスクリーンショットです（画像1〜{n}）。
それぞれの画像について、記事のどの見出しの節に載せるのがいちばん合うかを選んでください。

# 記事の見出し（h3）
{headings}

# 出力（JSONだけ）
shots: 画像ごとに1つ。[{{"image": 画像番号, "match": この記事のツールの画面ならtrue（別のツール・関係ない画像ならfalse）,
  "heading": 見出し番号（合う見出しがなければ0）, "caption": 何の画面かを読者向けに20字くらいで（例「初回起動時のダウンロード画面」）}}]
- 画面に写っている内容（ボタン名・表示）と、見出しの内容を照らし合わせて選ぶ
- 個人のメールアドレスや名前が写っていても、caption には書かない
"""
PLACE_SCHEMA = obj_schema({"shots": arr(obj_schema({"image": INT, "match": BOOL, "heading": INT, "caption": STR}))})


def figure(url: str, caption: str) -> str:
    cap = html.escape(f"{caption}（運営者のパソコンで撮影）" if caption else "運営者のパソコンで撮影")
    return (f'<figure class="wp-block-image size-large {SHOT_CLASS}"><img src="{html.escape(url)}" alt="{html.escape(caption)}"/>'
            f"<figcaption>{cap}</figcaption></figure>")


def _insert_at(body: str, h3_end: int) -> int:
    """見出しの直後の段落の後ろ（段落がなければ見出しの直後）。ダウンロードページへのリンクがあれば、その後ろ。"""
    nxt = body.find("<h", h3_end)
    close = body.find("</p>", h3_end)
    at = close + 4 if close != -1 and (nxt == -1 or close < nxt) else h3_end
    if body[at:].lstrip().startswith(f'<p class="{LINK_CLASS}">'):
        at = body.find("</p>", at) + 4
    if body[at:].lstrip().startswith('<figure class="wp-block-image size-large page-shot">'):
        at = body.find("</figure>", at) + 9
    return at


def place(body: str, title: str, files: list[Path], cfg: dict, upload) -> tuple[str, list[Path], list[int]]:
    """画像を見出しに割り当てて WordPress に上げ、本文に入れる。
    upload(data, filename, alt, mime) -> {"id", "url"}。返り値: (本文, 使った画像, メディアID)。
    このツールの画面ではないと判定された画像は使わない（次の記事のために残す）。"""
    files = files[:MAX_SHOTS]
    heads = list(re.finditer(r"(?s)<h3[^>]*>(.*?)</h3>", body))
    if not files or not heads:
        return body, [], []
    listed = "\n".join(f"{i}. {re.sub(r'<[^>]+>', '', m.group(1)).strip()}" for i, m in enumerate(heads, 1))
    datas = [p.read_bytes() for p in files]
    out = extract_json(call_llm(PLACE_PROMPT.format(title=title, n=len(files), headings=listed), cfg, 4000,
                                PLACE_SCHEMA, "shots", images=[(mime_of(d), d) for d in datas]))
    steps = [i for i, m in enumerate(heads, 1) if "ステップ" in m.group(1)]
    fallback = steps[-1] if steps else 0
    chosen: dict[int, list[tuple[int, str]]] = {}
    for s in out.get("shots", []) if isinstance(out, dict) else []:
        i = s.get("image")
        if not isinstance(i, int) or not 1 <= i <= len(files) or not s.get("match"):
            continue
        h = s.get("heading") if isinstance(s.get("heading"), int) and 1 <= s["heading"] <= len(heads) else fallback
        if h:
            chosen.setdefault(h, []).append((i, str(s.get("caption", ""))[:40]))
    used, media = [], []
    figs: dict[int, str] = {}
    for h in sorted(chosen):
        for i, caption in sorted(chosen[h]):
            p = files[i - 1]
            m = upload(datas[i - 1], f"shot-{p.stem}{p.suffix}", caption, mime_of(datas[i - 1]))
            figs[h] = figs.get(h, "") + "\n" + figure(m["url"], caption)
            used.append(p)
            media.append(m["id"])
    for h in sorted(figs, reverse=True):   # 後ろから入れて、前の位置をずらさない
        at = _insert_at(body, heads[h - 1].end())
        body = body[:at] + figs[h] + body[at:]
    return body, used, media


# ---------- 公式サイトの画面を自動で撮る ----------
def capture(url: str) -> bytes:
    """ヘッドレスブラウザでページを開いて、最初の1画面を JPEG で撮る。
    Playwright は記事生成の Actions でだけ入れる（requirements.txt には入れない）。入っていなければ ImportError。"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800}, locale="ja-JP")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:  # noqa: BLE001  広告などで終わらないページは、そのまま撮る
                pass
            page.wait_for_timeout(1500)
            return page.screenshot(type="jpeg", quality=75)
        finally:
            browser.close()


def page_figure(url: str, img_url: str) -> str:
    dom = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return (f'<figure class="wp-block-image size-large page-shot"><img src="{html.escape(img_url)}" '
            f'alt="{html.escape(dom)} の画面"/><figcaption>出典：公式サイト（{html.escape(dom)}）</figcaption></figure>')


def insert_page_shot(body: str, url: str, fig: str) -> str:
    """そのURLへのリンク段落（手順の中にあればそちら）の後ろに入れる。リンクがなければ入れない。"""
    at = body.rfind(f'<p class="{LINK_CLASS}">', 0, body.rfind(f'href="{html.escape(url)}"') + 1)
    if at == -1 or f'href="{html.escape(url)}"' not in body:
        return body
    end = body.find("</p>", at) + 4
    return body[:end] + "\n" + fig + body[end:]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", metavar="MESSAGE_ID")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--clear", action="store_true")
    args = ap.parse_args()
    try:
        if args.save:
            save(args.save)
            print(f"[shots] 保存しました（{len(pending())}枚）")   # 1枚ごとに LINE は送らない（Worker が受付を返す）
        elif args.clear:
            send_line(f"【スクショ】{clear()}枚を消しました")
        else:
            n = len(pending())
            send_line(f"【スクショ】{n}枚あります。次に作る記事の手順に入れます" if n else "【スクショ】ありません")
    except Exception as e:  # noqa: BLE001
        print(f"[shots] {e}", file=sys.stderr)
        try:
            send_line(f"【スクショ】エラー: {str(e)[:300]}")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()
