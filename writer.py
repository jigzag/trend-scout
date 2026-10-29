"""記事の執筆（Ver.1）。
  事実抽出 → 構成 → 執筆 → 校閲 → 類似度チェック（似すぎた段落だけ書き直し）
方針（SPEC §7）：参考記事は事実の材料にとどめる。想定シナリオは架空と明記する。
運営者が試していないことを「試した」と書かない。
"""
from __future__ import annotations

import html
import json
import re

from scorer import BOOL, INT, STR, arr, call_llm, extract_json, obj_schema, unwrap

PR_NOTE = '<p class="pr-note">※本記事にはプロモーション（広告）が含まれる場合があります。</p>'
NOTE_CLASS = "operator-note"   # 運営者メモの囲み。この中だけは「試した」表現を許す
EASY_CLASS = "easy-box"        # 冒頭の「ざっくり言うと」の囲み（Cocoon の memo-box の見た目を借りる）
EASY_DIV = f'<div class="{EASY_CLASS} memo-box common-icon-box block-box">'

# 運営者が試していない記事で使ってはいけない表現（運営者メモの囲みの外）
EXPERIENCE_WORDS = re.compile(r"試してみ|使ってみ|実際に試|実際に使|触ってみ|検証してみ|やってみ")
# 読者に調べ物を押し付ける逃げの表現（記事全体で1回まで）
HEDGE_WORDS = re.compile(r"公式(?:サイト|情報|発表|案内|ページ|ヘルプ)[^。]{0,20}?(?:確認|ご確認|参照|チェック)"
                         r"|明らかにされていません|明示されていません|記載(?:が|は)ありません|公表されていません")

RULES = """# 守ること
- 読者はプログラミングをしない人。AIにくわしくない人が読んでも分かるように書く。です・ます調
- 1文は60字くらいまで。ただし事実を1行ずつ並べない。「なので」「たとえば」「つまり」でつなぎ、話の流れが分かるように書く
- 機能を書いたら、読者の仕事や生活のどんな場面で、何がうれしいのかを必ず添える（機能名だけを並べない）
- 読者の判断に関係ない情報（フォロワー数・使っている技術・開発元の資金調達・どこでローンチしたか など）は書かない
- 専門用語・カタカナ語・英語は、できるだけふつうの言葉に言い換える。どうしても使うときは、初めて出たところで
  かっこ書きで説明する（例：「API（ほかのアプリとつなぐための窓口）」「エージェント（指示すると自分で作業を進めてくれるAI）」）
- 書いてよい事実は「事実メモ」にあるものだけ。料金・回数・日付・対応環境などを推測で足さない。
  事実メモにないことは**書かない（触れない）**。「分からないこと」を並べて文字数を稼がない
- 「公式サイトで確認してください」「明らかにされていません」「記載がありません」のような逃げの表現は、
  **記事全体で1回まで**。料金や対応環境など、読者の判断に直結する点が本当に不明なときだけ使う
- 事実は言い切る（「〜です」「〜できます」）。「〜と案内されています」「〜と説明されています」を毎文に付けない。
  出典を示すのは、意外な数字や公式の約束（「アップロードしない」など）を書くときだけ
- 参考記事の文章を写さない。言い回し・段落の順番・見出しも自分で組み立てる
- 英語の参考記事を訳さない。原文の順番どおりに訳したような文章にせず、事実を日本の読者向けに自分の言葉で説明する
- 料金はドルなど元の通貨のまま書く（円に換算しない）。製品名・機能名は原文の表記のまま書く
- 運営者はこのツールを試していない。「試してみた」「使ってみた」「実際に使うと」など、体験したかのような表現は禁止
- パソコン・スマホの一般的な操作（メモリや空き容量・OSの確かめ方、zipファイルの展開、Windowsの「WindowsによってPCが保護されました」が
  出たら「詳細情報」→「実行」、Macで「開発元を確認できない」と出たら「システム設定」→「プライバシーとセキュリティ」から開く許可を出す、など）は、
  事実メモになくても書いてよい。警告などは「〜と表示されることがあります。そのときは〜」の形で書く。
  ツール固有のボタン名・画面・数字・仕様は、事実メモにあるものだけ
- 【想定シナリオ】の節では、架空の人物（例：SNSで発信している会社員のAさん）の使い方の例を書く。
  見出しに【想定シナリオ】を付け、冒頭で架空の例だと断り、「〜できそうです」「〜という使い方が考えられます」の形で書く。
  架空の人物の感想・結果・数字（「30分短縮できた」など）は書かない
- 日付は「9月30日」「2026年10月2日」の形で書く（「2026-09-30」は使わない）
- 「事実メモ」「参考記事」「構成」など、この指示の中の言葉を本文に出さない。出典に触れるときは「公式発表では」「報道によると」と書く"""


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
- facts: 事実の一覧。[{{"fact": "日本語で言い換えた1文", "src": 参考番号}}]。数字・料金・日付・対象（無料/有料、対応OS、提供地域）を優先。
  料金・回数・容量・台数などは、**金額や数値をそのまま**入れる（「$49（買い切り）」「最大3台」など。「価格の案内がある」のようにぼかさない）。
  参考記事が英語でも、facts は日本語で書く。日本語に対応しているか、日本から使えるか、対応OS・ブラウザ・アプリが書かれていれば必ず入れる
