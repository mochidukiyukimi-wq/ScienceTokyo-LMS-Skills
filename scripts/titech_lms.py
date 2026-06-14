#!/usr/bin/env python3
"""
Science Tokyo Extic + LMS helper Skill.

Python port inspired by:
- TitechAppProject/science-tokyo-portal-kit: Science Tokyo Extic login flow
- TitechAppProject/moodle-core-swift: Moodle REST WebService wrapper

Credentials are loaded from a local JSON config file. Do not commit that file.
"""

from __future__ import annotations

import argparse
import base64
import copy
import getpass
import hashlib
import hmac
import json
import random
import re
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup, Tag


DEFAULT_EXTIC_ORIGIN = "https://isct.ex-tic.com"
DEFAULT_LMS_BASE_URL = "https://lms.s.isct.ac.jp/2025/"
DEFAULT_CONFIG_PATH = Path.home() / ".titech_lms" / "config.json"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 "
    "Mobile/15E148 Safari/604.1"
)


class PortalLoginError(RuntimeError):
    """Raised when the Extic / Science Tokyo portal login flow fails."""


class LMSLoginError(RuntimeError):
    """Raised when the LMS SAML handoff or Moodle mobile token flow fails."""


class MoodleAPIError(RuntimeError):
    """Raised when Moodle returns a standard exception JSON or an invalid response."""

    def __init__(self, message: str, *, payload: Any | None = None) -> None:
        super().__init__(message)
        self.payload = payload


@dataclass(frozen=True)
class HTMLInput:
    name: str
    type: str
    value: str


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    """Load the local JSON config file.

    Default path: ~/.titech_lms/config.json
    """
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    try:
        raw = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"Config file not found: {config_path}. "
            "Create it from examples/config.example.json or pass --config path/to/config.json."
        ) from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Config file is not valid JSON: {config_path}") from exc

    if not isinstance(parsed, dict):
        raise RuntimeError(f"Config root must be a JSON object: {config_path}")
    return parsed


def write_config(config: Mapping[str, Any], path: str | Path | None = None) -> None:
    config_path = Path(path).expanduser() if path else DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_secret(
    section: Mapping[str, Any],
    *,
    key: str,
    file_key: str,
    prompt_key: str,
    prompt: str,
    hidden: bool = True,
    required: bool = True,
) -> Optional[str]:
    """Read a secret from inline config, a file, or an interactive prompt.

    Precedence:
      1. section[key]
      2. section[file_key]
      3. prompt if section[prompt_key] is true, or if required and no value was found
    """
    value = section.get(key)
    if value not in (None, ""):
        return str(value).strip()

    file_value = section.get(file_key)
    if file_value not in (None, ""):
        secret_path = Path(str(file_value)).expanduser()
        try:
            return secret_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise PortalLoginError(f"Secret file not found: {secret_path}") from exc

    should_prompt = bool(section.get(prompt_key, required))
    if should_prompt:
        if hidden:
            return getpass.getpass(prompt).strip()
        return input(prompt).strip()

    if required:
        raise PortalLoginError(f"Config must contain portal.{key} or portal.{file_key}.")
    return None


