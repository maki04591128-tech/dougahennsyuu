"""YouTube へ動画を上げる（再開できる方式）。

- 既定の公開状態は「非公開（private）」。公開・限定公開・予約は本人が YouTube Studio で決める
  （投稿は取り消しにくい操作なので、AI が勝手に公開しない）
- 途中で切れても、同じ上げ先（session URL）に残りから送り直す（308 Resume Incomplete）
- サムネイル（JPG/PNG・2MB まで）は上げた後に付ける
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
THUMB_URL = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
CHUNK = 8 * 1024 * 1024  # 256KB の倍数であること（API の決まり）
GAMING_CATEGORY = "20"  # 「ゲーム」
TITLE_MAX = 100
DESC_MAX = 5000
TAGS_MAX_CHARS = 500
THUMB_MAX = 2 * 1024 * 1024

Privacy = Literal["private", "unlisted", "public"]


class YouTubeUploadError(RuntimeError):
    pass


@dataclass
class VideoMeta:
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category_id: str = GAMING_CATEGORY
    privacy: Privacy = "private"
    made_for_kids: bool = False
    # 現実の人・出来事と見分けにくい改変・合成の映像があるとき True
    # （ゲーム画面と立ち絵だけなら False）
    contains_synthetic_media: bool = False
    language: str = "ja"
    publish_at: str | None = None  # ISO 8601。指定すると予約公開（privacy は private のまま）

    def validate(self) -> list[str]:
        """直したら上げられる問題の一覧（空なら上げられる）。"""
        errs: list[str] = []
        if not self.title.strip():
            errs.append("題名が空です")
        if len(self.title) > TITLE_MAX:
            errs.append(f"題名が {TITLE_MAX} 字を超えています（{len(self.title)} 字）")
        if "<" in self.title or ">" in self.title:
            errs.append("題名に < > は使えません")
        if len(self.description.encode("utf-8")) > DESC_MAX:
            errs.append(f"説明が {DESC_MAX} バイトを超えています")
        if "<" in self.description or ">" in self.description:
            errs.append("説明に < > は使えません")
        if sum(len(t) + (2 if " " in t else 0) for t in self.tags) + max(0, len(self.tags) - 1) > (
            TAGS_MAX_CHARS
        ):
            errs.append(f"タグの合計が {TAGS_MAX_CHARS} 字を超えています")
        if self.publish_at and self.privacy != "private":
            errs.append("予約公開（publish_at）は privacy=private のときだけ使えます")
        return errs

    def body(self) -> dict[str, Any]:
        status: dict[str, Any] = {
            "privacyStatus": self.privacy,
            "selfDeclaredMadeForKids": self.made_for_kids,
            "containsSyntheticMedia": self.contains_synthetic_media,
            "embeddable": True,
        }
        if self.publish_at:
            status["publishAt"] = self.publish_at
        return {
            "snippet": {
                "title": self.title,
                "description": self.description,
                "tags": self.tags,
                "categoryId": self.category_id,
                "defaultLanguage": self.language,
                "defaultAudioLanguage": self.language,
            },
            "status": status,
        }

    @classmethod
    def from_file(cls, path: Path) -> VideoMeta:
        d = json.loads(path.read_text("utf-8"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def upload(
    http: httpx.Client,
    token: str,
    video: Path,
    meta: VideoMeta,
    progress: Callable[[int, int], None] | None = None,
    session_url: str | None = None,
    max_retries: int = 5,
) -> dict[str, Any]:
    """動画を上げて、YouTube の video リソース（id を含む）を返す。"""
    errs = meta.validate()
    if errs:
        raise YouTubeUploadError("上げる前に直してください: " + " / ".join(errs))
    if not video.is_file():
        raise YouTubeUploadError(f"動画がありません: {video}")
    size = video.stat().st_size
    auth = {"Authorization": f"Bearer {token}"}
    resumed = session_url is not None
    if session_url is None:
        r0 = http.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                **auth,
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/*",
                "X-Upload-Content-Length": str(size),
            },
            content=json.dumps(meta.body()).encode("utf-8"),
        )
        if r0.status_code != 200 or "location" not in r0.headers:
            msg = f"上げ先を作れませんでした（{r0.status_code}）: {r0.text[:300]}"
            raise YouTubeUploadError(msg)
        session_url = r0.headers["location"]
    # 前の上げ先から続けるときは、どこまで届いているかを先に聞く
    sent, done = _query_offset(http, session_url, auth, size) if resumed else (0, None)
    if done is not None:
        return done
    retries = 0
    with video.open("rb") as f:
        while True:
            f.seek(sent)
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            r: httpx.Response | None
            try:
                r = http.put(
                    session_url,
                    headers={
                        **auth,
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {sent}-{end}/{size}",
                    },
                    content=chunk,
                )
            except httpx.TransportError:
                r = None
            if r is not None and r.status_code in (200, 201):
                if progress:
                    progress(size, size)
                return dict(r.json())
            if r is not None and r.status_code == 308:
                sent = _range_end(r.headers.get("range"))
                retries = 0
                if progress:
                    progress(sent, size)
                continue
            if r is not None and r.status_code < 500 and r.status_code != 429:
                raise YouTubeUploadError(f"上げられませんでした（{r.status_code}）: {r.text[:300]}")
            # 5xx・429・回線切れ: どこまで届いたか聞いてから残りを送る
            retries += 1
            if retries > max_retries:
                raise YouTubeUploadError(
                    f"何度か送り直しましたが上げられませんでした。session_url={session_url}"
                )
            sent, done = _query_offset(http, session_url, auth, size)
            if done is not None:
                return done


def _range_end(header: str | None) -> int:
    """Range: bytes=0-12345 → 次に送る位置 12346（無ければ 0）。"""
    if not header or "-" not in header:
        return 0
    return int(header.rsplit("-", 1)[1]) + 1


def _query_offset(
    http: httpx.Client, url: str, auth: dict[str, str], size: int
) -> tuple[int, dict[str, Any] | None]:
    """(次に送る位置, 上げ終わっていれば video リソース)。"""
    r = http.put(url, headers={**auth, "Content-Length": "0", "Content-Range": f"bytes */{size}"})
    if r.status_code == 308:
        return _range_end(r.headers.get("range")), None
    if r.status_code in (200, 201):
        return size, dict(r.json())
    raise YouTubeUploadError(f"どこまで届いたか確かめられませんでした（{r.status_code}）")


def set_thumbnail(http: httpx.Client, token: str, video_id: str, image: Path) -> None:
    """サムネイルを付ける（カスタムサムネイルは電話番号の確認済みのチャンネルだけ）。"""
    data = image.read_bytes()
    if len(data) > THUMB_MAX:
        raise YouTubeUploadError("サムネイルは 2MB までです")
    ctype = "image/png" if image.suffix.lower() == ".png" else "image/jpeg"
    r = http.post(
        THUMB_URL,
        params={"videoId": video_id},
        headers={"Authorization": f"Bearer {token}", "Content-Type": ctype},
        content=data,
    )
    if r.status_code != 200:
        raise YouTubeUploadError(
            f"サムネイルを付けられませんでした（{r.status_code}）: {r.text[:300]}"
        )
