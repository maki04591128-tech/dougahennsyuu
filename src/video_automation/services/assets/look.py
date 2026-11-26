"""素材の見た目を揃える（統合版 Q3）。

画像ごとに次の順で直す（値は統合版の初期値）。
1. 輝度（Rec.709 の係数で、ガンマのかかった値から求めた輝度 Y'）の中央値が 0.45 未満か
   0.55 を超えたら、0.50 になるようにガンマで寄せる
2. グレーワールドの補正（各色の平均を全体の平均にそろえる量）の 50% だけ掛ける（線形の値で）
3. HSV の彩度 S の 95 パーセンタイルが 0.85 を超えたら 0.85 まで下げる
4. Lab の平均と標準偏差を、用途の型の配色（base・base2・surface・main・accent）から作った
   基準へ 40% 寄せる
スクリーンショット・図・史料（文字や平らな色が多い画像、色の無い画像）は 1〜3 の 1 枚ずつの
補正だけにする（色を寄せると画面の色・資料の色が変わってしまうため）。

実装で決めたこと: 4 で彩度が上がって 0.85 を超えたときは、最後にもう一度 3 を掛ける。
透明の部分（アルファ）はそのまま残す。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

LUMA_LOW, LUMA_HIGH, LUMA_TARGET = 0.45, 0.55, 0.50
GRAY_WORLD = 0.5
SAT_P95_MAX = 0.85
LAB_PULL = 0.4
_WEIGHTS = np.array([0.2126, 0.7152, 0.0722])
_INDIVIDUAL_NAME = re.compile(
    r"スクリーンショット|スクショ|screenshot|screen ?shot|画面|図|グラフ|表|史料|資料|古文書|"
    r"document|scan|diagram|chart",
    re.IGNORECASE,
)

# sRGB（D65）→ XYZ
_M = np.array(
    [
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ]
)
_MI = np.linalg.inv(_M)
_WHITE = np.array([0.95047, 1.0, 1.08883])


def _lin(v: np.ndarray) -> np.ndarray:
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _gam(v: np.ndarray) -> np.ndarray:
    v = np.clip(v, 0.0, 1.0)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * v ** (1 / 2.4) - 0.055)


def to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB（0〜1、最後の軸が RGB）→ CIELAB。"""
    xyz = _lin(rgb) @ _M.T / _WHITE
    d = 6 / 29
    f = np.where(xyz > d**3, np.cbrt(xyz), xyz / (3 * d * d) + 4 / 29)
    return np.stack(
        [116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])],
        axis=-1,
    )


def from_lab(lab: np.ndarray) -> np.ndarray:
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], axis=-1)
    d = 6 / 29
    xyz = np.where(f > d, f**3, 3 * d * d * (f - 4 / 29)) * _WHITE
    return _gam(xyz @ _MI.T)


def hex_rgb(c: str) -> np.ndarray:
    h = c.lstrip("#")
    return np.array([int(h[i : i + 2], 16) / 255 for i in (0, 2, 4)])


def luma(rgb: np.ndarray) -> np.ndarray:
    return rgb @ _WEIGHTS


def saturation(rgb: np.ndarray) -> np.ndarray:
    mx, mn = rgb.max(axis=-1), rgb.min(axis=-1)
    return np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)


def _scale_saturation(rgb: np.ndarray, k: float) -> np.ndarray:
    """HSV の色相と明度 V を保って彩度 S を k 倍する。"""
    v = rgb.max(axis=-1, keepdims=True)
    return v - (v - rgb) * k


def is_individual(rgb: np.ndarray, name: str = "") -> bool:
    """スクリーンショット・図・史料（1 枚ずつの補正だけにする画像）か。"""
    if _INDIVIDUAL_NAME.search(name or ""):
        return True
    px = rgb.reshape(-1, 3)
    if len(px) == 0:
        return True
    if float(np.percentile(saturation(px), 95)) < 0.12:  # 色の無い画像（白黒の写真・書類）
        return True
    q = (px * 31).round().astype(np.int32)
    keys = q[:, 0] * 1024 + q[:, 1] * 32 + q[:, 2]
    _, counts = np.unique(keys, return_counts=True)
    top = np.sort(counts)[::-1][:16].sum() / len(keys)
    return bool(top >= 0.6)  # 平らな色が多い（画面・図・絵記号）


@dataclass
class LookResult:
    individual: bool
    steps: list[str] = field(default_factory=list)


