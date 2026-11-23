"""品質検査の測定ログ解析（P9-02 / P9-04）。"""

from video_automation.services.qa.measure import parse_log

LOG = """
[silencedetect @ 0x1] silence_start: 0
[silencedetect @ 0x1] silence_end: 0.75 | silence_duration: 0.75
[blackdetect @ 0x2] black_start:2 black_end:3.04 black_duration:1.04
[freezedetect @ 0x3] lavfi.freezedetect.freeze_start: 1
[freezedetect @ 0x3] lavfi.freezedetect.freeze_duration: 2.5
[freezedetect @ 0x3] lavfi.freezedetect.freeze_end: 3.5
[freezedetect @ 0x3] lavfi.freezedetect.freeze_start: 7
[silencedetect @ 0x1] silence_start: 8.5
[Parsed_ebur128_0 @ 0x4] Summary:

  Integrated loudness:
    I:         -14.2 LUFS
    Threshold: -24.5 LUFS

  Loudness range:
    LRA:         3.1 LU

  True peak:
    Peak:       -1.3 dBFS
"""


def test_parse_log_reads_all_measurements():
    m = parse_log(LOG, 10.0)
    assert (m.integrated_lufs, m.loudness_range, m.true_peak_dbfs) == (-14.2, 3.1, -1.3)
    assert [(s.start, s.end) for s in m.silences] == [(0.0, 0.75), (8.5, 10.0)]
    assert [(b.start, b.end) for b in m.blacks] == [(2.0, 3.04)]
    assert [(f.start, f.end) for f in m.freezes] == [(1.0, 3.5), (7.0, 10.0)]


def test_parse_log_silent_input_has_no_loudness():
    m = parse_log("Summary:\n I:         -inf LUFS\n Peak:       -inf dBFS\n", 2.0)
    assert m.integrated_lufs is None and m.true_peak_dbfs is None
    assert m.silences == [] and m.blacks == []
