from video_automation.schemas.direction import DirectionPlan, Shot, ShotKind
from video_automation.services.direction.plan import normalize


def test_card_items_with_speaker_names_are_dropped():
    plan = DirectionPlan(
        title="熱中症",
        shots=[
            Shot(
                kind=ShotKind.CARD,
                from_line=1,
                heading="熱中症の怖さ",
                body=["ずんだもん、四国めたん、熱中症の怖さ", "水分をこまめに"],
            )
        ],
    )
    lines = ["熱中症は怖いのだ", "水はこまめに飲むわ"]
    out = normalize(plan, lines, "熱中症", ("ずんだもん", "四国めたん"))
    assert out.shots[0].body == ["水分をこまめに"]