def _thumb(rgb: np.ndarray, n: int = 256) -> np.ndarray:
    h, w = rgb.shape[:2]
    step = max(1, int(max(h, w) / n))
    return rgb[::step, ::step]


def match(
    rgb: np.ndarray,
    palette: list[str],
    name: str = "",
    individual: bool | None = None,
    alpha: np.ndarray | None = None,
) -> tuple[np.ndarray, LookResult]:
    """画像（H×W×3、0〜1）を直す。palette は用途の型の色（基準を作る色）。
    alpha（H×W、0〜1）があれば、数えるのは透明でない所だけ。"""
    out = rgb.astype(np.float64).copy()
    keep = None if alpha is None else _thumb(alpha[..., None])[..., 0] > 0.5

    def sample(a: np.ndarray) -> np.ndarray:
        t = _thumb(a)
        return t.reshape(-1, 3) if keep is None or not keep.any() else t[keep]

    ind = is_individual(sample(out), name) if individual is None else individual
    res = LookResult(individual=ind)
    # 1. 輝度の中央値
    for _ in range(3):  # ガンマ 1 回では中央値がちょうど 0.50 にならないので、3 回まで寄せる
        med = float(np.median(luma(sample(out))))
        if LUMA_LOW <= med <= LUMA_HIGH or not 0.0 < med < 1.0:
            break
        g = np.log(LUMA_TARGET) / np.log(med)
        out = np.clip(out, 0.0, 1.0) ** g
        if "luma" not in res.steps:
            res.steps.append("luma")
    # 2. グレーワールドの 50%
    lin = _lin(np.clip(out, 0.0, 1.0))
    means = _lin(np.clip(sample(out), 0.0, 1.0)).mean(axis=0)
    if np.all(means > 1e-4):
        gain = means.mean() / means
        gain = 1 + GRAY_WORLD * (gain - 1)
        if np.max(np.abs(gain - 1)) > 0.005:
            out = _gam(lin * gain)
            res.steps.append("gray_world")

    def cap() -> bool:
        nonlocal out
        p95 = float(np.percentile(saturation(sample(out)), 95))
        if p95 > SAT_P95_MAX:
            out = _scale_saturation(out, SAT_P95_MAX / p95)
            return True
        return False

    # 3. 彩度の上限
    if cap():
        res.steps.append("saturation")
    # 4. 用途の配色の基準へ Lab を 40% 寄せる（1 枚ずつの補正だけの画像は除く）
    if not ind and palette:
        ref = to_lab(np.array([hex_rgb(c) for c in palette]))
        mu_r, sd_r = ref.mean(axis=0), ref.std(axis=0)
        lab = to_lab(np.clip(out, 0.0, 1.0))
        t = to_lab(np.clip(sample(out), 0.0, 1.0))
        mu, sd = t.mean(axis=0), t.std(axis=0)
        mu2 = mu + LAB_PULL * (mu_r - mu)
        sd2 = sd + LAB_PULL * (sd_r - sd)
        k = np.where(sd > 1e-6, sd2 / np.maximum(sd, 1e-6), 1.0)
        lab = (lab - mu) * k + mu2
        lab[..., 0] = np.clip(lab[..., 0], 0.0, 100.0)
        out = from_lab(lab)
        res.steps.append("lab")
        if cap():
            res.steps.append("saturation")
    return np.clip(out, 0.0, 1.0), res


def palette_colors(p: object) -> list[str]:
    """基準を作る色（画面の面の色。文字の色は入れない）。"""
    return [getattr(p, k) for k in ("base", "base2", "surface", "main", "accent")]


def match_file(src: Path, dst: Path, palette: list[str], name: str = "") -> LookResult:
    """画像ファイルを直して dst（PNG）に書く。透明の部分はそのまま。"""
    with Image.open(src) as opened:
        im = ImageOps.exif_transpose(opened)  # 写真の向き（EXIF）を画素に反映する
        im.load()
        has_alpha = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
        alpha = im.convert("RGBA").getchannel("A") if has_alpha else None
        rgb = np.asarray(im.convert("RGB"), dtype=np.float64) / 255.0
    a = None if alpha is None else np.asarray(alpha, dtype=np.float64) / 255.0
    out, res = match(rgb, palette, name, alpha=a)
    img = Image.fromarray((out * 255 + 0.5).astype(np.uint8), "RGB")
    if alpha is not None:
        img.putalpha(alpha)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst)
    return res
