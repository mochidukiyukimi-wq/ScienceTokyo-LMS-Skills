# titech_lms_skill_extic

English: [`README.md`](README.md)

このSkillは、Science Tokyo ID、パスワード、OTP認証を使ってExticログインを自動化し、セッションを確立したうえで、ISCT LMS / Moodle API 操作までをPythonから自動化するためのものです。

現行の `https://isct.ex-tic.com/auth/session` から始まるログインフローを、TOTP secret を使ってHTTPで再現します。ログイン後は ISCT LMS に入り、Moodle mobile token を取得して、Moodle REST WebService を呼び出せます。

旧東工大ポータルのマトリクス認証ではなく、現行 Science Tokyo / Extic 向けです。

## できること

- Extic のID・パスワード確認
- Extic へのTOTP付きフルログイン
- ISCT LMS ダッシュボードへの到達確認
- Moodle mobile token / `ws_token` の取得と保存
- Moodle REST API の一部呼び出し
  - サイト情報
  - 履修コース
  - コース内容
  - 課題一覧
  - 通知
  - フォーラム
  - 小テスト一覧
  - ワークショップ一覧

## まだ未実装のこと

このSkillは、Moodle APIの全部を包んでいるわけではありません。

特に、次の操作は現時点では未実装です。

- 課題ファイルのアップロード
- 課題提出の保存・確定
- 小テストの受験開始
- 小テスト回答の保存・提出
- 成績取得
- カレンダー取得
- メッセージ送受信
- ファイル管理全般

内部には `MoodleClient.request(wsfunction, params)` という共通呼び出し口があります。Moodle側のtokenで許可されている関数なら、必要に応じてラッパーを追加できます。

## ファイル構造

```text
titech_lms_skill_extic/
├── SKILL.md
├── README.md
├── README-ja.md
├── requirements.txt
├── .gitignore
├── scripts/
│   ├── __init__.py
│   └── titech_lms.py
└── examples/
    ├── config.example.json
    └── example_usage.py
```

`README.md` は英語版の説明書です。

`README-ja.md` は日本語版の説明書です。インストール、設定、コマンド例、注意点をまとめています。

`SKILL.md` はSkillとして使うときの仕様メモです。ChatGPTや自動化側がこのSkillの用途を理解するための説明に近いです。

`scripts/titech_lms.py` が本体です。Exticログイン、LMS token取得、Moodle APIクライアント、CLI入口がまとまっています。

`examples/config.example.json` は設定ファイルの雛形です。コピーして自分用の `config.json` を作ります。

`examples/example_usage.py` はPythonコードから直接呼ぶ例です。

## インストール

```bash
pip install -r requirements.txt
python scripts/titech_lms.py --help
```

## 設定ファイル

デフォルトでは次の場所を読みます。

```text
~/.titech_lms/config.json
```

別の設定ファイルを使う場合は `--config` で指定します。

```bash
python scripts/titech_lms.py lms-token --config ./config.json
```

雛形から作る場合はこうです。

```bash
cp examples/config.example.json ./config.json
chmod 600 ./config.json
```

## config.json の形式

前のSkillと同じく、トップレベルは `portal` と `moodle` の2セクションです。

```json
{
  "portal": {
    "username": "00B00000",
    "password": "your_science_tokyo_password",
    "totp_secret": "BASE32_TOTP_SECRET_HERE",
    "matrixcode": {
      "a1": "legacy-field-kept-for-compatibility"
    }
  },
  "moodle": {
    "base_url": "https://lms.s.isct.ac.jp/2025/",
    "ws_token": "",
    "user_id": 12345
  }
}
```

`portal.username` は Science Tokyo ID です。

`portal.password` は Science Tokyo / Extic のパスワードです。

`portal.totp_secret` は6桁のワンタイムコードではなく、Authenticatorアプリに登録するbase32形式の秘密鍵です。

`portal.matrixcode` は旧ポータル互換の名残です。現行Exticログインでは使いません。

`moodle.base_url` は通常 `https://lms.s.isct.ac.jp/2025/` です。

`moodle.ws_token` は `lms-token --save` で保存できます。最初は空で大丈夫です。

`moodle.user_id` はコース一覧や通知取得で使います。`site-info` の結果から確認できます。

## 秘密情報を別ファイルに置く場合

`password` や `totp_secret` を `config.json` に直接書かず、別ファイルから読むこともできます。

```json
{
  "portal": {
    "username": "00B00000",
    "password_file": "~/.titech_lms/password.txt",
    "totp_secret_file": "~/.titech_lms/totp_secret.txt",
    "matrixcode": {}
  },
  "moodle": {
    "base_url": "https://lms.s.isct.ac.jp/2025/",
    "ws_token": "",
    "user_id": 12345
  }
}
```

