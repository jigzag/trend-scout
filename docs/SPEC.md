# trend-scout 仕様書

最終更新: 2026-10-04 / 対象バージョン: Ver.0（話題検知 + セール検知）、Ver.1/2（記事生成 + WordPress下書き。§9）

## 1. 目的と全体像

アフィリエイトブログの記事制作を、ネタ探しから公開まで段階的に自動化するシステム。
このリポジトリはネタ探し（Ver.0）と記事の下書き作成（Ver.1/2）を担う。

```
[Ver.0 運用中] 話題検知・セール検知 → LINE通知
[Ver.1 実装済み・本番未確認] 候補（＋任意の運営者メモ） → 参考記事2〜3件の取得・事実抽出 → 構成（想定シナリオ付き） → 執筆 → 校閲・類似度チェック → 記事HTML
[Ver.2 実装済み・本番未確認] WordPress 下書き投稿（REST API）
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
| 記事生成 | Actions → write-article → Run workflow（候補番号・メモ・日付）。`repository_dispatch`（type `write-article`）でも起動できる |
| 言語 | Python 3.12、依存は `requests` と `PyYAML` のみ |
| AI | OpenAI `gpt-5-mini`（`config.yaml` の `llm.provider` で anthropic に切り替え可能） |
| 通知 | LINE Messaging API の push（LINE Notify は2025年3月に終了済み）。無料枠は月200通 |
| 永続化 | `data/` をワークフローがコミットして保存する（毎日コミットされるので、60日無活動でcronが止まる問題も回避できる） |

### GitHub Secrets（1つの名前に1つの値。3つ別々に登録する）
- `OPENAI_API_KEY`（Anthropicに切り替える場合は `ANTHROPIC_API_KEY`）
- `LINE_CHANNEL_ACCESS_TOKEN`（Messaging API設定タブの、長期のチャネルアクセストークン）
- `LINE_USER_ID`（チャネル基本設定タブの「あなたのユーザーID」。`U`で始まる）
- 記事生成用（任意。なくても動くが機能が減る）：`TAVILY_API_KEY`（関連記事の検索。なければ元記事だけで書く）、`WP_URL` / `WP_USER` / `WP_APP_PASSWORD`（なければ WordPress に投稿せず data/articles/ に保存だけ）

## 3. 処理フロー（main.py `run()`）

```
1. fetch_all(sources)          各ソースを取得。1つ失敗しても続行し、stats.failed に記録
                               各Itemに extra.buzz = ソース内順位から 0〜10 を付与
2. dedupe → History.filter_new URL正規化とタイトル一致で重複をまとめる。14日以内に通知済みのものは除外
3. score_all（AI 1回目）       全件を relevance / usecase / novelty / monetizable（0〜10）＋ topic ラベルで採点（2026-10-26〜）
   total = (rel*0.30 + usecase*0.25 + novelty*0.20 + mon*0.10 + buzz*0.15) * 10（重みは config.yaml）
   AIには「8以上は全体の1割まで」と甘く付けないよう指示
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
| `research.py` | Ver.1：Tavily で関連記事を検索・本文抽出（`gather`）。Tavily がなければ requests で元記事だけ取得 |
| `writer.py` | Ver.1：事実抽出 → 構成 → 執筆 → 校閲のプロンプト、類似度チェックと書き直し、機械チェック（体験表現・想定シナリオ・文字数） |
| `wordpress.py` | Ver.2：`upload_media`、`post_draft`（REST API、アプリケーションパスワード、status=draft 固定） |
| `wp_command.py` | LINE の「公開」「削除」：WordPress の投稿を公開 / ゴミ箱へ |
| `worker/line_webhook.js` | Cloudflare Workers に貼る中継（LINE Webhook → GitHub repository_dispatch） |
| `.github/workflows/wp-command.yml` | 公開・削除の実行（repository_dispatch `wp-command` / 手動） |
| `images.py` | 記事画像の生成（OpenAI Images API、`config.yaml` の `image`）、本文への挿入 |
| `article.py` | Ver.1/2 の CLI：候補の読み込み → 生成 → data/articles/ に保存 → WordPress 下書き → LINE 通知 |
| `tests/test_article.py` | 記事生成のオフラインテスト（Tavily・LLM・WordPress・LINE はモック） |
| `.github/workflows/article.yml` | 記事生成の手動実行（候補番号・メモ・日付）と repository_dispatch、data/articles/ のコミット |

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
- JSONを返す呼び出し（採点・詳細・セール・事実抽出・構成・書き直し）は、OpenAI では **Structured Outputs（strict）** で形式を保証する（2026-10-25〜。スキーマは各モジュールの `*_SCHEMA`、ルートは必ずオブジェクト）
- 念のため `extract_json` は、配列の一部が崩れていても読める要素だけ返す。採点は半分未満しか読めなければ1回だけやり直す（10/25 に途中1件の崩れで全体が止まった対策）
- 出力が上限で途中で切れた（finish_reason=length）ときと、拒否（refusal）のときは、理由つきでエラーにする
- 1回目の採点は全件を1回で処理する（約120件）。gpt-5系は推論トークンも上限に含まれるため、`max_completion_tokens` は16000以上にする
- セール判定では「文面にない割引率や価格は書かない」と指示している（Amazonの価格表示規約と、事実誤りへの対策）

