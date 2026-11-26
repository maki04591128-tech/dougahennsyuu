"""ASS / SRT 出力 (P4-05 / P4-06)。焼き込みと外部字幕は同じ字幕データから生成する。"""

from fractions import Fraction

from video_automation.schemas.subtitle import SubtitleStyle
from video_automation.services.design.guard import confusable
from video_automation.services.design.oklch import ALT_OUTLINE, OUTLINE, TEXT, derive


def frame_to_seconds(frame: int, fps: Fraction) -> Fraction:
    return Fraction(frame) / fps


def srt_time(seconds: Fraction) -> str:
    ms_total = round(seconds * 1000)
    h, rem = divmod(ms_total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_time(seconds: Fraction) -> str:
    cs_total = round(seconds * 100)
    h, rem = divmod(cs_total, 360_000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def ass_color(hex_color: str) -> str:
    """#RRGGBB / #AARRGGBB（AA=不透明度）→ ASS の &HAABBGGRR（AA=透明度）。"""
    h = hex_color.lstrip("#")
    alpha = 255 - int(h[:2], 16) if len(h) == 8 else 0
    rgb = h[-6:]
    r, g, b = rgb[0:2], rgb[2:4], rgb[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_escape(text: str) -> str:
    # 波括弧は override タグになるため全角に置換。改行は \N
    return text.replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", "\\N")


LABEL_RATIO = 0.7  # 話者名の大きさ（字幕に対する比）


def _font_size(st: SubtitleStyle, width: int, height: int) -> int:
    return st.font_size or max(16, round(min(width, height) * st.font_size_ratio))


# 統合版 3節: キャラクターの入り 8 種（30fps）。出はすべて 3f
ENTRANCE_OUT_MS = 100


def entrance_tags(kind: str, st: SubtitleStyle, width: int, height: int, mv: int) -> str:
    """キャラクターの入り（統合版 3節の 8 種）の ASS のタグ。分からない入りは今のフェード。"""
    px = height / 1080
    out = ENTRANCE_OUT_MS
    bottom_center = st.alignment == 2
    x, y = width // 2, height - mv
    if kind == "ポップ小":  # scale 0.92→1.0、4f、back 1.2
        return (
            f"{{\\fad(133,{out})\\fscx92\\fscy92\\t(0,100,\\fscx102\\fscy102)"
            f"\\t(100,133,\\fscx100\\fscy100)}}"
        )
    if kind == "ポップ中":  # 0.88→1.0、5f、back 1.70158
        return (
            f"{{\\fad(167,{out})\\fscx88\\fscy88\\t(0,117,\\fscx104\\fscy104)"
            f"\\t(117,167,\\fscx100\\fscy100)}}"
        )
    if kind in ("フェード200", "フェード250", "フェード300"):
        ms = {"フェード200": 200, "フェード250": 267, "フェード300": 300}[kind]
        return f"{{\\fad({ms},{out})}}"
    if kind == "上昇フェード" and bottom_center:  # y +8px→0、8f
        return f"{{\\fad(267,{out})\\move({x},{y + round(8 * px)},{x},{y},0,267)}}"
    if kind == "スライド" and bottom_center:  # x −16px→0、6f
        return f"{{\\fad(200,{out})\\move({x - round(16 * px)},{y},{x},{y},0,200)}}"
    if kind == "入りなし":
        return f"{{\\fad(0,{out})}}"
    return f"{{\\fad({st.fade_in_ms},{st.fade_out_ms})}}" if st.fade_in_ms or st.fade_out_ms else ""


def tint(hex_color: str) -> str:
    """話者の色から字幕の文字の色（OKLCH で L 0.95・C 最大 0.05。統合版 K2）。"""
    return derive(hex_color, TEXT)


def darken(hex_color: str) -> str:
    """話者の色から字幕の縁の色（OKLCH で L 0.36・C 最大 0.11。統合版 K2）。"""
    return derive(hex_color, OUTLINE)


def alt_darken(hex_color: str) -> str:
    """縁が同時に出る話者と近いときの代替の縁（L 0.24・C 最大 0.08）。"""
    return derive(hex_color, ALT_OUTLINE)


def _style_line(
    st: SubtitleStyle,
    width: int,
    height: int,
    primary: str | None = None,
    outline: str | None = None,
    font: str | None = None,
    spacing_extra: float = 0.0,
) -> str:
    size = _font_size(st, width, height)
    mv = round(height * st.margin_v_ratio)
    mh = round(width * st.margin_h_ratio)
    spacing = tracking(size) + round(size * spacing_extra)
    bold = st.bold
    if font and font.rsplit(" ", 1)[-1] in NAMED_WEIGHTS:
        bold = False  # 太さを名前で選んだ書体に太字を重ねない（にじむ）
    return (
        f"Style: {st.name},{font or st.font_name},{size},{ass_color(primary or st.primary_color)},"
        f"&H000000FF,{ass_color(outline or st.outline_color)},{ass_color(st.back_color)},"
        f"{-1 if bold else 0},0,0,0,100,100,{spacing},0,1,{st.outline:g},{st.shadow:g},"
        f"{st.alignment},{mh},{mh},{mv},1"
    )


# 統合版 C1: 二重縁の幅（明るい縁 = 文字の大きさ × 0.06、暗い縁 = それ ＋ 3px（1080p））
DOUBLE_OUTLINE_RATIO = 0.06
DOUBLE_OUTLINE_EXTRA_PX = 3.0
NAMED_WEIGHTS = frozenset({"Thin", "Light", "Regular", "Medium", "SemiBold", "ExtraBold", "Black"})
SAME_FONT_SPACING = 0.04  # 統合版 3節: 書体が同じ 2 人の後の方は字間 +4%
# 統合版 C2: 字間は文字の大きさの −2%（ASS の Spacing、Blender の space_character 0.98）
TRACKING = -0.02


def tracking(size: float) -> int:
    """ASS の Spacing（px）。字間を文字の大きさの −2% にする。"""
    return round(size * TRACKING)


def text_width(text: str, size: float) -> float:
    """推定の文字幅（全角=1、半角=0.6、字間 −2%）。字幕の帯の幅を決めるのに使う。"""
    return sum(size * (0.6 if ord(ch) < 0x2E80 else 1.0) for ch in text) * (1 + TRACKING)


def _band(
    st: SubtitleStyle,
    width: int,
    height: int,
    lines: int,
    label: bool,
    text: str = "",
    label_text: str = "",
) -> str:
    """字幕の後ろの半透明の帯（ベクター描画）。下揃えの字幕のときだけ。

    full: 画面幅で下端まで。fitted: 字幕の文字の幅と高さだけ（角を少し落とした形）。"""
    size = _font_size(st, width, height)
    mv = round(height * st.margin_v_ratio)
    text_h = lines * size * 1.2 + (size * LABEL_RATIO * 1.2 if label else 0)
    pad = size * 0.4
    top = max(0, round(height - mv - text_h - pad))
    alpha = ass_color(st.band_color)[2:4]
    color = ass_color(st.band_color)[4:]
    head = f"{{\\an7\\pos(0,{top})\\bord0\\shad0\\1c&H{color}&\\1a&H{alpha}&\\p1}}"
    if st.band_mode == "fitted" and text:
        widths = [text_width(ln, size) for ln in text.split("\n")]
        if label:
            widths.append(text_width(label_text, size * LABEL_RATIO))
        bw = min(width, round(max(widths) + size * 1.2))
        x0 = round((width - bw) / 2)
        mh = round(width * st.margin_h_ratio)
        if st.alignment == 1:  # 左揃えの字幕は左の余白に寄せる
            x0 = max(0, mh - round(size * 0.6))
        elif st.alignment == 3:
            x0 = min(width - bw, width - mh - bw + round(size * 0.6))
        x1, y1 = x0 + bw, round(text_h + pad * 2)
        r = round(size * 0.25)  # 角を落とす量
        path = (
            f"m {x0 + r} 0 l {x1 - r} 0 l {x1} {r} l {x1} {y1 - r} l {x1 - r} {y1} "
            f"l {x0 + r} {y1} l {x0} {y1 - r} l {x0} {r}"
        )
        return head + path + "{\\p0}"
    h = height - top  # 画面の下端まで
    return head + f"m 0 0 l {width} 0 l {width} {h} l 0 {h}{{\\p0}}"


def build_ass(
    cues: list[tuple[int, int, str, str]],
    fps: Fraction,
    width: int,
    height: int,
    style: SubtitleStyle,
    speaker_colors: dict[str, str] | None = None,
    title: str = "VideoAutomation",
    speaker_accents: dict[str, str] | None = None,
    multi_speaker: bool | None = None,
    speaker_fonts: dict[str, str] | None = None,
    speaker_design: dict[str, dict[str, str]] | None = None,
    speaker_entrance: dict[str, str] | None = None,
    ruby: dict[int, list[tuple[int, int, int, str]]] | None = None,
) -> str:
    """cues: (start_frame, end_frame, speaker, text)。

    speaker_design は話者ごとの字幕の色（fill・outline・alt_outline。統合版 K1 の手元の
    データか利用者の登録）。あればそれを使い、無ければ話者の色から OKLCH で作る（K2）。

    speaker_colors は話者別の文字色。speaker_accents は話者の色で、名前の表示と
    縁取り（暗くした色）に使う（style.speaker_label / speaker_outline）。
    名前は話者が 2 人以上のときだけ出す。"""
    speaker_colors = dict(speaker_colors or {})
    accents = speaker_accents or {}
    design = speaker_design or {}
    fonts = (speaker_fonts or {}) if style.speaker_fonts else {}
    multi_spk = (
        multi_speaker
        if multi_speaker is not None
        else len({spk for _, _, spk, _ in cues if spk}) >= 2
    )
    if style.speaker_fill and style.speaker_outline and multi_spk:
        # 話者の色を薄めた文字色（縁取りは話者の色を暗くした色）で、名前なしでも区別できる
        for spk, col in accents.items():
            speaker_colors.setdefault(spk, design.get(spk, {}).get("fill") or tint(col))
    styles = [_style_line(style, width, height)]
    style_of: dict[str, str] = {}
    speakers = sorted(
        set(speaker_colors)
        | (set(accents) if style.speaker_outline else set())
        | (set(fonts) if multi_spk else set())
    )
    # 統合版 3節の衝突の決まり（登場の順）: 縁の色が前の話者と見分けにくければ代替の縁、
    # 書体が前の話者と同じなら字間を +4%
    order = list(dict.fromkeys(spk for _, _, spk, _ in cues if spk))
    order += [x for x in speakers if x not in order]
    outline_of: dict[str, str] = {}
    spaced: set[str] = set()
    for k, spk in enumerate(order):
        if style.speaker_outline and spk in accents:
            d = design.get(spk, {})
            o = d.get("outline") or darken(accents[spk])
            if any(confusable(o, prev) for prev in outline_of.values()):
                o = d.get("alt_outline") or alt_darken(accents[spk])
            outline_of[spk] = o
        if multi_spk and fonts.get(spk) and any(fonts.get(x) == fonts[spk] for x in order[:k]):
            spaced.add(spk)
    for i, spk in enumerate(speakers):
        name = f"{style.name}_S{i + 1}"
        outline = outline_of.get(spk)
        styles.append(
            _style_line(
                style.model_copy(update={"name": name}),
                width,
                height,
                speaker_colors.get(spk),
                outline,
                fonts.get(spk) if multi_spk else None,
                SAME_FONT_SPACING if spk in spaced else 0.0,
            )
        )
        style_of[spk] = name
    fade = (
        f"{{\\fad({style.fade_in_ms},{style.fade_out_ms})}}"
        if style.fade_in_ms or style.fade_out_ms
        else ""
    )
    multi = multi_spk
    size = _font_size(style, width, height)
    bottom = style.alignment in (1, 2, 3)
    lines = [
        "[Script Info]",
        f"Title: {title}",
        "ScriptType: v4.00+",
        "WrapStyle: 2",  # 自動改行しない（改行は生成側で制御）
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    entrances = speaker_entrance or {}
    mv = round(height * style.margin_v_ratio)
    rubies = ruby or {}
    for k_cue, (start, end, spk, text) in enumerate(cues):
        st_name = style_of.get(spk, style.name)
        cue_fade = fade
        if style.character_entrance and spk in entrances:
            cue_fade = entrance_tags(entrances[spk], style, width, height, mv)
        t0 = ass_time(frame_to_seconds(start, fps))
        t1 = ass_time(frame_to_seconds(end, fps))
        name = spk.replace(",", "，")
        label = ""
        if style.speaker_label and multi and spk:
            color = ass_color(accents.get(spk, style.primary_color))[4:]
            edge = ass_color(style.outline_color)[4:]  # 縁取りは字幕と同じ色
            label = (
                f"{{\\fs{round(size * LABEL_RATIO)}\\1c&H{color}&\\3c&H{edge}&}}"
                f"{_ass_escape(spk)}{{\\r}}\\N"
            )
        etext = _ass_escape(text)
        rb = rubies.get(k_cue) if bottom and style.mode != "lyric" else None
        if rb:
            # 統合版 P7: 行の間（と話者名の下）に、文字の大きさ × 0.55 の空きを入れる
            gap = f"{{\\fs{round(size * RUBY_SPACE)}}}\\h{{\\fs{size}}}\\N"
            etext = etext.replace("\\N", "\\N" + gap)
            if label:
                label += gap
        if style.mode == "lyric":
            # 歌詞: 下の層に太い縁取りをぼかした光（文字本体は透明）、上の層に文字
            if style.glow > 0:
                glow = ass_color(style.glow_color)[4:]
                lines.append(
                    f"Dialogue: 0,{t0},{t1},{style.name},{name},0,0,0,,{fade}"
                    f"{{\\1a&HFF&\\3c&H{glow}&\\3a&H70&"
                    f"\\bord{max(2.0, size * 0.12):.1f}\\blur{style.glow:g}}}"
                    f"{_ass_escape(text)}"
                )
            lines.append(
                f"Dialogue: 1,{t0},{t1},{style.name},{name},0,0,0,,{fade}"
                f"{{\\blur0.6}}{_ass_escape(text)}"
            )
            continue
        if style.band and bottom:
            band = _band(style, width, height, text.count("\n") + 1, bool(label), text, spk)
            lines.append(f"Dialogue: 0,{t0},{t1},{style.name},{name},0,0,0,,{fade}{band}")
        if style.double_outline:
            # 統合版 C1（実機のプレイテストで直した）: 上の層は文字と暗い縁（文字の 6%）、下の層は
            # 文字を透明にして明るい縁（それ ＋ 3px）。仕様どおり内側の縁を文字と同じ色にすると、
            # 縁が文字を太らせて「連続」「補強」のような画数の多い字がつぶれた
            dark = ass_color(outline_of.get(spk) or style.outline_color)[4:]
            light_src = (
                style.double_outline_outer or speaker_colors.get(spk) or (style.primary_color)
            )
            light = ass_color(light_src)[4:]
            inner = size * DOUBLE_OUTLINE_RATIO
            outer = inner + DOUBLE_OUTLINE_EXTRA_PX * height / 1080
            lines.append(
                f"Dialogue: 1,{t0},{t1},{st_name},{name},0,0,0,,{cue_fade}"
                f"{{\\1a&HFF&\\3c&H{light}&\\bord{outer:.1f}\\shad0}}{label}{etext}"
            )
            lines.append(
                f"Dialogue: 2,{t0},{t1},{st_name},{name},0,0,0,,{cue_fade}"
                f"{{\\3c&H{dark}&\\bord{inner:.1f}\\shad0}}{label}{etext}"
            )
            if rb:
                lines += _ruby_events(
                    rb, text, t0, t1, st_name, name, fade, size, width, height, mv
                )
            continue
        lines.append(f"Dialogue: 1,{t0},{t1},{st_name},{name},0,0,0,,{cue_fade}{label}{etext}")
        if rb:
            lines += _ruby_events(rb, text, t0, t1, st_name, name, fade, size, width, height, mv)
    return "\r\n".join(lines) + "\r\n"


RUBY_RATIO, RUBY_SPACE = 0.48, 0.55  # 統合版 P7: ふりがなは本文の 48%・行の上に 0.55 の空き
LINE_HEIGHT = 1.2  # 1 行の高さ（文字の大きさに対する比。_band と同じ）


def _ruby_events(
    rb: list[tuple[int, int, int, str]],
    text: str,
    t0: str,
    t1: str,
    st_name: str,
    name: str,
    fade: str,
    size: int,
    width: int,
    height: int,
    mv: int,
) -> list[str]:
    """ふりがなを 1 語ずつ \\pos で置く（下揃え・中央揃えの字幕）。"""
    rows = text.split("\n")
    n = len(rows)
    lh, sp = size * LINE_HEIGHT, size * LINE_HEIGHT * RUBY_SPACE
    rs = max(8, round(size * RUBY_RATIO))
    out = []
    for ln, a, b, reading in rb:
        if not 0 <= ln < n or not reading:
            continue
        row = rows[ln]
        bottom_k = height - mv - (n - 1 - ln) * (lh + sp)
        y = round(bottom_k - lh + size * 0.12)
        left = width / 2 - text_width(row, size) / 2
        x = round(left + text_width(row[:a], size) + text_width(row[a:b], size) / 2)
        out.append(
            f"Dialogue: 3,{t0},{t1},{st_name},{name},0,0,0,,{fade}"
            f"{{\\an2\\pos({x},{y})\\fs{rs}\\bord{max(1.0, size * 0.04):.1f}\\shad0}}"
            f"{_ass_escape(reading)}"
        )
    return out


def build_srt(cues: list[tuple[int, int, str, str]], fps: Fraction) -> str:
    blocks = []
    for i, (start, end, _spk, text) in enumerate(cues, 1):
        blocks.append(
            f"{i}\r\n{srt_time(frame_to_seconds(start, fps))} --> "
            f"{srt_time(frame_to_seconds(end, fps))}\r\n{text.replace(chr(10), chr(13) + chr(10))}"
        )
    return "\r\n\r\n".join(blocks) + "\r\n"
