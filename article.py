"""記事生成 Ver.1（＋ Ver.2 の WordPress 下書き投稿）
  候補の読み込み → 参考記事の取得 → 事実抽出 → 構成 → 執筆 → 校閲 → 類似度チェック
  → data/articles/ に保存 → WordPress に下書き → LINE に通知

使い方:
  python article.py --n 1                      # 今日の候補1で記事を作る
  python article.py --n 2 --memo "無料版は3回まで"   # 運営者メモ付き
  python article.py --n 1 --date 2026-10-04 --dry-run   # 投稿も通知もせず、画面とファイルに出す
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

import yaml

import affiliate
import images
import research
import shots
import wordpress
import writer
from main import BASE, JST
from notifier import send_line


def load_candidate(n: int, day: str | None) -> tuple[str, dict]:
    folder = BASE / "data" / "candidates"
    if day:
        path = folder / f"{day}.json"
    else:
        files = sorted(folder.glob("*.json"))
        if not files:
            raise RuntimeError("data/candidates に候補ファイルがありません")
        path = files[-1]  # いちばん新しい日付
    cands = json.loads(path.read_text(encoding="utf-8"))
    if not 1 <= n <= len(cands):
        raise RuntimeError(f"{path.name} の候補は {len(cands)} 件です（--n {n} は範囲外）")
    return path.stem, cands[n - 1]


def llm_cfg(cfg: dict) -> dict:
    """記事用のモデル設定（article.llm で上書きできる）。"""
    return {**cfg["llm"], **(cfg.get("article", {}).get("llm") or {})}


def build(cfg: dict, cand: dict, memo: str) -> dict:
    opt = cfg.get("article", {})
    lc = llm_cfg(cfg)
    item = cand["item"]
    topic = f"{item['title']}（{cand.get('detail', {}).get('summary', '')}）"

    sources = research.gather(cand, opt)
    if not sources:
        raise RuntimeError(f"参考記事の本文を1件も取得できませんでした: {item['url']}")
    print(f"[article] 参考記事 {len(sources)}件: " + ", ".join(s["url"] for s in sources))

    links = research.link_candidates(item["url"], sources)
    facts = writer.extract_facts(topic, sources, lc, links)
    # 話題と関係なかった参考記事は、出典にも載せない（元記事=参考1は必ず残す）
    keep = {i for i in facts.pop("relevant_sources", None) or range(1, len(sources) + 1) if isinstance(i, int)} | {1}
    all_sources = sources  # 類似度チェックは、AIに見せた全参考記事と照合する
    dropped = [s["url"] for i, s in enumerate(sources, 1) if i not in keep]
    sources = [s for i, s in enumerate(sources, 1) if i in keep]
    if dropped:
        print("[article] 話題と関係ない参考記事を除外: " + ", ".join(dropped))
    needs_coding, coding_reason = facts.pop("needs_coding", False), facts.pop("coding_reason", "")
    reader_fit, fit_reason = facts.pop("reader_fit", True), facts.pop("fit_reason", "")
    outline = writer.make_outline(cfg["genre"], facts, memo, lc, cfg.get("categories"))
    body = writer.write_body(cfg["genre"], outline, facts, memo, opt, lc)
    body = writer.review(body, facts, memo, lc)
    body = writer.trim_hedges(body, lc)
    body, clarity = writer.clarify(body, lc)
    body, sim = writer.fix_similarity(body, all_sources, opt, lc)
    body = writer.add_links(body, facts.get("official_url", ""), facts.get("download_url", ""), facts.get("tool_type", ""))
    warnings = writer.check_rules(body, memo, opt.get("min_chars", 3000), facts.get("tool_type", ""))
    if not reader_fit:
        warnings.insert(0, f"個人がすぐ使えるツールではない可能性（ボツ推奨）: {fit_reason}")
    if len(clarity["words"]) + len(clarity["sentences"]) >= 15:
        warnings.append("分かりにくい言葉・文が多かった（直したが、難しい話題の可能性）")
    if needs_coding:
        warnings.insert(0, f"コードを書く前提の話題で、読者（非エンジニア）に合わない可能性: {coding_reason}")
    if len(sources) < 2:
        warnings.append("話題に関係する参考記事が1件だけ。事実が少ないので内容を確認してください")
    if not sim["ok"]:
        warnings.append(f"参考記事との類似が高い（一致率{sim['ratio']:.1%}、最長{sim['longest']}字）")
    return {
        "title": outline.get("title") or item["title"], "slug": outline.get("slug", ""),
        "excerpt": outline.get("excerpt", ""),
        "category": outline.get("category", ""), "tool_type": facts.get("tool_type", ""),
        "official_url": facts.get("official_url", ""), "download_url": facts.get("download_url", ""),
        "body": body, "_sources": sources, "chars": len(writer.plain(body)),
        "image_prompts": {"featured": outline.get("image_featured", ""), "body": outline.get("image_body", "")},
        "similarity": sim, "warnings": warnings, "memo": memo, "clarity": clarity,
        "sources": [{"title": s["title"], "url": s["url"]} for s in sources],
        "candidate": {"title": item["title"], "url": item["url"]},
    }


# 自動公開を止めない警告（画像などの付け足しの失敗だけ。内容の問題は下書きで止める）
SOFT_WARNINGS = ("画像（", "公式サイトの画面を", "カテゴリを設定", "分かりにくい言葉")


def blocking(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not w.startswith(SOFT_WARNINGS)]


def message(art: dict, wp: dict | None, published: bool = False) -> str:
    lines = ["【記事を公開しました】" if published else "【下書きができました】", art["title"] + (f"（{art['category']}）" if art.get("category") else ""), "",
             f"本文 {art['chars']}字 / 参考記事 {len(art['sources'])}件 / "
             f"類似度 一致率{art['similarity']['ratio']:.1%}・最長{art['similarity']['longest']}字 / "
             f"画像 {len(art.get('images', {}).get('media', []))}枚 / "
             + ("公式サイトの画面あり / " if art.get("page_shot") else "")
             + ("公式動画あり / " if art.get("official_video") else "")
             + f"アフィリンク {len(art.get('affiliates', []))}件"
             + (f"（{'、'.join(art['affiliates'])}）" if art.get("affiliates") else "")]
    if art["warnings"]:
        lines += ["", "確認してほしい点:"] + [f"・{w}" for w in art["warnings"]]
    lines.append("")
    if wp and published:
        lines += [wp["link"], "", f"取り下げるときは、管理画面で下書きに戻してください（投稿ID {wp['id']}）"]
    elif wp:
        base = wp["edit_url"].split("/wp-admin/")[0]
        if art.get("held"):
            lines += ["上の点があったので、公開せず下書きにしました。", ""]
        lines += ["プレビュー（ログイン済みのブラウザで開く）:", f"{base}/?p={wp['id']}&preview=true", "",
                  "ログインがまだなら先にこちら:", f"{base}/wp-admin/", "",
                  "編集画面:", wp["edit_url"], "",
                  f"よければ「公開」、ボツなら「削除」と返信（投稿ID {wp['id']}）"]
    else:
        lines.append("WordPress 未設定のため、GitHub の data/articles/ に保存しました")
    return "\n".join(lines)


def add_images(art: dict, opt: dict) -> dict:
    """画像を作って WordPress に上げ、本文に挿入する。失敗しても記事は止めない（警告に入れる）。
    返り値: {"featured": メディアID or None, "media": [ID...]}"""
    out = {"featured": None, "media": []}
    prompts = art["image_prompts"]
    jobs = [("featured", prompts.get("featured") or art["title"])]
    if opt.get("body_image", True):
        jobs.append(("body", prompts.get("body") or art["title"]))
    for kind, prompt in jobs:
        try:
            data = images.generate(prompt, opt)
            m = wordpress.upload_media(data, f"{art['slug'] or 'article'}-{kind}.jpg", art["title"])
        except Exception as e:  # noqa: BLE001
            art["warnings"].append(f"画像（{'アイキャッチ' if kind == 'featured' else '本文'}）を作れなかった: {str(e)[:150]}")
            continue
        out["media"].append(m["id"])
        if kind == "featured":
            out["featured"] = m["id"]
        else:
            art["body"] = images.insert_body_image(art["body"], images.figure(m["url"], art["title"]))
    return out


def add_page_shot(art: dict, pics: dict) -> None:
    """ダウンロード（登録）ページ、なければ公式サイトの画面を撮って、そのリンクの後ろに入れる。"""
    url = art.get("download_url") or art.get("official_url")
    if not url:
        return
    try:
        data = shots.capture(url)
    except ImportError:
        print("[article] Playwright がないので公式サイトの画面は撮らない")
        return
    except Exception as e:  # noqa: BLE001
        art["warnings"].append(f"公式サイトの画面を撮れなかった: {str(e)[:120]}")
        return
    try:
        m = wordpress.upload_media(data, f"{art['slug'] or 'article'}-site.jpg", f"{art['title']} 公式サイト")
    except Exception as e:  # noqa: BLE001
        art["warnings"].append(f"公式サイトの画面を上げられなかった: {str(e)[:120]}")
        return
    art["body"] = shots.insert_page_shot(art["body"], url, shots.page_figure(url, m["url"]))
    pics["media"].append(m["id"])
    art["page_shot"] = url


def add_video(art: dict) -> None:
    """公式ページに YouTube の紹介動画があれば、「何ができるか」の節の手前に埋め込む（出典付き）。"""
    try:
        vid = shots.page_video(shots.fetch_page(art["official_url"]))
    except Exception as e:  # noqa: BLE001  動画の失敗では記事を止めない
        print(f"[article] 公式ページを取れなかった: {e}")
        return
    if vid:
        before = art["body"]
        art["body"] = shots.insert_video(before, shots.video_figure(vid, art["official_url"]))
        art["official_video"] = art["body"] != before


def run(n: int, memo: str = "", day: str | None = None, dry_run: bool = False,
        config_path: Path = BASE / "config.yaml") -> dict:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    cand_day, cand = load_candidate(n, day)
    print(f"[article] {cand_day} 候補{n}: {cand['item']['title']}")
    art = build(cfg, cand, memo.strip())
    use_wp = not dry_run and wordpress.configured()
    img_opt = cfg.get("image") or {}
    pics = add_images(art, img_opt) if use_wp and img_opt.get("enabled") else {"featured": None, "media": []}
    if use_wp and img_opt.get("page_shot"):
        add_page_shot(art, pics)
    if img_opt.get("official_video") and art.get("official_url"):
        add_video(art)
    ledger = affiliate.load()
    found = affiliate.match(ledger, art["title"], writer.plain(art["body"]))
    cat_links = affiliate.by_category(ledger, art.get("category", ""), found)
    art["body"] = affiliate.insert(art["body"], found, cat_links)
    art["affiliates"] = [e["name"] for e in found + cat_links]
    art["content"] = writer.assemble(art.pop("body"), art.pop("_sources"))
    art["images"] = pics

    out_dir = BASE / "data" / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{cand_day}-{n}-{datetime.now(JST):%H%M%S}"  # 同じ候補で作り直しても上書きしない
    (out_dir / f"{stem}.html").write_text(art["content"], encoding="utf-8")

    wp, published = None, False
    if use_wp:
        cats = []
        if art.get("category"):
            try:
                cats = [wordpress.category_id(art["category"])]
            except Exception as e:  # noqa: BLE001  カテゴリの失敗では投稿を止めない
                art["warnings"].append(f"カテゴリを設定できなかった: {str(e)[:120]}")
        auto = cfg.get("article", {}).get("auto_publish", False)
        published = auto and not blocking(art["warnings"])
        art["held"] = auto and not published
        wp = wordpress.post_draft(art["title"], art["content"], art["slug"], art["excerpt"], pics["featured"], cats,
                                  status="publish" if published else "draft")
        print(f"[article] WordPress {'公開' if published else '下書き'}: {wp['link'] or wp['edit_url']}")
    meta = {k: v for k, v in art.items() if k != "content"}
    meta["wordpress"] = wp
    meta["published"] = published
    meta["created_at"] = datetime.now(JST).isoformat(timespec="seconds")
    (out_dir / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    text = message(art, wp, published)
    if dry_run:
        print("\n" + text)
    else:
        send_line(text)
    return {**art, "wordpress": wp, "published": published}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=1, help="候補番号（1〜3）")
    ap.add_argument("--memo", default="", help="運営者メモ（任意）")
    ap.add_argument("--date", default=None, help="候補の日付 YYYY-MM-DD（省略時はいちばん新しい日）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        run(args.n, args.memo, args.date or None, args.dry_run)
    except Exception:
        err = traceback.format_exc()
        print(err, file=sys.stderr)
        if not args.dry_run:
            try:
                send_line("【記事生成】エラー\n" + err[-1500:])
            except Exception as e:  # noqa: BLE001
                print(f"[notify] LINEへのエラー通知も失敗: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
