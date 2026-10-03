# CLAUDE.md — 引き継ぎメモ（Claude Code 用）

仕様の全体は `docs/SPEC.md` を先に読むこと。ここには「経緯・現状・次にやること・ハマりどころ・進め方」をまとめる。

## 運営者（KJ）との進め方
- **KJが何を作るかを決め、Claudeが実装する。** ただの追従はしない。設計や方針に問題があれば、根拠を添えて先に指摘する
- **事実を確認してから解釈を重ねる。** API仕様、料金、規約、サービスの終了などは、推測で書かずに調べて確認する
- KJは非エンジニア寄りで、Windows PC を使い、GitHub は Web 画面で操作する。手順を書くときは：
  - 番号付きで、クリックする場所の名前を**画面の表記どおり**に書く
  - 「1つの名前に1つの値」のように、迷いやすいところは明示する（Secretを1つにまとめて登録してしまい、ハマった実績あり）
  - コマンドは PowerShell 用で、コピーすればそのまま動く形にする
- 返答は日本語で簡潔に。結論を先に書く

## 環境
- ローカル: `C:\Users\sekin\Desktop\cloudecode\trend-scout`（Windows）
- リモート: `https://github.com/jigzag/trend-scout`（Private）、ブランチは `main`
- push：このPC（`Desktop\cloudecode\trend-scout`）では Claude から `git pull --rebase` → `git push` できる（2026-10-04 確認）。別の環境では認証情報がない場合があるので、そのときは KJ が PowerShell で push する
- テスト: `python tests/test_offline.py`（ネット不要。変更したら必ず実行し、テストも更新する。Windows でも動くよう、一時フォルダは `tempfile`、ファイル読み書きは `encoding="utf-8"` を指定する）
- 試運転: `python main.py --dry-run`（APIキーが必要。LINEには送らない）

## 現在の状態（2026-10-04 時点）
- 毎朝7時にLINEへ「今日の記事候補」3件＋セール欄が届く。全コミットpush済み（OpenAI gpt-5-mini）
- 2026-10-04 の手動実行で、全8ソース（はてブ / Zenn / Qiita / HN / Product Hunt / ITmedia AI+ / gori.me / PR TIMES）の取得、同一話題の除外、非エンジニア向けの絞り込み、セール欄を本番で確認済み
- 10/4 の結果で気になった点（1週間分の結果を見てから `genre` / `focus` をまとめて調整する予定）：
  - 「発表まとめ」記事（OpenAI DevDay まとめ）の検証が8と高すぎる。「試すなら」の中身が「読んで箇条書き」だけだった → 10/4 に `genre` へ「まとめ記事は検証3以下」を追加済み。効いているか確認する
  - セールが2件とも高額な iPad（33万円の iPad Pro 2TB など）で、読者からずれている。期間も空欄
  - 紹介候補に「（発表内容に準拠）」のような、具体的な商品名でないものが混じる
- 同じ日に再実行すると `data/candidates/日付.json` が上書きされる

## 経緯で決まったこと（変える場合は KJ に確認）
- 「何でもありのトレンドブログ」は却下。**自分のジャンル内の話題を拾い、実際に試して書く**方式にした
- 毎朝の候補は3件、LINEで受け取る
- 読者は**非エンジニア**。Qiita / Zenn / HN は残しているが、1週間ほど運用してから止めるかを判断する（KJの判断待ち）
- セール情報は記事候補とは**別枠**。Amazonの本格連携（Creators API）は、サイトとアソシエイトの準備ができてから
- AIは OpenAI（KJがキーを登録済み）。Anthropic のキーはまだない
- 掲載先は**独自ドメインの WordPress ＋ エックスサーバー（スタンダード）＋ Cocoon**に決定（2026-10-04、KJから一任）。理由と手順は `docs/SITE_SETUP.md`。ドメインは `nocode-ai.net`（.jp はスタンダードの無料特典対象外のため）。サイト名は「ノンプログラミングでAIを！」。運営者名は zag。2026-10-04 に契約・WordPress（Cocoon子テーマ、パーマリンク=投稿名）・REST API の国外制限OFF・プライバシーポリシー/運営者情報/お問い合わせ（Contact Form 7）の公開まで完了。フッターメニュー設定とお問い合わせの送信テストも済み。キャッチフレーズは「ノンプログラミングでAIを！ 最新AIツールを、非エンジニア目線でわかりやすく。」（「実際に試して」は方針変更で削除。運営者情報・プライバシーポリシーの文言も想定シナリオ方式に合わせた）。残り：パスワード変更と2段階認証（KJ）、Amazonアソシエイト承認後に定型文をプライバシーポリシーへ追加

