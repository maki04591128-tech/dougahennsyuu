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


def _style_line(st: SubtitleStyle, width: int, height: int, primary: str | None = None) -> str:
    size = st.font_size or max(16, round(min(width, height) * 0.055))
    mv = round(height * st.margin_v_ratio)
    mh = round(width * st.margin_h_ratio)
    return (
        f"Style: {st.name},{st.font_name},{size},{ass_color(primary or st.primary_color)},"
        f"&H000000FF,{ass_color(st.outline_color)},{ass_color(st.back_color)},"
        f"{-1 if st.bold else 0},0,0,0,100,100,0,0,1,{st.outline:g},{st.shadow:g},"
        f"{st.alignment},{mh},{mh},{mv},1"
    )


def build_ass(
    cues: list[tuple[int, int, str, str]],
    fps: Fraction,
    width: int,
    height: int,
    style: SubtitleStyle,
    speaker_colors: dict[str, str] | None = None,
    title: str = "VideoAutomation",
) -> str:
    """cues: (start_frame, end_frame, speaker, text)。話者に色指定があれば話者別スタイルを作る。"""
    speaker_colors = speaker_colors or {}
    styles = [_style_line(style, width, height)]
    style_of: dict[str, str] = {}
    for i, (spk, color) in enumerate(sorted(speaker_colors.items())):
        name = f"{style.name}_S{i + 1}"
        styles.append(_style_line(style.model_copy(update={"name": name}), width, height, color))
        style_of[spk] = name
    fade = (
        f"{{\\fad({style.fade_in_ms},{style.fade_out_ms})}}"
        if style.fade_in_ms or style.fade_out_ms
        else ""
    )
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
        lines.append(
            f"Dialogue: 0,{ass_time(frame_to_seconds(start, fps))},"
            f"{ass_time(frame_to_seconds(end, fps))},{st_name},{spk.replace(',', '，')},"
            f"0,0,0,,{fade}{_ass_escape(text)}"
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
