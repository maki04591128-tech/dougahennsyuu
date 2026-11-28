"""書き出す前のデザイン検査（ガード）。

作った要素の記録（DesignManifest）を用途の型と突き合わせ、別の用途の部品・型の外の色や書体・
読めない字幕を見つける。error が 1 つでもあれば書き出しを止める（例外は ProjectDesign.extras）。
"""

import colorsys
import re
from dataclasses import dataclass, field
from typing import Any

from video_automation.schemas.design import (
    ANY,
    Component,
    DesignCheck,
    DesignProfile,
    Purpose,
    Violation,
)

LABELS = {
    Component.SUBTITLE: "字幕",
    Component.SPEAKER_LABEL: "話者名",
    Component.TITLE_CARD: "題名カード",
    Component.CARD: "要点カード",
    Component.CHART: "図表",
    Component.IMAGE: "画像",
    Component.KEYWORD: "要点テロップ",
    Component.CHAPTER_LABEL: "章ラベル",
    Component.LOWER_THIRD: "名前・肩書きの帯",
    Component.EMPHASIS_TEXT: "強調テロップ",
    Component.KINETIC_TEXT: "1 文字ずつ動く文字",
    Component.LYRIC_TEXT: "歌詞",
    Component.BACKGROUND_MOTION: "背景の動き",
    Component.TRANSITION_CUT: "カット・クロスフェード",
    Component.TRANSITION_SHAPE: "図形・パネルの転換",
    Component.TRANSITION_WHIP: "ウィップパン・ズームブラー",
    Component.SHAKE_PUNCH: "揺れ・ズームパンチ",
    Component.FLASH: "フラッシュ",
    Component.GLITCH: "グリッチ・色ずれ",
    Component.BLOOM: "光のにじみ",
    Component.LETTERBOX: "黒帯",
    Component.BEAT_CUT: "拍に合わせたカット",
    Component.SFX: "演出の効果音",
    Component.CHARACTER: "立ち絵・キャラ",
}


@dataclass
class Placement:
    """画面に置いた文字の範囲（画面比 0〜1）。"""

    component: Component
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class SubtitleUse:
    size_ratio: float  # 文字の高さ（短辺比）
    band: str  # none / fitted / full
    font: str
    lines: list[tuple[str, float]] = field(default_factory=list)  # (文, 表示秒)
    # 各字幕の元の音声の速さ（字/秒）。音声に合わせていない字幕は None
    voice_rates: list[float | None] = field(default_factory=list)


@dataclass
class TextUse:
    """画面に出した文字のまとまり（カード・テロップ）。sizes は文字の大きさ（短辺比）の一覧、
    start・end は表示の秒。"""

    kind: str
    sizes: list[float]
    chars: int
    start: float
    end: float


@dataclass
class ClipMotion:
    """映像のクリップ 1 つの時間と動き（キーフレームは (秒, キーフレームの dict)）。"""

    track: str
    start: float
    end: float
    keys: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    preset: str | None = None


@dataclass
class DesignManifest:
    used: dict[Component, int] = field(default_factory=dict)
    colors: list[str] = field(default_factory=list)  # 使った色（#RRGGBB）
    fonts: set[str] = field(default_factory=set)
    subtitle: SubtitleUse | None = None
    placements: list[Placement] = field(default_factory=list)
    vertical: bool = False
    empty_spans: list[tuple[float, float]] = field(default_factory=list)  # 背景だけの区間（秒）
    transitions: list[tuple[float, str]] = field(default_factory=list)  # (秒, 切り替えの種類)
    duration_sec: float = 0.0
    size: tuple[int, int] = (1920, 1080)
    clips: list[ClipMotion] = field(default_factory=list)  # 映像のクリップ（動きの検査）
    texts: list[TextUse] = field(default_factory=list)  # カード・テロップの文字（文字の検査）
    end_screen_at: float | None = None  # 終了画面の始まり（秒。統合版 S9。変化の間隔の検査の外）
    parts: list[str] = field(default_factory=list)  # 置いた 5d の部品（統合版 Z2 の検査）

    def add(self, c: Component, n: int = 1) -> None:
        self.used[c] = self.used.get(c, 0) + n


