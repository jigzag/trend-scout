"""記事の執筆（Ver.1）。
  事実抽出 → 構成 → 執筆 → 校閲 → 類似度チェック（似すぎた段落だけ書き直し）
方針（SPEC §7）：参考記事は事実の材料にとどめる。想定シナリオは架空と明記する。
運営者が試していないことを「試した」と書かない。
"""
from __future__ import annotations

import html
import json
import re

from scorer import call_llm, extract_json

PR_NOTE = '<p class="pr-note">※本記事にはプロモーション（広告）が含まれる場合があります。</p>'
NOTE_CLASS = "operator-note"   # 運営者メモの囲み。この中だけは「試した」表現を許す

# 運営者が試していない記事で使ってはいけない表現（運営者メモの囲みの外）
EXPERIENCE_WORDS = re.compile(r"試してみ|使ってみ|実際に試|実際に使|触ってみ|検証してみ|やってみ")

RULES = """# 守ること
- 読者はプログラミングをしない人。専門用語は使わないか、使うならひとこと説明する。です・ます調
- 書いてよい事実は「事実メモ」にあるものだけ。料金・回数・日付・対応環境などを推測で足さない。
  事実メモにない点は「公式発表では明らかにされていません」「最新情報は公式サイトで確認してください」と書く
- 参考記事の文章を写さない。言い回し・段落の順番・見出しも自分で組み立てる
- 運営者はこのツールを試していない。「試してみた」「使ってみた」「実際に使うと」など、体験したかのような表現は禁止
- 【想定シナリオ】の節では、架空の人物（例：研修担当のAさん）の使い方の例を書く。
  見出しに【想定シナリオ】を付け、冒頭で架空の例だと断り、「〜できそうです」「〜という使い方が考えられます」の形で書く。
  架空の人物の感想・結果・数字（「30分短縮できた」など）は書かない
- 日付は「9月30日」「2026年10月2日」の形で書く（「2026-09-30」は使わない）"""


def _join_sources(sources: list[dict]) -> str:
    return "\n\n".join(f"## 参考{i + 1}: {s['title']}\nURL: {s['url']}\n{s['text']}" for i, s in enumerate(sources))


# ---------- 1. 事実抽出 ----------
FACTS_PROMPT = """あなたは調査担当の編集者です。次の話題について、参考記事から「事実」だけを抜き出してください。

# 話題
{topic}

# 参考記事
{sources}

# 出力（JSONオブジェクトだけ）
- summary: 何が起きたか（2文）
- facts: 事実の一覧。[{{"fact": "日本語で言い換えた1文", "src": 参考番号}}]。数字・料金・日付・対象（無料/有料、対応OS、提供地域）を優先
- steps: 使い始める手順として書かれていること（書かれていなければ空配列）
- cautions: 制限・注意点・未確定の点
- unknowns: 読者が知りたいのに参考記事に書かれていないこと
文章をそのまま写さず、短く言い換えること。推測は入れないこと。
{{"summary": "", "facts": [], "steps": [], "cautions": [], "unknowns": []}}
"""

# ---------- 2. 構成 ----------
OUTLINE_PROMPT = """あなたはブログ編集長です。次のブログに載せる記事の構成を作ってください。

# ブログ
{genre}

# 事実メモ
{facts}

# 運営者メモ（空なら運営者は試していない）
{memo}

{rules}

# 作るもの（JSONオブジェクトだけ）
- title: 記事タイトル（32文字前後。読者が得られることが分かるもの。「試してみた」系は禁止）
- slug: URL用の英小文字とハイフン（例 chatgpt-virtual-try-on）
- excerpt: 検索結果に出る説明文（80〜120文字）
- persona: 想定シナリオの架空の人物（例「社員研修の動画を毎月作る人事担当のAさん」）
- sections: 見出し(h2)の配列。[{{"h2": "", "points": ["この節で書くこと"]}}]
  導入 → 何ができるか → 料金・条件 → 使い始め方 → 【想定シナリオ】 → 注意点 → まとめ を基本に、事実メモに合わせて調整する
{{"title": "", "slug": "", "excerpt": "", "persona": "", "sections": []}}
"""

