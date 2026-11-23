"""APIキー保護。Windows DPAPI (CryptProtectData) でユーザー単位に暗号化し、DBに平文保存しない。

Windows 以外では保存を拒否する（環境変数 VA_*_API_KEY での指定は可能）。
"""

import ctypes
import sys
from typing import Protocol


class SecretStorageUnavailableError(RuntimeError):
    pass


class SecretProtector(Protocol):
    def protect(self, data: bytes) -> bytes: ...

    def unprotect(self, blob: bytes) -> bytes: ...


class UnavailableProtector:
    def protect(self, data: bytes) -> bytes:
        raise SecretStorageUnavailableError("secret storage requires Windows DPAPI")

    def unprotect(self, blob: bytes) -> bytes:
        raise SecretStorageUnavailableError("secret storage requires Windows DPAPI")


class DpapiProtector:  # pragma: no cover - Windows専用（実機テストで確認）
    _ENTROPY = b"VideoAutomation/api-key/v1"

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(self, data: bytes) -> tuple["DpapiProtector._Blob", ctypes.Array[ctypes.c_char]]:
        # バッファは呼び出し完了まで参照を保持する（GCで解放させない）
        buf = ctypes.create_string_buffer(data, len(data))
        return self._Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf

    def _call(self, fn_name: str, data: bytes) -> bytes:
        crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        (src, _keep1), (ent, _keep2), out = (
            self._blob(data),
            self._blob(self._ENTROPY),
            self._Blob(),
        )
        fn = getattr(crypt32, fn_name)
        ok = fn(ctypes.byref(src), None, ctypes.byref(ent), None, None, 0x01, ctypes.byref(out))
        if not ok:
            raise SecretStorageUnavailableError(f"{fn_name} failed")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            kernel32.LocalFree(out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call("CryptProtectData", data)

    def unprotect(self, blob: bytes) -> bytes:
        return self._call("CryptUnprotectData", blob)


def default_protector() -> SecretProtector:
    if sys.platform == "win32":
        return DpapiProtector()
    return UnavailableProtector()
