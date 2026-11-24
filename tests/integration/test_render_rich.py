"""書き出し時の画面演出（rich_direction）。偽の blender で流れ（字幕なし→演出→字幕）を確かめる。"""

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

from fakes import FakeProtector, blender_calls, fake_blender
from video_automation.api.app import create_app
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.schemas.timeline import RichLevel
from video_automation.services.design.profiles import PROFILES
from video_automation.services.rendering.rich import plan_rich


def test_plan_rich_is_capped_by_purpose():
    from video_automation.schemas.design import Purpose

    # 字幕動画: 動きの強さ 0 → 演出なし
    assert not plan_rich(RichLevel.BOLD, PROFILES[Purpose.SUBTITLE]).active
    # 解説: 控えめまで。図形の転換・パンチは使わない
    r = plan_rich(RichLevel.BOLD, PROFILES[Purpose.EXPLAINER])
    assert r.level == RichLevel.SUBTLE and r.notes
    assert not r.parts.get("transitions") and not r.parts.get("punch")
    # 解説では撮影処理（ブルーム・ビネット）を付けない（図・文字の色が濁る）
    assert "look" not in r.parts
    assert "look" not in plan_rich(RichLevel.STANDARD, PROFILES[Purpose.PROMO]).parts
    # PR: 派手まで使える。色収差・フラッシュも用途で許されている
    r = plan_rich(RichLevel.BOLD, PROFILES[Purpose.PROMO], fps=60)
    assert r.level == RichLevel.BOLD and r.parts["glitch"] and r.parts["flash"]
    assert r.params["intro_frames"] == 192  # 60fps では 2 倍のフレーム（秒は同じ）
    # 標準: 冒頭とパンチはあるが、RGB ずらし・フラッシュは無い
    r = plan_rich(RichLevel.STANDARD, PROFILES[Purpose.PROMO])
    assert r.parts["intro"] and r.parts["punch"] and "glitch" not in r.parts
    assert r.params["dispersion"] == 0 and r.params["flash_alpha"] == 0
    # 歌詞を動く文字で出すのは MV・リリックだけ（解説・PR は字幕のまま）
    for pu in (Purpose.LYRIC, Purpose.MV):
        assert plan_rich(RichLevel.STANDARD, PROFILES[pu]).parts.get("lyrics")
    assert not plan_rich(RichLevel.BOLD, PROFILES[Purpose.PROMO]).parts.get("lyrics")
    assert not plan_rich(RichLevel.BOLD, PROFILES[Purpose.EXPLAINER]).parts.get("lyrics")


@pytest_asyncio.fixture
async def bclient(media_settings, fake, tmp_path):
    s = media_settings.model_copy(update={"blender_path": fake_blender(tmp_path)})
    app = create_app(s, http_transport=fake.transport, protector=FakeProtector())
    async with LifespanManager(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test/api/v1") as c:
            c.app = app  # type: ignore[attr-defined]
            yield c


async def _timeline(c, media_dir):
    from test_render_api import _project_with_timeline

    return await _project_with_timeline(c, media_dir)


async def test_render_rich_requires_purpose(mclient, media_dir):
    pid = await _timeline(mclient, media_dir)
    r = await mclient.post("/render", json={"project_id": pid, "rich_direction": "standard"})
    assert r.status_code == 422 and "用途" in r.text


async def test_render_rich_requires_blender(mclient, media_dir):
    pid = await _timeline(mclient, media_dir)
    await mclient.put(f"/projects/{pid}/design", json={"purpose": "promo"})
    r = await mclient.post("/render", json={"project_id": pid, "rich_direction": "standard"})
    assert r.status_code == 422 and "Blender" in r.text


async def test_render_rich_flow(bclient, media_dir, media_settings, tmp_path):
    c = bclient
    pid = await _timeline(c, media_dir)
    await c.put(f"/projects/{pid}/design", json={"purpose": "promo"})
    # 演出プランを適用したときに残る題名・章の区切り
    await c.app.state.container.design.record(
        pid,
        "direction",
        {"outline": {"title": "題名", "catch": "なぜ？", "chapters": [[0, "導入"], [60, "本題"]]}},
    )
    r = await c.post(
        "/render",
        json={"project_id": pid, "rich_direction": "bold", "quality": 35, "loudnorm": False},
    )
    assert r.status_code == 202, r.text
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    rich = job.result["rich_direction"]
    # 題名カードがある（演出プランの題名がある）ので、冒頭で題名を繰り返さない
    assert rich["level"] == "bold" and rich["intro_frames"] == 0 and not rich["failed_parts"]
    call = [x for x in blender_calls(tmp_path) if x["script"] == "rich_direction"][-1]
    assert call["chapters"] == [[0, "導入"], [60, "本題"]] and call["title"] == ""
    assert not call["parts"]["intro"] and call["parts"]["look"]
    assert call["parts"]["letterbox"] and call["colors"]["main"].startswith("#")
    out = Path(media_settings.data_dir) / job.result["output_path"]
    info = await FFmpeg().probe(out)
    assert (info.width, info.height) == (1920, 1080) and info.has_audio
    assert abs(info.duration_sec - 3.0) < 0.15
    # 途中の作業ファイルは残さない
    assert not list(out.parent.glob("*.clean.mp4")) and not list(out.parent.glob("*.rich.mp4"))

    # 用途で上限を下げる: 解説は控えめまで（冒頭なし＝長さは元のまま）
    await c.put(f"/projects/{pid}/design", json={"purpose": "explainer"})
    r = await c.post("/render", json={"project_id": pid, "rich_direction": "bold", "quality": 35})
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    rich = job.result["rich_direction"]
    assert rich["level"] == "subtle" and rich["intro_frames"] == 0 and rich["notes"]
    info = await FFmpeg().probe(Path(media_settings.data_dir) / job.result["output_path"])
    assert abs(info.duration_sec - 3.0) < 0.15

    # 字幕動画は演出なし → 通常の書き出し
    await c.put(f"/projects/{pid}/design", json={"purpose": "subtitle"})
    r = await c.post("/render", json={"project_id": pid, "rich_direction": "bold", "quality": 35})
    job = await c.app.state.jobs.wait(r.json()["job_id"])
    assert job.status == "SUCCEEDED", job.error
    assert "rich_direction" not in job.result


@pytest.mark.parametrize("level", list(RichLevel))
def test_levels_have_params(level):
    from video_automation.schemas.design import Purpose

    r = plan_rich(level, PROFILES[Purpose.TRAILER])
    assert (level == RichLevel.OFF) != r.active
