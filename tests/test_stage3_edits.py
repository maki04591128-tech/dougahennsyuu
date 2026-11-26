"""段階3 W2: 利用者の直しの記録と集計。"""

from pathlib import Path

from video_automation.schemas.timeline import (
    Batch,
    ClipRead,
    ClipTransform,
    DeleteClip,
    MoveClip,
    SetClipProps,
    TimelineRead,
    TrackRead,
    TrimClip,
)
from video_automation.services.design.edits import (
    DesignDefaults,
    DesignEditLog,
    adjust_transform,
    edit_items,
    proposals,
)


def _tl() -> TimelineRead:
    def clip(cid: str, track: str, x: float = 0.0, scale: float = 1.0) -> ClipRead:
        return ClipRead(
            id=cid,
            track_id=track,
            asset_id="a",
            start_frame=30,
            end_frame=90,
            source_in_frame=0,
            source_out_frame=60,
            transform=ClipTransform(x=x, scale=scale),
            volume=1.0,
            fade_in_frames=0,
            fade_out_frames=0,
        )

    return TimelineRead(
        project_id="p",
        width=1920,
        height=1080,
        fps_num=30,
        fps_den=1,
        duration_frames=300,
        tracks=[
            TrackRead(id="t1", kind="video", name="演出・文字", index=0, muted=False),
            TrackRead(id="t2", kind="audio", name="BGM", index=1, muted=False),
            TrackRead(id="t3", kind="audio", name="ナレーション", index=2, muted=False),
        ],
        clips=[clip("kw", "t1", x=0.3, scale=0.5), clip("bgm", "t2"), clip("nar", "t3")],
        assets=[],
    )


ROLES = {"kw": "keyword"}


def test_edit_items_per_component_and_item() -> None:
    tl = _tl()
    cmd = Batch(
        commands=[
            SetClipProps(clip_id="kw", transform=ClipTransform(x=0.25, scale=0.6)),
            SetClipProps(clip_id="kw", transform=ClipTransform(x=0.25, scale=0.6, y=0.0)),
            TrimClip(clip_id="kw", end_frame=120),
            MoveClip(clip_id="kw", start_frame=45),
            SetClipProps(clip_id="bgm", volume=0.5),
            SetClipProps(clip_id="nar", volume=0.5),  # ナレーションは演出の部品ではない
        ]
    )
    got = {(x.component, x.item): x.amount for x in edit_items(tl, cmd, ROLES)}
    assert got == {
        ("keyword", "x"): -0.05,
        ("keyword", "scale"): 1.2,
        ("keyword", "duration_sec"): 1.0,
        ("keyword", "start_sec"): 0.5,
        ("bgm", "volume"): 0.5,
    }
    # 同じ値をもう一度送った 2 つ目の SetClipProps は直しに数えない（順に当てて比べる）
    assert sum(1 for x in edit_items(tl, cmd, ROLES) if x.item == "x") == 1
    dl = edit_items(tl, DeleteClip(clip_id="kw"), ROLES)
    assert [(x.component, x.item) for x in dl] == [("keyword", "deleted")]
    # 不透明度は記録しない（文字のコントラストに関わる）
    assert not edit_items(
        tl,
        SetClipProps(clip_id="kw", transform=ClipTransform(x=0.3, scale=0.5, opacity=0.5)),
        ROLES,
    )


def _rows(n: int, moved: int, dy: list[float] | None = None) -> list[dict]:
    rows = []
    for i in range(n):
        rows.append(
            {
                "ts": f"2026-11-{i + 1:02d}T00:00:00+00:00",
                "kind": "made",
                "project": f"p{i}",
                "purpose": "explainer",
                "components": ["keyword", "card"],
            }
        )
    for i in range(moved):
        rows.append(
            {
                "ts": "x",
                "kind": "edit",
                "project": f"p{i}",
                "purpose": "explainer",
                "component": "keyword",
                "item": "y",
                "amount": (dy or [-0.05] * moved)[i],
                "clip": "c1",
            }
        )
    return rows


def test_proposal_needs_five_videos_and_eighty_percent() -> None:
    ps = proposals(_rows(5, 4, [-0.04, -0.05, -0.06, -0.05]))
    assert [(p.component, p.item, p.amount, p.videos, p.agree) for p in ps] == [
        ("keyword", "y", -0.05, 5, 4)
    ]
    assert "上へ画面の高さの 5.0%" in ps[0].text
    assert not proposals(_rows(5, 3))  # 60% では出さない
    assert not proposals(_rows(4, 4))  # 4 本では出さない
    # 向きが割れていれば出さない
    assert not proposals(_rows(5, 4, [-0.05, -0.05, 0.05, 0.05]))
    # 長さの直しは記録だけで、提案しない
    rows = _rows(5, 0) + [
        {
            "ts": "x",
            "kind": "edit",
            "project": f"p{i}",
            "purpose": "explainer",
            "component": "keyword",
            "item": "duration_sec",
            "amount": 1.0,
            "clip": "c",
        }
        for i in range(5)
    ]
    assert not proposals(rows)


def test_accepted_and_rejected_are_not_proposed_again() -> None:
    rows = _rows(5, 5)
    d = DesignDefaults(accepted={"explainer": {"keyword": {"y": -0.05}}})
    assert not proposals(rows, d)
    d = DesignDefaults(rejected={"explainer/keyword/y": 5})
    assert not proposals(rows, d)
    more = (
        rows
        + _rows(10, 0)[5:]
        + [{**r, "project": f"p{i + 5}"} for i, r in enumerate(_rows(5, 5)[5:])]
    )
    assert proposals(more, d)  # 断ってから 5 本増えたらまた出す


def test_log_roundtrip_accept_and_reset(tmp_path: Path) -> None:
    log = DesignEditLog(tmp_path / "training" / "design_edits.jsonl", tmp_path / "defaults.json")
    tl = _tl()
    for i in range(5):
        log.made(f"p{i}", "explainer", ["keyword"])
        log.record(
            f"p{i}",
            "explainer",
            tl,
            SetClipProps(clip_id="kw", transform=ClipTransform(x=0.3, y=-0.04, scale=0.5)),
            ROLES,
        )
    (p,) = log.proposals()
    assert (p.component, p.item, p.amount) == ("keyword", "y", -0.04)
    d = log.accept("explainer", "keyword", "y")
    assert d.accepted == {"explainer": {"keyword": {"y": -0.04}}}
    assert log.overrides("explainer") == {"keyword": {"y": -0.04}}
    assert not log.proposals()
    log.reset("explainer")
    assert log.overrides("explainer") == {}
    # 壊れた行は飛ばす
    with (tmp_path / "training" / "design_edits.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"kind": "edit", "proj\n')
    assert len(log.rows()) == 10


def test_adjust_transform_stays_on_screen() -> None:
    tf = adjust_transform(ClipTransform(x=0.95, y=0.0, scale=0.5), {"x": 0.2, "scale": 1.2})
    assert (tf.x, tf.y, tf.scale) == (1.0, 0.0, 0.6)
    assert adjust_transform(ClipTransform(), {}) == ClipTransform()
