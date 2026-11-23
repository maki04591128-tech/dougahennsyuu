"""クリップのアニメーション → FFmpeg フィルタ（P7-02〜05 の描画部分）。

時刻の扱い: 各クリップのフィルタ列では、クリップの描画開始時点を 0 とする時刻 t が流れる。
範囲指定の書き出しでクリップの途中から始まる場合があるため、クリップ先頭からの秒は t + k。
- zoompan（カメラ）: on / fps + k
- rotate / scale(eval=frame): t + k
- geq（不透明度・ワイプ）: T + k
- overlay（位置）: t - off + k（overlay の t は出力全体の時刻）
"""

from dataclasses import dataclass, field
from fractions import Fraction

from video_automation.schemas.animation import (
    PROPS,
    ClipAnimation,
    Easing,
    TransitionType,
)
from video_automation.services.animation.motion import (
    ease,
    is_static,
    num,
    track_expr,
    windows,
)

SLIDE_DIR = {
    TransitionType.SLIDE_LEFT: (1, 0),  # 右から入って左へ
    TransitionType.SLIDE_RIGHT: (-1, 0),
    TransitionType.SLIDE_UP: (0, 1),  # 下から入って上へ
    TransitionType.SLIDE_DOWN: (0, -1),
}
EXTENDS_PREVIOUS = {
    TransitionType.DISSOLVE,
    TransitionType.SLIDE_LEFT,
    TransitionType.SLIDE_RIGHT,
    TransitionType.SLIDE_UP,
    TransitionType.SLIDE_DOWN,
    TransitionType.WIPE_LEFT,
    TransitionType.WIPE_RIGHT,
    TransitionType.ZOOM_IN,
}


@dataclass
class ClipAnimFilters:
    pre: list[str] = field(default_factory=list)  # レイアウト前（カメラ）
    post: list[str] = field(default_factory=list)  # レイアウト後（回転・不透明度。サイズ固定）
    # 要素の拡大縮小（フレームごとにサイズが変わる）。geq・fade 等はサイズ変化に対応せず
    # FFmpeg 6.1 ではメモリ破壊で異常終了したため、必ず最後に置く
    resize: list[str] = field(default_factory=list)
    dx: str | None = None  # overlay の x に足す式（画面幅の割合）
    dy: str | None = None
    audio_fade_in_sec: float = 0.0  # 入りのトランジションに合わせた音のフェード


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _enable(wins: list[tuple[float, float]], k: float) -> str:
    return "+".join(f"between(t,{num(a - k)},{num(b - k)})" for a, b in wins)


