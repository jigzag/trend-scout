# LINE だけで操作するための設定（所要時間 20〜30分・すべて無料）

設定が終わると、LINE の返信だけで次のことができます。

| 返信 | 動き |
|---|---|
| `1`（または 2, 3） | その番号の候補で記事の下書きを作る |
| `1 無料版は3回まで` | メモ付きで作る（番号のあとに空白を入れて感想を書く） |
| `公開` | いちばん新しく作った下書きを公開する |
| `削除` | いちばん新しく作った下書きをゴミ箱へ（公開済みの記事は消さない） |
| `ヘルプ` | 使い方を返す |

仕組み：LINE → Cloudflare Workers（中継、無料）→ GitHub Actions（記事作成・公開）→ LINE に結果

---

## 1. GitHub：Actions を起動するための鍵（トークン）を作る

1. GitHub 右上の自分のアイコン →「**Settings**」
2. 左メニューの一番下「**Developer settings**」→「**Personal access tokens**」→「**Fine-grained tokens**」
3. 「**Generate new token**」
4. 次のように入力する
   - Token name：`line-webhook`
   - Expiration：「**1 year**」（期限が来たら作り直す。カレンダーに入れておく）
   - Repository access：「**Only select repositories**」→ `jigzag/trend-scout` を選ぶ
   - Permissions →「Repository permissions」→「**Contents**」を「**Read and write**」にする（ほかは触らない）
5. 一番下の「**Generate token**」→ 表示された `github_pat_...` を**コピーしてメモ帳に一時保存**（画面を閉じると二度と表示されない）

## 2. LINE：チャネルシークレットを確認する

1. [LINE Developers](https://developers.line.biz/console/) → 通知用のチャネルを開く
2. 「**チャネル基本設定**」タブ →「**チャネルシークレット**」をコピーしてメモ帳へ
3. 「**Messaging API設定**」タブの「チャネルアクセストークン（長期）」も、すでに GitHub に登録したものと同じ値をメモ帳へ（「発行」は押さない。押すと古いトークンが使えなくなる）
4. 「チャネル基本設定」の「**あなたのユーザーID**」（`U` で始まる）もメモ帳へ

## 3. Cloudflare：中継プログラムを置く

1. [Cloudflare](https://dash.cloudflare.com/sign-up) で無料アカウントを作る（メール認証まで）
2. 左メニュー「**Workers & Pages**」→「**作成**（Create）」→「**Hello World から始める**（Start with Hello World!）」
3. 名前を `line-webhook` にして「**デプロイ**（Deploy）」
4. 「**コードを編集**（Edit code）」を押し、左側のコードを**すべて消して**、このリポジトリの `worker/line_webhook.js` の中身を**まるごと貼り付け**、右上の「**デプロイ**（Deploy）」
5. Worker の画面に戻り「**設定**（Settings）」→「**変数とシークレット**（Variables and Secrets）」→「**追加**（Add）」で、次の5つを**1つずつ**登録する。タイプはすべて「**シークレット**（Secret）」

   | 変数名 | 値 |
   |---|---|
   | `LINE_CHANNEL_SECRET` | 手順2-2 のチャネルシークレット |
   | `LINE_CHANNEL_ACCESS_TOKEN` | 手順2-3 のチャネルアクセストークン |
   | `LINE_USER_ID` | 手順2-4 の `U` で始まるID |
   | `GITHUB_TOKEN` | 手順1-5 の `github_pat_...` |
   | `GITHUB_REPO` | `jigzag/trend-scout` |

6. Worker の画面に表示されている URL（`https://line-webhook.○○○.workers.dev`）をコピー

## 4. LINE：返信が Cloudflare に届くようにする

1. LINE Developers →「**Messaging API設定**」タブ →「**Webhook URL**」の「編集」→ 手順3-6 の URL を貼って「更新」
2. 「**検証**」を押して「成功」と出ればOK
3. すぐ下の「**Webhookの利用**」をオンにする
4. [LINE Official Account Manager](https://manager.line.biz/) →「設定」→「**応答設定**」で、「**Webhook**」をオン、「**応答メッセージ**」をオフ

## 5. 試す

1. LINE で `ヘルプ` と送る → 使い方が返ってくればOK
2. `1` と送る →「受け付けました」→ 数分後に「下書きができました」
3. プレビューを見て、`削除`（試しなのでボツ）と送る →「ゴミ箱へ移しました」

メモ帳に一時保存したトークンやシークレットは、設定が終わったら消してください。

## うまくいかないとき

| 症状 | 原因と対処 |
|---|---|
| 手順4-2 の「検証」が失敗する | Worker のデプロイ忘れ、または `LINE_CHANNEL_SECRET` の値違い |
| 何を送っても反応がない | 「Webhookの利用」がオフ、または `LINE_USER_ID` の値違い（運営者以外のメッセージは無視する作り） |
| 「GitHub の起動に失敗しました（401）」 | `GITHUB_TOKEN` の値違いか期限切れ → 手順1で作り直し、Cloudflare の値を更新 |
| 「GitHub の起動に失敗しました（403/404）」 | トークンの対象リポジトリか、Contents の権限が足りない |
| 定型の自動返信が出る | Official Account Manager の「応答メッセージ」がオンのまま |