- tool_type: ツールの種類。"app"=パソコンやスマホにダウンロードして入れるアプリ／"web"=ブラウザで使うWebサービス／
  "feature"=ChatGPT・Canva など既存のアプリやサービスに加わった新機能
- requirements: 動作環境・必要なもの（対応OS・必要なメモリ・空き容量・必要なアカウントや有料プランなど。数値はそのまま）
- steps: 使い始める手順として書かれていること。ダウンロード先・押すボタンの名前・選ぶ項目・かかる時間・表示される画面など、
  書かれている具体的なことはすべて入れる（書かれていなければ空配列）
- cautions: 制限・注意点・未確定の点
- relevant_sources: この話題について実際に書かれていた参考番号の一覧（話題と関係ない記事の番号は入れない）
- reader_fit: 個人が一人でアカウントを作れば使い始められるなら true。特定の業務システム（HubSpot・Salesforce・会計ソフト等）が前提、
  専門職（マーケター・経理など）向けの分析/管理ツール、法人契約が前提なら false
- fit_reason: reader_fit が false の理由（1文。true なら空文字）
- needs_coding: 読者がこの話題を試すのに、プログラムを書く・コマンドを打つ・設定ファイル（JSON等）を編集する必要があるなら true
- coding_reason: needs_coding が true の理由（1文。false なら空文字）
文章をそのまま写さず、短く言い換えること。推測は入れないこと。
**この話題に直接関係する事実だけ**を入れること。会社や製品全般の紹介、別の製品・別の機能の話、書籍やイベントの宣伝など、
この話題を説明するのに不要なものは、参考記事に書かれていても入れない。
{{"summary": "", "facts": [], "tool_type": "web", "requirements": [], "steps": [], "cautions": [], "relevant_sources": [], "reader_fit": true, "fit_reason": "", "needs_coding": false, "coding_reason": ""}}
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
- persona: 想定シナリオの架空の人物を{persona_count}人、読点区切りで。このツールを本当に使いそうな人を選ぶ
  （例「ネットショップを営む個人事業主のAさん、子どもの行事の写真を整理したい会社員のBさん」）。職種や目的が重ならないようにする
- category: 記事のカテゴリ。次の中から1つ: {categories}
- sections: 見出し(h2)の配列。[{{"h2": "", "points": ["この節で書くこと"]}}]
  {structure}
  を基本に、事実メモに合わせて調整する。
  事実メモに材料がない節は作らない（料金が分からなければ料金の節は作らない）。「明らかにされていない点」だけを並べる節は作らない
- image_featured: アイキャッチ画像の内容（英語1〜2文）。記事のテーマを象徴する場面や物のイラスト
- image_scenario: 想定シナリオの場面の画像の内容（英語1〜2文）。persona の1人目が仕事をしている場面のイラスト
  どちらも、文字・ロゴ・実在のアプリ画面・実在の人物は入れない
{{"title": "", "slug": "", "excerpt": "", "persona": "", "category": "", "sections": [], "image_featured": "", "image_scenario": ""}}
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
- HTMLだけを出力する。使ってよいタグは h2, h3, p, ul, ol, li, strong, table, tr, th, td のみ（h1・タイトル・コードフェンスは出さない。div は「ざっくり言うと」と「運営者のひとこと」の囲みだけ）
- 冒頭（最初のh2の前）に、この記事で分かることを2〜3文で書く。見出しに「導入」「はじめに」は使わない
- そのすぐ後（最初のh2の前）に、次の形で「ざっくり言うと」の囲みを必ず入れる。
  {easy_div}<p><strong>ざっくり言うと</strong></p><ul><li>…</li><li>…</li><li>…</li></ul></div>
  中身は3〜4項目の箇条書き。AIにくわしくない中学生でも分かる言葉だけで書く（専門用語・英語・カタカナの製品機能名は使わない）。
  「何をしてくれる道具か（身近なものにたとえて『〜みたいなもの』）」「何がうれしいか」「いくらで、だれ向けか」が分かるようにする