この場合も、秘密情報ファイルには自分以外が読めない権限を付けてください。

```bash
chmod 600 ~/.titech_lms/password.txt
chmod 600 ~/.titech_lms/totp_secret.txt
```

## 基本コマンド

ID・パスワードが通るかだけ確認します。TOTPは送信しません。

```bash
python scripts/titech_lms.py portal-login-check --config ./config.json
```

Exticにフルログインします。

```bash
python scripts/titech_lms.py portal-login --config ./config.json
```

Exticログイン後、LMSダッシュボードまで到達できるか確認します。

```bash
python scripts/titech_lms.py lms-login --config ./config.json
```

Moodle mobile token を取得します。

```bash
python scripts/titech_lms.py lms-token --config ./config.json
```

取得したtokenを `moodle.ws_token` に保存します。

```bash
python scripts/titech_lms.py lms-token --save --config ./config.json
```

## Moodle API コマンド

保存済みの `moodle.ws_token` を使って、Moodle REST APIを呼びます。

```bash
python scripts/titech_lms.py moodle site-info --config ./config.json
python scripts/titech_lms.py moodle courses --config ./config.json
python scripts/titech_lms.py moodle contents --course-id 123 --config ./config.json
python scripts/titech_lms.py moodle assignments --config ./config.json
python scripts/titech_lms.py moodle notifications --config ./config.json
python scripts/titech_lms.py moodle forums --course-id 123 --config ./config.json
python scripts/titech_lms.py moodle quizzes --config ./config.json
python scripts/titech_lms.py moodle workshops --config ./config.json
```

`moodle.ws_token` がまだ空の場合、一時的にExticログインしてtokenを取ることもできます。

```bash
python scripts/titech_lms.py moodle site-info --auto-token --config ./config.json
```

## Pythonコードから使う例

```python
from scripts.titech_lms import (
    load_config,
    ScienceTokyoPortalAccount,
    MoodleCredentials,
    ScienceTokyoPortalClient,
    MoodleClient,
)

config = load_config("./config.json")
account = ScienceTokyoPortalAccount.from_config(config=config)
moodle_config = MoodleCredentials.from_config(config=config)

portal = ScienceTokyoPortalClient(lms_base_url=moodle_config.base_url)
ws_token = portal.login_and_get_lms_token(account)

moodle = MoodleClient(moodle_config.base_url, ws_token)
print(moodle.get_site_info())
```

## Moodle APIを追加したいとき

`scripts/titech_lms.py` の `MoodleClient` にメソッドを追加します。

例として、WebService関数 `some_ws_function` を呼ぶだけならこうです。

```python
def some_feature(self, course_id: int):
    return self.request("some_ws_function", {"courseid": int(course_id)})
```

MoodleのWebServiceは、サイト側で有効化されている関数・tokenの権限・ユーザー権限に依存します。Moodle本体に関数があっても、そのtokenで必ず使えるとは限りません。

## 安全上の注意

`config.json`、パスワードファイル、TOTP secretファイル、`ws_token` は公開しないでください。GitHubなどにpushしないようにしてください。

`.gitignore` には最低限の除外設定を入れています。自分用の設定ファイル名を使う場合は、必要に応じて追加してください。

```gitignore
config.json
*.secret
.titech_lms/
```

課題提出や小テスト提出のような取り返しにくい操作は、現時点では実装していません。今後追加する場合も、下書き保存と提出確定は分け、提出確定には明示的な確認フラグを付けるのが安全です。

## よくあるエラー

`Config file not found` が出る場合は、設定ファイルの場所が違います。`--config ./config.json` を付けるか、`~/.titech_lms/config.json` に置いてください。

`Config must contain portal.totp_secret` が出る場合は、TOTP secretがありません。6桁コードではなく、Authenticator登録用のbase32 secretを設定してください。

`LMS returned a policy page` が出る場合は、ブラウザで一度LMSを開き、サイトポリシーに同意してください。

`No Moodle ws_token found` が出る場合は、先に次を実行してください。

```bash
python scripts/titech_lms.py lms-token --save --config ./config.json
```

## 実装メモ

Exticログインはだいたい次の順番で進みます。

```text
GET /auth/session
↓
GET /auth/session/first_factor
↓
POST /auth/session
↓
GET /auth/session/second_factor
↓
POST /auth/session/second_factor
↓
waiting page
↓
POST /idm/user/login/saml2/sso/user-isct
↓
LMS dashboard
↓
admin/tool/mobile/launch.php
↓
Moodle ws_token
```

このフローはページHTMLやhidden inputの構造に依存します。ExticやLMS側の画面構造が変わると壊れる可能性があります。