@dataclass(frozen=True)
class ScienceTokyoPortalAccount:
    """Science Tokyo Extic account.

    The config layout intentionally keeps the old Skill's top-level structure:

    {
      "portal": {
        "username": "00B00000",
        "password": "...",
        "totp_secret": "BASE32...",
        "matrixcode": {"a1": "..."}  // ignored by Extic; kept for compatibility
      },
      "moodle": {
        "base_url": "https://lms.s.isct.ac.jp/2025/",
        "ws_token": "...",
        "user_id": 12345
      }
    }
    """

    username: str
    password: str
    totp_secret: str

    @staticmethod
    def from_config(path: str | Path | None = None, *, config: Optional[Mapping[str, Any]] = None) -> "ScienceTokyoPortalAccount":
        root = dict(config or load_config(path))
        portal = root.get("portal", root)
        if not isinstance(portal, Mapping):
            raise PortalLoginError("Config field 'portal' must be an object.")

        username = portal.get("username")
        if not username:
            raise PortalLoginError("Config must contain portal.username.")

        password = _read_secret(
            portal,
            key="password",
            file_key="password_file",
            prompt_key="password_prompt",
            prompt="Science Tokyo password: ",
            hidden=True,
            required=True,
        )
        assert password is not None

        # Accept a few names so the config can evolve without breaking old files.
        totp_secret = _read_secret(
            portal,
            key="totp_secret",
            file_key="totp_secret_file",
            prompt_key="totp_secret_prompt",
            prompt="TOTP secret (base32): ",
            hidden=True,
            required=False,
        )
        if not totp_secret:
            # Also accept portal.second_factor.totp_secret, but keep portal.* as the primary spec.
            second_factor = portal.get("second_factor") or {}
            if isinstance(second_factor, Mapping):
                totp_secret = _read_secret(
                    second_factor,
                    key="totp_secret",
                    file_key="totp_secret_file",
                    prompt_key="totp_secret_prompt",
                    prompt="TOTP secret (base32): ",
                    hidden=True,
                    required=False,
                )
        if not totp_secret:
            raise PortalLoginError(
                "Config must contain portal.totp_secret or portal.totp_secret_file. "
                "Extic automation requires the TOTP seed, not a one-time 6-digit code."
            )

        return ScienceTokyoPortalAccount(
            username=str(username).strip(),
            password=password,
            totp_secret=totp_secret,
        )


@dataclass(frozen=True)
class MoodleCredentials:
    base_url: str
    ws_token: Optional[str] = None
    user_id: Optional[int] = None

    @staticmethod
    def from_config(path: str | Path | None = None, *, config: Optional[Mapping[str, Any]] = None) -> "MoodleCredentials":
        root = dict(config or load_config(path))
        moodle = root.get("moodle", {})
        if not isinstance(moodle, Mapping):
            raise MoodleAPIError("Config field 'moodle' must be an object.")

        base_url = str(moodle.get("base_url") or DEFAULT_LMS_BASE_URL).strip()
        token = moodle.get("ws_token") or moodle.get("token")
        user_id_raw = moodle.get("user_id")
        user_id = int(user_id_raw) if user_id_raw not in (None, "") else None
        return MoodleCredentials(base_url=base_url, ws_token=str(token).strip() if token else None, user_id=user_id)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", "html.parser")


def _text_contains(html: str, *needles: str) -> bool:
    body = _soup(html).body
    text = body.get_text(" ") if body else html
    return any(needle in text for needle in needles)


def _select_fragment(html: str, selector: str) -> str:
    doc = _soup(html)
    node = doc.select_one(selector)
    if node is None:
        return ""
    return str(node)


def _parse_inputs(html_or_node: str | Tag) -> List[HTMLInput]:
    if isinstance(html_or_node, Tag):
        nodes = html_or_node.select("input")
    else:
        nodes = _soup(html_or_node).select("input")
    return [
        HTMLInput(
            name=node.get("name", ""),
            type=(node.get("type", "text") or "text").lower(),
            value=node.get("value", ""),
        )
        for node in nodes
    ]


def _parse_metas(html: str) -> Dict[str, str]:
    metas: Dict[str, str] = {}
    for meta in _soup(html).select("meta"):
        name = meta.get("name") or meta.get("http-equiv") or ("charset" if meta.get("charset") else "")
        content = meta.get("content") or meta.get("charset") or ""
        if name:
            metas[name] = content
    return metas


def _csrf_headers(html: str) -> Dict[str, str]:
    token = _parse_metas(html).get("csrf-token")
    return {"X-CSRF-Token": token} if token else {}