- 手順の節（導入手順・登録から最初の1回まで・機能の使い方）は、h3 で1ステップずつ見出しを立て（例「ステップ1：自分のパソコンが対応しているか確かめる」）、
  「どこで・何を押すと・何が起きるか」まで書く。読者がこの記事だけを見ながら進められることを目指す
- 【想定シナリオ】では、構成の persona の人それぞれに h3 の見出し（例「ネットショップを営むAさんの場合」）を付け、
  「困っていること → このツールをどの場面でどう使うか（手順に沿って）→ 期待できること」を2〜3段落の文章で書く。
  節の冒頭に「以下は架空の人物を想定した使い方の例です」と書く。人物ごとに使う機能や場面を変え、同じ話を繰り返さない
- 本文が {min_chars} 文字に届かないときは、事実メモの範囲で「どんな人に向いているか」「似たツールとの違い」「よくある疑問」を足す。
  よくある疑問は、事実メモで**答えられる質問だけ**にする（答えが「公式で確認」になる質問は入れない）。
  文字数が足りなくても、分からないことの列挙や同じ内容の繰り返しで埋めない
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
1. 事実メモにない数字・料金・日付・仕様が書かれていたら、その文ごと削除する（「公式サイトで確認」に置き換えない）。
   ただし、パソコン・スマホの一般的な操作の説明（メモリの確かめ方、警告が出たときの進め方など）は残す
2. 体験したかのような表現（運営者のひとこと の囲みの外）を、一般的な説明に直す
3. 【想定シナリオ】が架空の例だと分かる書き方になっているか。架空の人物の結果や数字があれば消す
3b. 「ざっくり言うと」の囲み（class="{easy_class}" の div）は消さない。中に専門用語や英語があれば、やさしい言葉に直す。
   本文の専門用語も、初めて出たところにかっこ書きの説明がなければ足す
