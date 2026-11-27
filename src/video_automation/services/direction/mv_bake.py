"""MV のショットのカットを 1 本の動画に焼く（統合版 V1・V2 の書き出しの負荷対策）。

書き出しは、クリップ 1 つごとに FFmpeg の入力を 1 つ開く。1 枚の絵の入力でも 1080p で
20〜45MB のメモリを使うので、4 分の MV（拍ごとのカットで 300〜400 カット）を
そのまま書き出すと 10GB を超える。カットを GROUP 個ずつの部分の動画に書き出してから
つなぎ、タイムラインには 1 本の動画として置く。

カメラ（ズーム・パン）は書き出しと同じ式（render_anim.clip_filters）で付ける。
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from video_automation.schemas.animation import ClipAnimation, Keyframe
from video_automation.services.animation.render_anim import clip_filters

GROUP = 40  # 1 回の FFmpeg で開く絵の数の上限
CRF = "14"  # 中間の動画（書き出しでもう一度圧縮するので画質を高めに）


def _num(x: float) -> str:
    return f"{x:.6f}".rstrip("0").rstrip(".")


def group_args(
    items: list[tuple[Path, int, list[Keyframe]]],
    fps: Fraction,
    w: int,
    h: int,
    out: Path,
    pad_last: int = 0,
) -> list[str]:
    """カット（絵, フレーム数, カメラ）を順につないだ動画を作る FFmpeg の引数（-y は除く）。

    pad_last: 最後のカットを止めたまま延ばすフレーム数（動画の長さの丸めで 1 フレーム
    足りなくならないように、全体の最後の部分だけ 1 にする）。
    """
    fps_s = f"{fps.numerator}/{fps.denominator}"
    inputs: list[str] = []
    filters: list[str] = []
    for i, (path, n, keys) in enumerate(items):
        dur = float(Fraction(n + 2) / fps)  # 少し長く読み、trim でちょうど n にする
        inputs += ["-loop", "1", "-framerate", fps_s, "-t", _num(dur), "-i", str(path)]
        pre: list[str] = []
        if keys:
            af = clip_filters(
                ClipAnimation(keyframes=keys),
                fps=fps,
                k=0.0,
                off=0.0,
                clip_sec=float(Fraction(n) / fps),
                src_size=(w, h),
                canvas=(w, h),
                is_image=True,
            )
            pre = af.pre
        # fps フィルターは使わない（1〜2 フレームの短いカットで最後のフレームを落とすため）。
        # 入力は -framerate で 1 フレームずつ出るので、trim でフレーム数を数える
        chain = [f"trim=end_frame={n}", "setpts=N/FRAME_RATE/TB", *pre]
        if pad_last and i == len(items) - 1:
            chain.append(f"tpad=stop_mode=clone:stop={pad_last}")
        chain += [f"scale={w}:{h}", "setsar=1", "format=yuv420p"]
        filters.append(f"[{i}:v]{','.join(chain)}[c{i}]")
    labels = "".join(f"[c{i}]" for i in range(len(items)))
    # 1 フレームだけのカットは長さ 0 として次のカットと同じ時刻になり、書き出しで落ちる。
    # つないだ後に、1 フレームずつ時刻を振り直す
    tb = f"{fps.denominator}/{fps.numerator}"
    filters.append(f"{labels}concat=n={len(items)}:v=1:a=0,settb={tb},setpts=N[out]")
    return [
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[out]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        CRF,
        "-pix_fmt",
        "yuv420p",
        "-r",
        fps_s,
        str(out),
    ]


def groups(n: int, size: int = GROUP) -> list[tuple[int, int]]:
    """n 個のカットを size 個ずつに分けた範囲。"""
    return [(a, min(n, a + size)) for a in range(0, n, size)]


def concat_list(parts: list[Path]) -> str:
    """concat デマルチプレクサの一覧（パスの ' は '\\'' に置き換える）。"""
    lines = []
    for p in parts:
        s = str(p.resolve()).replace("'", "'\\''")
        lines.append(f"file '{s}'")
    return "\n".join(lines) + "\n"
