"""YouTube への投稿（PC で実行する）。

  uv run python tools/youtube_upload.py auth
      本人が 1 回だけ。ブラウザで許可し、更新用の鍵を暗号化して data/youtube/token.bin に保存する
  uv run python tools/youtube_upload.py upload <動画.mp4> <meta.json> [--thumbnail 画像] [--confirm]
      --confirm が無いときは確認だけ（何を上げるかを表示して終わる。上げない）
      上げた結果は <動画>.youtube.json に書く（video id・URL・公開状態）

meta.json の例（既定は非公開。公開は本人が YouTube Studio で行う）:
  {"title": "…", "description": "…", "tags": ["…"], "privacy": "private"}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from video_automation.app.settings import get_settings  # noqa: E402
from video_automation.integrations.youtube import oauth, uploader  # noqa: E402


def _folder() -> Path:
    return Path(get_settings().data_dir).resolve() / "youtube"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="YouTube への投稿")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="許可を取る（1 回だけ）")
    up = sub.add_parser("upload", help="動画を上げる")
    up.add_argument("video", type=Path)
    up.add_argument("meta", type=Path)
    up.add_argument("--thumbnail", type=Path)
    up.add_argument("--confirm", action="store_true", help="本当に上げる（無ければ確認だけ）")
    a = ap.parse_args(argv)
    folder = _folder()
    if a.cmd == "auth":
        oauth.authorize(folder)
        print(f"YOUTUBE_AUTH_OK {folder / oauth.TOKEN_FILE}")
        return 0
    meta = uploader.VideoMeta.from_file(a.meta)
    errs = meta.validate()
    size_mb = a.video.stat().st_size / 1e6 if a.video.is_file() else 0
    print(
        f"上げる動画: {a.video}（{size_mb:.1f} MB）\n題名: {meta.title}\n公開状態: {meta.privacy}"
        + (f"（予約 {meta.publish_at}）" if meta.publish_at else "")
        + f"\nサムネイル: {a.thumbnail or 'なし'}"
    )
    if errs:
        print("NG: " + " / ".join(errs))
        return 2
    if not a.confirm:
        print("YOUTUBE_DRYRUN（--confirm を付けると上げます）")
        return 0
    with httpx.Client(timeout=httpx.Timeout(60, write=600)) as http:
        token = oauth.access_token(folder, http)
        res = uploader.upload(
            http,
            token,
            a.video,
            meta,
            progress=lambda s, t: print(f"  {s * 100 // max(t, 1)}%", flush=True),
        )
        vid = res["id"]
        note = ""
        if a.thumbnail:
            try:
                uploader.set_thumbnail(http, token, vid, a.thumbnail)
            except uploader.YouTubeUploadError as exc:
                note = str(exc)
    out = {
        "id": vid,
        "url": f"https://youtu.be/{vid}",
        "studio": f"https://studio.youtube.com/video/{vid}/edit",
        "privacy": res.get("status", {}).get("privacyStatus", meta.privacy),
        "thumbnail_note": note,
    }
    a.video.with_suffix(".youtube.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        f"YOUTUBE_UPLOAD_OK {out['url']} privacy={out['privacy']}" + (f" ({note})" if note else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
