"""書き出す前のデザイン検査（ガード）。

作った要素の記録（DesignManifest）を用途の型と突き合わせ、別の用途の部品・型の外の色や書体・
読めない字幕を見つける。error が 1 つでもあれば書き出しを止める（例外は ProjectDesign.extras）。
"""

import colorsys
import re
from dataclasses import dataclass, field

from video_automation.schemas.design import (
    ANY,
    Component,
    DesignCheck,
    DesignProfile,
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
class DesignManifest:
    used: dict[Component, int] = field(default_factory=dict)
    colors: list[str] = field(default_factory=list)  # 使った色（#RRGGBB）
    fonts: set[str] = field(default_factory=set)
    subtitle: SubtitleUse | None = None
    placements: list[Placement] = field(default_factory=list)
    vertical: bool = False
    empty_spans: list[tuple[float, float]] = field(default_factory=list)  # 背景だけの区間（秒）

    def add(self, c: Component, n: int = 1) -> None:
        self.used[c] = self.used.get(c, 0) + n


def is_dark(color: str) -> bool:
    """色が暗いか（相対輝度 0.4 未満）。文字の縁取りを明暗どちらにするかに使う。"""
    h = color.lstrip("#")[-6:]
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b < 0.4


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
    sa = profile.safe_area
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
    return DesignCheck(
        purpose=profile.purpose,
        ok=not any(v.severity == "error" for v in out),
        violations=out,
        used={c: n for c, n in m.used.items() if n > 0},
    )
