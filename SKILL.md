# Science Tokyo Extic + LMS Skill

このSkillは、Science Tokyo ID、パスワード、OTP認証からExticセッションの確立までを自動化し、そのセッションを使ってISCT LMS / Moodle REST API操作までをPythonから自動化するためのものです。

## 目的

- Exticログイン: `https://isct.ex-tic.com/auth/session`
- Science Tokyo ID + password + TOTP secretによる自動ログイン
- Extic SAML handoff後にLMSダッシュボードへ到達
- Moodle mobile token (`ws_token`) の取得
- Moodle REST WebServiceの主要API呼び出し

## 設定

デフォルト設定ファイル:

```text
~/.titech_lms/config.json
```

別ファイル指定:

```bash
python scripts/titech_lms.py <command> --config ./config.json
```

設定ファイルの基本形は前Skillと同じく、`portal` と `moodle` の2セクションです。

```json
{
  "portal": {
    "username": "00B00000",
    "password": "your_science_tokyo_password",
    "totp_secret": "BASE32_TOTP_SECRET_HERE",
    "matrixcode": {}
  },
  "moodle": {
    "base_url": "https://lms.s.isct.ac.jp/2025/",
    "ws_token": "",
    "user_id": 12345
  }
}
```

`matrixcode` は旧Titech Portal互換のため残してよいですが、このExtic版では使いません。

## 実装仕様

Exticログインは、公開されている `science-tokyo-portal-kit` の流れに合わせています。

1. `GET /auth/session`
2. HTMLから `meta[name=csrf-token]` と `div#identifier-field-wrapper input` を取得
3. usernameを注入して `GET /auth/session/first_factor`
4. `form#login input` にusername/passwordを注入して `POST /auth/session`
5. `GET /auth/session/second_factor`
6. `form#totp-form input` にローカル計算したTOTPを注入して `POST /auth/session/second_factor`
7. 返ってきた `window.location = "..."` を解析して待機ページを取得
8. 待機ページのhidden inputを `POST /idm/user/login/saml2/sso/user-isct`
9. Resource Listに到達したらログイン成功
10. LMSにアクセスし、SAML ACSへhidden inputをPOSTしてダッシュボードへ到達
11. `admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=...&urlscheme=moodlemobile` から `ws_token` を抽出

## 主要コマンド

```bash
python scripts/titech_lms.py portal-login-check --config ./config.json
python scripts/titech_lms.py portal-login --config ./config.json
python scripts/titech_lms.py lms-login --config ./config.json
python scripts/titech_lms.py lms-token --config ./config.json
python scripts/titech_lms.py lms-token --save --config ./config.json
```

Moodle API:

```bash
python scripts/titech_lms.py moodle site-info --config ./config.json
python scripts/titech_lms.py moodle courses --config ./config.json
python scripts/titech_lms.py moodle contents --course-id 123 --config ./config.json
python scripts/titech_lms.py moodle assignments --config ./config.json
python scripts/titech_lms.py moodle notifications --config ./config.json
python scripts/titech_lms.py moodle forums --course-id 123 --config ./config.json
```

`moodle.ws_token` が空の場合:

```bash
python scripts/titech_lms.py moodle site-info --auto-token --config ./config.json
```

## Python API例

```python
from scripts.titech_lms import (
    ScienceTokyoPortalAccount,
    ScienceTokyoPortalClient,
    MoodleClient,
    MoodleCredentials,
    load_config,
)

config = load_config("./config.json")
account = ScienceTokyoPortalAccount.from_config(config=config)
moodle_config = MoodleCredentials.from_config(config=config)

portal = ScienceTokyoPortalClient(lms_base_url=moodle_config.base_url)
portal.login(account)
portal.get_lms_dashboard()
ws_token = portal.get_lms_token()

moodle = MoodleClient(moodle_config.base_url, ws_token)
print(moodle.get_site_info())
```

## セキュリティ注意

`portal.totp_secret` はワンタイムコードではなく、コードを生成するための秘密鍵です。パスワードと同等に扱い、Gitや共有フォルダに置かないでください。