def is_dark(color: str) -> bool:
    """色が暗いか（相対輝度 0.4 未満）。文字の縁取りを明暗どちらにするかに使う。"""
    h = color.lstrip("#")[-6:]
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.4


def relative_luminance(color: str) -> float:
    """WCAG の相対輝度（sRGB → 線形 → Rec.709 の係数）。"""
    h = color.lstrip("#")[-6:]
    c = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast_ratio(a: str, b: str) -> float:
    """WCAG のコントラスト比（1〜21）。"""
    la, lb = relative_luminance(a), relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


TEXT_CONTRAST = 4.5  # 本文の文字（統合版 G1）
LARGE_TEXT_CONTRAST = 3.0  # 大きな文字（画面の高さの 5.5% 以上）
LARGE_TEXT_RATIO = 0.055


def min_contrast(size_ratio: float) -> float:
    """文字の大きさ（画面の高さ比）に対する最小のコントラスト比。"""
    return LARGE_TEXT_CONTRAST if size_ratio >= LARGE_TEXT_RATIO else TEXT_CONTRAST


def palette_contrast(profile: DesignProfile) -> list[Violation]:
    """型の配色で、文字に使う色が背景（base・base2）に対して読めるか（統合版 G1）。

    文字に使う色は text・text_muted・main・accent・話者・図表。どれも本文の大きさで
    使い得るので、本文の基準（4.5）で調べる。"""
    pal = profile.palette
    roles = {
        "文字": [pal.text],
        "弱い文字": [pal.text_muted],
        "メイン": [pal.main],
        "アクセント": [pal.accent],
        "話者": pal.speakers,
        "図表": pal.chart,
    }
    out: list[Violation] = []
    seen: set[str] = set()
    for role, colors in roles.items():
        for c in colors:
            if c.upper() in seen:
                continue
            seen.add(c.upper())
            worst = min(contrast_ratio(c, pal.base), contrast_ratio(c, pal.base2))
            if worst < TEXT_CONTRAST:
                out.append(
                    Violation(
                        code="contrast",
                        message=(
                            f"「{profile.label}」の{role}の色 {c} は背景とのコントラスト比が"
                            f" {worst:.2f} です（{TEXT_CONTRAST:g} 以上が必要）"
                        ),
                    )
                )
    return out


# 統合版 S8: 色覚の検査。Machado 2009（強さ 1.0）の行列で、線形 RGB を P型・D型の見え方に変える
_MACHADO = {
    "P型": (
        (0.152286, 1.052583, -0.204868),
        (0.114503, 0.786281, 0.099216),
        (-0.003882, -0.048116, 1.051998),
    ),
    "D型": (
        (0.367322, 0.860646, -0.227968),
        (0.280085, 0.672501, 0.047413),
        (-0.011820, 0.042940, 0.968881),
    ),
}
CVD_MIN_DELTA_E = 15.0