## 7. 方針・制約（変更前に運営者に確認すること）
- **大量のAI記事を自動公開する方式は取らない**。Googleの scaled content abuse ポリシー（2024/3〜）に当たるため。参考記事3件の言い換えも、著作権と品質の両面で禁止
- 参考記事は「事実と論点の材料」にとどめる。運営者メモは任意（2026-10-04 に必須から変更）
- **架空の人物の体験を、実体験のように書かない。** 想定シナリオは見出しに【想定シナリオ】と付け、「〜できそうです」「〜という使い方が考えられます」の形で書く。運営者が試していないことを「試した」「使ってみた」と書かない（読者の誤認、景品表示法の優良誤認、ASP規約への抵触を避けるため）
- ステマ規制（2023/10〜）への対応として、記事に「PR」表記を自動で入れる（Ver.2以降）
- Amazon: PA-API 5.0 は廃止済み（403が返る）で、後継は Creators API。価格はAPIから取得した値だけを表示する。利用には売上実績などの条件がある見込み
- 検索API: Bing Search API は2025/8に終了、Google Custom Search JSON API は2027/1に終了予定。Ver.1 は **Tavily**（無料で月1,000クレジット、カード不要。1記事あたり検索1＋抽出1の約2クレジット）。Brave は2026年に無料プランが終わり、月5ドル分のクレジット制になった
- 公開は当面「下書き」まで。品質ゲートが安定してから自動公開を検討する

## 8. 既知の制約・未実装
- 収益スコアはAIの推測。実際にアフィリエイト案件があるかはわからない（Ver.3 で商品DBと照らし合わせる）
- 紹介候補の製品名に、無関係なものが混ざることがある
- セール検知はキーワードでふるっているため、英語の表現ゆれは取りこぼしうる
- 通知は1日1通で、5000字を超えた分は切り捨てる

## 9. 記事生成 Ver.1 ＋ WordPress 下書き Ver.2（article.py）

```
1. load_candidate          data/candidates/日付.json の n 番目（日付省略時はいちばん新しいファイル）
2. research.gather         元記事の本文（Tavily Extract → 失敗なら requests で直接取得）
                           ＋ Tavily Search（topic＋タイトル、直近1か月、日本優先）から別ドメインの記事を足して最大3件
                           600字未満の本文は使わない。1件あたり6000字で切る
3. writer.extract_facts    参考記事から事実・手順・注意点・不明点をJSONで抜き出す（言い換え、推測なし）
4. writer.make_outline     タイトル・slug・抜粋・想定シナリオの人物・h2構成
5. writer.write_body       HTML本文（3000〜4000字）。h2/h3/p/ul/ol/li/strong/table のみ
6. writer.review           事実メモにない数字の削除、体験表現の修正、想定シナリオの書き方、誤字
6b. writer.trim_hedges     「公式サイトで確認」「明らかにされていません」系が2回以上なら、1回だけ残して削る（2026-10-26〜。KJ指摘：逃げの多い記事は最悪）
7. writer.fix_similarity   参考記事と12文字単位で照合。連続一致40字以上の段落を最大2回書き直す
                           （全体の一致率が8%を超えても段落を特定できない場合は警告だけ出す）
8. writer.check_rules      機械チェック：体験表現（運営者メモの囲みの外）、【想定シナリオ】の有無、文字数
9. assemble                先頭に PR 表記、末尾に「参考にした情報」（出典リンク）を付ける
10. 画像（images.py）       OpenAI の画像生成でアイキャッチ1枚＋【想定シナリオ】の見出し下に1枚。メディアに上げて featured_media に設定
                           イラスト調・文字/ロゴ/実在の画面/実在の人物なし。本文の画像には「イメージ画像（AI生成）」のキャプション
                           WordPress に投稿するときだけ作る。失敗しても記事は止めず、LINE の警告に出す
11. 保存 → 投稿 → 通知     data/articles/日付-n-時刻.html / .json、WordPress に下書き（公開はしない）、LINE に編集URLと警告
```