4. 誤字・不自然な日本語・同じ内容の繰り返し
5. 「公式サイトで確認してください」「明らかにされていません」「記載がありません」などの逃げの表現は、記事全体で1回まで残し、
   それ以外は文ごと消す。「〜と案内されています」「〜と説明されています」は、言い切りの形に直す

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
次の形式のJSONだけ: {{"blocks": ["書き直した段落", ...]}}
"""

FACTS_SCHEMA = obj_schema({"summary": STR, "facts": arr(obj_schema({"fact": STR, "src": INT})),
                           "tool_type": {"type": "string", "enum": ["app", "web", "feature"]}, "requirements": arr(STR),
                           "steps": arr(STR), "cautions": arr(STR),
                           "relevant_sources": arr(INT), "reader_fit": BOOL, "fit_reason": STR,
                           "needs_coding": BOOL, "coding_reason": STR})
OUTLINE_SCHEMA = obj_schema({"title": STR, "slug": STR, "excerpt": STR, "persona": STR, "category": STR,
                             "sections": arr(obj_schema({"h2": STR, "points": arr(STR)})),
                             "image_featured": STR, "image_scenario": STR})
REWRITE_SCHEMA = obj_schema({"blocks": arr(STR)})


def strip_fence(text: str) -> str:
    text = re.sub(r"^\s*```(?:html)?\s*", "", text.strip())
    return re.sub(r"\s*```\s*$", "", text)


def extract_facts(topic: str, sources: list[dict], cfg: dict) -> dict:
    raw = call_llm(FACTS_PROMPT.format(topic=topic, sources=_join_sources(sources)), cfg, 8000, FACTS_SCHEMA, "facts")
    facts = extract_json(raw)
    return facts if isinstance(facts, dict) else {"facts": facts}


# ツールの種類ごとの記事の流れ（読者がいちばん知りたいことを厚くする）
STRUCTURE = {
    "app": ("こんなことで困っていませんか → このツールで何が変わるか → 入れる前に確認すること（対応OS・メモリ・空き容量と、"
            "自分のパソコンでの確かめ方）→ 導入手順（記事の中心。ダウンロードからインストール、初期設定、警告が出たときの進め方まで）→ "
            "最初の使い方（最初に何をすると、どう返ってくるか）→ 料金 → 【想定シナリオ】使い方の想定（短め）→ 気をつけること・向いている人 → まとめ", "1〜2"),
    "web": ("こんなことで困っていませんか → このツールで何が変わるか → 登録から最初の1回まで（記事の中心。アカウント作成・最初の操作・"
            "できあがるもの）→ 料金・日本から使えるか・日本語対応 → 【想定シナリオ】使い方の想定 → 気をつけること・向いている人 → まとめ", "2"),
    "feature": ("こんなことで困っていませんか → 新しい機能で何が変わるか → 機能の場所と使い方（記事の中心。どの画面のどこから使うか、"
                "使えるプラン）→ 【想定シナリオ】使い方の想定 → 気をつけること・向いている人 → まとめ", "2〜3"),
}


def make_outline(genre: str, facts: dict, memo: str, cfg: dict, categories: list[str] | None = None) -> dict:
    cats = categories or ["その他"]
    structure, persona_count = STRUCTURE.get(facts.get("tool_type"), STRUCTURE["web"])
    schema = json.loads(json.dumps(OUTLINE_SCHEMA))
    schema["properties"]["category"] = {"type": "string", "enum": cats}  # 決めたカテゴリ以外は選ばせない
    raw = call_llm(OUTLINE_PROMPT.format(genre=genre.strip(), facts=json.dumps(facts, ensure_ascii=False),
                                         memo=memo or "（なし）", rules=RULES, categories="、".join(cats),
                                         structure=structure, persona_count=persona_count),
                   cfg, 4000, schema, "outline")
    out = extract_json(raw)
    if out.get("category") not in cats:  # Anthropic など形式を保証しない場合
        out["category"] = cats[-1]
    return out


def write_body(genre: str, outline: dict, facts: dict, memo: str, opt: dict, cfg: dict) -> str:
    raw = call_llm(WRITE_PROMPT.format(
        genre=genre.strip(), outline=json.dumps(outline, ensure_ascii=False),
        facts=json.dumps(facts, ensure_ascii=False), memo=memo or "（なし）", rules=RULES,
        min_chars=opt.get("min_chars", 3000), max_chars=opt.get("max_chars_body", 4000), note_class=NOTE_CLASS,
        easy_div=EASY_DIV),
        cfg, 16000)
    return strip_fence(raw)


def review(article: str, facts: dict, memo: str, cfg: dict) -> str:
    raw = call_llm(REVIEW_PROMPT.format(facts=json.dumps(facts, ensure_ascii=False), memo=memo or "（なし）",
                                        rules=RULES, article=article, easy_class=EASY_CLASS), cfg, 16000)
    out = strip_fence(raw)
    # 校閲で極端に短くなったら（途中で切れた等）元の記事を使う
    return out if len(plain(out)) >= len(plain(article)) * 0.6 else article


# ---------- 4b. 逃げの表現を削る ----------
HEDGE_PROMPT = """次の記事には「公式サイトで確認してください」「明らかにされていません」のような、読者に調べ物を押し付ける表現が多すぎます。
記事全体で1回だけ残し（料金や対応環境など、読者の判断にいちばん影響する箇所）、それ以外は文ごと削ってください。
削ったことで中身のなくなった箇条書きや、質問だけ残った「よくある疑問」の項目も消してください。
「〜と案内されています」「〜と説明されています」は言い切りの形（「〜です」「〜できます」）に直してください。
ほかの内容・事実・HTMLタグは変えないでください。

# 記事
{article}

