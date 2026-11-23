"""素材サービス（Pexels / Pixabay / Freesound / Openverse / Wikimedia）と ACE-Step の偽サーバー。

応答形式は各公式ドキュメント（2026-11 時点）に合わせる。メディア実体は ffmpeg で生成した小さな
ファイルを CDN 役のホストから返す。
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

import httpx

PEXELS_KEY = "pexels-key-123"
PIXABAY_KEY = "pixabay-key-456"
FREESOUND_KEY = "freesound-key-789"
ACESTEP = "127.0.0.1:8001"

_MEDIA: dict[str, Path] = {}


def media() -> dict[str, Path]:
    """テスト用メディアを一度だけ生成する（jpg 1920x1280 / mp4 1920x1080・640x360 / mp3 / wav）。"""
    if _MEDIA:
        return _MEDIA
    ff = shutil.which("ffmpeg")
    d = Path(tempfile.mkdtemp(prefix="va_fake_media_"))
    specs = {
        "photo.jpg": ["-f", "lavfi", "-i", "testsrc=s=1920x1280:d=1", "-frames:v", "1"],
        "small.jpg": ["-f", "lavfi", "-i", "testsrc=s=320x200:d=1", "-frames:v", "1"],
        "hd.mp4": ["-f", "lavfi", "-i", "testsrc=s=1920x1080:r=30:d=2", "-pix_fmt", "yuv420p"],
        "sd.mp4": ["-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=2", "-pix_fmt", "yuv420p"],
        "sfx.mp3": ["-f", "lavfi", "-i", "sine=f=880:d=1"],
        "music.wav": ["-f", "lavfi", "-i", "sine=f=220:d=4", "-ac", "2", "-ar", "48000"],
    }
    for name, args in specs.items():
        subprocess.run([ff, "-v", "error", "-y", *args, str(d / name)], check=True)
    _MEDIA.update({k: d / k for k in specs})
    return _MEDIA


CDN_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".mp4": "video/mp4", ".mp3": "audio/mpeg"}


async def fake_resolver(host: str) -> list[str]:
    if host.endswith(".internal") or host == "metadata.example":
        return ["10.0.0.5"]
    if host == "localhost.example":
        return ["127.0.0.1"]
    return ["93.184.216.34"]


class FakeAssetWeb:
    HOSTS = {
        "api.pexels.com",
        "images.pexels.com",
        "videos.pexels.com",
        "pixabay.com",
        "cdn.pixabay.com",
        "freesound.org",
        "cdn.freesound.org",
        "api.openverse.org",
        "live.staticflickr.com",
        "audio.example.org",
        "commons.wikimedia.org",
        "upload.wikimedia.org",
        "img.internal",
        "metadata.example",
    }

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.rate_limited: set[str] = set()

    def handles(self, host: str) -> bool:
        return host in self.HOSTS

    def handle(self, req: httpx.Request) -> httpx.Response:
        host, path = req.url.host, req.url.path
        self.calls.append((host, path))
        if host in self.rate_limited:
            return httpx.Response(429, headers={"Retry-After": "30"}, json={"error": "rate"})
        q = {k: v[0] for k, v in parse_qs(req.url.query.decode()).items()}
        if host == "api.pexels.com":
            return self._pexels(req, path)
        if host == "pixabay.com" and path.startswith("/api/"):
            return self._pixabay(path, q)
        if host == "freesound.org" and path.startswith("/apiv2/"):
            return self._freesound(req, path)
        if host == "api.openverse.org" and not path.endswith("/thumb/"):
            return self._openverse(path)
        if host == "commons.wikimedia.org":
            return self._wikimedia(q)
        return self._cdn(host, path)

    # ---------- CDN ----------
    def _cdn(self, host: str, path: str) -> httpx.Response:
        m = media()
        if host == "videos.pexels.com" and path.endswith("/sd-redirect.mp4"):
            return httpx.Response(
                302, headers={"Location": "https://videos.pexels.com/real/sd.mp4"}
            )
        if path.endswith("/to-metadata.jpg"):
            return httpx.Response(302, headers={"Location": "https://metadata.example/x.jpg"})
        if path.endswith("/wrong-type.jpg"):
            return httpx.Response(
                200, content=b"<html></html>", headers={"Content-Type": "text/html"}
            )
        if path.endswith("/thumb/"):
            name = "small.jpg"
        elif path.endswith("sd.mp4"):
            name = "sd.mp4"
        elif path.endswith(".mp4"):
            name = "hd.mp4"
        elif path.endswith(".mp3"):
            name = "sfx.mp3"
        elif "small" in path or "_640" in path or "medium" in path:
            name = "small.jpg"
        else:
            name = "photo.jpg"
        data = m[name].read_bytes()
        ctype = CDN_TYPES[Path(name).suffix]
        return httpx.Response(
            200, content=data, headers={"Content-Type": ctype, "Content-Length": str(len(data))}
        )

    # ---------- Pexels ----------
    def _pexels(self, req: httpx.Request, path: str) -> httpx.Response:
        if req.headers.get("Authorization") != PEXELS_KEY:
            return httpx.Response(401, json={"error": "Unauthorized"})
        photo = {
            "id": 101,
            "width": 1920,
            "height": 1280,
            "url": "https://www.pexels.com/photo/101/",
            "photographer": "Hanako Pexels",
            "photographer_url": "https://www.pexels.com/@hanako",
            "alt": "夜の東京タワー",
            "src": {
                "original": "https://images.pexels.com/photos/101/original.jpeg",
                "large2x": "https://images.pexels.com/photos/101/large2x.jpeg",
                "medium": "https://images.pexels.com/photos/101/medium.jpeg",
            },
        }
        video = {
            "id": 201,
            "width": 1920,
            "height": 1080,
            "duration": 2,
            "url": "https://www.pexels.com/video/201/",
            "image": "https://images.pexels.com/v/201/medium.jpeg",
            "user": {"name": "Jiro Video", "url": "https://www.pexels.com/@jiro"},
            "video_files": [
                {
                    "quality": "hls",
                    "file_type": "video/mp4",
                    "width": None,
                    "height": None,
                    "link": "https://player.vimeo.com/x.m3u8",
                },
                {
                    "quality": "hd",
                    "file_type": "video/mp4",
                    "width": 1920,
                    "height": 1080,
                    "link": "https://videos.pexels.com/video-files/201/hd.mp4",
                },
                {
                    "quality": "sd",
                    "file_type": "video/mp4",
                    "width": 640,
                    "height": 360,
                    "link": "https://videos.pexels.com/video-files/201/sd-redirect.mp4",
                },
            ],
        }
        if path == "/v1/search":
            return httpx.Response(200, json={"photos": [photo], "page": 1, "per_page": 20})
        if path == "/v1/videos/search":
            return httpx.Response(200, json={"videos": [video]})
        if path == "/v1/photos/101":
            return httpx.Response(200, json=photo)
        if path == "/v1/videos/videos/201":
            return httpx.Response(200, json=video)
        return httpx.Response(404, json={"error": "not found"})

    # ---------- Pixabay ----------
    def _pixabay(self, path: str, q: dict[str, str]) -> httpx.Response:
        if q.get("key") != PIXABAY_KEY:
            return httpx.Response(400, text="[ERROR 400] Invalid or missing API key")
        img = {
            "id": 301,
            "pageURL": "https://pixabay.com/photos/sakura-301/",
            "tags": "桜, 春, 花",
            "previewURL": "https://cdn.pixabay.com/photo/301_150.jpg",
            "webformatURL": "https://pixabay.com/get/301_640.jpg",
            "largeImageURL": "https://pixabay.com/get/301_1280.jpg",
            "imageWidth": 3000,
            "imageHeight": 2000,
            "user": "saburo",
            "user_id": 77,
        }
        vid = {
            "id": 401,
            "pageURL": "https://pixabay.com/videos/ocean-401/",
            "tags": "海",
            "duration": 2,
            "user": "shiro",
            "user_id": 88,
            "videos": {
                "large": {"url": "", "width": 0, "height": 0, "size": 0, "thumbnail": ""},
                "medium": {
                    "url": "https://cdn.pixabay.com/video/401_medium.mp4",
                    "width": 1920,
                    "height": 1080,
                    "size": 1000,
                    "thumbnail": "https://cdn.pixabay.com/video/401_small.jpg",
                },
                "small": {
                    "url": "https://cdn.pixabay.com/video/401_sd.mp4",
                    "width": 640,
                    "height": 360,
                    "size": 500,
                    "thumbnail": "",
                },
            },
        }
        hits = [img] if path == "/api/" else [vid]
        if "id" in q and q["id"] not in ("301", "401"):
            hits = []
        return httpx.Response(200, json={"total": len(hits), "totalHits": len(hits), "hits": hits})

    # ---------- Freesound ----------
    def _freesound(self, req: httpx.Request, path: str) -> httpx.Response:
        if req.headers.get("Authorization") != f"Token {FREESOUND_KEY}":
            return httpx.Response(401, json={"detail": "Invalid token"})
        sounds = {
            501: ("Door knock", "http://creativecommons.org/licenses/by/4.0/", 1.0),
            502: ("Rain loop", "Creative Commons 0", 1.0),
            503: ("Beep", "Attribution NonCommercial", 1.0),
            504: ("Long ambience", "Creative Commons 0", 120.0),
        }

        def item(i: int) -> dict[str, Any]:
            name, lic, dur = sounds[i]
            return {
                "id": i,
                "name": name,
                "username": f"user{i}",
                "license": lic,
                "duration": dur,
                "url": f"https://freesound.org/people/user{i}/sounds/{i}/",
                "tags": ["sfx"],
                "previews": {
                    "preview-hq-mp3": f"https://cdn.freesound.org/previews/{i}/{i}_hq.mp3",
                    "preview-lq-mp3": f"https://cdn.freesound.org/previews/{i}/{i}_lq.mp3",
                },
                "images": {"waveform_m": f"https://cdn.freesound.org/displays/{i}_wave_M.jpg"},
            }

        if path == "/apiv2/search/":
            return httpx.Response(200, json={"count": 4, "results": [item(i) for i in sounds]})
        if path.startswith("/apiv2/sounds/"):
            sid = int(path.rstrip("/").rsplit("/", 1)[1])
            if sid in sounds:
                return httpx.Response(200, json=item(sid))
        return httpx.Response(404, json={"detail": "Not found"})

    # ---------- Openverse ----------
    def _openverse(self, path: str) -> httpx.Response:
        images = {
            "ov-1": {
                "license": "by-sa",
                "license_version": "2.0",
                "url": "https://live.staticflickr.com/1/ov1.jpg",
                "creator": "Flickr Taro",
                "title": "Mt. Fuji",
            },
            "ov-2": {
                "license": "by-nc",
                "license_version": "4.0",
                "url": "https://live.staticflickr.com/1/ov2.jpg",
                "creator": "Nc Hanako",
                "title": "Night street",
            },
            "ov-3": {
                "license": "cc0",
                "license_version": "1.0",
                "url": "https://img.internal/secret.jpg",
                "creator": "x",
                "title": "internal",
            },
        }
        audio = {
            "ova-1": {
                "license": "cc0",
                "license_version": "1.0",
                "url": "https://audio.example.org/a.mp3",
                "creator": "Sound Taro",
                "title": "Chime",
                "duration": 1000,
            }
        }

        def item(k: str, d: dict[str, Any]) -> dict[str, Any]:
            return {
                "id": k,
                "title": d["title"],
                "creator": d["creator"],
                "creator_url": "https://example.org/u",
                "foreign_landing_url": f"https://www.flickr.com/photos/{k}",
                "url": d["url"],
                "license": d["license"],
                "license_version": d["license_version"],
                "license_url": None,
                "provider": "flickr",
                "source": "flickr",
                "width": 1920,
                "height": 1280,
                "thumbnail": f"https://api.openverse.org/v1/images/{k}/thumb/",
                "duration": d.get("duration"),
                "tags": [{"name": "fuji"}],
            }

        if path == "/v1/images/":
            return httpx.Response(
                200, json={"result_count": 3, "results": [item(k, d) for k, d in images.items()]}
            )
        if path == "/v1/audio/":
            return httpx.Response(200, json={"results": [item(k, d) for k, d in audio.items()]})
        parts = path.strip("/").split("/")
        if len(parts) == 3:
            table = images if parts[1] == "images" else audio
            if parts[2] in table:
                return httpx.Response(200, json=item(parts[2], table[parts[2]]))
        return httpx.Response(404, json={"detail": "Not found"})

    # ---------- Wikimedia ----------
    def _wikimedia(self, q: dict[str, str]) -> httpx.Response:
        page = {
            "pageid": 701,
            "ns": 6,
            "title": "File:Mount Fuji.jpg",
            "index": 1,
            "imageinfo": [
                {
                    "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Mount_Fuji.jpg",
                    "descriptionurl": "https://commons.wikimedia.org/wiki/File:Mount_Fuji.jpg",
                    "thumburl": "https://upload.wikimedia.org/thumb/Mount_Fuji_640.jpg",
                    "width": 1920,
                    "height": 1280,
                    "size": 12345,
                    "mime": "image/jpeg",
                    "extmetadata": {
                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
                        "Artist": {
                            "value": '<a href="//commons.wikimedia.org/wiki/User:Taro">Taro &amp; Co</a>'
                        },
                        "ObjectName": {"value": "Mount Fuji"},
                    },
                }
            ],
        }
        if q.get("pageids") and q["pageids"] != "701":
            return httpx.Response(
                200, json={"query": {"pages": [{"pageid": int(q["pageids"]), "missing": True}]}}
            )
        return httpx.Response(200, json={"batchcomplete": True, "query": {"pages": [page]}})


class FakeAceStep:
    def __init__(self) -> None:
        self.up = False  # 既存テストは「未起動」を前提にしている
        self.released: list[dict[str, Any]] = []
        self.polls = 0
        self.fail = False
        self.openai_models = False  # 実機の版は /v1/models を OpenAI 互換形式で返す

    @staticmethod
    def _env(data: Any) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": data,
                "code": 200,
                "error": None,
                "timestamp": 1700000000000,
                "extra": None,
            },
        )

    def handle(self, req: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("refused", request=req)
        p = req.url.path
        if p == "/health":
            return self._env(
                {
                    "status": "ok",
                    "service": "ACE-Step API",
                    "version": "1.0",
                    "models_initialized": False,
                    "loaded_model": "acestep-v15-turbo",
                }
            )
        if p == "/v1/models" and self.openai_models:
            return httpx.Response(200, json={"object": "list", "data": []})
        if p == "/v1/models":
            return self._env(
                {
                    "models": [{"name": "acestep-v15-turbo", "is_default": True}],
                    "default_model": "acestep-v15-turbo",
                }
            )
        if p == "/release_task":
            body = json.loads(req.content)
            self.released.append(body)
            return self._env(
                {"task_id": f"task-{len(self.released)}", "status": "queued", "queue_position": 1}
            )
        if p == "/query_result":
            ids = json.loads(req.content)["task_id_list"]
            self.polls += 1
            if self.polls < 2:
                return self._env([{"task_id": ids[0], "status": 0, "result": ""}])
            if self.fail:
                return self._env([{"task_id": ids[0], "status": 2, "result": "[]"}])
            body = self.released[-1]
            res = [
                {
                    "file": f"/v1/audio?path=%2Ftmp%2Fapi_audio%2F{ids[0]}_{i}.wav",
                    "wave": "",
                    "status": 1,
                    "prompt": body.get("prompt"),
                    "lyrics": body.get("lyrics"),
                    "metas": {
                        "bpm": 100,
                        "duration": 4,
                        "keyscale": "C Major",
                        "timesignature": "4",
                    },
                    "seed_value": "12345",
                    "lm_model": "acestep-5Hz-lm-0.6B",
                    "dit_model": "acestep-v15-turbo",
                }
                for i in range(body.get("batch_size", 1))
            ]
            return self._env([{"task_id": ids[0], "status": 1, "result": json.dumps(res)}])
        if p == "/v1/audio":
            path = unquote(req.url.params.get("path", ""))
            assert path.startswith("/tmp/api_audio/")
            return httpx.Response(
                200,
                content=media()["music.wav"].read_bytes(),
                headers={"Content-Type": "audio/wav"},
            )
        return httpx.Response(404, json={"detail": "Not Found"})
