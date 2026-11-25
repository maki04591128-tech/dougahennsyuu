"""文字起こしのモデルを、よく置く場所から見つける（ダウンロードはしない）。"""

from video_automation.app.bootstrap import _find_whisper
from video_automation.app.settings import Settings


def test_finds_model_next_to_the_app(tmp_path):
    app = tmp_path / "wakuwaku" / "app"
    s = Settings(data_dir=app / "data")
    assert _find_whisper(s) is None
    small = tmp_path / "wakuwaku" / "models" / "faster-whisper-small"
    large = tmp_path / "wakuwaku" / "models" / "faster-whisper-large-v3"
    for d in (small, large):
        d.mkdir(parents=True)
        (d / "model.bin").write_bytes(b"x")
    assert _find_whisper(s) == large  # large-v3 を優先
    (large / "model.bin").unlink()
    assert _find_whisper(s) == small