def _lin(color: str) -> tuple[float, float, float]:
    h = color.lstrip("#")[-6:]
    c = [int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    r, g, b = (x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)
    return r, g, b


def _lab(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    """線形 sRGB → CIELAB（D65）。"""
    r, g, b = (min(max(v, 0.0), 1.0) for v in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 216 / 24389 else (24389 / 27 * t + 16) / 116

    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def cvd_delta_e(a: str, b: str, kind: str) -> float:
    """2 色を P型・D型の見え方にしたときの ΔE76。"""
    m = _MACHADO[kind]

    def sim(c: str) -> tuple[float, float, float]:
        v = _lin(c)
        return tuple(sum(m[i][j] * v[j] for j in range(3)) for i in range(3))  # type: ignore[return-value]

    la, lb = _lab(sim(a)), _lab(sim(b))
    return sum((p - q) ** 2 for p, q in zip(la, lb, strict=True)) ** 0.5


def delta_e(a: str, b: str) -> float:
    """2 色の ΔE76（ふつうの色覚）。"""
    la, lb = _lab(_lin(a)), _lab(_lin(b))
    return sum((p - q) ** 2 for p, q in zip(la, lb, strict=True)) ** 0.5


def confusable(a: str, b: str, limit: float = CVD_MIN_DELTA_E) -> bool:
    """ふつうの色覚・P型・D型のどれかで ΔE76 が limit 未満か（統合版 3節の衝突の決まり）。"""
    return delta_e(a, b) < limit or any(cvd_delta_e(a, b, k) < limit for k in _MACHADO)


def color_vision(profile: DesignProfile) -> list[Violation]:
    """図表・話者の色の組が、P型・D型の色覚で見分けられるか（統合版 S8。ΔE76 15 未満は注意）。"""
    pal = profile.palette
    out: list[Violation] = []
    for role, colors in (("図表", pal.chart), ("話者", pal.speakers)):
        uniq = list(dict.fromkeys(c.upper() for c in colors))
        for i, a in enumerate(uniq):
            for b in uniq[i + 1 :]:
                for kind in _MACHADO:
                    d = cvd_delta_e(a, b, kind)
                    if d < CVD_MIN_DELTA_E:
                        out.append(
                            Violation(
                                code="color_vision",
                                severity="warning",
                                message=(
                                    f"「{profile.label}」の{role}の色 {a} と {b} は、{kind}の色覚で"
                                    f"見分けにくい（ΔE {d:.1f}。{CVD_MIN_DELTA_E:g} 以上にする）"
                                ),
                            )
                        )
    return out


def font_key(name: str) -> str:
    """書体名・ファイル名を同じ書体の比較用に揃える（meiryob / Meiryo → meiryo）。"""
    k = re.sub(r"[\s\-_]", "", name.lower()).replace("udp", "ud")
    k2 = re.sub(r"(bold|regular|b|r)$", "", k)
    return k2 if len(k2) >= 4 else k


def hue_groups(colors: list[str], step: float = 30.0) -> list[float]:
    """有彩色（彩度・明度がある色）の色相を step 度ごとにまとめた代表値。"""
    groups: list[float] = []
    for c in colors:
        h = c.lstrip("#")[-6:]
        r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
        hue, light, sat = colorsys.rgb_to_hls(r, g, b)
        if sat < 0.25 or light < 0.12 or light > 0.95:
            continue  # 白・黒・灰色に近い色は色相に数えない
        deg = hue * 360
        if not any(min(abs(deg - x), 360 - abs(deg - x)) < step for x in groups):
            groups.append(deg)
    return groups


# 穏やかな切り替え（統合版 E1 の「強い切り替え」に数えない）
SOFT_TRANSITIONS = frozenset({"dissolve", "dip_to_black"})


def _max_per_minute(times: list[float]) -> int:
    """どの 60 秒の区間にも入る数の最大。"""
    best, j = 0, 0
    for i, t in enumerate(times):
        while times[j] <= t - 60.0:
            j += 1
        best = max(best, i - j + 1)
    return best


def transition_rate(profile: DesignProfile, m: DesignManifest) -> list[Violation]:
    """切り替えの数の上限（統合版 E1）。1 分あたりの数と、強い切り替えの 1 本あたりの数。"""
    spec = profile.motion
    out: list[Violation] = []
    times = [t for t, _ in m.transitions]
    strong = [t for t, k in m.transitions if k not in SOFT_TRANSITIONS]
    label = profile.label
    if spec.transitions_per_min is not None:
        n = _max_per_minute(times)
        if n > spec.transitions_per_min:
            out.append(
                Violation(
                    code="transition_rate",
                    severity="warning",
                    message=(
                        f"切り替えが多すぎます（1 分に {n} 回）。「{label}」は 1 分に "
                        f"{spec.transitions_per_min} 回までにすると落ち着いて見られます"
                    ),
                )
            )
    if spec.strong_per_min is not None:
        n = _max_per_minute(strong)
        if n > spec.strong_per_min:
            out.append(
                Violation(
                    code="strong_transition",
                    severity="warning",
                    message=(
                        f"強い切り替え（スライド・ワイプ・ズーム）が 1 分に {n} 回あります。"
                        f"「{label}」は {spec.strong_per_min} 回までです"
                    ),
                )
            )
    if spec.strong_total is not None and len(strong) > spec.strong_total:
        out.append(
            Violation(
                code="strong_transition",
                severity="warning",
                message=(
                    f"強い切り替えが {len(strong)} 回あります。「{label}」は 1 本で "
                    f"{spec.strong_total} 回まで（それ以外はディゾルブかカット）にします"
                ),
            )
        )
    return out


# 統合版 2.1: 動きの量（G2）を検査する用途（1 要素の移動量が短辺の 10% を超えたら注意）
CALM_MOVE = frozenset(
    {
        Purpose.SUBTITLE,
        Purpose.INTERVIEW,
        Purpose.DOCUMENTARY,
        Purpose.STORY,
        Purpose.PODCAST,
        Purpose.EXPLAINER,
        Purpose.LECTURE,
        Purpose.TUTORIAL,
        Purpose.RECIPE,
        Purpose.NEWS,
        Purpose.CORPORATE,
    }
)
MOVE_MAX = 0.10  # 短辺に対する比
MAX_MOVING = 2  # 同時に動いている要素の上限（M4）
EXPRESSIVE_MOVING = frozenset({Purpose.MV, Purpose.LYRIC, Purpose.TRAILER})  # 超えても注意だけ
ELEMENT_KEYS = ("scale", "x", "y", "rotation", "opacity")
KEN_BURNS = frozenset(
    {"ken_burns_in", "ken_burns_out", "pan_left", "pan_right", "pan_up", "pan_down"}
)


def _moving_spans(c: ClipMotion) -> list[tuple[float, float]]:
    """要素のプロパティが変わっている区間（秒。タイムライン上）。カメラ（ズーム・パン）は数えない。"""
    out: list[tuple[float, float]] = []
    for prop in ELEMENT_KEYS:
        pts = [(t, k[prop]) for t, k in c.keys if k.get(prop) is not None]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:], strict=False):
            if abs(v1 - v0) > 1e-6 and t1 > t0:
                out.append((c.start + t0, c.start + t1))
    return out


def motion_checks(profile: DesignProfile, m: DesignManifest) -> list[Violation]:
    """動きの量（G2）・同じ動きの連続（G3）・同時に動く要素（M4）・変化の間隔（Q2）。"""
    out: list[Violation] = []
    label = profile.label
    w, h = m.size
    short = max(1, min(w, h))
    # G2: 1 要素の移動量（x・y は画面の幅・高さに対する比）
    if profile.purpose in CALM_MOVE:
        worst = 0.0
        for c in m.clips:
            for ax, dim in (("x", w), ("y", h)):
                vals = [k[ax] for _, k in c.keys if k.get(ax) is not None]
                if len(vals) >= 2:
                    worst = max(worst, (max(vals) - min(vals)) * dim / short)
        if worst > MOVE_MAX + 1e-9:
            out.append(
                Violation(
                    code="motion_amount",
                    severity="warning",
                    message=(
                        f"「{label}」で、画面の短辺の {worst:.0%} 動く要素があります"
                        f"（{MOVE_MAX:.0%} までにすると落ち着いて見られます）"
                    ),
                )
            )
    # G3: 同じトラックで同じ向きのケン・バーンズが 2 回続く
    by_track: dict[str, list[ClipMotion]] = {}
    for c in m.clips:
        by_track.setdefault(c.track, []).append(c)
    for clips in by_track.values():
        kb = [c for c in sorted(clips, key=lambda c: c.start) if c.preset in KEN_BURNS]
        for a, b in zip(kb, kb[1:], strict=False):
            if a.preset == b.preset:
                out.append(
                    Violation(
                        code="repeated_motion",
                        severity="warning",
                        message=f"同じ向きのケン・バーンズ（{a.preset}）が 2 回続いています",
                    )
                )
                break
    # M4: 同時に動いている要素の数
    events: list[tuple[float, int]] = []
    for c in m.clips:
        for a, b in _merge(_moving_spans(c)):
            events += [(a, 1), (b, -1)]
    cur = peak = 0
    at = 0.0
    for t, d in sorted(events, key=lambda e: (e[0], e[1])):
        cur += d
        if cur > peak:
            peak, at = cur, t
    if peak > MAX_MOVING:
        out.append(
            Violation(
                code="concurrent_motion",
                severity="warning" if profile.purpose in EXPRESSIVE_MOVING else "error",
                message=(
                    f"{at:.1f} 秒で {peak} つの要素が同時に動いています"
                    f"（{MAX_MOVING} つまでにすると目が追えます）"
                ),
            )
        )
    # Q2: 新しい要素の登場・動き・切り替えの間隔の最大
    limit = profile.motion.change_interval_sec
    dur = m.duration_sec if m.end_screen_at is None else min(m.duration_sec, m.end_screen_at)
    if limit is not None and dur > 0:
        times = sorted(
            {round(c.start, 3) for c in m.clips}
            | {round(a, 3) for c in m.clips for a, _ in _moving_spans(c)}
            | {round(t, 3) for t, _ in m.transitions}
        )
        times = [t for t in times if 0 <= t <= dur] + [dur]
        gaps = [(b - a, a) for a, b in zip([0.0, *times], times, strict=False)]
        gap, start = max(gaps) if gaps else (0.0, 0.0)
        if gap > limit + 1e-6:
            out.append(
                Violation(
                    code="change_interval",
                    severity="warning",
                    message=(
                        f"{start:.1f} 秒から {gap:.1f} 秒間、画面が変わりません"
                        f"（「{label}」は {limit:g} 秒ごとに何かを変えると飽きにくい）"
                    ),
                )
            )
    return out


# 統合版 T39・T40・T52
MIN_TEXT = 0.035  # 読ませる文字の最小の大きさ（画面の短辺比。1080p で 38px）
MAX_TEXT_SIZES = 4  # 同時に出ている文字の大きさの種類の上限（差が 10% 未満は同じ種類）
NO_READ_CHECK = frozenset({Purpose.MV, Purpose.LYRIC})  # 歌詞は声と同時に読むので T39 を除く
READ_IN_MIN_SEC = 4 / 30  # 足りないときは入りを 4f に縮めて計算し直す


def _size_classes(sizes: list[float]) -> int:
    """差が 10% 未満の大きさを同じ種類として数える。"""
    n, last = 0, -1.0
    for s in sorted(sizes):
        if last <= 0 or s > last * 1.1:
            n += 1
            last = s
    return n


def text_checks(profile: DesignProfile, m: DesignManifest) -> list[Violation]:
    """読める静止の長さ（T39）・読ませる文字の最小の大きさ（T40）・大きさの段数（T52）。"""
    out: list[Violation] = []
    label = profile.label
    small = [t for t in m.texts if min(t.sizes, default=1.0) < MIN_TEXT - 1e-6]
    if m.subtitle is not None and m.subtitle.size_ratio < MIN_TEXT - 1e-6:
        out.append(
            Violation(
                code="text_too_small",
                severity="warning",
                message=f"字幕の文字が小さすぎます（画面の短辺の {m.subtitle.size_ratio:.1%}。"
                f"{MIN_TEXT:.1%} 以上にする）",
            )
        )
    if small:
        worst = min(min(t.sizes) for t in small)
        out.append(
            Violation(
                code="text_too_small",
                severity="warning",
                message=(
                    f"小さすぎて読めない文字が {len(small)} か所あります（最小は画面の短辺の "
                    f"{worst:.1%}。{MIN_TEXT:.1%} 以上にする）"
                ),
            )
        )
    if profile.purpose not in NO_READ_CHECK:
        cps = profile.subtitle.chars_per_sec
        in_s, out_s = profile.motion.in_frames / 30, profile.motion.out_frames / 30
        short = []
        for t in m.texts:
            need = t.chars / cps
            still = (t.end - t.start) - in_s - out_s
            if still < need and (t.end - t.start) - READ_IN_MIN_SEC - out_s < need:
                short.append(t)
        if short:
            out.append(
                Violation(
                    code="text_too_short",
                    severity="warning",
                    message=(
                        f"読み切れないほど短く出る文字が {len(short)} か所あります"
                        f"（「{label}」は 1 秒 {cps:g} 字で読める長さにする）"
                    ),
                )
            )
    # T52: 同時に出ている文字の大きさの種類（字幕は 1 種類として数える）
    sub = [m.subtitle.size_ratio] if m.subtitle is not None else []
    times = sorted({t.start for t in m.texts})
    worst_n, at = 0, 0.0
    for t0 in times:
        sizes = sub + [s for t in m.texts if t.start <= t0 < t.end for s in t.sizes]
        n = _size_classes(sizes)
        if n > worst_n:
            worst_n, at = n, t0
    if worst_n > MAX_TEXT_SIZES:
        out.append(
            Violation(
                code="text_sizes",
                severity="warning",
                message=(
                    f"{at:.1f} 秒で、文字の大きさが {worst_n} 種類あります"
                    f"（{MAX_TEXT_SIZES} 種類までにすると整って見えます）"
                ),
            )
        )
    return out


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


OPENING_FRAMES = 15  # 統合版 S5: 縦型の最初の 15f（30fps）に入りの動き


def opening_motion(m: DesignManifest) -> bool:
    """最初の 15f のうちに、要素が入ってくる動き（不透明度・位置・拡大のキー・題名の入り）が
    あるか。ケン・バーンズ（素材の中で寄る・動く）は数えない。"""
    limit = OPENING_FRAMES / 30
    # 題名（Blender で書き出した動画）は入りの動き（T16）を画の中に持つので、クリップに
    # キーフレームが無くても入りの動きに数える（縦型ショートで題名が入っているのに warning が出た）
    if any(t.kind == "title" and t.start < limit for t in m.texts):
        return True
    for c in m.clips:
        if c.start >= limit or c.preset in KEN_BURNS:
            continue
        if any(lo < limit for lo, _hi in _moving_spans(c)):
            return True
    return False


def check(
    profile: DesignProfile, m: DesignManifest, extras: list[Component] | None = None
) -> DesignCheck:
    extras = extras or []
    out: list[Violation] = []
    label = profile.label
    for c, n in sorted(m.used.items(), key=lambda kv: kv[0].value):
        if n <= 0:
            continue
        allow = profile.allowance(c)
        if c in extras:
            continue
        if allow == 0:
            out.append(
                Violation(
                    code="denied_component",
                    component=c,
                    message=f"「{label}」では「{LABELS[c]}」を使いません（{n} 回使われています）",
                )
            )
        elif allow != ANY and n > allow:
            out.append(
                Violation(
                    code="over_limit",
                    component=c,
                    message=f"「{LABELS[c]}」は「{label}」では {allow} 回までです（{n} 回）",
                )
            )
    if profile.orientation == "vertical" and not m.vertical:
        out.append(Violation(code="orientation", message=f"「{label}」は縦長（9:16）で作ります"))
    if profile.orientation == "landscape" and m.vertical:
        out.append(
            Violation(
                code="orientation",
                severity="warning",
                message=f"「{label}」は横長が基本です。縦で作るなら用途「縦型ショート」を検討してください",
            )
        )
    out += palette_contrast(profile)
    out += color_vision(profile)
    out += transition_rate(profile, m)
    out += motion_checks(profile, m)
    out += text_checks(profile, m)
    hues = hue_groups(m.colors)
    if len(hues) > profile.max_hues:
        out.append(
            Violation(
                code="hue_count",
                severity="warning",
                message=(
                    f"色相が {len(hues)} 種類あります（「{label}」は {profile.max_hues} 種類まで）"
                ),
            )
        )
    fonts = {font_key(f) for f in m.fonts}
    if len(fonts) > profile.typography.max_fonts:
        out.append(
            Violation(
                code="font_count",
                severity="warning",
                message=(
                    f"書体が {len(m.fonts)} 種類あります（{profile.typography.max_fonts} 種類まで）"
                ),
            )
        )
    sub = m.subtitle
    spec = profile.subtitle
    if sub is not None and m.used.get(Component.SUBTITLE, 0):
        lo, hi = spec.size_range
        if not lo <= sub.size_ratio <= hi:
            out.append(
                Violation(
                    code="subtitle_size",
                    component=Component.SUBTITLE,
                    message=f"字幕の大きさ {sub.size_ratio:.3f} が範囲（{lo}〜{hi}）の外です",
                )
            )
        if sub.band != spec.band:
            out.append(
                Violation(
                    code="subtitle_band",
                    component=Component.SUBTITLE,
                    severity="warning",
                    message=f"字幕の帯が「{sub.band}」です（「{label}」は「{spec.band}」）",
                )
            )
        long_lines = [
            t for t, _ in sub.lines for ln in t.split("\n") if len(ln) > spec.max_chars_per_line
        ]
        if long_lines:
            out.append(
                Violation(
                    code="subtitle_line_length",
                    component=Component.SUBTITLE,
                    severity="warning",
                    message=(
                        f"1 行 {spec.max_chars_per_line} 字を超える字幕が"
                        f" {len(long_lines)} 件あります"
                    ),
                )
            )
        # 音声に合わせた字幕は、音声の速さまでは許す（音声と同時に聞けるため）。
        # 音声より 25% 以上速く切り替わる字幕と、上限を超える字幕だけを警告する
        rates = sub.voice_rates or [None] * len(sub.lines)
        fast: list[str] = []
        fast_voiced: list[str] = []
        for (t, sec), vr in zip(sub.lines, rates, strict=False):
            if sec <= 0:
                continue
            speed = len(t.replace("\n", "")) / sec
            if vr is None:
                if speed > spec.chars_per_sec:
                    fast.append(t)
            else:
                limit = min(max(spec.chars_per_sec, vr * 1.25), spec.speech_chars_per_sec)
                if speed > limit:
                    fast_voiced.append(t)
        if fast_voiced:
            out.append(
                Violation(
                    code="subtitle_speed",
                    component=Component.SUBTITLE,
                    severity="warning",
                    message=(
                        f"音声より速く切り替わる（または 1 秒 {spec.speech_chars_per_sec:g} 字を"
                        f"超える）字幕が {len(fast_voiced)} 件あります。区切りを減らしてください"
                    ),
                )
            )
        if fast:
            out.append(
                Violation(
                    code="subtitle_speed",
                    component=Component.SUBTITLE,
                    severity="warning",
                    message=(
                        f"読む速さ（1 秒 {spec.chars_per_sec:g} 字）を超える字幕が"
                        f" {len(fast)} 件あります"
                    ),
                )
            )
    if profile.max_empty_sec is not None:
        long_empty = [(a, b) for a, b in m.empty_spans if b - a > profile.max_empty_sec]
        if long_empty:
            where = "、".join(f"{a:.0f}〜{b:.0f} 秒" for a, b in long_empty[:4])
            out.append(
                Violation(
                    code="empty_screen",
                    severity="warning",
                    message=(
                        f"背景だけの時間が {profile.max_empty_sec:g} 秒を超える所が"
                        f" {len(long_empty)} か所あります（{where}）"
                    ),
                )
            )
    from video_automation.services.design.profiles import safe_area_for

    if m.vertical and m.clips and not opening_motion(m):
        out.append(
            Violation(
                code="opening_motion",
                severity="warning",
                message="縦型の最初の 15 フレームに、要素が入ってくる動きがありません（統合版 S5）",
            )
        )

    w, h = m.size
    sa = safe_area_for(profile, w, h)
    for pl in m.placements:
        if (
            pl.x0 < sa.left - 1e-3
            or pl.y0 < sa.top - 1e-3
            or pl.x1 > 1 - sa.right + 1e-3
            or (pl.y1 > 1 - sa.bottom + 1e-3)
        ):
            out.append(
                Violation(
                    code="safe_area",
                    component=pl.component,
                    severity="warning",
                    message=f"「{LABELS[pl.component]}」が安全範囲の外にかかっています",
                )
            )
    from video_automation.services.design.motion_elements import check_parts

    for msg in check_parts(m.parts):  # 統合版 Z2: 部品を要素に読み替えた組み合わせの決まり
        out.append(Violation(code="motion_elements", severity="warning", message=msg))
    return DesignCheck(
        purpose=profile.purpose,
        ok=not any(v.severity == "error" for v in out),
        violations=out,
        used={c: n for c, n in m.used.items() if n > 0},
    )
