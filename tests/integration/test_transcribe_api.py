"""文字起こし（⑤）: モデルを使わず、認識結果を差し替えて流れを確かめる。"""

from video_automation.services.transcribe.align import Word, align_lyrics, lines_from_segments
from video_automation.services.transcribe.service import Recognized


def test_align_lyrics_takes_text_from_lyrics_and_time_from_recognition():
    words = [
        Word("よるを", 0.0, 1.0),
        Word("こえて", 1.0, 2.0),
        Word("ぴかりの", 3.0, 4.0),  # 誤認識（光の→ぴかりの）
        Word("ほうへ", 4.0, 5.0),
    ]
    lines = align_lyrics(["夜をこえて", "", "光のほうへ", "ラララ"], words)
    assert [x.text for x in lines] == ["夜をこえて", "光のほうへ", "ラララ"]
    a, b, c = lines
    assert a.start < 0.5 and abs(a.end - 2.0) < 0.01
    assert 3.0 <= b.start < 3.6 and abs(b.end - 5.0) < 0.01
    assert c.matched == 0.0 and c.start >= b.end  # 一致の無い行は後ろに置く
    assert all(x.end > x.start for x in lines)


def test_align_lyrics_without_recognized_words_spreads_over_song():
    """歌声が聞き取れなかったときは曲の長さに等分する（冒頭 0.6 秒ずつに詰め込まない）。"""
    lines = align_lyrics(["一行目", "二行目", "三行目", "四行目"], [], duration=40.0)
    assert [round(x.start, 2) for x in lines] == [0.0, 10.0, 20.0, 30.0]
    assert abs(lines[-1].end - 40.0) < 0.01 and all(x.matched == 0.0 for x in lines)
    # 曲の長さも分からなければ従来どおり 0.6 秒ずつ
    assert abs(align_lyrics(["a", "b"], [])[-1].end - 1.2) < 0.01


def test_align_lyrics_unmatched_starts_at_first_recognized_word():
    words = [Word("らららら", 12.0, 14.0), Word("るるるる", 30.0, 32.0)]
    lines = align_lyrics(["夜をこえて", "光のほうへ"], words, duration=60.0)
    assert lines[0].start == 12.0 and abs(lines[-1].end - 32.0) < 0.01


def test_lines_from_segments_skips_empty():
    out = lines_from_segments([(" こんにちは ", 0.0, 1.0), ("  ", 1.0, 2.0), ("世界", 2.0, 3.0)])
    assert [(x.text, x.start) for x in out] == [("こんにちは", 0.0), ("世界", 2.0)]


async def test_transcribe_creates_script_and_timed_subtitles(mclient, media_dir):
    c = mclient
    pid = (await c.post("/projects", json={"name": "MV"})).json()["id"]
    asset = (
        await c.post(
            "/assets/import", json={"project_id": pid, "path": str(media_dir / "clip.mp4")}
        )
    ).json()
    # モデル未設定なら使えないと答える
    st = (await c.get("/transcribe/status")).json()
    assert st["model"] is False and st["available"] is False

    calls = []

    def fake(audio, language):
        calls.append((len(audio) > 16000 * 2, language))  # 3 秒の音声が 16kHz で届く
        return Recognized(
            words=[Word("よるを", 0.2, 1.0), Word("こえて", 1.0, 1.8), Word("ほうへ", 2.0, 2.8)],
            segments=[("夜を越えて", 0.2, 1.8), ("方へ", 2.0, 2.8)],
            language="ja",
            duration=3.0,
        )

    c.app.state.container.transcriber._recognizer = fake
    await c.put(f"/projects/{pid}/design", json={"purpose": "mv"})
    r = await c.post(
        "/transcribe",
        json={"project_id": pid, "asset_id": asset["id"], "lyrics": "夜をこえて\n光のほうへ"},
    )
    assert r.status_code == 202
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "SUCCEEDED", job.error
    res = job.result
    assert calls == [(True, "ja")] and [x["text"] for x in res["lines"]] == [
        "夜をこえて",
        "光のほうへ",
    ]
    cues = (await c.get(f"/subtitles/{res['script_id']}")).json()
    assert [x["text"] for x in cues] == ["夜をこえて", "光のほうへ"]
    assert cues[0]["start_frame"] < cues[0]["end_frame"] <= cues[1]["start_frame"]
    # MV の型なので歌詞の見た目で書き出される（字幕ではなく歌詞として検査される）
    chk = (
        await c.get(f"/projects/{pid}/design/check", params={"script_id": res["script_id"]})
    ).json()
    assert chk["used"].get("lyric_text") == 2 and "subtitle" not in chk["used"]
    # 文字起こしだけ（歌詞なし）
    r = await c.post("/transcribe", json={"project_id": pid, "asset_id": asset["id"]})
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert [x["text"] for x in job.result["lines"]] == ["夜を越えて", "方へ"]
    # 存在しない素材
    r = await c.post("/transcribe", json={"project_id": pid, "asset_id": "nope"})
    job = await c.app.state.jobs.wait(r.json()["id"])
    assert job.status == "FAILED"


async def test_long_lines_are_split_by_profile(mclient, media_dir):
    c = mclient
    pid = (await c.post("/projects", json={"name": "MV2"})).json()["id"]
    asset = (
        await c.post(
            "/assets/import", json={"project_id": pid, "path": str(media_dir / "clip.mp4")}
        )
    ).json()
    long = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほ"  # 30 字

    def fake(audio, language):
        return Recognized(words=[], segments=[(long, 0.0, 3.0)], language="ja", duration=3.0)

    c.app.state.container.transcriber._recognizer = fake
    await c.put(f"/projects/{pid}/design", json={"purpose": "mv"})  # 歌詞は 1 行 20 字まで
    r = await c.post("/transcribe", json={"project_id": pid, "asset_id": asset["id"]})
    job = await c.app.state.jobs.wait(r.json()["id"])
    cues = (await c.get(f"/subtitles/{job.result['script_id']}")).json()
    assert len(cues) == 2 and all(len(x["text"]) <= 20 and "\n" not in x["text"] for x in cues)
    assert cues[0]["start_frame"] == 0 and cues[-1]["end_frame"] == 90
    assert cues[0]["end_frame"] == cues[1]["start_frame"]


def test_whisper_retries_without_vad_when_nothing_heard(tmp_path):
    """伴奏つきの歌声を VAD が落として何も聞き取れなかったら、VAD なしで試し直す。"""
    from types import SimpleNamespace

    from video_automation.services.transcribe.service import WhisperRecognizer

    calls = []

    class Model:
        def transcribe(self, audio, **kw):
            calls.append(kw["vad_filter"])
            info = SimpleNamespace(language="ja", duration=10.0)
            if kw["vad_filter"]:
                return iter([]), info
            w = SimpleNamespace(word="ひかり", start=2.0, end=3.0)
            return iter([SimpleNamespace(text="ひかり", start=2.0, end=3.0, words=[w])]), info

    r = WhisperRecognizer(tmp_path)
    r._model = Model()
    rec = r._run(None, "ja")
    assert calls == [True, False] and [w.text for w in rec.words] == ["ひかり"]
