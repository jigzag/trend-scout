# 話題検知 Ver.0（trend-scout）

毎朝7時に、はてブ・Zenn・Qiita・Hacker News・Product Hunt から話題を集め、
AIが「このブログで記事にすべきか」を採点して、上位3件をLINEに送ります。

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
2. リポジトリの Settings → Secrets and variables → Actions → New repository secret で、以下を登録する

   | 名前 | 値 |
   |---|---|
   | `ANTHROPIC_API_KEY` | ClaudeのAPIキー（OpenAI利用時は `OPENAI_API_KEY`） |
   | `LINE_CHANNEL_ACCESS_TOKEN` | 手順1-4のトークン |
   | `LINE_USER_ID` | 手順1-5のユーザーID |

3. Actions タブ → `daily-trend-scout` → **Run workflow** で手動実行し、LINEに届くか確認する

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

## 次の工程（Ver.1 以降）

```
data/candidates/日付.json ＋ LINEで返信した「自分メモ」
  → 参考記事3件の取得・事実抽出 → 構成 → 執筆 → 校閲・類似度チェック → WordPress下書き
```
