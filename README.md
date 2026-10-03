# 話題検知 Ver.0（trend-scout）

毎朝7時に、はてブ・Zenn・Qiita・Hacker News・Product Hunt・ITmedia AI+ から話題を集め、
AIが「このブログで記事にすべきか」を採点して、上位3件をLINEに送ります。
あわせて、読者に関係のあるセール・キャンペーン情報を最大3件、別枠で付けます。

詳しい仕様は `docs/SPEC.md`、Claude Code への引き継ぎは `CLAUDE.md` を参照。

```
取得（5ソース）→ 重複除去 → 既出除外（14日）→ AI一括採点 → 上位3件の詳細生成 → LINE → 記録
```

採点基準は3つです。比重は `config.yaml` で変えられます。

- **関連**：ジャンルとの関連度
- **検証**：自分で触って一次情報を書けるか
- **収益**：紹介できる有料ツールがありそうか

送った候補は `data/candidates/日付.json` に保存します。次の工程（記事生成）はこのファイルを入力にします。

---

## セットアップ（所要時間 20〜30分）

### 1. LINE側：通知用のLINE公式アカウントを作る

LINE Notify は2025年3月に終了しているため、Messaging API を使います。
無料プランでも月200通まで送れます。1日1通なら月30通前後なので、無料の範囲で収まります。

1. [LINE Official Account Manager](https://manager.line.biz/) でLINE公式アカウントを作る
2. 設定 → Messaging API →「Messaging APIを利用する」→ プロバイダーを選ぶ
   （プライバシーポリシー・利用規約のURLは空欄でOK。この画面の Channel ID / secret は使わない）
3. 同じ画面の「LINE Developersコンソール」リンクからチャネルを開く
4. 「Messaging API設定」タブの一番下 → **チャネルアクセストークン（長期）** を発行してコピー
5. 「チャネル基本設定」タブの一番下 → **あなたのユーザーID**（`U`で始まる）をコピー
6. 「Messaging API設定」タブのQRコードで、自分のLINEから**友だち追加**（しないと届きません）
7. Official Account Manager で「応答メッセージ」をオフにしておくと余計な自動返信が出ません

### 2. AIのAPIキー

- Claude を使う場合：[Anthropic Console](https://console.anthropic.com/) でAPIキーを発行する
- OpenAI を使う場合：`config.yaml` の `provider: openai` に変更し、OpenAI のAPIキーを用意する

1日の消費は、採点1回と詳細生成1回の計2回です。

### 3. GitHub で毎朝動かす（PCの電源が切れていても動きます）

1. GitHub で **Private** リポジトリを作り、このフォルダの中身をすべてpushする
2. Secretを**3つ別々に**登録する（1つにまとめて入れると動きません）

   Settings → 左メニュー「Secrets and variables」→「**Actions**」→「**Secrets**」タブ →「**New repository secret**」

   ここで「Nameに名前を1つ、Secretに値を1つ入れて Add secret」を**3回繰り返す**。

   | 回 | Name（そのままコピー） | Secret（値だけ。`名前=` や改行は入れない） |
   |---|---|---|
   | 1回目 | `ANTHROPIC_API_KEY` | `sk-ant-api03-...` のキー（OpenAI利用時は名前を `OPENAI_API_KEY`） |
   | 2回目 | `LINE_CHANNEL_ACCESS_TOKEN` | 手順1-4のトークン |
   | 3回目 | `LINE_USER_ID` | 手順1-5の `U` で始まるID |

   登録後、「Repository secrets」の一覧に名前が3つ並んでいればOK。
   「Variables」タブや「Environment secrets」に入れても読まれません。

3. Actions タブ → `daily-trend-scout` → **Run workflow**
   - まず mode = `test-line` で実行 → LINEに「テスト送信です」が届けばLINE設定OK
   - 次に mode = `run` で通常実行

以後は毎朝7時（日本時間）に自動で実行されます。

### ローカルで試す場合（Windows）

```powershell
pip install -r requirements.txt
$env:ANTHROPIC_API_KEY="sk-ant-..."
python main.py --dry-run     # LINEに送らず、通知文を画面に出す
```

---

## 調整ポイント

- **ジャンルを変える**：`config.yaml` の `genre` を書き換える。採点基準はこの文章から決まります
- **候補が的外れ**：`genre` に「対象外」の例を書き足すのが一番効きます（例：「Web開発一般、インフラ、セキュリティは対象外」）
- **件数**：`top_n`
- **ソースを止める**：`sources.xxx.enabled: false`
- **ソースが取得失敗**：通知の末尾に「取得失敗: xxx」と出ます。他のソースはそのまま続行します。全体が落ちた場合は、エラー内容がLINEに届きます

## 記事の下書きを作る（Ver.1/2）

LINEで届いた候補から1つ選んで、記事の下書きを作ります。

1. Actions タブ → `write-article` → **Run workflow**
2. 「候補番号」に LINE の ■ の番号（1〜3）を選ぶ
3. 「運営者メモ」は任意。試した感想があれば一言（例：無料版は3回まで、日本語はいまいち）
4. 緑の **Run workflow** を押す → 数分で LINE に「下書きができました」と編集画面のURLが届く

記事は、参考記事2〜3件から事実を抜き出して新しく書き、【想定シナリオ】（架空の人物での使い方の例）を入れます。
WordPress には**下書き**で入るだけで、公開はしません。内容を確認してから公開してください。

使うための Secret（README の手順と同じ画面で、1つずつ登録）：

| Name | 値 | ないとき |
|---|---|---|
| `TAVILY_API_KEY` | [Tavily](https://app.tavily.com/) で発行したキー（`tvly-` で始まる。無料・カード不要） | 元記事1件だけで書く |
| `WP_URL` | `https://nocode-ai.net` | WordPress に投稿せず、GitHub の data/articles/ に保存だけ |
| `WP_USER` | WordPress のユーザー名 | 同上 |
| `WP_APP_PASSWORD` | WordPress のアプリケーションパスワード（`docs/SITE_SETUP.md` 手順5） | 同上 |

## 次の工程

```
data/candidates/日付.json ＋（任意）LINEで返信した「自分メモ」
  → 参考記事3件の取得・事実抽出 → 構成 → 執筆 → 校閲・類似度チェック → WordPress下書き
```
