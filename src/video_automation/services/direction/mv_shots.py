"""MV のショットとカット（統合版 段階5b: V1・V2・V3・V7・V8・V12）。

1 枚の絵（利用者の画像。透明の部分があれば切り抜きとして使う）から 8 種のショットを作り、
曲のセクションごとの拍の単位でカットを並べる。

- V1 ショット: 全身・上半身・顔の寄り・目の寄り・シルエット・切り抜きと面・グリッド・一瞬のカット
- V2 カットの間隔（拍）と順は下の SECTION_CUTS。1 拍が 0.34 秒未満（BPM 176 超）なら 1 拍を 2 拍に
- V8 一瞬のカット: M1 のピークに 2f、1 秒に最大 1 回。奇数番目は色相 180°、偶数番目はピクセルソート
- V12 配色: ジャンルごとの主色（切り抜きと面・グリッドの地）

立ち絵の部品（「!目」「!口」）を使う寄りは、立ち絵の画像の取り出しを作るときに足す。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageFilter, ImageOps

from video_automation.schemas.animation import Easing, Keyframe

SHOTS = ("full", "upper", "face", "eye", "silhouette", "cutout", "grid", "flash")
SHOT_LABEL = {
    "full": "全身",
    "upper": "上半身",
    "face": "顔の寄り",
    "eye": "目の寄り",
    "silhouette": "シルエット",
    "cutout": "切り抜きと面",
    "grid": "グリッド",
    "flash": "一瞬のカット",
}
# セクション → (カットの間隔の拍, ショットの順)。サビは別の決まり（CHORUS_*）
SECTION_CUTS: dict[str, tuple[int, tuple[str, ...]]] = {
    "intro": (2, ("eye", "face", "silhouette", "full")),
    "verse": (4, ("full", "upper", "face", "upper")),
    "bridge": (2, ("face", "eye", "upper", "silhouette")),
    "drop": (8, ("face", "full")),
    "interlude": (2, ("cutout", "grid", "silhouette", "upper")),
    "outro": (8, ("full", "face", "full")),
}
CHORUS_FAST = ("face", "full", "eye", "cutout", "upper", "silhouette")  # 1 拍のカットを 4 小節
CHORUS_HOLD = "full"  # 続けて 8 拍保つ
FAST_BEAT_SEC = 0.34
V12_MAIN = {
    "ballad": ("#3E5C9A", "#FFFFFF"),
    "citypop": ("#E8A33D", "#111111"),
    "rock": ("#C8102E", "#FFFFFF"),
    "edm": ("#00B3C7", "#111111"),
    "vocaloid": ("#7A3CE0", "#FFFFFF"),
    "idol": ("#FF4FA0", "#111111"),
    "pop": ("#FFC82E", "#111111"),
}


@dataclass(frozen=True)
class Cut:
    start: int  # フレーム
    end: int
    shot: str


def _section_at(t: float, spans: list[tuple[float, float, str]], first: float, last: float) -> str:
    if t < first:
        return "intro"
    if t >= last:
        return "outro"
    for a, b, s in spans:
        if a <= t < b:
            return "chorus" if s == "chorus_first" else s
    return "interlude"


def line_spans(starts: list[float], ends: list[float], secs: list[str], bar_sec: float):
    """歌詞の行の区間 → セクションの区間。次の行まで 1 小節を超えて空く所は間奏。"""
    out = []
    for i, (a, b, s) in enumerate(zip(starts, ends, secs, strict=True)):
        nxt = starts[i + 1] if i + 1 < len(starts) else b
        out.append((a, nxt if nxt - b <= bar_sec else b, s))
    return out


def plan_cuts(
    beats: list[float],
    spans: list[tuple[float, float, str]],
    total_sec: float,
    fps: float,
) -> list[Cut]:
    """V2: 拍の時刻に最も近いフレームでカットを並べる。"""
    if len(beats) < 2 or not spans:
        return []
    period = float(np.median(np.diff(beats)))
    step = 2 if period < FAST_BEAT_SEC else 1  # 1 拍が短すぎる曲は 1 拍を 2 拍に
    first, last = spans[0][0], spans[-1][1]
    marks: list[tuple[float, str]] = []  # (時刻, ショット)
    i = 0
    counters: dict[str, int] = {}
    chorus_pos = 0  # サビの中の拍の数（4 小節の速いカット → 8 拍の保ち）
    prev_sec = ""
    while i < len(beats):
        t = beats[i]
        sec = _section_at(t, spans, first, last)
        if sec != prev_sec:
            chorus_pos = 0
        if sec == "chorus":
            cycle = 16 + 8
            pos = chorus_pos % cycle
            if pos < 16:
                k = counters.get("chorus", 0)
                counters["chorus"] = k + 1
                marks.append((t, CHORUS_FAST[k % len(CHORUS_FAST)]))
                adv = step
            else:
                marks.append((t, CHORUS_HOLD))
                adv = 8
            chorus_pos += adv
        else:
            n, order = SECTION_CUTS[sec]
            if sec == "intro" and t >= first - 4 * period:
                n = 1  # イントロの最後の 1 小節は 1 拍
            k = counters.get(sec, 0)
            counters[sec] = k + 1
            marks.append((t, order[k % len(order)]))
            adv = max(n, step) if n == 1 else n * step
        prev_sec = sec
        i += adv
    if marks and marks[0][0] > 0:
        marks.insert(0, (0.0, marks[0][1]))
    total = round(total_sec * fps)
    cuts: list[Cut] = []
    for j, (t, shot) in enumerate(marks):
        a = round(t * fps)
        b = round(marks[j + 1][0] * fps) if j + 1 < len(marks) else total
        if b > a and a < total:
            if cuts and cuts[-1].shot == shot:
                cuts[-1] = Cut(cuts[-1].start, min(b, total), shot)
            else:
                cuts.append(Cut(a, min(b, total), shot))
    return cuts


def insert_flashes(cuts: list[Cut], peaks: list[int], fps: float) -> list[Cut]:
    """V8: ピークのフレームに 2f の一瞬のカットを差し込む（1 秒に最大 1 回）。"""
    out = list(cuts)
    last = -(10**9)
    for p in sorted(peaks):
        if p - last < fps:
            continue
        for k, c in enumerate(out):
            if c.start < p and p + 2 < c.end and c.shot != "flash":
                out[k : k + 1] = [
                    Cut(c.start, p, c.shot),
                    Cut(p, p + 2, "flash"),
                    Cut(p + 2, c.end, c.shot),
                ]
                last = p
                break
    return out


# ---------------- ショットの絵 ----------------


def _fit(
    img: Image.Image, w: int, h: int, bg: tuple[int, int, int, int] = (0, 0, 0, 0)
) -> Image.Image:
    canvas = Image.new("RGBA", (w, h), bg)
    im = ImageOps.contain(img, (w, h))
    canvas.alpha_composite(im, ((w - im.width) // 2, (h - im.height) // 2))
    return canvas


def _crop(img: Image.Image, cx: float, cy: float, cw: float, ch: float) -> Image.Image:
    W, H = img.size
    x0, y0 = max(0, round((cx - cw / 2) * W)), max(0, round((cy - ch / 2) * H))
    return img.crop((x0, y0, min(W, x0 + round(cw * W)), min(H, y0 + round(ch * H))))


def _rgb(c: str) -> tuple[int, int, int]:
    h = c.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def has_cutout(img: Image.Image) -> bool:
    """透明の部分がある（背景を除いた絵）か。"""
    if img.mode != "RGBA":
        return False
    a = np.asarray(img.getchannel("A"))
    return bool((a < 16).mean() > 0.05)


def shot_image(
    src: Image.Image, shot: str, w: int, h: int, main: str, ink: str, k: int = 0
) -> Image.Image:
    """1 つのショットの絵（w×h、RGBA）。"""
    img = src.convert("RGBA")
    cut = has_cutout(img)
    if shot == "full":
        return _fit(img, w, round(h * 0.9) if cut else h, (0, 0, 0, 255)).resize((w, h))
    if shot == "upper":
        top = _crop(img, 0.5, 0.225 if cut else 0.3, 1.0, 0.45 if cut else 0.6)
        return _fit(top, w, h, (0, 0, 0, 255))
    if shot == "face":
        s = 0.35 * img.width / img.height
        return _fit(_crop(img, 0.5, 1 / 6 + 0.04, 0.35, s), w, h, (0, 0, 0, 255))
    if shot == "eye":
        s = 0.15 * img.width / img.height
        return _fit(_crop(img, 0.5, 1 / 6 + 0.02, 0.15, s), w, h, (0, 0, 0, 255))
    if shot == "silhouette":
        if not cut:
            return shot_image(src, "full", w, h, main, ink)
        a = img.getchannel("A")
        sil = Image.new("RGBA", img.size, (17, 17, 17, 255))
        sil.putalpha(a)
        return _fit(sil, w, round(h * 0.9), (255, 255, 255, 255)).resize((w, h))
    if shot == "cutout":  # V3: 単色の面に切り抜きと、文字の色の影
        bg = Image.new("RGBA", (w, h), (*_rgb(main), 255))
        fg = _fit(img, w, round(h * 0.86))
        off = round(h * 0.012)
        if cut:
            shadow = Image.new("RGBA", fg.size, (*_rgb(ink), 255))
            shadow.putalpha(fg.getchannel("A"))
        else:  # 切り抜きが無い絵は、絵の四角の影
            shadow = Image.new("RGBA", fg.size, (0, 0, 0, 0))
            box = fg.getchannel("A").getbbox() or (0, 0, *fg.size)
            shadow.paste((*_rgb(ink), 255), box)
        y = (h - fg.height) // 2
        bg.alpha_composite(shadow, (off, y + off))
        bg.alpha_composite(fg, (0, y))
        return bg
    if shot == "grid":  # V7: 上半身を 4×4
        cell = shot_image(src, "upper", w // 4, h // 4, main, ink)
        g = Image.new("RGBA", (w, h), (*_rgb(main), 255))
        for r in range(4):
            for c in range(4):
                g.alpha_composite(cell, (c * (w // 4), r * (h // 4)))
        return g
    if shot == "flash":  # V8
        base = shot_image(src, "full", w, h, main, ink).convert("RGB")
        return (hue_rotate(base) if k % 2 == 0 else pixel_sort(base)).convert("RGBA")
    raise ValueError(shot)


def _cutout_alpha(src: Image.Image, w: int, h: int) -> Image.Image:
    """切り抜きと面のショットで、切り抜きの絵が置かれた所のアルファ（shot_image と同じ置き方）。"""
    fg = _fit(src.convert("RGBA"), w, round(h * 0.86))
    a = Image.new("L", (w, h), 0)
    a.paste(fg.getchannel("A"), (0, (h - fg.height) // 2))
    return a


def hue_rotate(img: Image.Image) -> Image.Image:
    """色相を 180° 回す（明るさ V は変えない）。"""
    hsv = np.asarray(img.convert("HSV")).copy()
    hsv[..., 0] = (hsv[..., 0].astype(int) + 128) % 256
    return Image.fromarray(hsv, "HSV").convert("RGB")


def pixel_sort(img: Image.Image) -> Image.Image:
    """各行で、輝度 0.25〜0.85 が続く区間を輝度の昇順に並べ替える。"""
    a = np.asarray(img.convert("RGB")).copy()
    lum = (0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]) / 255
    mask = (lum >= 0.25) & (lum <= 0.85)
    for y in range(a.shape[0]):
        row, m, lr = a[y], mask[y], lum[y]
        x = 0
        n = len(m)
        while x < n:
            if not m[x]:
                x += 1
                continue
            e = x
            while e < n and m[e]:
                e += 1
            order = np.argsort(lr[x:e], kind="stable")
            row[x:e] = row[x:e][order]
            x = e
    return Image.fromarray(a, "RGB")


def save_shot(
    out: Path,
    src: Path,
    shot: str,
    w: int,
    h: int,
    main: str,
    ink: str,
    k: int = 0,
    look: bool = True,
) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        ImageOps.exif_transpose(im).load()
        src_im = ImageOps.exif_transpose(im)
        shot_im = shot_image(src_im, shot, w, h, main, ink, k)
        if look and shot not in ("silhouette", "grid", "flash"):  # 統合版 V4
            cut_a = None
            if shot == "cutout" and has_cutout(src_im.convert("RGBA")):
                cut_a = _cutout_alpha(src_im, w, h)
            shot_im = film(shot_im, cut_a)
        shot_im.save(out)
    return out


def camera(shot: str, n: int, fps: float) -> list[Keyframe]:
    """V1 のショットのカメラ（ズーム・パン）。止めのショットは空。"""
    k24 = min(n - 1, round(24 * fps / 30))
    if n < 2:
        return []
    if shot == "full":  # 24f で 1.00→1.06（easeInOutSine）
        return [
            Keyframe(frame=0, zoom=1.0, easing=Easing.SINE_IN_OUT),
            Keyframe(frame=max(1, k24), zoom=1.06),
        ]
    if shot == "upper":  # 画面の幅の 4% を左から右へ（linear）
        return [
            Keyframe(frame=0, zoom=1.04, pan_x=-1.0, easing=Easing.LINEAR),
            Keyframe(frame=n - 1, zoom=1.04, pan_x=1.0),
        ]
    if shot == "face":  # 1.00→1.10（easeOutCubic）
        return [
            Keyframe(frame=0, zoom=1.0, easing=Easing.CUBIC_OUT),
            Keyframe(frame=n - 1, zoom=1.10),
        ]
    if shot == "cutout":  # 横に画面の幅の 6%（easeInOutSine）
        return [
            Keyframe(frame=0, zoom=1.06, pan_x=1.0, easing=Easing.SINE_IN_OUT),
            Keyframe(frame=n - 1, zoom=1.06, pan_x=-1.0),
        ]
    return []


# ---------------- V4 撮影処理 ----------------

DIFFUSION_PX = 24  # 1080p
LIGHT_COLOR, LIGHT_ALPHA = (0xFF, 0xB4, 0x6A), 0.35
SHADOW_ALPHA = 0.40
RIM_COLOR, RIM_ALPHA, RIM_PX = (0xFF, 0xF2, 0xD6), 0.70, 3


def _diagonal(w: int, h: int) -> np.ndarray:
    """右上の角を 0、左下の角を 1 とする対角線の位置（0〜1）。"""
    yy, xx = np.mgrid[0:h, 0:w]
    return np.clip(((w - 1 - xx) / max(1, w - 1) + yy / max(1, h - 1)) / 2, 0, 1)


def film(img: Image.Image, cutout: Image.Image | None = None) -> Image.Image:
    """V4: ディフュージョン（ぼかしを比較（明）50% → ソフトライト 50%）→ 右上からの光（35%）。

    cutout（切り抜きの絵を置いた所のアルファ）があれば、形の影（右上→左下へ黒、40%）と
    逆光（縁の帯を明るく、70%）も付ける。"""
    w, h = img.size
    k = h / 1080
    base = img.convert("RGB")
    blur = base.filter(ImageFilter.GaussianBlur(DIFFUSION_PX * k))
    out = Image.blend(base, ImageChops.lighter(base, blur), 0.5)
    out = Image.blend(out, ImageChops.soft_light(out, blur), 0.5)
    a = np.asarray(out).astype(np.float64)
    d = _diagonal(w, h)
    light = np.clip(1 - d / 0.6, 0, 1)[..., None] * LIGHT_ALPHA  # 対角線の 60% まで
    a = a + light * np.array(LIGHT_COLOR, np.float32)
    if cutout is not None:
        m = np.asarray(cutout.convert("L")).astype(np.float32) / 255
        a = a * (1 - (m * d * SHADOW_ALPHA)[..., None])
        inner = (
            np.asarray(
                cutout.convert("L").filter(ImageFilter.MinFilter(max(3, round(RIM_PX * k) * 2 + 1)))
            ).astype(np.float32)
            / 255
        )
        rim = Image.fromarray(np.clip((m - inner) * 255, 0, 255).astype(np.uint8))
        rim_a = np.asarray(rim.filter(ImageFilter.GaussianBlur(6 * k))).astype(np.float32) / 255
        a = a + (rim_a * RIM_ALPHA)[..., None] * np.array(RIM_COLOR, np.float32)
    res = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB").convert("RGBA")
    if img.mode == "RGBA":
        res.putalpha(img.getchannel("A"))
    return res


# ---------------- V10 画面の比率 ----------------

SCOPE = 2.39
LETTERBOX_FRAMES = 12


def letterbox_bar(w: int, h: int) -> int:
    """2.39:1 にするときの上下の黒帯の高さ（px）。"""
    return max(0, round((h - w / SCOPE) / 2))


def letterbox_spans(
    spans: list[tuple[float, float, str]], total_sec: float
) -> list[tuple[float, float]]:
    """V10: 黒帯を出す区間（秒）。イントロ（最初のサビの頭まで）と落ちサビ。"""
    out: list[tuple[float, float]] = []
    first_chorus = next((a for a, _, s in spans if s in ("chorus", "chorus_first")), None)
    if first_chorus is not None and first_chorus > 0:
        out.append((0.0, first_chorus))
    for a, b, s in spans:
        if s == "drop":
            if out and abs(out[-1][1] - a) < 1e-6:
                out[-1] = (out[-1][0], b)
            else:
                out.append((a, b))
    return [(a, min(b, total_sec)) for a, b in out if b > a]
