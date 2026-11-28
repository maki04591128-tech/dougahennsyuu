"""MV・リリックの題名は、指示の文ではなく曲名（「」『』）か曲のファイル名。"""

from pathlib import Path

from video_automation.services.build.service import _song_title


def test_song_title_from_quotes_or_file_name():
    assert _song_title("「夜明けの歌」の MV を作って", Path("C:/m/x.wav")) == "夜明けの歌"
    assert _song_title("『星の海』のリリックビデオ", Path("/m/a.mp3")) == "星の海"
    assert _song_title("この曲の MV を作って", Path("/m/my_new-song.wav")) == "my new song"
    assert _song_title("この曲の MV を作って", Path("/m/__.wav")) == "新しい曲"
    assert len(_song_title("MV", Path("/m/" + "a" * 80 + ".wav"))) == 40
