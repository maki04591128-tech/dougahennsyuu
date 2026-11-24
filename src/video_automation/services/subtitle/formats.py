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


def text_width(text: str, size: float) -> float:
    """推定の文字幅（全角=1、半角=0.6）。字幕の帯の幅を決めるのに使う。"""
    return sum(size * (0.6 if ord(ch) < 0x2E80 else 1.0) for ch in text)


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
    multi = (
        multi_speaker
        if multi_speaker is not None
        else len({spk for _, _, spk, _ in cues if spk}) >= 2
    )
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
            edge = ass_color(style.outline_color)[4:]  # 縁取りは字幕と同じ色
            label = (
                f"{{\\fs{round(size * LABEL_RATIO)}\\1c&H{color}&\\3c&H{edge}&}}"
                f"{_ass_escape(spk)}{{\\r}}\\N"
            )
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