def clip_filters(
    anim: ClipAnimation,
    *,
    fps: Fraction,
    k: float,
    off: float,
    clip_sec: float,
    src_size: tuple[int | None, int | None],
    canvas: tuple[int, int],
    is_image: bool,
) -> ClipAnimFilters:
    out = ClipAnimFilters()
    fps_s = f"{fps.numerator}/{fps.denominator}"

    def tr(prop: str) -> list[tuple[int, float, Easing]]:
        return anim.track(prop)

    # ---------- カメラ（素材の中でズーム・パン） ----------
    zoom, panx, pany = tr("zoom"), tr("pan_x"), tr("pan_y")
    aw, ah = src_size
    if aw and ah and not (is_static(zoom, 1.0) and is_static(panx, 0.0) and is_static(pany, 0.0)):
        cw, ch = canvas
        f = max(cw / aw, ch / ah)
        zw, zh = _even(aw * f), _even(ah * f)
        # zoompan は位置を整数で丸めるため、2倍に拡大してから処理して揺れを抑える（4K は等倍）
        j = 2 if max(zw, zh) <= 2200 else 1
        tz = f"(on*{fps.denominator}/{fps.numerator}+{num(k)})"
        z = f"max(1,{track_expr(zoom, fps, tz, 1.0)})"
        px = track_expr(panx, fps, tz, 0.0)
        py = track_expr(pany, fps, tz, 0.0)
        out.pre += [
            f"scale={zw * j}:{zh * j}",
            f"zoompan=z='{z}':x='(iw-iw/zoom)*(0.5+0.5*({px}))':"
            f"y='(ih-ih/zoom)*(0.5+0.5*({py}))':d=1:s={zw}x{zh}:fps={fps_s}",
            "setsar=1",
        ]

    # ---------- 要素（回転・拡大・不透明度・位置） ----------
    tt = f"(t+{num(k)})"
    rot = tr("rotation")
    if not is_static(rot, 0.0):
        # c=none は前のフレームを消さず残像が積み重なるため、透明で塗りつぶす
        out.post.append(
            f"rotate=a='({track_expr(rot, fps, tt, 0.0)})*PI/180':c=black@0:ow='hypot(iw,ih)':oh=ow"
        )

    tran = anim.transition_in
    d = float(Fraction(tran.duration_frames) / fps) if tran else 0.0
    d = min(d, clip_sec) if tran else 0.0
    sc = tr("scale")
    scale_terms: list[str] = []
    if not is_static(sc, 1.0):
        scale_terms.append(f"({track_expr(sc, fps, tt, 1.0)})")
    if tran and tran.type == TransitionType.ZOOM_IN and d > 0:
        u = f"min(max({tt}/{num(d)},0),1)"
        scale_terms.append(f"(1.15-0.15*{ease(u, Easing.EASE_OUT)})")
    if scale_terms:
        s = "*".join(scale_terms)
        out.resize.append(
            f"scale=w='max(2,trunc(iw*{s}/2)*2)':h='max(2,trunc(ih*{s}/2)*2)':eval=frame"
        )

    # 不透明度（geq は重いので値が 1 未満になりうる区間だけ有効にする）
    gt = f"(T+{num(k)})"
    alpha_terms: list[str] = []
    wins: list[tuple[float, float]] = []
    op = tr("opacity")
    if not is_static(op, 1.0):
        alpha_terms.append(f"({track_expr(op, fps, gt, 1.0)})")
        wins += windows(op, fps, lambda v: v < 1.0, clip_sec)
    if tran and d > 0:
        half = d / 2 if tran.type == TransitionType.DIP_TO_BLACK else d
        if tran.type in (TransitionType.WIPE_LEFT, TransitionType.WIPE_RIGHT):
            u = f"min(max({gt}/{num(d)},0),1)"
            cond = "lte(X,W*{u})" if tran.type == TransitionType.WIPE_LEFT else "gte(X,W*(1-{u}))"
            alpha_terms.append(cond.format(u=ease(u, Easing.EASE_IN_OUT)))
        elif tran.type not in SLIDE_DIR:
            alpha_terms.append(f"min(max({gt}/{num(half)},0),1)")
        if tran.type not in SLIDE_DIR:
            wins.append((0.0, d))
        out.audio_fade_in_sec = half
    if alpha_terms:
        merged: list[tuple[float, float]] = []
        for a, b in sorted(wins):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        en = _enable(merged, k) if merged else "1"
        out.post.append(
            "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='alpha(X,Y)*{'*'.join(alpha_terms)}':enable='{en}'"
        )

    # 位置（overlay）
    to = f"(t-{num(off)}+{num(k)})"
    xs, ys = tr("x"), tr("y")
    dx = [] if is_static(xs, 0.0) else [f"({track_expr(xs, fps, to, 0.0)})"]
    dy = [] if is_static(ys, 0.0) else [f"({track_expr(ys, fps, to, 0.0)})"]
    if tran and tran.type in SLIDE_DIR and d > 0:
        sx, sy = SLIDE_DIR[tran.type]
        u = f"min(max({to}/{num(d)},0),1)"
        rest = f"(1-{ease(u, Easing.EASE_OUT)})"
        if sx:
            dx.append(f"{sx}*{rest}")
        if sy:
            dy.append(f"{sy}*{rest}")
    out.dx = "+".join(dx) or None
    out.dy = "+".join(dy) or None
    return out


def defaults_only(anim: ClipAnimation) -> bool:
    """描画に影響しない（すべて既定値）なら True。"""
    if anim.transition_in:
        return False
    return all(is_static(anim.track(p), PROPS[p][0]) for p in PROPS)
