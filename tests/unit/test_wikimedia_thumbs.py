"""Wikimedia の大きすぎる画像は縮小版を選ぶ（書き出しが止まらない大きさ）。"""

from video_automation.providers.assets.base import pick_variant
from video_automation.providers.assets.open_sources import WikimediaSource
from video_automation.schemas.asset_source import (
    AssetSearchResult,
    LicenseInfo,
    MediaType,
    MediaVariant,
    SourceName,
)


def _ii(w: int, h: int) -> dict:
    return {
        "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Big.jpg",
        # 実際の API は 640 を頼んでも 960px の版と追跡用の ? を返す
        "thumburl": "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Big.jpg/960px-Big.jpg"
        "?utm_source=commons.wikimedia.org",
        "width": w,
        "height": h,
        "mime": "image/jpeg",
    }


def test_thumbs_for_huge_image_and_pick_smallest_enough():
    ii = _ii(30000, 23800)
    thumbs = WikimediaSource._thumbs(ii, MediaType.IMAGE)
    assert [t.width for t in thumbs] == [1920, 3840]
    assert thumbs[0].url.endswith("/1920px-Big.jpg") and thumbs[0].height == round(
        23800 * 1920 / 30000
    )
    r = AssetSearchResult(
        source=SourceName.WIKIMEDIA,
        source_id="1",
        media_type=MediaType.IMAGE,
        title="Big",
        variants=[
            MediaVariant(url=ii["url"], width=30000, height=23800, label="original"),
            *thumbs,
        ],
        license=LicenseInfo(
            code="pdm",
            name="Public domain",
            attribution_required=False,
            commercial_use=True,
            modification=True,
        ),
    )
    url, w, _ = pick_variant(r, 1920, 1080)
    assert w == 1920 and url.endswith("/1920px-Big.jpg")
    # 元が小さければ縮小版は作らない。動画にも作らない
    assert WikimediaSource._thumbs(_ii(2000, 1300), MediaType.IMAGE) == []
    assert WikimediaSource._thumbs(_ii(30000, 20000), MediaType.VIDEO) == []
