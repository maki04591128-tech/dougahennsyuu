"""VOICEVOX系エンジン（VOICEVOX Nemo / VOICEVOX Engine）のHTTPクライアント。

実機確認 (2026-11-23, VOICEVOX Nemo Engine 0.26.0 windows-cpu, port 50121):
/version /engine_manifest /speakers /supported_devices /audio_query /accent_phrases
/mora_data /mora_pitch /mora_length /synthesis /user_dict /user_dict_word が存在。
テキスト等はクエリパラメータで渡す仕様。
"""

from typing import Any

import httpx


class VoicevoxError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class VoicevoxUnavailableError(VoicevoxError):
    pass


class VoicevoxClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.base_url,
            transport=transport,
            trust_env=False,  # localhost にプロキシを通さない
            timeout=httpx.Timeout(timeout, connect=3.0),
        )

    async def _req(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        try:
            r = await self._http.request(
                method,
                path,
                params=params,
                json=json,
                timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            raise VoicevoxUnavailableError(type(exc).__name__) from exc
        if r.status_code >= 400:
            detail: Any = r.text[:300]
            try:
                detail = r.json().get("detail", detail)
            except ValueError:
                pass
            raise VoicevoxError(f"HTTP {r.status_code}: {str(detail)[:300]}", r.status_code)
        return r

    async def get_json(self, path: str, **params: Any) -> Any:
        return (await self._req("GET", path, params=params or None, timeout=10.0)).json()

    async def version(self) -> str:
        return str(await self.get_json("/version"))

    async def engine_manifest(self) -> dict[str, Any]:
        return await self.get_json("/engine_manifest")

    async def speakers(self) -> list[dict[str, Any]]:
        return await self.get_json("/speakers")

    async def supported_devices(self) -> dict[str, bool]:
        return await self.get_json("/supported_devices")

    async def audio_query(self, text: str, speaker: int) -> dict[str, Any]:
        r = await self._req("POST", "/audio_query", params={"text": text, "speaker": speaker})
        return r.json()

    async def accent_phrases(self, text: str, speaker: int, is_kana: bool = False) -> list[Any]:
        r = await self._req(
            "POST",
            "/accent_phrases",
            params={"text": text, "speaker": speaker, "is_kana": str(is_kana).lower()},
        )
        return r.json()

    async def recompute_moras(
        self, endpoint: str, accent_phrases: list[Any], speaker: int
    ) -> list[Any]:
        if endpoint not in ("mora_data", "mora_pitch", "mora_length"):
            raise ValueError(endpoint)
        r = await self._req(
            "POST", f"/{endpoint}", params={"speaker": speaker}, json=accent_phrases
        )
        return r.json()

    async def synthesis(self, query: dict[str, Any], speaker: int) -> bytes:
        r = await self._req(
            "POST", "/synthesis", params={"speaker": speaker}, json=query, timeout=300.0
        )
        return r.content

    async def initialize_speaker(self, speaker: int) -> None:
        await self._req(
            "POST",
            "/initialize_speaker",
            params={"speaker": speaker, "skip_reinit": "true"},
            timeout=120.0,
        )

    # ----- ユーザー辞書 -----
    async def user_dict(self) -> dict[str, Any]:
        return await self.get_json("/user_dict")

    async def add_word(self, **params: Any) -> str:
        r = await self._req("POST", "/user_dict_word", params=params)
        return str(r.json())

    async def update_word(self, word_uuid: str, **params: Any) -> None:
        await self._req("PUT", f"/user_dict_word/{word_uuid}", params=params)

    async def delete_word(self, word_uuid: str) -> None:
        await self._req("DELETE", f"/user_dict_word/{word_uuid}")

    async def aclose(self) -> None:
        await self._http.aclose()