# ---------- 3. 執筆 ----------
WRITE_PROMPT = """あなたはブログライターです。構成と事実メモに沿って記事本文を書いてください。

# ブログ
{genre}

# 構成
{outline}

# 事実メモ
{facts}

# 運営者メモ
{memo}

{rules}

# 書き方
- 本文は {min_chars}〜{max_chars} 文字
- HTMLだけを出力する。使ってよいタグは h2, h3, p, ul, ol, li, strong, table, tr, th, td のみ（h1・タイトル・コードフェンスは出さない）
- 冒頭（最初のh2の前）に、この記事で分かることを2〜3文で書く。見出しに「導入」「はじめに」は使わない
- 【想定シナリオ】は、架空の人物1人について「困っていること → この機能をどの場面でどう使うか（手順に沿って）→ 期待できること」を3〜5段落の文章で書く（箇条書きで人物を並べない）。冒頭に「以下は架空の人物を想定した使い方の例です」と書く
- 本文が {min_chars} 文字に届かないときは、事実メモの範囲で「どんな人に向いているか」「似たツールとの違い」「よくある疑問」を足す
- 運営者メモがあるときだけ、<div class="{note_class}"><h2>運営者のひとこと</h2>…</div> を【想定シナリオ】の後に入れ、メモの内容を運営者の言葉として書く（メモにないことは足さない）
- 参考記事の一覧や出典リンクは書かない（あとで自動で付ける）
"""

# ---------- 4. 校閲 ----------
REVIEW_PROMPT = """あなたは校閲者です。記事を事実メモと照らし合わせ、問題を直した完成版のHTMLを出力してください。

# 事実メモ
{facts}

# 運営者メモ
{memo}

{rules}

# チェックすること
1. 事実メモにない数字・料金・日付・仕様が書かれていたら、削除するか「公式サイトで確認してください」に置き換える
2. 体験したかのような表現（運営者のひとこと の囲みの外）を、一般的な説明に直す
3. 【想定シナリオ】が架空の例だと分かる書き方になっているか。架空の人物の結果や数字があれば消す
4. 誤字・不自然な日本語・同じ内容の繰り返し

# 記事
{article}

# 出力
直したHTMLだけを出力する（説明やコードフェンスは不要）。問題がなければそのまま出力する。
"""

# ---------- 5. 書き直し（類似度） ----------
REWRITE_PROMPT = """次の段落は、参考記事と言い回しが似すぎています。意味と事実は変えずに、語順・言葉選び・文の区切りを変えて書き直してください。
HTMLタグの種類はそのまま保ち、書き直した段落だけを、入力と同じ順番・同じ数で出力してください。

# 段落（JSON配列）
{blocks}

# 出力
書き直した段落のJSON配列（文字列の配列）だけ
"""


def strip_fence(text: str) -> str:
    text = re.sub(r"^\s*```(?:html)?\s*", "", text.strip())
    return re.sub(r"\s*```\s*$", "", text)


def extract_facts(topic: str, sources: list[dict], cfg: dict) -> dict:
    raw = call_llm(FACTS_PROMPT.format(topic=topic, sources=_join_sources(sources)), cfg, 8000)
    facts = extract_json(raw)
    return facts if isinstance(facts, dict) else {"facts": facts}


def make_outline(genre: str, facts: dict, memo: str, cfg: dict) -> dict:
    raw = call_llm(OUTLINE_PROMPT.format(genre=genre.strip(), facts=json.dumps(facts, ensure_ascii=False),
                                         memo=memo or "（なし）", rules=RULES), cfg, 4000)
    return extract_json(raw)


def write_body(genre: str, outline: dict, facts: dict, memo: str, opt: dict, cfg: dict) -> str:
    raw = call_llm(WRITE_PROMPT.format(
        genre=genre.strip(), outline=json.dumps(outline, ensure_ascii=False),
        facts=json.dumps(facts, ensure_ascii=False), memo=memo or "（なし）", rules=RULES,
        min_chars=opt.get("min_chars", 3000), max_chars=opt.get("max_chars_body", 4000), note_class=NOTE_CLASS),
        cfg, 16000)
    return strip_fence(raw)


def review(article: str, facts: dict, memo: str, cfg: dict) -> str:
    raw = call_llm(REVIEW_PROMPT.format(facts=json.dumps(facts, ensure_ascii=False), memo=memo or "（なし）",
                                        rules=RULES, article=article), cfg, 16000)
    out = strip_fence(raw)
    # 校閲で極端に短くなったら（途中で切れた等）元の記事を使う
    return out if len(plain(out)) >= len(plain(article)) * 0.6 else article


