"""長いプレイ録画から見どころだけを集めて、決めた長さ（既定 15 分）以内にまとめる。

- 見どころの元はプレイ記録の印（遊ぶ AI が mark で付けたもの）。印の前後を大事さに応じて残す
- 重なる・近い区間はつなげる。収まらないときは「大事さの合計 ÷ 長さ」が高い区間から選び、
  選んだ区間は時刻の順に並べる（話の順は変えない）
- 区間のつなぎ目は 0.25 秒の暗転なしのフェード（映像・音とも）で切れ目の音のブツッを消す
- 選んだ区間の中の印は、まとめた後の時刻に付け直す（台詞の時刻合わせに使う）
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from video_automation.services.capture.session import Mark

LIMIT_SEC = 15 * 60
INTRO_SEC = 12.0  # 最初の画面（どんなゲームか分かる所）は必ず残す
MERGE_GAP = 3.0
FADE = 0.25
# 大事さごとの残す長さ（印の前, 後）
WINDOWS = {1: (3.0, 6.0), 2: (5.0, 10.0), 3: (8.0, 18.0)}


@dataclass
class Span:
    start: float
    end: float
    score: float = 0.0

    @property
    def length(self) -> float:
        return self.end - self.start


def plan_spans(marks: list[Mark], duration: float, limit: float = LIMIT_SEC) -> list[Span]:
    """残す区間（元の録画の時刻、時刻順）。録画が limit 以下なら全部。"""
    if duration <= 0:
        return []
    if duration <= limit:
        return [Span(0.0, duration, 0.0)]
    raw: list[Span] = [Span(0.0, min(INTRO_SEC, duration), 1.0)]
    for m in marks:
        if m.kind == "scene":
            continue
        imp = max(1, min(3, m.importance)) + (1 if m.kind == "highlight" else 0)
        pre, post = WINDOWS[min(3, imp)]
        raw.append(Span(max(0.0, m.t - pre), min(duration, m.t + post), float(imp * imp)))
    raw.sort(key=lambda s: s.start)
    merged: list[Span] = []
    for s in raw:
        if merged and s.start <= merged[-1].end + MERGE_GAP:
            merged[-1].end = max(merged[-1].end, s.end)
            merged[-1].score += s.score
        else:
            merged.append(Span(s.start, s.end, s.score))
    total = sum(s.length for s in merged)
    if total <= limit:
        return merged
    # 収まらない: 最初の区間は残し、残りを密度の高い順に選ぶ
    first, rest = merged[0], merged[1:]
    budget = limit - first.length
    chosen = [first]
    for s in sorted(rest, key=lambda s: (-(s.score / max(s.length, 1.0)), s.start)):
        if s.length <= budget:
            chosen.append(s)
            budget -= s.length
        elif budget >= 6.0 and s.score > 0:  # 長すぎる区間は印の多い頭の方を切って入れる
            chosen.append(Span(s.start, s.start + budget, s.score))
            budget = 0
        if budget < 3.0:
            break
    return sorted(chosen, key=lambda s: s.start)


def retime(marks: list[Mark], spans: list[Span]) -> list[Mark]:
    """選んだ区間の中の印を、まとめた後の時刻に付け直す（区間の外の印は落とす）。"""
    out: list[Mark] = []
    offset = 0.0
    for s in spans:
        for m in marks:
            if s.start <= m.t < s.end:
                out.append(Mark(round(offset + m.t - s.start, 2), m.text, m.kind, m.importance))
        offset += s.length
    return out


def ffmpeg_args(src: Path, dst: Path, spans: list[Span], has_audio: bool = True) -> list[str]:
    """区間を切ってつなぐ FFmpeg の引数（つなぎ目は短いフェード）。"""
    parts: list[str] = []
    labels: list[str] = []
    for i, s in enumerate(spans):
        d = s.length
        f = min(FADE, d / 4)
        parts.append(
            f"[0:v]trim=start={s.start:.3f}:end={s.end:.3f},setpts=PTS-STARTPTS,"
            f"fade=t=in:st=0:d={f:.3f},fade=t=out:st={d - f:.3f}:d={f:.3f}[v{i}]"
        )
        labels.append(f"[v{i}]")
        if has_audio:
            parts.append(
                f"[0:a]atrim=start={s.start:.3f}:end={s.end:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={f:.3f},afade=t=out:st={d - f:.3f}:d={f:.3f}[a{i}]"
            )
            labels.append(f"[a{i}]")
    n = len(spans)
    tail = "[a]" if has_audio else ""
    parts.append("".join(labels) + f"concat=n={n}:v=1:a={1 if has_audio else 0}[v]{tail}")
    args = ["-v", "error", "-y", "-i", str(src), "-filter_complex", ";".join(parts), "-map", "[v]"]
    if has_audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    args += ["-r", "30", "-movflags", "+faststart", str(dst)]
    return args


def reference_text(marks: list[Mark]) -> str:
    """動画編集ツールの参考資料に入れるプレイ記録（見出しつき）。"""
    head = "プレイ記録（時刻は録画の頭から）:"
    lines = [head]
    for m in marks:
        tag = {"highlight": "★", "comment": "感想", "scene": "場面"}.get(m.kind, "")
        mm, ss = divmod(int(m.t), 60)
        lines.append(f"[{mm}:{ss:02d}] {tag + ' ' if tag else ''}{m.text}")
    return "\n".join(lines)


def summary(spans: list[Span], marks: list[Mark], src_duration: float) -> dict[str, Any]:
    return {
        "source_duration_sec": round(src_duration, 2),
        "duration_sec": round(sum(s.length for s in spans), 2),
        "spans": [asdict(s) for s in spans],
        "marks": [asdict(m) for m in marks],
        "reference": reference_text(marks),
    }


def write_summary(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