## 次にやること（優先順）
1. 1週間ほど（〜10/11）運用し、候補の精度を見て `config.yaml` の `genre`（特に「対象外」）を調整する。ソースの入れ替えも検討する
2. **Ver.1 記事生成**（SPEC §1）：
   - 入力：`data/candidates/日付.json`（＋任意のKJメモ）。メモは LINE 返信で受けたい（KJ希望）→ Cloudflare Workers で Webhook を受けて repository_dispatch で Actions を起動する案。まず記事生成本体を作り、そのあとつなぐ
   - 検索：Brave Search API か Tavily（Bing / Google CSE は使えない）
   - 参考2〜3件から事実・論点・出典をJSONで抽出 → 構成 → 執筆（【想定シナリオ】で具体性を出す。メモがあれば「運営者の感想」として足す）→ 校閲 → 元記事との類似度チェック → HTMLを出力
   - 想定シナリオは架空だと明記する。試していないことを「試した」と書かない（SPEC §7）
3. サイト立ち上げ（`docs/SITE_SETUP.md` の手順で KJ が契約・設定）→ Ver.2 下書き投稿
4. ASP登録 / Amazonアソシエイト → Ver.3 商品DBとリンク挿入

## ハマりどころ（実際に起きたこと）
- **Actions が毎朝 `data/` を自動コミットするので、ローカルから push すると `rejected (fetch first)` になる。** push の前に必ず `git pull --rebase` を実行する
- **エックスサーバーは初期状態で国外IPからの REST API を拒否する。** GitHub Actions は海外から動くので、Ver.2 の投稿が 403 になる。サーバーパネル →「WordPressセキュリティ設定」→「国外アクセス制限設定」→「REST API アクセス制限」を OFF にする（SITE_SETUP 手順2）
- **Actions の「Re-run jobs」は、その実行が最初に使ったコミットで再実行する。** コードを修正した後は、必ず「Run workflow」で新しく実行する
- Secretが `null` になる原因：1つのSecretに3つまとめて登録した / Variables タブに登録した / Environment secrets に登録した / 名前のタイプミス
- `x-api-key header is required` や `OPENAI_API_KEY が空です` は、キーが**空**という意味（名前違いや未登録）。401 で invalid と出るのは、キーの中身が違う場合
- LINE：公式アカウントを友だち追加していないと届かない。Channel secret や Channel ID は使わない。チャネルの作成は Official Account Manager から行う（LINE Developers から直接ではない）
- gpt-5系では `max_tokens` が使えないので `max_completion_tokens` を使う。推論トークンも上限に含まれるので、小さくしすぎると返答が空になる
- Cowork などの Linux VM からこのフォルダで git を操作すると、`.git` に `HEAD.lock` や `tmp_obj_*` が残ることがある。残っていたら削除する（Windows 側の git が失敗する原因になる）

## コーディング規約
- 依存を増やさない（requests / PyYAML のみ。SDKではなく REST を直接呼ぶ）
- 1つのソースや判定が失敗しても全体は止めない。失敗は `stats["failed"]` に入れて、通知の末尾に出す
- 設定はコードに書かず、`config.yaml` に置く。ジャンル定義の文章が採点基準になる
- コメントやメッセージは日本語。コミットメッセージも日本語で、何を変えたかを1行で書く
