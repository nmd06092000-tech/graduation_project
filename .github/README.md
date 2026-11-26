# 地域の困りごと報告システム

地域の住民が、道路の穴・街灯の故障・公園遊具の破損・不法投棄といった身近な困りごとを、写真と位置情報つきで行政へ報告できる Web アプリケーションです。
行政職員は報告を状況別に管理でき、AI（Google Gemini）が報告のカテゴリ・緊急度・担当部署の候補を自動で判定します。

## 目次

- [主な機能](#主な機能)
- [技術スタック](#技術スタック)
- [システム構成](#システム構成)
- [ディレクトリ構成](#ディレクトリ構成)
- [動作環境](#動作環境)
- [セットアップ](#セットアップ)
- [起動](#起動)
- [テスト](#テスト)
- [管理コマンド](#管理コマンド)
- [本番環境](#本番環境)

## 主な機能

アクター（利用者）ごとに画面が分かれています。

### 住民（市民・県民）

- アカウント作成、ログイン、ログアウト
- パスワード再設定（メールのリンクで本人確認）、ログイン中のパスワード変更
- 困りごとの報告（件名・内容・発生場所・写真）
  - カメラで撮影、または端末の写真を選択
  - 位置情報（GPS または写真の EXIF）を取得。位置情報がない場合は、入力した住所から座標を検索（国土地理院の住所検索 API）
- 報告履歴の一覧、マイページ
- お知らせ（報告の受付、AI の判定、職員による対応状況の更新）。未読バッジと既読管理つき
- プロフィール設定、アイコンの設定（撮影・削除）

### 職員

- ダッシュボード（本日の優先対応案件、地域の問題マップ）
- 報告一覧（検索・絞り込み）と報告詳細
- 対応状況の更新（未対応 → 対応中 → 対応済み など）と対応履歴の記録
- 緊急度の判定（AI の判定結果と異なる場合は理由を入力）
- AI 分析ログの確認と、AI モデルの再実行
- 地図表示（Google Maps。緊急度ごとにピンの色を変更）
- 重複報告の確認（代表案件への統合、重複なしの判定、一括ステータス変更）
- 統計画面と、レポート出力（PDF / CSV）
- AI 再判定の結果の通知（ダッシュボードに表示。既読管理つき）

### 管理者

- 管理者ポータル
- ユーザー一覧（職員・管理者）、削除（論理削除）、ロック解除、再設定コードの発行
- 新規職員・管理者アカウントの登録
- システム管理（担当部署・カテゴリのマスタの追加・名称変更・非表示／再表示）

### AI 機能

| 機能 | 内容 |
|---|---|
| 報告の分類 | カテゴリ・緊急度・担当部署の候補を判定し、信頼度とともに保存（職員が修正しても AI の元の結果は残る） |
| 写真の確認 | 撮影した写真が報告に使えるかを確認 |
| 住所の推定 | GPS の座標から町名・丁目程度の住所を推定 |
| 緊急度の再判定 | 未対応のまま一定時間（既定 12 時間）が過ぎた報告を再判定し、職員に通知 |

`GEMINI_API_KEY` を設定すると Gemini が有効になります。キーがない場合、または `AI_PROVIDER=rule_based` の場合は、キーワードによる判定（穴・街灯・遊具・ゴミなど）に切り替わります。この場合、写真の確認と住所の推定は行いません。
AI の呼び出しに失敗しても報告は保存され、キーワード判定にフォールバックします。AI へは氏名・メールアドレスを送らず、写真は EXIF（位置情報）を除去して送信します。

## 技術スタック

| 区分 | 使用技術 |
|---|---|
| バックエンド | Python / Flask 3、Flask-SQLAlchemy、Flask-Migrate（Alembic）、Flask-Login、Flask-WTF（CSRF 対策） |
| データベース | PostgreSQL（psycopg 3） |
| フロントエンド | Jinja2 テンプレート、Bootstrap 5、Chart.js、Google Maps JavaScript API |
| AI | Google Gemini（`google-genai`）、キーワード判定（フォールバック） |
| その他 | Pillow（写真の検証・縮小）、reportlab（PDF 出力。日本語フォントは IPAex ゴシックを埋め込み） |
| テスト | pytest |

## システム構成

アプリケーションは 3 層構成で、依存の向きは一方向（route → service → model）です。

```
Route（app/blueprints）  →  Service（app/services, app/ai）  →  Model（app/models）
```

| 層 | 役割 |
|---|---|
| Route | リクエストの受け取り、service の呼び出し、画面描画・リダイレクト・フラッシュメッセージ |
| Service | 業務ロジックのすべて（入力チェック、DB 更新、AI 呼び出し、写真の保存） |
| Model | テーブルとリレーションの定義 |

- アクター（公開・認証・市民・職員・管理者）ごとに Blueprint を分けています。
- 報告の状態変更は `status_service.py` だけが行い、変更のたびに対応履歴を 1 行記録します。
- AI の判定結果は報告本体とは別のテーブルに保存し、職員の最終判断を上書きしません。

## ディレクトリ構成

```
.
├── run.py                 # 起動エントリ（create_app() を呼ぶ）
├── config.py              # .env を読み込む設定
├── requirements.txt       # 依存ライブラリ
├── .env.example           # 環境変数のひな形
├── app/
│   ├── blueprints/        # public / auth / citizen / staff / admin
│   ├── services/          # 業務ロジック
│   ├── ai/                # Gemini・キーワード判定
│   ├── models/            # SQLAlchemy モデル
│   ├── utils/             # 共通処理（権限チェック、メール送信、位置情報など）
│   ├── templates/         # Jinja2 テンプレート
│   ├── static/            # CSS・JavaScript・画像
│   ├── fonts/             # PDF 用フォント（IPAex ゴシック）
│   ├── cli.py             # flask コマンド
│   └── constants.py       # ステータス・メッセージなどの定数
├── migrations/            # DB マイグレーション（Flask-Migrate）
├── scripts/               # seed.py（マスタデータの投入）
├── tests/                 # pytest
└── instance/uploads/      # アップロードされた写真（Git 管理外）
```

## 動作環境

| 項目 | バージョン |
|---|---|
| Python | 3.13（動作確認済み） |
| PostgreSQL | 17 |

重複報告の自動検出で PostgreSQL のアドバイザリロックを使うため、データベースは PostgreSQL が必要です。

## セットアップ

### 1. リポジトリの取得と仮想環境

```bash
git clone <このリポジトリの URL>
cd task-app

python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# macOS / Linux:        source .venv/bin/activate

pip install -r requirements.txt
```

### 2. データベースの作成

PostgreSQL に管理者権限で接続し、アプリ用のロールとデータベースを作成します（名前・パスワードは任意で変更できます）。

```bash
psql -U postgres -h localhost -c "CREATE ROLE saitama_app LOGIN PASSWORD '<パスワード>'"
psql -U postgres -h localhost -c "CREATE DATABASE saitama_help OWNER saitama_app"
```

### 3. 環境変数（.env）

[.env.example](../.env.example) をコピーして `.env` を作成し、値を設定します。`.env` は Git に含めないでください。

```bash
cp .env.example .env
```

| 変数 | 必須 | 説明 |
|---|---|---|
| `SECRET_KEY` | 必須 | セッションの署名に使う、十分に長いランダムな文字列 |
| `DATABASE_URL` | 必須 | 例: `postgresql+psycopg://saitama_app:<パスワード>@localhost:5432/saitama_help` |
| `GEMINI_API_KEY` | 任意 | 設定すると Gemini による AI 判定が有効になる（空ならキーワード判定） |
| `GOOGLE_MAPS_API_KEY` | 任意 | 設定すると Google Maps の地図を表示する（空なら静的な地図画像） |
| `TEST_DATABASE_URL` | テスト時のみ | テスト専用 DB の接続先（[テスト](#テスト)を参照） |
| `SESSION_COOKIE_SECURE` | 任意 | HTTPS で配信する場合は `true`（既定 `false`） |
| `PUBLIC_BASE_URL` | 任意 | メール内のリンクの元になる公開 URL（既定 `http://localhost:5000`） |
| `MAIL_SERVER` ほか `MAIL_*` | 任意 | SMTP 設定。`MAIL_SERVER` が空の場合、メールは送信されず内容がログに出力される |
| `AI_PROVIDER` | 任意 | `gemini`（既定）または `rule_based` |
| `UPLOAD_FOLDER` | 任意 | 写真の保存先（既定 `instance/uploads`） |

そのほかの任意の変数（ログインのロック回数、重複検出の距離など）は `.env.example` のコメントを参照してください。真偽値や整数に誤った値を設定すると、アプリは起動時にエラーになります。

### 4. テーブルの作成と初期データの投入

```bash
flask --app run db upgrade
python -m scripts.seed
```

`scripts.seed` は担当部署とカテゴリのマスタを登録します。何度実行しても重複しません。

開発用に動作確認用アカウント（市民・職員・管理者 各 1 件）が必要な場合だけ、次のように実行します。

```bash
python -m scripts.seed --with-test-users
```

> **注意**: `--with-test-users` は開発環境専用です。サーバーでは実行しないでください。パスワードは [scripts/seed.py](../scripts/seed.py) を参照してください。

### 5. 管理者アカウントの作成

マスタの投入だけでは管理者アカウントは作成されません。最初の管理者は次のコマンドで作成します（パスワードは非表示で入力します）。

```bash
flask --app run create-admin
```

## 起動

```bash
python run.py
```

ブラウザで http://localhost:5000 を開きます。`run.py` はデバッグモードで起動するため、開発用です。

| 利用者 | ログイン URL |
|---|---|
| 市民（住民） | http://localhost:5000/login/citizen |
| 職員 | http://localhost:5000/login/staff |
| 管理者 | http://localhost:5000/login/admin |

住民のアカウント作成は http://localhost:5000/register から行えます。職員・管理者のアカウントは、管理者が `/admin/users/register` で登録するか、[管理コマンド](#管理コマンド)で作成します。

## テスト

テストは専用の PostgreSQL データベースを使い、テストごとにテーブルを作り直します。開発用 DB を指定しないでください（`DATABASE_URL` と同じ値だとテストは中断します）。

1. テスト用 DB を作成します。データベース名は `_test` で終わる必要があります。

   ```bash
   psql -U postgres -h localhost -c "CREATE DATABASE saitama_help_test OWNER saitama_app"
   ```

2. `.env` に `TEST_DATABASE_URL` を設定します。

   ```
   TEST_DATABASE_URL=postgresql+psycopg://saitama_app:<パスワード>@localhost:5432/saitama_help_test
   ```

3. テストを実行します。`pytest` は [requirements.txt](../requirements.txt) に含まれないため、未インストールの場合は先に `pip install pytest` を実行してください。

   ```bash
   python -m pytest
   ```

`TEST_DATABASE_URL` が未設定の場合、DB を使うテストはスキップされます。

## 管理コマンド

`task-app` のルートディレクトリで `flask --app run <コマンド>` として実行します。定義は [app/cli.py](../app/cli.py) にあります。

| コマンド | 内容 |
|---|---|
| `create-admin` | 管理者アカウントを作成する（ID に `@` は使えない） |
| `create-staff` | 職員アカウントを作成する（所属部署が必須） |
| `issue-reset-code <ID>` | 職員・管理者のパスワード再設定コードを発行する（1 回限り・有効期限あり） |
| `set-password <ID>` | 任意のアカウントのパスワードを変更し、ログインロックを解除する |
| `restore-user [ID] [--list]` | 管理者が削除したアカウントを復元する。`--list` で一覧を表示 |
| `remove-test-users [--dry-run] [--yes] [--force]` | 開発用の動作確認アカウントを削除または無効化する |
| `rejudge-urgency` | 未対応のまま一定時間が過ぎた報告の緊急度を AI で再判定する（定期実行向け） |
| `detect-duplicates` | 重複報告をまとめて検出する（手動実行。新しい報告は送信時に自動で検出される） |

パスワードは入力を求められ、8 文字以上が必要です。

## 本番環境

本番環境は AWS EC2 上に、Nginx（リバースプロキシ・HTTPS）と gunicorn で構成しています。`gunicorn` は [requirements.txt](../requirements.txt) に含まれないため、本番環境で別途インストールします。

- デプロイ後は `flask --app run db upgrade` を実行します。
- `SESSION_COOKIE_SECURE=true` を設定します（HTTPS で配信する場合）。
- メール通知を使う場合は `PUBLIC_BASE_URL` と `MAIL_*` を設定します。`MAIL_SERVER` が空のままだと、再設定リンクを含むメール内容がログに出力されます。
- Nginx の背後で動かす場合は `PROXY_FIX_HOPS=1` を設定します。次の条件を満たす場合だけにしてください。
  - Nginx が `Host` と `X-Forwarded-Proto` を gunicorn へ渡している。
  - gunicorn が `127.0.0.1` またはソケットだけで待ち受けている。
- 緊急度の再判定は、`flask --app run rejudge-urgency` を systemd timer で 1 時間ごとに実行します。
- 開発用の動作確認アカウントは、本番環境に作成しないでください。作成済みの場合は `remove-test-users` で削除します。
- 写真は個人情報を含む可能性があるため、公開ディレクトリ（`static/`）ではなく `instance/uploads` に保存します。バックアップ対象に含めるか検討してください。