# ---------- 類似度チェック ----------
BLOCK_RE = re.compile(r"<(p|li|h2|h3|td|th)\b[^>]*>.*?</\1>", re.S)


def plain(s: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", s))


def _norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", plain(s).lower())


def _grams(s: str, k: int) -> set[str]:
    return {s[i:i + k] for i in range(len(s) - k + 1)}


def similarity(article: str, sources: list[dict], k: int = 12) -> dict:
    """文字 k-gram の一致で、参考記事とどれだけ似ているかを測る。
    ratio: 記事の k-gram のうち参考記事にもある割合 / longest: 連続して一致した最長の文字数（概算）
    flagged: longest_run 以上に一致が続く段落のHTML"""
    src = set()
    for s in sources:
        src |= _grams(_norm(s["text"]), k)
    a = _norm(article)
    hits = [a[i:i + k] in src for i in range(max(len(a) - k + 1, 0))]
    ratio = sum(hits) / len(hits) if hits else 0.0
    longest = run = 0
    for h in hits:
        run = run + 1 if h else 0
        longest = max(longest, run)
    return {"ratio": round(ratio, 3), "longest": longest + k - 1 if longest else 0, "_src": src, "_k": k}


def flagged_blocks(article: str, sim: dict, max_run: int) -> list[str]:
    src, k = sim["_src"], sim["_k"]
    out = []
    for m in BLOCK_RE.finditer(article):
        b = _norm(m.group(0))
        run = longest = 0
        for i in range(len(b) - k + 1):
            run = run + 1 if b[i:i + k] in src else 0
            longest = max(longest, run)
        if longest and longest + k - 1 >= max_run:
            out.append(m.group(0))
    return out


def fix_similarity(article: str, sources: list[dict], opt: dict, cfg: dict) -> tuple[str, dict]:
    max_run, max_ratio = opt.get("max_run", 40), opt.get("max_ratio", 0.08)
    sim = similarity(article, sources)
    for _ in range(opt.get("rewrite_rounds", 2)):
        blocks = flagged_blocks(article, sim, max_run)
        if not blocks and sim["ratio"] <= max_ratio:
            break
        if not blocks:
            break  # 全体の割合だけ高い（固有名詞が多いなど）。段落単位では直せないので警告で返す
        try:
            new = extract_json(call_llm(REWRITE_PROMPT.format(blocks=json.dumps(blocks, ensure_ascii=False)), cfg, 8000))
        except Exception as e:  # noqa: BLE001
            print(f"[writer] 書き直し失敗 {e}")
            break
        for old, nb in zip(blocks, new):
            if isinstance(nb, str) and nb.strip():
                article = article.replace(old, nb.strip(), 1)
        sim = similarity(article, sources)
    result = {"ratio": sim["ratio"], "longest": sim["longest"],
              "ok": sim["longest"] < max_run and sim["ratio"] <= max_ratio}
    return article, result


# ---------- 機械チェック ----------
def check_rules(article: str, memo: str, min_chars: int = 3000) -> list[str]:
    warnings = []
    outside = re.sub(rf'(?s)<div class="{NOTE_CLASS}">.*?</div>', "", article)
    words = sorted(set(EXPERIENCE_WORDS.findall(outside)))
    if words:
        warnings.append("体験したかのような表現: " + "、".join(words))
    if "想定シナリオ" not in article:
        warnings.append("【想定シナリオ】の節がない")
    if not memo and NOTE_CLASS in article:
        warnings.append("運営者メモがないのに『運営者のひとこと』がある")
    n = len(plain(article))
    if n < min_chars * 0.8:
        warnings.append(f"本文が短い（{n}字）")
    return warnings


def sources_html(sources: list[dict]) -> str:
    items = "".join(f'<li><a href="{html.escape(s["url"])}" target="_blank" rel="noopener">'
                    f'{html.escape(s["title"] or s["url"])}</a></li>' for s in sources)
    return f"<h2>参考にした情報</h2><ul>{items}</ul>"


def assemble(body: str, sources: list[dict]) -> str:
    return f"{PR_NOTE}\n{body}\n{sources_html(sources)}"
