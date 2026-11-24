"""ローカル LLM のスキル（SKILL.md）: 読み込み・節に分ける・関係する節を選ぶ・ユーザーの上書き。"""

from pathlib import Path

from video_automation.services.skills.library import SkillLibrary, split_sections, terms


def _skill(root: Path, name: str, desc: str, body: str) -> None:
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f'---\nname: "{name}"\ndescription: "{desc}"\n---\n\n{body}', encoding="utf-8"
    )


def test_bundled_skills_load():
    lib = SkillLibrary(None)
    names = {s.name for s in lib.all()}
    assert {"video-design-by-purpose", "blender-video-motion-design"} <= names
    for s in lib.all():
        assert s.description and s.sections and s.source == "bundled"
    assert "video-design-by-purpose:" in lib.catalog()


def test_split_sections_keeps_heading_path_and_ignores_hash_in_code():
    body = "# 題\n前書き\n## 1. 色\n本文A\n### 1.1 文字\n本文B\n```py\n# not a heading\n```\n## 2. 動き\n本文C\n"
    secs = split_sections("x", body)
    paths = [s.path for s in secs]
    assert paths == ["題", "1. 色", "1. 色 > 1.1 文字", "2. 動き"]
    assert "# not a heading" in secs[2].text


def test_terms_drop_hiragana_only_bigrams():
    t = terms("字幕が速すぎるので")
    assert "字幕" in t and "字" in t and "ので" not in t and "るの" not in t


def test_search_picks_relevant_sections_within_budget(tmp_path):
    body = (
        "# スキル\n## 1. 字幕の速さ\n字幕は 1 秒 12 文字まで。速すぎる字幕は読めない。\n"
        "```py\nprint('code')\n```\n"
        "## 2. 色\n色はベース 70%・メイン 25%・アクセント 5%。\n"
        "## 3. 情報源\n- https://example.com\n"
    )
    _skill(tmp_path, "demo", "字幕と色の決まり", body)
    lib = SkillLibrary(tmp_path, bundled=tmp_path / "none")
    ctx, picked = lib.context("字幕が速すぎるので読みやすくして")
    assert [p.path for p in picked] == ["1. 字幕の速さ"]
    assert "（コード例は省略）" in ctx and "print('code')" not in ctx
    assert lib.context("こんにちは")[0] == ""  # 関係する節が無ければ何も付けない
    # 出典の節は選ばない
    assert not any(p.path.endswith("情報源") for p in lib.search("情報源 example", min_score=0))
    # 文字数の上限
    long = "# s\n" + "".join(f"## {i}. 字幕\n" + "字幕の決まり。" * 80 + "\n" for i in range(5))
    _skill(tmp_path, "long", "字幕", long)
    lib.reload()
    picked = lib.search("字幕の決まり", budget=1500)
    assert sum(len(p.text) for p in picked) <= 1500


def test_user_skill_overrides_bundled_and_bad_names_are_skipped(tmp_path):
    bundled = tmp_path / "b"
    user = tmp_path / "u"
    _skill(bundled, "same", "同梱", "# a\n## x\n同梱の本文\n")
    _skill(user, "same", "ユーザー", "# a\n## x\nユーザーの本文\n")
    _skill(user, "Bad_Name", "x", "# a\n## x\ny\n")
    lib = SkillLibrary(user, bundled=bundled)
    s = lib.get("same")
    assert s is not None and s.source == "user" and "ユーザーの本文" in s.body
    assert lib.get("Bad_Name") is None and len(lib.all()) == 1


def test_tutorial_plan_context_includes_enhanced_defaults():
    from video_automation.services.direction.service import SKILLS_FOR_PLAN

    lib = SkillLibrary(None)
    assert "screen-tutorial-direction" in SKILLS_FOR_PLAN and lib.get("screen-tutorial-direction")
    q = "操作説明・ハウツー 手順と画面 演出 字幕 既定で入れる演出 画面録画 ズーム 章の切り替え 立ち絵 BGM 効果音"
    ctx, picked = lib.context(q, names=SKILLS_FOR_PLAN, budget=4000, min_score=0.0)
    assert any(p.skill == "screen-tutorial-direction" for p in picked)
    assert "ズーム" in ctx and "立ち絵" in ctx
