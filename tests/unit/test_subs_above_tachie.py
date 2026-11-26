"""縦長で立ち絵を置いたときは、字幕を立ち絵の上に上げる（横長・立ち絵なしはそのまま）。"""

import asyncio
from types import SimpleNamespace

from video_automation.schemas.subtitle import SubtitleStyle
from video_automation.services.characters.overlay import layout
from video_automation.services.rendering.render_service import RenderService


def _svc(with_tachie: bool, muted: bool = False) -> RenderService:
    tracks = [SimpleNamespace(id="t1", name="立ち絵", muted=muted)] if with_tachie else []
    clips = [SimpleNamespace(track_id="t1")] if with_tachie else []

    class Tl:
        async def get_timeline(self, project_id: str):
            return SimpleNamespace(tracks=tracks, clips=clips)

    svc = RenderService.__new__(RenderService)
    svc.timeline = Tl()
    return svc


def test_portrait_with_tachie_raises_subtitles():
    st = SubtitleStyle(margin_v_ratio=0.12)
    out = asyncio.run(_svc(True)._clear_of_tachie("p", 1080, 1920, st))
    assert out.margin_v_ratio == round(layout(1080, 1920)[1] + 0.03, 3)
    assert out.margin_v_ratio > st.margin_v_ratio


def test_landscape_or_no_tachie_keeps_subtitles():
    st = SubtitleStyle(margin_v_ratio=0.12)
    assert asyncio.run(_svc(True)._clear_of_tachie("p", 1920, 1080, st)).margin_v_ratio == 0.12
    # 統合版 S5: 縦型は立ち絵が無くても、字幕を下の 35%（y = 1248px）より上に出す
    assert asyncio.run(_svc(False)._clear_of_tachie("p", 1080, 1920, st)).margin_v_ratio == 0.35
    assert (
        asyncio.run(_svc(True, muted=True)._clear_of_tachie("p", 1080, 1920, st)).margin_v_ratio
        == 0.35
    )