def _inject_username_password(inputs: Sequence[HTMLInput], *, username: str, password: str) -> List[HTMLInput]:
    copied = list(copy.deepcopy(inputs))
    text_types = {"", "text", "email", "search", "tel", "url"}

    for index, item in enumerate(copied):
        if item.type in text_types:
            copied[index] = HTMLInput(name=item.name, type=item.type, value=username)
            break

    for index, item in enumerate(copied):
        if item.type == "password":
            copied[index] = HTMLInput(name=item.name, type=item.type, value=password)
            break

    return copied


def _inject_first_text(inputs: Sequence[HTMLInput], value: str) -> List[HTMLInput]:
    copied = list(copy.deepcopy(inputs))
    text_types = {"", "text", "email", "search", "tel", "url", "number"}
    for index, item in enumerate(copied):
        if item.type in text_types:
            copied[index] = HTMLInput(name=item.name, type=item.type, value=value)
            return copied
    # Some OTP forms use password-like input masking.
    for index, item in enumerate(copied):
        if item.type == "password":
            copied[index] = HTMLInput(name=item.name, type=item.type, value=value)
            return copied
    raise PortalLoginError("Could not find a text/password input to inject the value.")


def _form_pairs(inputs: Sequence[HTMLInput]) -> List[Tuple[str, str]]:
    # Keep pairs instead of a dict so duplicate field names are preserved.
    return [(item.name, item.value) for item in inputs if item.name != ""]


def _calculate_totp(secret: str, *, current: Optional[float] = None, digits: int = 6, period: int = 30) -> str:
    """RFC 6238 compatible TOTP using HMAC-SHA1.

    Extic's Swift kit calculates a 6-digit, 30-second, HMAC-SHA1 TOTP locally.
    """
    cleaned = re.sub(r"\s+", "", secret).upper()
    padding = "=" * ((8 - len(cleaned) % 8) % 8)
    try:
        key = base64.b32decode(cleaned + padding, casefold=True)
    except Exception as exc:  # noqa: BLE001 - keep config errors clear.
        raise PortalLoginError("portal.totp_secret is not valid base32.") from exc

    timestamp = int(current if current is not None else time.time())
    counter = timestamp // period
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10 ** digits)).zfill(digits)


def _parse_window_location(script: str) -> str:
    match = re.search(r"window\.location\s*=\s*[\"']([^\"']+)[\"']", script or "")
    if not match:
        raise PortalLoginError("Could not parse window.location from Extic response script.")
    location = match.group(1)
    # Handle common JS escaping in a conservative way.
    try:
        location = json.loads(json.dumps(location))
    except Exception:
        pass
    return location


def _decode_moodlemobile_token(href: str) -> str:
    href = unquote(href or "")
    prefix = "moodlemobile://token="
    if prefix not in href:
        raise LMSLoginError("Moodle mobile launch link does not contain a moodlemobile token.")
    encoded = href.split(prefix, 1)[1]
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise LMSLoginError("Could not base64-decode Moodle mobile token payload.") from exc
    parts = decoded.split(":::")
    if len(parts) <= 1 or not parts[1]:
        raise LMSLoginError("Could not parse Moodle wsToken from decoded mobile token payload.")
    return parts[1]


