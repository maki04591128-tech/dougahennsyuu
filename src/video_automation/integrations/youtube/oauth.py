"""YouTube の許可（OAuth 2.0 のデスクトップアプリ方式・ループバック＋PKCE）。

使い方（本人が PC で 1 回だけ）:
1. Google Cloud でプロジェクトを作り「YouTube Data API v3」を有効にする
2. OAuth 同意画面（外部・テストユーザーに自分を追加）→ 認証情報
   「OAuth クライアント ID（デスクトップ アプリ）」を作り、
   JSON をダウンロードして data/youtube/client_secret.json に置く
3. `uv run python tools/youtube_upload.py auth` → ブラウザで許可 → 更新用の鍵を暗号化して保存

鍵（refresh_token）は Windows DPAPI で暗号化し、平文では保存しない（APIキーと同じ扱い）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx

from video_automation.app.secrets import SecretProtector, default_protector

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# 上げる・サムネイルを付けるだけ。動画の削除や公開状態の変更はこの許可ではしない
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
TOKEN_FILE = "token.bin"
CLIENT_FILE = "client_secret.json"


class YouTubeAuthError(RuntimeError):
    pass


@dataclass
class ClientInfo:
    client_id: str
    client_secret: str


def load_client(folder: Path) -> ClientInfo:
    """ダウンロードした client_secret.json（installed 形式）を読む。"""
    p = folder / CLIENT_FILE
    if not p.is_file():
        raise YouTubeAuthError(
            f"{p} がありません。Google Cloud で「デスクトップ アプリ」の OAuth クライアントを作り、"
            "JSON をこの名前で置いてください"
        )
    data = json.loads(p.read_text("utf-8"))
    body = data.get("installed") or data.get("web") or {}
    if not body.get("client_id") or not body.get("client_secret"):
        raise YouTubeAuthError(
            "client_secret.json の形式が違います（installed の client_id が無い）"
        )
    return ClientInfo(body["client_id"], body["client_secret"])


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge[S256])。"""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def auth_url(client: ClientInfo, redirect_uri: str, challenge: str, state: str) -> str:
    q = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


class TokenStore:
    """更新用の鍵の保存（DPAPI で暗号化）。"""

    def __init__(self, folder: Path, protector: SecretProtector | None = None) -> None:
        self.folder = folder
        self.protector = protector or default_protector()

    @property
    def path(self) -> Path:
        return self.folder / TOKEN_FILE

    def save(self, refresh_token: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(self.protector.protect(refresh_token.encode("utf-8")))

    def load(self) -> str:
        if not self.path.is_file():
            raise YouTubeAuthError(
                "まだ許可されていません。先に `youtube_upload.py auth` を実行してください"
            )
        return self.protector.unprotect(self.path.read_bytes()).decode("utf-8")


def _wait_code(port: int, state: str, timeout: float) -> tuple[str, int]:
    """127.0.0.1 のループバックで許可コードを 1 回だけ受け取る。(code, 使ったポート)。"""
    got: dict[str, str] = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server の決まり
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state and "code" in q:
                got["code"] = q["code"][0]
                msg = "許可を受け取りました。このタブは閉じてかまいません。"
            else:
                got["error"] = q.get("error", ["state_mismatch"])[0]
                msg = "許可を受け取れませんでした。"
            body = f"<meta charset='utf-8'><p>{msg}</p>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", port), H)
    srv.timeout = 1.0
    used = srv.server_address[1]
    try:
        end = time.monotonic() + timeout
        while not got and time.monotonic() < end:
            srv.handle_request()
    finally:
        srv.server_close()
    if "code" not in got:
        raise YouTubeAuthError(f"許可を受け取れませんでした: {got.get('error', 'timeout')}")
    return got["code"], used


def authorize(folder: Path, open_browser: bool = True, timeout: float = 300.0) -> None:
    """本人が PC で 1 回だけ行う許可。更新用の鍵を保存する。"""
    client = load_client(folder)
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    # 空きポートを先に決める（リダイレクト先に入れるため）
    probe = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    port = probe.server_address[1]
    probe.server_close()
    redirect = f"http://127.0.0.1:{port}"
    url = auth_url(client, redirect, challenge, state)
    print("ブラウザで YouTube への投稿を許可してください:\n" + url, flush=True)
    if open_browser:
        webbrowser.open(url)
    code, _ = _wait_code(port, state, timeout)
    r = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
        timeout=30,
    )
    if r.status_code != 200 or "refresh_token" not in r.json():
        raise YouTubeAuthError(f"鍵を受け取れませんでした（{r.status_code}）")
    TokenStore(folder).save(r.json()["refresh_token"])


def access_token(
    folder: Path, client_http: httpx.Client | None = None, store: TokenStore | None = None
) -> str:
    """保存した更新用の鍵から、その場で使う鍵（1 時間有効）を受け取る。"""
    client = load_client(folder)
    refresh = (store or TokenStore(folder)).load()
    http = client_http or httpx.Client(timeout=30)
    r = http.post(
        TOKEN_URL,
        data={
            "client_id": client.client_id,
            "client_secret": client.client_secret,
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        },
    )
    if r.status_code != 200:
        raise YouTubeAuthError(
            f"鍵を更新できませんでした（{r.status_code}）。許可を取り直してください（auth）"
        )
    return str(r.json()["access_token"])
