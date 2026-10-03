# trend-scout 仕様書

最終更新: 2026-10-04 / 対象バージョン: Ver.0（話題検知 + セール検知）

## 1. 目的と全体像

アフィリエイトブログの記事制作を、ネタ探しから公開まで段階的に自動化するシステム。
このリポジトリは**その最初の工程（ネタ探し）**を担う。

```
[Ver.0 ← いまここ] 話題検知・セール検知 → LINE通知
[Ver.1] 候補（＋任意の運営者メモ） → 参考記事2〜3件の取得・事実抽出 → 構成（想定シナリオ付き） → 執筆 → 校閲・類似度チェック → 記事HTML
[Ver.2] WordPress 下書き投稿（REST API）
[Ver.3] アフィリエイトリンク挿入（商品DB / ASP / Amazon Creators API）
[Ver.4] 公開後の順位・CTR計測 → 自動リライト判断
```

### ブログの前提（運営者が決定済み）
- 読者は**非エンジニア**。制作・事務をAIで楽にしたい個人事業主・中小企業担当・研修/人事担当
- 扱うのは、ブラウザやアプリで使えるAIツール・SaaS、制作機材、その新機能やセール
- 記事は、参考記事2〜3件から事実を抜き出して新しく書き起こし、**【想定シナリオ】**（架空の読者像での使い方例。架空だと明記する）で具体性を出す（2026-10-04 運営者決定）
- 運営者メモは任意。ある日だけ「運営者の感想」として足す
- サイト（WordPress）はまだない。ASPやAmazonアソシエイトも未登録

## 2. 実行環境

| 項目 | 内容 |
|---|---|
| 実行 | GitHub Actions（`jigzag/trend-scout`、Private）、毎朝 7:00 JST（cron `0 22 * * *` UTC） |
| 手動実行 | Actions → daily-trend-scout → Run workflow（mode: `run` / `test-line`） |
| 言語 | Python 3.12、依存は `requests` と `PyYAML` のみ |
| AI | OpenAI `gpt-5-mini`（`config.yaml` の `llm.provider` で anthropic に切り替え可能） |
| 通知 | LINE Messaging API の push（LINE Notify は2025年3月に終了済み）。無料枠は月200通 |
| 永続化 | `data/` をワークフローがコミットして保存する（毎日コミットされるので、60日無活動でcronが止まる問題も回避できる） |

### GitHub Secrets（1つの名前に1つの値。3つ別々に登録する）
- `OPENAI_API_KEY`（Anthropicに切り替える場合は `ANTHROPIC_API_KEY`）
- `LINE_CHANNEL_ACCESS_TOKEN`（Messaging API設定タブの、長期のチャネルアクセストークン）
- `LINE_USER_ID`（チャネル基本設定タブの「あなたのユーザーID」。`U`で始まる）

## 3. 処理フロー（main.py `run()`）

```
1. fetch_all(sources)          各ソースを取得。1つ失敗しても続行し、stats.failed に記録
                               各Itemに extra.buzz = ソース内順位から 0〜10 を付与
2. dedupe → History.filter_new URL正規化とタイトル一致で重複をまとめる。14日以内に通知済みのものは除外
3. score_all（AI 1回目）       全件を relevance / testable / monetizable（0〜10）＋ topic ラベルで採点
   total = (rel*0.40 + test*0.30 + mon*0.15 + buzz*0.15) * 10
   pick_diverse                min_relevance 未満は除外し、同じ topic は1件だけで上位 top_n(3) 件を選ぶ
   add_details（AI 2回目）     上位だけ summary / why / try / angle（タイトル案） / products を生成
3b. セール検知（AI 3回目）      全ソース + sale_watch.sources を取得 → keywords で一次ふるい
                               → judge_sales で読者に関係あるものを最大3件選ぶ
4. build_message → send_line   1日1通（テキスト、5000字以内）
5. History.mark / 保存          data/seen.json、data/candidates/YYYY-MM-DD.json（上位候補。Ver.1 の入力になる）
```

失敗時は main() が例外を受け取り、トレースバックをLINEに送る（それも失敗した場合はログに出す）。
`--dry-run` は送信も記録もしない。`--test-line` はLINEへのテスト送信だけ行う。