- AI の呼び出しは1記事あたり4〜6回。モデルは `llm` と同じ（`article.llm` で上書き。初期値は reasoning_effort=medium）
- 運営者メモがあるときだけ `<div class="operator-note"><h2>運営者のひとこと</h2>…</div>` を入れる。この囲みの中だけ体験表現を許す
- 失敗したらトレースバックを LINE に送る。data/articles/ はワークフローの最後に必ずコミットする
- LINE 操作（`worker/line_webhook.js`、設定手順は `docs/LINE_SETUP.md`）：Cloudflare Workers が LINE の Webhook を受け、
  署名（X-Line-Signature, HMAC-SHA256）と送信者（LINE_USER_ID）を確認して GitHub の repository_dispatch を呼ぶ。受付はリプライで返す（push 枠を使わない）
  - `1〜3 [メモ]` → `write-article` {n, memo, date}
  - `公開 [ID]` / `削除 [ID]`（`ボツ` も可）→ `wp-command` {action: publish|trash, id}。ID 省略時は data/articles/ の created_at が最新の記事
  - それ以外 → 使い方をリプライ
- `wp_command.py`：公開済みの記事は LINE から削除しない。ゴミ箱の記事は公開しない。削除はゴミ箱への移動（完全削除しない）
- `wp-command.yml` は `write-article` と同じ concurrency グループ（作成中の記事を先に公開しないため）

## 10. 海外ツール中心の方針（2026-10-26〜、KJ決定）
- 海外で出たばかりのAIツール・新機能を、日本語の情報が少ないうちに紹介し、「日本の読者ならどう使えるか」を中心に書く
- **翻訳はしない**（翻訳権の侵害になる）。英語の記事から事実だけを抜き出し、日本語で新しく書く。料金は元の通貨のまま
- ソース：Product Hunt（AIカテゴリ、20件）、TechCrunch AI、The Verge AI を追加。関連記事の検索は国の指定なし（英語の記事も探す）
- 記事構成：どんなツールか → できること → 料金・日本から使えるか・日本語対応 → 【想定シナリオ】2〜3人（記事の半分）→ 違い・向いている人 → まとめ
- カテゴリ：config.yaml の `categories` から AI が1つ選ぶ（Structured Outputs の enum で縛る）。WordPress になければ自動で作る
- 毎朝の通知は「関連・用途・新規・収益・話題」の点と「使い方の例」を出す
- 記事冒頭に「ざっくり言うと」の囲み（`easy-box memo-box`、Cocoon のメモボックスの見た目）を必ず入れる。中学生でも分かる言葉で3〜4項目。本文も1文40字目安、専門用語は初出でかっこ書き説明（2026-10-27 KJ：説明が難しすぎる）

## 11. アフィリエイトリンク Ver.3（affiliate.py、2026-10-27〜）
- 台帳 `data/affiliates.yaml`：ツールごとに `name` / `aliases` / `link` / `added`。`link` は URL か、ASP が出す HTML（計測用の img タグ込み）をそのまま入れる
- 登録は LINE から：`リンク ツール名 URL`（別名は `ツール名/別名` のように / で区切る）、`リンク削除 ツール名`、`リンク一覧`。Worker → `affiliate.yml` → 台帳をコミットして LINE に結果を返す。登録済みの名前・別名で送るとリンクを差し替える
- 記事生成時（画像の後・組み立ての前）に、タイトルと本文に出てくるツールを台帳から探し（英字名は単語単位、大文字小文字は区別しない）、出現回数の多い順に最大2件を「この記事で紹介したツール」の囲み（`affiliate-box blank-box`）に入れる。場所は想定シナリオの章の終わりと本文の最後の2か所
- カテゴリで出すリンク：`リンクカテゴリ ツール名 画像、動画` で台帳の `categories` を設定（短い言葉は config のカテゴリ名に部分一致で直す。「なし」で解除）。そのカテゴリの記事の最後に「自分で作るのが難しければ、プロに頼む方法もあります → ツール名」を1回入れる（前置きは台帳の `pitch` で変えられる）。ココナラのように本文に名前が出ない広告用
- URL のリンクには `rel="sponsored noopener"` を付ける。PR表記は全記事の冒頭に既にある
- LINE の下書き通知に「アフィリンク N件（ツール名）」を出す
- 未実装：公開済み記事へのあと付け、Amazon（アソシエイト承認後）

