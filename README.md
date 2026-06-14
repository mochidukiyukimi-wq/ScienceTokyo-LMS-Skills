# titech_lms_skill_extic

日本語版: [`README-ja.md`](README-ja.md)

This Skill automates Extic login using a Science Tokyo ID, password, and OTP authentication, establishes a session, and then automates ISCT LMS / Moodle API operations from Python.

It reproduces the current login flow that starts from `https://isct.ex-tic.com/auth/session` over HTTP using a TOTP secret. After login, it enters ISCT LMS, retrieves a Moodle mobile token, and calls Moodle REST WebService APIs.

This is for the current Science Tokyo / Extic authentication flow, not the legacy Tokyo Tech portal matrix-code authentication flow.

## What it can do

- Check whether an Extic ID and password are accepted
- Perform a full Extic login with TOTP
- Verify that the ISCT LMS dashboard is reachable
- Retrieve and save a Moodle mobile token / `ws_token`
- Call part of the Moodle REST API
  - Site information
  - Enrolled courses
  - Course contents
  - Assignment list
  - Notifications
  - Forums
  - Quiz list
  - Workshop list

## Not implemented yet

This Skill does not wrap the entire Moodle API.

The following operations are not implemented at the moment:

- Assignment file upload
- Saving or confirming assignment submissions
- Starting quiz attempts
- Saving or submitting quiz answers
- Grade retrieval
- Calendar retrieval
- Sending or receiving messages
- General file management

Internally, there is a common entry point: `MoodleClient.request(wsfunction, params)`. If a Moodle function is allowed by the token on the LMS side, you can add a wrapper for it as needed.

## File structure

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

`README.md` is the English guide.

`README-ja.md` is the Japanese guide. It covers installation, configuration, command examples, and cautions.

`SKILL.md` is the Skill specification note. It explains the purpose and usage of this Skill for ChatGPT or automation systems.

`scripts/titech_lms.py` is the main implementation. It contains the Extic login flow, LMS token retrieval, Moodle API client, and CLI entry point.

`examples/config.example.json` is a configuration template. Copy it to create your own `config.json`.

`examples/example_usage.py` shows how to call the Skill directly from Python code.

## Installation

```bash
pip install -r requirements.txt
python scripts/titech_lms.py --help
```

## Configuration file

By default, the Skill reads this file:

```text
~/.titech_lms/config.json
```

To use another configuration file, pass it with `--config`.

```bash
python scripts/titech_lms.py lms-token --config ./config.json
```

To start from the template:

```bash
cp examples/config.example.json ./config.json
chmod 600 ./config.json
```

## config.json format

As in the previous Skill version, the top level has two sections: `portal` and `moodle`.

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

`portal.username` is your Science Tokyo ID.

`portal.password` is your Science Tokyo / Extic password.

`portal.totp_secret` is not the six-digit one-time code. It is the base32 secret registered in an authenticator app.

`portal.matrixcode` is kept only for legacy compatibility. It is not used for the current Extic login flow.

`moodle.base_url` is normally `https://lms.s.isct.ac.jp/2025/`.

`moodle.ws_token` can be saved by running `lms-token --save`. It can be empty at first.

`moodle.user_id` is used for course lists and notification retrieval. You can confirm it from the result of `site-info`.

## Keeping secrets in separate files

Instead of writing `password` and `totp_secret` directly in `config.json`, you can read them from separate files.

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

Make sure only you can read the secret files.

```bash
chmod 600 ~/.titech_lms/password.txt
chmod 600 ~/.titech_lms/totp_secret.txt
```

## Basic commands

Check whether the ID and password are accepted. This does not submit TOTP.

```bash
python scripts/titech_lms.py portal-login-check --config ./config.json
```

Perform a full Extic login.

```bash
python scripts/titech_lms.py portal-login --config ./config.json
```

After Extic login, check whether the LMS dashboard is reachable.

```bash
python scripts/titech_lms.py lms-login --config ./config.json
```

Retrieve a Moodle mobile token.

```bash
python scripts/titech_lms.py lms-token --config ./config.json
```

Save the retrieved token to `moodle.ws_token`.

```bash
python scripts/titech_lms.py lms-token --save --config ./config.json
```

## Moodle API commands

These commands use the saved `moodle.ws_token` to call the Moodle REST API.

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

If `moodle.ws_token` is still empty, you can temporarily log in through Extic and retrieve a token.

```bash
python scripts/titech_lms.py moodle site-info --auto-token --config ./config.json
```

## Python usage

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

## Adding Moodle API wrappers

Add methods to `MoodleClient` in `scripts/titech_lms.py`.

For example, to call a WebService function named `some_ws_function`:

```python
def some_feature(self, course_id: int):
    return self.request("some_ws_function", {"courseid": int(course_id)})
```

Moodle WebService availability depends on which functions are enabled on the site, the token permissions, and the user permissions. Even if a function exists in Moodle itself, it is not always available with a given token.

## Safety notes

Do not publish `config.json`, password files, TOTP secret files, or `ws_token`. Do not push them to GitHub.

The `.gitignore` file includes minimal exclusions. If you use your own local configuration filename, add it as needed.

```gitignore
config.json
*.secret
.titech_lms/
```

Operations that are hard to undo, such as assignment submission and quiz submission, are not implemented at the moment. If they are added later, draft saving and final submission should be separated, and final submission should require an explicit confirmation flag.

## Common errors

If `Config file not found` appears, the configuration file path is wrong. Pass `--config ./config.json` or place the file at `~/.titech_lms/config.json`.

If `Config must contain portal.totp_secret` appears, the TOTP secret is missing. Set the base32 secret used by the authenticator app, not a six-digit code.

If `LMS returned a policy page` appears, open the LMS once in a browser and accept the site policy.

If `No Moodle ws_token found` appears, run this first:

```bash
python scripts/titech_lms.py lms-token --save --config ./config.json
```

## Implementation notes

The Extic login flow roughly proceeds as follows:

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

This flow depends on page HTML and hidden input structures. It may break if Extic or LMS changes its page structure.
