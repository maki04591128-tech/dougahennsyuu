"""ASS / SRT 出力 (P4-05 / P4-06)。焼き込みと外部字幕は同じ字幕データから生成する。"""

from fractions import Fraction

from video_automation.schemas.subtitle import SubtitleStyle


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


def darken(hex_color: str, factor: float = 0.42) -> str:
    """縁取り用に暗くした色（#RRGGBB）。"""
    h = hex_color.lstrip("#")[-6:]
    r, g, b = (round(int(h[i : i + 2], 16) * factor) for i in (0, 2, 4))
    return f"#{r:02X}{g:02X}{b:02X}"


def _style_line(
    st: SubtitleStyle,
    width: int,
    height: int,
    primary: str | None = None,
    outline: str | None = None,
) -> str:
    size = _font_size(st, width, height)
    mv = round(height * st.margin_v_ratio)
    mh = round(width * st.margin_h_ratio)
    return (
        f"Style: {st.name},{st.font_name},{size},{ass_color(primary or st.primary_color)},"
        f"&H000000FF,{ass_color(outline or st.outline_color)},{ass_color(st.back_color)},"
        f"{-1 if st.bold else 0},0,0,0,100,100,0,0,1,{st.outline:g},{st.shadow:g},"
        f"{st.alignment},{mh},{mh},{mv},1"
    )


def _band(st: SubtitleStyle, width: int, height: int, lines: int, label: bool) -> str:
    """字幕の後ろの半透明の帯（ベクター描画）。下揃えの字幕のときだけ。"""
    size = _font_size(st, width, height)
    mv = round(height * st.margin_v_ratio)
    text_h = lines * size * 1.2 + (size * LABEL_RATIO * 1.2 if label else 0)
    pad = size * 0.4
    top = max(0, round(height - mv - text_h - pad))
    h = height - top  # 画面の下端まで
    alpha = ass_color(st.band_color)[2:4]
    color = ass_color(st.band_color)[4:]
    return (
        f"{{\\an7\\pos(0,{top})\\bord0\\shad0\\1c&H{color}&\\1a&H{alpha}&\\p1}}"
        f"m 0 0 l {width} 0 l {width} {h} l 0 {h}{{\\p0}}"
    )


def build_ass(
    cues: list[tuple[int, int, str, str]],
    fps: Fraction,
    width: int,
    height: int,
    style: SubtitleStyle,
    speaker_colors: dict[str, str] | None = None,
    title: str = "VideoAutomation",
    speaker_accents: dict[str, str] | None = None,
) -> str:
    """cues: (start_frame, end_frame, speaker, text)。

    speaker_colors は話者別の文字色。speaker_accents は話者の色で、名前の表示と
    縁取り（暗くした色）に使う（style.speaker_label / speaker_outline）。
    名前は話者が 2 人以上のときだけ出す。"""
    speaker_colors = speaker_colors or {}
    accents = speaker_accents or {}
    styles = [_style_line(style, width, height)]
    style_of: dict[str, str] = {}
    speakers = sorted(set(speaker_colors) | (set(accents) if style.speaker_outline else set()))
    for i, spk in enumerate(speakers):
        name = f"{style.name}_S{i + 1}"
        outline = darken(accents[spk]) if style.speaker_outline and spk in accents else None
        styles.append(
            _style_line(
                style.model_copy(update={"name": name}),
                width,
                height,
                speaker_colors.get(spk),
                outline,
            )
        )
        style_of[spk] = name
    fade = (
        f"{{\\fad({style.fade_in_ms},{style.fade_out_ms})}}"
        if style.fade_in_ms or style.fade_out_ms
        else ""
    )
    multi = len({spk for _, _, spk, _ in cues if spk}) >= 2
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
    for start, end, spk, text in cues:
        st_name = style_of.get(spk, style.name)
        t0 = ass_time(frame_to_seconds(start, fps))
        t1 = ass_time(frame_to_seconds(end, fps))
        name = spk.replace(",", "，")
        label = ""
        if style.speaker_label and multi and spk:
            color = ass_color(accents.get(spk, style.primary_color))[4:]
            label = (
                f"{{\\fs{round(size * LABEL_RATIO)}\\1c&H{color}&\\3c&H000000&}}"
                f"{_ass_escape(spk)}{{\\r}}\\N"
            )
        if style.band and bottom:
            band = _band(style, width, height, text.count("\n") + 1, bool(label))
            lines.append(f"Dialogue: 0,{t0},{t1},{style.name},{name},0,0,0,,{fade}{band}")
        lines.append(
            f"Dialogue: 1,{t0},{t1},{st_name},{name},0,0,0,,{fade}{label}{_ass_escape(text)}"
        )
    return "\r\n".join(lines) + "\r\n"


def build_srt(cues: list[tuple[int, int, str, str]], fps: Fraction) -> str:
    blocks = []
    for i, (start, end, _spk, text) in enumerate(cues, 1):
        blocks.append(
            f"{i}\r\n{srt_time(frame_to_seconds(start, fps))} --> "
            f"{srt_time(frame_to_seconds(end, fps))}\r\n{text.replace(chr(10), chr(13) + chr(10))}"
        )
    return "\r\n\r\n".join(blocks) + "\r\n"