## 12. 分かりやすさ（2026-10-29〜。記事の流れ・ツールの種類別の構成は §14 で置き換え）
- 読者は「コードを書かない個人」。業務システム（HubSpot等）前提・専門職向け・法人契約前提のツールは genre の対象外、採点の usecase も3以下
- 事実抽出で `reader_fit`（個人が一人で使い始められるか）を判定し、false なら LINE の下書き通知に「ボツ推奨」の警告を出す
- 構成の基本：困りごと → 何が変わるか → 使い始めるまで → 料金・日本から使えるか → 【想定シナリオ】 → 注意・向いている人 → まとめ
- 執筆・校閲・逃げ表現の削除の後に `writer.clarify`：AIにくわしくない読者の立場で読ませ（Structured Outputs で words / sentences）、挙がった言葉・文だけを直す。15個以上なら警告
- ツールの種類で記事の中心を変える（2026-10-29 KJ「こういう記事に必要なのは導入手順」、Survival LLM の下書き58）：事実抽出で `tool_type`（app=入れるアプリ / web=Webサービス / feature=既存アプリの新機能）と `requirements`（動作環境）を取り、`writer.STRUCTURE` で構成を切り替える。app は「入れる前の確認 → 導入手順（h3でステップごと）→ 最初の使い方」を中心に、想定シナリオは1〜2人。web は登録から最初の1回まで、feature は機能の場所と使い方が中心
- パソコン・スマホの一般的な操作（メモリの確かめ方、SmartScreen や Gatekeeper の警告の進め方など）は事実メモになくても書いてよい。ツール固有の画面・数字は事実メモのみ
- app なのに h3「ステップ」が3つ未満なら警告

## 13. 公式リンクと運営者のスクショ（2026-10-29〜、KJ「リンクとか無理なん？」「仮想OSでスクショは？」。手順・スクショ・操作画面は §14 で廃止）
- 公式サイト・ダウンロードページ：`research.link_candidates` が「参考記事のURL＋元記事の本文に貼られた別サイトへのリンク（SNS・同じサイト内は除く）」を候補にし、事実抽出で `official_link` / `download_link` を**番号で**選ばせる（候補にないURLは記事に出ない。0なら付けない）。`writer.add_links` が、公式サイトを「ざっくり言うと」の下に、ダウンロード（Webなら登録）ページを手順の最初の該当 h3（ダウンロード・インストール・登録など）の段落の後ろに入れる。全部の書き直しが終わった後に入れるので、AIがURLを変えることはない
- スクショ：運営者が LINE に画像を送る → Worker が `line-shot` を起動（画像の中身は扱わない）→ `shots.yml` が LINE から取って `data/shots/pending/日時-ID.jpg` にコミット。まとめ送りは最後の1枚にだけ返事
- 記事を作るとき（WordPress 投稿時のみ）、貯まった画像（最大8枚）を見出し（h3）の一覧と一緒に AI に見せ、どの見出しの画面か・このツールの画面か・キャプションを選ばせる。合う見出しの段落の後ろに「（運営者のパソコンで撮影）」付きで入れ、使った画像は消す。このツールの画面でないものは残し、7日たったら消す
- LINE：`スクショ確認`（枚数）/ `スクショ取消`（全部消す）。下書き通知に「スクショ N枚」
- 撮影は Windows サンドボックス（Windows 11 Pro 標準の使い捨て環境）を推奨。手順は `docs/SCREENSHOT.md`
- 公式サイトの画面（自動、2026-10-29 KJ「勝手にとってほしい」）：記事生成の Actions で Playwright（Chromium）を入れ、ダウンロード（登録）ページ、なければ公式サイトの最初の1画面（1280×800）を撮って、手順の中のリンクの直後に「出典：公式サイト（ドメイン）」付きで入れる（`shots.capture`、config の `image.page_shot`）。Playwright が入っていなければ黙って省く
- 公式ページの画像と動画（2026-10-30 KJ「各ステップの画面がほしい」、Gemini の下書き66）：公式サイト（official_url）のページから og:image と本文の img / picture の画像（30KB以上、SVG・GIF・幅や高さ300未満は除く、最大10枚）を集め、AI に見出し一覧と一緒に見せて「手順の説明に役立つ操作画面か・どの見出しか」を選ばせる（`shots.place_official`、1見出し1枚）。合う見出しの段落の後ろに「（出典：ドメイン）」付きで入れる。ページに YouTube 動画が埋め込まれていれば、最初の「ステップ」見出しの手前に youtube-nocookie で埋め込む（Google の発表は操作の様子を動画で載せることが多い）。config の `image.official_images`
- ログインが必要なサービスの画面は自動では撮らない（運営者のアカウント情報を外に置く危険と、ロボット判定でアカウントが止まるおそれ）。KJ のスクショで補う
- 手順の中のリンクを入れる見出しの目印に「ログイン・サインイン・開く」を追加（Gemini の記事で、画面が冒頭に入ってしまったため）
- アプリを入れた後の画面の全自動（AIが Actions の Windows を操作して撮る）は保留：1記事50〜150円の見込み、失敗が多い、数十GBのアプリは無理、Mac は撮れない。公式サイトの画面で足りなければ KJ に金額を示して試す：アプリごとに画面が違い自動で進められない、数十GBのアプリは容量・時間が足りない、Mac版は撮れない

