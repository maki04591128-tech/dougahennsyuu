"""obs-websocket 5.x の最小の同期クライアント（OBS 28 以降に同梱）。

- 127.0.0.1 だけにつなぐ。パスワードは %USERPROFILE%\\.va_obs\\ws_password.txt（導入の bat が作る）
- 認証: secret = b64(sha256(password + salt))、auth = b64(sha256(secret + challenge))
"""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from websockets.sync.client import ClientConnection, connect

DEFAULT_URL = "ws://127.0.0.1:4455"
SECRET_DIR = Path.home() / ".va_obs"


class ObsError(RuntimeError):
    pass


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


def read_password(folder: Path = SECRET_DIR) -> str:
    p = folder / "ws_password.txt"
    return p.read_text("utf-8").strip() if p.is_file() else ""


class ObsClient:
    def __init__(self, url: str = DEFAULT_URL, password: str | None = None, timeout: float = 10):
        self.url = url
        self.password = read_password() if password is None else password
        self.timeout = timeout
        self.ws: ClientConnection | None = None

    def __enter__(self) -> ObsClient:
        self.open()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def open(self) -> None:
        self.ws = connect(self.url, open_timeout=self.timeout, max_size=2**24)
        hello = json.loads(self.ws.recv(timeout=self.timeout))
        if hello.get("op") != 0:
            raise ObsError("OBS の最初の応答が Hello ではありません")
        ident: dict[str, Any] = {"rpcVersion": 1, "eventSubscriptions": 0}
        a = hello["d"].get("authentication")
        if a:
            if not self.password:
                raise ObsError("OBS の WebSocket のパスワードがありません")
            ident["authentication"] = auth_string(self.password, a["salt"], a["challenge"])
        self.ws.send(json.dumps({"op": 1, "d": ident}))
        res = json.loads(self.ws.recv(timeout=self.timeout))
        if res.get("op") != 2:
            raise ObsError("OBS に認証されませんでした（パスワードを確かめてください）")

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
            self.ws = None

    def call(self, request_type: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        """要求を 1 つ送り、応答の responseData を返す。失敗は ObsError。"""
        if self.ws is None:
            self.open()
        assert self.ws is not None
        rid = uuid.uuid4().hex
        self.ws.send(
            json.dumps(
                {
                    "op": 6,
                    "d": {"requestType": request_type, "requestId": rid, "requestData": data or {}},
                }
            )
        )
        while True:
            msg = json.loads(self.ws.recv(timeout=self.timeout))
            if msg.get("op") == 7 and msg["d"].get("requestId") == rid:
                st = msg["d"]["requestStatus"]
                if not st.get("result"):
                    why = f"（{st.get('code')}）: {st.get('comment', '')}"
                    raise ObsError(f"{request_type} に失敗しました{why}")
                return dict(msg["d"].get("responseData") or {})