class ScienceTokyoPortalClient:
    """HTTP implementation of the Science Tokyo Extic login flow.

    This mirrors science-tokyo-portal-kit's public flow:
      - GET /auth/session
      - GET /auth/session/first_factor with username fields and X-CSRF-Token
      - POST /auth/session with username/password fields
      - GET /auth/session/second_factor
      - POST /auth/session/second_factor with locally calculated TOTP
      - GET waiting page from returned window.location
      - POST hidden SAML fields to /idm/user/login/saml2/sso/user-isct
      - access LMS and Moodle mobile launch token
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        *,
        extic_origin: str = DEFAULT_EXTIC_ORIGIN,
        lms_base_url: str = DEFAULT_LMS_BASE_URL,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 30.0,
    ) -> None:
        self.session = session or requests.Session()
        self.extic_origin = extic_origin.rstrip("/")
        self.extic_host = self.extic_origin.removeprefix("https://").removeprefix("http://")
        self.lms_base_url = lms_base_url.rstrip("/") + "/"
        self.lms_host = self.lms_base_url.split("//", 1)[-1].split("/", 1)[0]
        self.user_agent = user_agent
        self.timeout = timeout
        self.session.headers.update({"User-Agent": self.user_agent})

    def _extic_headers(self, *, referer: Optional[str] = None, ajax: bool = False, accept: Optional[str] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Connection": "keep-alive",
            "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja",
        }
        if referer:
            headers["Referer"] = referer
        if ajax:
            headers.update(
                {
                    "Host": self.extic_host,
                    "Origin": self.extic_origin,
                    "X-Requested-With": "XMLHttpRequest",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                }
            )
        return headers

    @property
    def auth_session_url(self) -> str:
        return f"{self.extic_origin}/auth/session"

    @property
    def first_factor_url(self) -> str:
        return f"{self.extic_origin}/auth/session/first_factor"

    @property
    def second_factor_url(self) -> str:
        return f"{self.extic_origin}/auth/session/second_factor"

    @property
    def resource_list_url(self) -> str:
        return f"{self.extic_origin}/idm/user/login/saml2/sso/user-isct"

    def fetch_username_page(self) -> str:
        response = self.session.get(self.auth_session_url, headers=self._extic_headers(), timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def submit_username(self, username_page_html: str, username: str) -> str:
        fragment = _select_fragment(username_page_html, "div#identifier-field-wrapper")
        if not fragment:
            raise PortalLoginError("Could not find username input wrapper: div#identifier-field-wrapper")
        inputs = _inject_username_password(_parse_inputs(fragment), username=username, password="")
        headers = self._extic_headers(
            referer=self.auth_session_url,
            ajax=True,
            accept="application/json, text/javascript, */*; q=0.01",
        )
        headers.update(_csrf_headers(username_page_html))
        response = self.session.get(
            self.first_factor_url,
            params=_form_pairs(inputs),
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.text

    def submit_password(self, username_page_html: str, username: str, password: str) -> str:
        fragment = _select_fragment(username_page_html, "form#login")
        if not fragment:
            raise PortalLoginError("Could not find password form: form#login")
        inputs = _inject_username_password(_parse_inputs(fragment), username=username, password=password)
        headers = self._extic_headers(
            referer=self.auth_session_url,
            ajax=True,
            accept="*/*;q=0.5, text/javascript, application/javascript, application/ecmascript, application/x-ecmascript",
        )
        headers.update(_csrf_headers(username_page_html))
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        response = self.session.post(
            self.auth_session_url,
            data=_form_pairs(inputs),
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.text

    def fetch_second_factor_page(self) -> str:
        response = self.session.get(
            self.second_factor_url,
            headers=self._extic_headers(referer=self.auth_session_url),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.text

    def submit_totp(self, second_factor_html: str, totp: str) -> str:
        fragment = _select_fragment(second_factor_html, "form#totp-form")
        if not fragment:
            raise PortalLoginError("Could not find TOTP form: form#totp-form")
        inputs = _inject_first_text(_parse_inputs(fragment), totp)
        headers = self._extic_headers(
            referer=self.second_factor_url,
            ajax=True,
            accept="*/*;q=0.5, text/javascript, application/javascript, application/ecmascript, application/x-ecmascript",
        )
        headers.update(_csrf_headers(second_factor_html))
        headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        response = self.session.post(
            self.second_factor_url,
            data=_form_pairs(inputs),
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.text

    def fetch_waiting_page(self, location: str) -> Tuple[str, str]:
        url = urljoin(self.extic_origin + "/", location)
        response = self.session.get(
            url,
            headers=self._extic_headers(referer=self.second_factor_url),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.text, response.url

    def fetch_resource_list_page(self, waiting_page_html: str, *, referer: str) -> str:
        inputs = _parse_inputs(waiting_page_html)
        headers = self._extic_headers(referer=referer)
        headers.update(
            {
                "Host": self.extic_host,
                "Origin": self.extic_origin,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            }
        )
        response = self.session.post(
            self.resource_list_url,
            data=_form_pairs(inputs),
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.text

    @staticmethod
    def validate_username_page(html: str) -> bool:
        return _text_contains(
            html,
            "Enter Science Tokyo ID (8 alphanumerics).",
            "Science Tokyo ID(英数字８文字)を入力してください。",
        )

    @staticmethod
    def validate_username_submit_json(json_text: str, username: str) -> bool:
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError:
            return False
        return bool(payload.get("password") is True and payload.get("identifier") == username)

    @staticmethod
    def validate_submit_script(script: str) -> bool:
        return bool(re.search(r"window\.location\s*=\s*[\"']([^\"']+)[\"']", script or ""))

    @staticmethod
    def validate_second_factor_page(html: str) -> bool:
        return _text_contains(html, "Please select an authentication method.", "認証方法を選択してください。")

    @staticmethod
    def validate_waiting_page(html: str) -> bool:
        return _text_contains(html, "Please wait for a moment", "しばらくお待ちください。")

    @staticmethod
    def validate_resource_list_page(html: str) -> bool:
        return _text_contains(html, "Account", "アカウント")

    @staticmethod
    def detect_policy_error(html: str) -> bool:
        doc = _soup(html)
        title = doc.title.get_text(" ") if doc.title else ""
        return "ポリシー" in title or "Policies" in title

    @staticmethod
    def validate_lms_dashboard(html: str) -> bool:
        return _text_contains(html, "ダッシュボード", "Dashboard")

    def check_username_password(self, username: str, password: str) -> bool:
        username_html = self.fetch_username_page()
        if self.validate_resource_list_page(username_html):
            raise PortalLoginError("Already logged in.")
        if not self.validate_username_page(username_html):
            raise PortalLoginError("Invalid username page HTML.")
        username_json = self.submit_username(username_html, username)
        if not self.validate_username_submit_json(username_json, username):
            raise PortalLoginError("Extic first-factor response was not the expected username JSON.")
        password_script = self.submit_password(username_html, username, password)
        return self.validate_submit_script(password_script)

    def login(self, account: ScienceTokyoPortalAccount) -> None:
        username_html = self.fetch_username_page()
        if self.validate_resource_list_page(username_html):
            raise PortalLoginError("Already logged in.")
        if not self.validate_username_page(username_html):
            raise PortalLoginError("Invalid username page HTML.")

        username_json = self.submit_username(username_html, account.username)
        if not self.validate_username_submit_json(username_json, account.username):
            raise PortalLoginError("Extic first-factor response was not the expected username JSON.")

        password_script = self.submit_password(username_html, account.username, account.password)
        if not self.validate_submit_script(password_script):
            raise PortalLoginError("Password submission did not return a window.location script.")

        second_factor_html = self.fetch_second_factor_page()
        if not self.validate_second_factor_page(second_factor_html):
            raise PortalLoginError("Invalid second-factor selection page HTML.")

        totp = _calculate_totp(account.totp_secret)
        otp_script = self.submit_totp(second_factor_html, totp)
        if not self.validate_submit_script(otp_script):
            raise PortalLoginError("TOTP submission did not return a window.location script.")

        waiting_location = _parse_window_location(otp_script)
        waiting_html, waiting_url = self.fetch_waiting_page(waiting_location)
        if not self.validate_waiting_page(waiting_html):
            raise PortalLoginError("Invalid waiting page HTML.")

        resource_html = self.fetch_resource_list_page(waiting_html, referer=waiting_url)
        if not self.validate_resource_list_page(resource_html):
            raise PortalLoginError("Extic did not reach the resource list page after SAML SSO POST.")

    def get_lms_dashboard(self) -> str:
        response = self.session.get(
            self.lms_base_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja-jp",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        first_html = response.text

        if self.validate_lms_dashboard(first_html):
            return first_html

        if not any(cookie.name == "MoodleSession" for cookie in self.session.cookies):
            raise LMSLoginError("LMS did not set a MoodleSession cookie.")

        inputs = _parse_inputs(first_html)
        if not inputs:
            raise LMSLoginError("Could not parse LMS SAML redirect inputs.")

        acs_url = urljoin(self.lms_base_url, "auth/saml2/sp/saml2-acs.php/lms.isct.ac.jp")
        response = self.session.post(
            acs_url,
            data=_form_pairs(inputs),
            headers={
                "Referer": self.extic_origin,
                "Host": self.lms_host,
                "Origin": self.extic_origin,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        html = response.text
        if self.detect_policy_error(html):
            raise LMSLoginError("LMS returned a policy page. Open the LMS in a browser and accept the policy first.")
        if not self.validate_lms_dashboard(html):
            raise LMSLoginError("LMS did not reach the dashboard page.")
        return html

    def get_lms_token(self) -> str:
        query = {
            "service": "moodle_mobile_app",
            "passport": str(random.uniform(0, 1000)),
            "urlscheme": "moodlemobile",
        }
        url = urljoin(self.lms_base_url, "admin/tool/mobile/launch.php")
        response = self.session.get(url, params=query, headers={"User-Agent": self.user_agent}, timeout=self.timeout)
        response.raise_for_status()
        doc = _soup(response.text)
        launch = doc.select_one("a#launchapp")
        href = launch.get("href") if launch else None
        if not href:
            raise LMSLoginError("Could not find a#launchapp in Moodle mobile launch page.")
        return _decode_moodlemobile_token(href)

    def login_and_get_lms_token(self, account: ScienceTokyoPortalAccount) -> str:
        self.login(account)
        self.get_lms_dashboard()
        return self.get_lms_token()


class MoodleClient:
    """Small Moodle REST client matching moodle-core-swift's public API surface."""

    def __init__(
        self,
        base_url: str,
        ws_token: str,
        session: Optional[requests.Session] = None,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.ws_token = ws_token
        self.session = session or requests.Session()
        self.user_agent = user_agent

    @property
    def endpoint(self) -> str:
        return urljoin(self.base_url, "webservice/rest/server.php")

    def request(
        self,
        wsfunction: str,
        params: Optional[Mapping[str, Any]] = None,
        *,
        method: str = "GET",
        include_token_in_body: bool = False,
    ) -> Any:
        query: Dict[str, Any] = {
            "moodlewsrestformat": "json",
            "wstoken": self.ws_token,
            "wsfunction": wsfunction,
        }
        if params:
            query.update(params)

        headers = {"User-Agent": self.user_agent}
        if method.upper() == "GET":
            response = self.session.get(self.endpoint, params=query, headers=headers)
        elif method.upper() == "POST":
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            if include_token_in_body:
                response = self.session.post(self.endpoint, data=query, headers=headers)
            else:
                url_params = {"moodlewsrestformat": "json", "wsfunction": wsfunction}
                response = self.session.post(self.endpoint, params=url_params, data=query, headers=headers)
        else:
            raise ValueError(f"Unsupported method: {method}")

        if not (200 <= response.status_code < 300):
            raise MoodleAPIError(f"Moodle returned HTTP {response.status_code}", payload=response.text)

        text = response.text or ""
        if "サイトポリシー" in text or "Policies" in text:
            raise MoodleAPIError("Moodle site policy page was returned instead of JSON.", payload=text)

        try:
            payload = response.json()
        except ValueError as exc:
            raise MoodleAPIError("Moodle response was not JSON.", payload=text) from exc

        if isinstance(payload, dict) and {"errorcode", "exception", "message"}.issubset(payload.keys()):
            raise MoodleAPIError(payload.get("message") or payload.get("errorcode") or "Moodle API error", payload=payload)
        return payload

    def get_site_info(self) -> Dict[str, Any]:
        return self.request("core_webservice_get_site_info")

    def get_users_by_field(self, user_ids: Iterable[int]) -> List[Dict[str, Any]]:
        params = {"field": "id"}
        for index, user_id in enumerate(user_ids):
            params[f"values[{index}]"] = int(user_id)
        return self.request("core_user_get_users_by_field", params)

    def get_user_courses(self, user_id: int) -> List[Dict[str, Any]]:
        return self.request("core_enrol_get_users_courses", {"userid": int(user_id)})

    def get_course_categories(self) -> List[Dict[str, Any]]:
        return self.request("core_course_get_categories")

    def get_course_contents(self, course_id: int) -> List[Dict[str, Any]]:
        return self.request("core_course_get_contents", {"courseid": int(course_id)})

    def get_assignments(self) -> Dict[str, Any]:
        return self.request("mod_assign_get_assignments")

    def get_assignment_submission_status(self, assignment_id: int, user_id: int) -> Dict[str, Any]:
        return self.request("mod_assign_get_submission_status", {"assignid": int(assignment_id), "userid": int(user_id)})

    def update_activity_completion_status_manually(self, module_id: int, completed: bool) -> Dict[str, Any]:
        return self.request(
            "core_completion_update_activity_completion_status_manually",
            {"cmid": int(module_id), "completed": 1 if completed else 0},
        )

    def get_popup_notification(self, user_id: int) -> Dict[str, Any]:
        return self.request("message_popup_get_popup_notifications", {"useridto": int(user_id)})

    def mark_notification_read(self, notification_id: int) -> Dict[str, Any]:
        return self.request("core_message_mark_notification_read", {"notificationid": int(notification_id)})

    def get_assignment_submission_comments(self, instance_id: int, item_id: int) -> Dict[str, Any]:
        return self.request(
            "core_comment_get_comments",
            {
                "contextlevel": "module",
                "instanceid": int(instance_id),
                "component": "assignsubmission_comments",
                "itemid": int(item_id),
                "area": "submission_comments",
            },
        )

    def add_comments(self, instance_id: int, item_id: int, comment: str) -> List[Dict[str, Any]]:
        return self.request(
            "core_comment_add_comments",
            {
                "comments[0][contextlevel]": "module",
                "comments[0][instanceid]": int(instance_id),
                "comments[0][component]": "assignsubmission_comments",
                "comments[0][itemid]": int(item_id),
                "comments[0][area]": "submission_comments",
                "comments[0][content]": comment,
            },
            method="POST",
            include_token_in_body=False,
        )

    def delete_comments(self, comment_id: int) -> Optional[List[Dict[str, Any]]]:
        return self.request("core_comment_delete_comments", {"comments[0]": int(comment_id)}, method="POST")

    def get_quizzes(self) -> Dict[str, Any]:
        return self.request("mod_quiz_get_quizzes_by_courses")

    def get_workshops(self) -> Dict[str, Any]:
        return self.request("mod_workshop_get_workshops_by_courses")

    def get_forum_discussions(self, forum_id: int) -> Dict[str, Any]:
        return self.request("mod_forum_get_forum_discussions", {"forumid": int(forum_id)})

    def get_forum_by_course(self, course_id: Optional[int] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {}
        if course_id is not None:
            params["courseids[0]"] = int(course_id)
        return self.request("mod_forum_get_forums_by_courses", params)


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _get_ws_token_for_cli(args: argparse.Namespace, config: Mapping[str, Any]) -> str:
    explicit = getattr(args, "token", None)
    if explicit:
        return explicit
    moodle = MoodleCredentials.from_config(config=config)
    if moodle.ws_token:
        return moodle.ws_token
    if getattr(args, "auto_token", False):
        account = ScienceTokyoPortalAccount.from_config(config=config)
        portal = ScienceTokyoPortalClient(lms_base_url=moodle.base_url)
        return portal.login_and_get_lms_token(account)
    raise MoodleAPIError("No Moodle ws_token found. Run `lms-token --save` first, or pass --auto-token.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Science Tokyo Extic and Moodle helper Skill")
    config_parent = argparse.ArgumentParser(add_help=False)
    config_parent.add_argument("--config", default=None, help=f"Credential config JSON path. Default: {DEFAULT_CONFIG_PATH}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("portal-login-check", parents=[config_parent], help="Check username/password validity; does not submit TOTP")
    sub.add_parser("portal-login", parents=[config_parent], help="Perform full Extic login using TOTP secret from the config file")
    sub.add_parser("lms-login", parents=[config_parent], help="Perform Extic login and reach the LMS dashboard")

    token_parser = sub.add_parser("lms-token", parents=[config_parent], help="Perform Extic/LMS login and print Moodle mobile wsToken")
    token_parser.add_argument("--save", action="store_true", help="Write the retrieved token to moodle.ws_token in the config file")

    moodle = sub.add_parser("moodle", parents=[config_parent], help="Call Moodle REST WebService")
    moodle.add_argument(
        "action",
        choices=["site-info", "courses", "contents", "assignments", "notifications", "forums", "quizzes", "workshops"],
    )
    moodle.add_argument("--base-url", help="Override Moodle base URL from config")
    moodle.add_argument("--token", help="Override Moodle WebService token from config")
    moodle.add_argument("--auto-token", action="store_true", help="Login through Extic and fetch a temporary Moodle token if ws_token is missing")
    moodle.add_argument("--user-id", type=int, help="Override Moodle user ID from config")
    moodle.add_argument("--course-id", type=int)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "portal-login-check":
        account = ScienceTokyoPortalAccount.from_config(config=config)
        portal = ScienceTokyoPortalClient()
        _json_print({"ok": portal.check_username_password(account.username, account.password)})
        return 0

    if args.command == "portal-login":
        account = ScienceTokyoPortalAccount.from_config(config=config)
        portal = ScienceTokyoPortalClient()
        portal.login(account)
        _json_print({"logged_in": True})
        return 0

    if args.command == "lms-login":
        account = ScienceTokyoPortalAccount.from_config(config=config)
        moodle_config = MoodleCredentials.from_config(config=config)
        portal = ScienceTokyoPortalClient(lms_base_url=moodle_config.base_url)
        portal.login(account)
        portal.get_lms_dashboard()
        _json_print({"lms_dashboard": True})
        return 0

    if args.command == "lms-token":
        account = ScienceTokyoPortalAccount.from_config(config=config)
        moodle_config = MoodleCredentials.from_config(config=config)
        portal = ScienceTokyoPortalClient(lms_base_url=moodle_config.base_url)
        token = portal.login_and_get_lms_token(account)
        if args.save:
            writable = copy.deepcopy(config)
            writable.setdefault("moodle", {})
            if not isinstance(writable["moodle"], dict):
                raise MoodleAPIError("Config field 'moodle' must be an object to save ws_token.")
            writable["moodle"]["ws_token"] = token
            write_config(writable, args.config)
        _json_print({"ws_token": token, "saved": bool(args.save)})
        return 0

    if args.command == "moodle":
        moodle_config = MoodleCredentials.from_config(config=config)
        base_url = args.base_url or moodle_config.base_url
        token = _get_ws_token_for_cli(args, config)
        user_id = args.user_id if args.user_id is not None else moodle_config.user_id
        client = MoodleClient(base_url, token)
        if args.action == "site-info":
            _json_print(client.get_site_info())
        elif args.action == "courses":
            if user_id is None:
                parser.error("courses requires --user-id or moodle.user_id in the config file")
            _json_print(client.get_user_courses(int(user_id)))
        elif args.action == "contents":
            if args.course_id is None:
                parser.error("contents requires --course-id")
            _json_print(client.get_course_contents(args.course_id))
        elif args.action == "assignments":
            _json_print(client.get_assignments())
        elif args.action == "notifications":
            if user_id is None:
                parser.error("notifications requires --user-id or moodle.user_id in the config file")
            _json_print(client.get_popup_notification(int(user_id)))
        elif args.action == "forums":
            _json_print(client.get_forum_by_course(args.course_id))
        elif args.action == "quizzes":
            _json_print(client.get_quizzes())
        elif args.action == "workshops":
            _json_print(client.get_workshops())
        return 0

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