## 14. 方針転換：手順記事をやめ、全自動で書ける記事に（2026-10-31〜）
- 経緯：KJ「一回も満足いく記事出来ない。根本的に違うのか？」→ 誰も触っていないツールの使い方記事を、ネットの情報だけで書いていたのが根本原因（画面・体験・判断は触った人しか書けない）。A（KJが触ってAIが書く）と B（触らなくても価値が出る記事に変える）のうち、KJ が B を選択
- 毎日の記事（番号を返すと作る流れは同じ）＝「新ツール解説」：何が出たか → 何ができて何がうれしいか（中心）→ 料金・日本から使えるか・日本語 → 似たツールとの違い（事実抽出の `compare` にあるときだけ）→ 気をつけること → 向いている人・向いていない人 → まとめ（`writer.STRUCTURE`）
  - やめたもの：導入手順のステップ、架空の人物の【想定シナリオ】、ツールの種類別の構成、公式ページの操作画面の割り当て、LINE のスクショの取り込み（Worker・shots.yml は残っているが記事では使わない）
  - 残したもの：公式サイト・登録（ダウンロード）ページのリンク（「ざっくり言うと」の下にまとめて）、公式サイトの画面（リンクの下）、公式の YouTube 動画（2つ目の見出しの手前。`image.official_video`）、本文の画像（2つ目の見出しの直後）
  - `check_rules`：ステップの見出し・架空の人物があれば警告
- 週1回の「今週の新着AIツールまとめ」（`roundup.py`、`.github/workflows/roundup.yml`、毎週日曜 8:00）
  - 直近7日の候補を評価（total）の高い順に、同じURL・同じ話題を除いて選ぶ → 1件ずつ元記事から事実を取り（個人向けでない・ツールの話でないものは外す）、最大6件
  - 3件未満ならその週は作らず LINE で知らせる
  - 記事：書き出し → 「ざっくり言うと」（1ツール1行）→ ツールごとの h2（本文250〜400字＋向いている人・料金）＋公式サイトのリンク → まとめ。逃げ表現の削除・分かりやすさチェック・類似度チェックは毎日の記事と同じ
  - **自動で公開**（2026-10-31 KJ「自動公開でいいよ」、`roundup.auto_publish`）。体験表現・指示の言葉の混入・元記事との類似が高いときは下書きにして、LINE に「公開 ID」「削除 ID」の案内を出す
  - カテゴリ「今週のAIツールまとめ」、URL は `スラッグ-YYYYMMDD`。結果は `data/roundups/日付.json` / `.html`
  - 費用：1回 約20〜30円（事実抽出6件＋執筆＋チェック＋アイキャッチ1枚）
- 比較記事（「動画生成AI 5つ比較」など）は、情報が貯まってから検討