## 4. ファイル構成

| ファイル | 役割 |
|---|---|
| `config.yaml` | ジャンル定義（AIの採点基準そのもの）、重み、ソース、セール検知、AI設定 |
| `sources.py` | `Item` データクラス、RSS1.0/2.0/Atom の簡易パーサ、各ソースの取得関数、`fetch_rss`（汎用） |
| `history.py` | URL正規化、`dedupe`、`History`（既出の管理。recheck_days×2 日または30日より古いものは削除） |
| `scorer.py` | `call_llm`（Anthropic/OpenAI を requests で直接呼ぶ）、`extract_json`、`score_all`、`pick_diverse`、`add_details`、`keyword_hit`、`judge_sales` |
| `notifier.py` | `build_message`（通知本文の組み立て）、`clean_url`（utm除去）、`send_line` |
| `main.py` | `fetch_all`、`run`、CLI |
| `tests/test_offline.py` | ネットに出ない E2E テスト（各フィードのサンプル、LLM・LINEのモック）。`python tests/test_offline.py` |
| `.github/workflows/daily.yml` | 定期実行、手動実行（mode選択）、data/ のコミット |

## 5. ソース

| キー | 取得先 | 本番での状態 |
|---|---|---|
| hatena | b.hatena.ne.jp/hotentry/it.rss（RSS1.0、bookmarkcount） | 動作確認済み |
| zenn | zenn.dev/api/articles?order=daily（非公式API） | 動作確認済み |
| qiita | qiita.com/popular-items/feed（Atom） | 動作確認済み |
| hackernews | hn.algolia.com front_page | 動作確認済み |
| producthunt | producthunt.com/feed | 動作確認済み |
| itmedia_ai | rss.itmedia.co.jp/rss/2.0/aiplus.xml | **未確認** |
| sale: gorime | gori.me/feed | **未確認** |
| sale: prtimes | prtimes.jp/index.rdf | **未確認** |

Qiita / Zenn / HN はエンジニア向けの記事が中心で、読者とずれている。運用データを見て停止するかを決める（運営者の判断待ち）。

## 6. AI プロンプトの約束事
- 出力はJSONのみ。`extract_json` でコードフェンスを取り除き、最初の `[` / `{` から抜き出す
- 1回目の採点は全件を1回で処理する（約120件）。gpt-5系は推論トークンも上限に含まれるため、`max_completion_tokens` は16000以上にする
- セール判定では「文面にない割引率や価格は書かない」と指示している（Amazonの価格表示規約と、事実誤りへの対策）

## 7. 方針・制約（変更前に運営者に確認すること）
- **大量のAI記事を自動公開する方式は取らない**。Googleの scaled content abuse ポリシー（2024/3〜）に当たるため。参考記事3件の言い換えも、著作権と品質の両面で禁止
- 参考記事は「事実と論点の材料」にとどめる。運営者メモは任意（2026-10-04 に必須から変更）
- **架空の人物の体験を、実体験のように書かない。** 想定シナリオは見出しに【想定シナリオ】と付け、「〜できそうです」「〜という使い方が考えられます」の形で書く。運営者が試していないことを「試した」「使ってみた」と書かない（読者の誤認、景品表示法の優良誤認、ASP規約への抵触を避けるため）
- ステマ規制（2023/10〜）への対応として、記事に「PR」表記を自動で入れる（Ver.2以降）
- Amazon: PA-API 5.0 は廃止済み（403が返る）で、後継は Creators API。価格はAPIから取得した値だけを表示する。利用には売上実績などの条件がある見込み
- 検索API: Bing Search API は2025/8に終了、Google Custom Search JSON API は2027/1に終了予定。Ver.1 では Brave Search API / Tavily などを使う
- 公開は当面「下書き」まで。品質ゲートが安定してから自動公開を検討する

## 8. 既知の制約・未実装
- 収益スコアはAIの推測。実際にアフィリエイト案件があるかはわからない（Ver.3 で商品DBと照らし合わせる）
- 紹介候補の製品名に、無関係なものが混ざることがある
- セール検知はキーワードでふるっているため、英語の表現ゆれは取りこぼしうる
- 通知は1日1通で、5000字を超えた分は切り捨てる