# 出力
直したHTMLだけ（説明やコードフェンスは不要）
"""


def trim_hedges(article: str, cfg: dict) -> str:
    """逃げの表現が2回以上なら、1回だけ残して削らせる。"""
    if len(HEDGE_WORDS.findall(plain(article))) <= 1:
        return article
    out = strip_fence(call_llm(HEDGE_PROMPT.format(article=article), cfg, 16000))
    return out if len(plain(out)) >= len(plain(article)) * 0.6 else article


# ---------- 4c. 分かりやすさチェック ----------
CLARITY_PROMPT = """あなたは、AIにくわしくない読者です。パソコンやスマホは使えますが、IT用語・マーケティング用語・英語の略語は知りません。
次の記事を読んで、説明なしでは意味が分からない言葉と、読んでも「で、自分に何がうれしいの？」となる文を挙げてください。

# 記事
{text}

# 出力（JSONだけ）
- words: 説明なしでは分からない言葉（記事に出てくる表記のまま。最大15個。なければ空配列）
- sentences: 意味が分からない文、または読者にとって何がうれしいのか分からない文（記事からそのまま抜き出す。最大10個）
{{"words": [], "sentences": []}}
"""
CLARITY_SCHEMA = obj_schema({"words": arr(STR), "sentences": arr(STR)})

EASY_FIX_PROMPT = """次の記事には、AIにくわしくない読者には分からない言葉と文があります。分かるように直した完成版のHTMLを出力してください。
- 言葉：ふつうの言葉に言い換える。言い換えられないときは、初めて出たところにかっこで短い説明を足す
- 文：読者の仕事や生活で何がうれしいのかが分かる文に書き直す。読者に関係ない文なら消す
- 事実（数字・料金・機能・日付）は変えない・足さない。HTMLタグ、見出し、囲み（div）はそのまま残す
- 挙げていない部分は変えない

# 分からない言葉
{words}

# 分からない文
{sentences}

# 記事
{article}

# 出力
直したHTMLだけ（説明やコードフェンスは不要）
"""


def clarify(article: str, cfg: dict) -> tuple[str, dict]:
    """AIにくわしくない読者の立場で読ませ、分からない言葉・文を挙げさせて直す。返り値: (記事, {"words": [...], "sentences": [...]})"""
    found = extract_json(call_llm(CLARITY_PROMPT.format(text=plain_text(article)), cfg, 8000, CLARITY_SCHEMA, "clarity"))
    words, sentences = found.get("words") or [], found.get("sentences") or []
    report = {"words": words, "sentences": sentences}
    if not words and not sentences:
        return article, report
    out = strip_fence(call_llm(EASY_FIX_PROMPT.format(
        words="、".join(words) or "（なし）", sentences="\n".join(f"- {s}" for s in sentences) or "（なし）",
        article=article), cfg, 16000))
    return (out if len(plain(out)) >= len(plain(article)) * 0.6 else article), report


def plain_text(article: str) -> str:
    """読者が読む形（タグを外し、段落ごとに改行）。"""
    return re.sub(r"\n{2,}", "\n", re.sub(r"<[^>]+>", "\n", article)).strip()


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
            new = unwrap(extract_json(call_llm(REWRITE_PROMPT.format(blocks=json.dumps(blocks, ensure_ascii=False)),
                                               cfg, 8000, REWRITE_SCHEMA, "rewrite")), "blocks")
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
def check_rules(article: str, memo: str, min_chars: int = 3000, tool_type: str = "") -> list[str]:
    warnings = []
    if tool_type == "app" and len(re.findall(r"<h3[^>]*>[^<]*ステップ", article)) < 3:
        warnings.append("パソコンに入れるアプリなのに、導入手順がステップごとに書かれていない")
    outside = re.sub(rf'(?s)<div class="{NOTE_CLASS}">.*?</div>', "", article)
    words = sorted(set(EXPERIENCE_WORDS.findall(outside)))
    if words:
        warnings.append("体験したかのような表現: " + "、".join(words))
    if EASY_CLASS not in article:
        warnings.append("「ざっくり言うと」の囲みがない")
    if "想定シナリオ" not in article:
        warnings.append("【想定シナリオ】の節がない")
    hedges = len(HEDGE_WORDS.findall(plain(article)))
    if hedges > 1:
        warnings.append(f"「公式で確認」「明らかにされていない」系の表現が{hedges}回（1回までにしたい）")
    leaked = sorted(set(re.findall(r"事実メモ|参考記事|運営者メモ", outside)))
    if leaked:
        warnings.append("指示の言葉が本文に出ている: " + "、".join(leaked))
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
