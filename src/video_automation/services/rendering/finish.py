"""書き出しの仕上げ（統合版 段階5: M9・L4・L5・S7）。FFmpeg のフィルターの文字列を作る。

- S7 縞の対策: `noise=alls=2:allf=t`（M9 のグレインを掛ける用途では掛けない）
- M9 仕上げ: グレイン（OVERLAY・不透明度 4%）→ 周辺減光 → 色収差（1080p で画面の端に 2px、中央は 0）
- L4 グロー: 線形の輝度 0.8 より明るい所を、半径 8・32・96px のブラーを 1 : 0.5 : 0.25 で加算
- L5 ハレーション: 輝度 180 より明るい所を gblur sigma 12・赤へ寄せ（rr 1・gg 0.35・bb 0.2）、
  スクリーンで 0.06

字幕は仕上げの後に焼き込む（文字にグレイン・色収差を掛けない）。LUT は同梱していないので使わない。
"""

from __future__ import annotations

from video_automation.schemas.design import Component, DesignProfile, Purpose

GRAIN_OPACITY = 0.04  # M9 のグレイン（OVERLAY）の不透明度
DITHER = "noise=alls=2:allf=t"  # S7
ABERRATION_PX = 2.0  # 1080p での画面の端の色のずれ
GLOW_THRESHOLD = 0.906  # 線形の輝度 0.8 を sRGB の値にしたもの（0.8 ** (1 / 2.2)）
GLOW_RADII = (8, 32, 96)  # 1080p での半径
GLOW_WEIGHTS = (1.0, 0.5, 0.25)
HALATION_LEVEL = 180 / 255
HALATION_SIGMA = 12
HALATION_AMOUNT = 0.06


def _even(x: float) -> int:
    return max(2, round(x / 2) * 2)


def aberration(width: int, height: int) -> str:
    """色収差: 赤を中心から少し大きく、青を少し小さくする（端で ±2px、中央で 0）。"""
    k = height / 1080
    d = ABERRATION_PX * k
    rw, rh = _even(width + 2 * d), _even(height + 2 * d * height / width)
    bw, bh = _even(width - 2 * d), _even(height - 2 * d * height / width)
    return (
        "format=gbrp,split=3[ca_g][ca_b][ca_r];"
        "[ca_g]extractplanes=g,setsar=1[ca_gp];"
        f"[ca_b]extractplanes=b,scale={bw}:{bh},pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[ca_bp];"
        f"[ca_r]extractplanes=r,scale={rw}:{rh},crop={width}:{height},setsar=1[ca_rp];"
        "[ca_gp][ca_bp][ca_rp]mergeplanes=0x001020:gbrp"
    )


def _bright(level: float) -> str:
    """輝度（灰色）で level より明るい所だけを取り出す（色の濃い原色だけが光らないように）。"""
    lo = round(level * 255)
    return f"format=gray,lut=c0='clip((val-{lo})*255/(255-{lo}),0,255)'"


def glow(height: int) -> str:
    k = height / 1080
    blurs = ";".join(
        f"[gl_s{i}]gblur=sigma={max(1.0, r * k / 2):.1f}[gl_b{i}]" for i, r in enumerate(GLOW_RADII)
    )
    w = " ".join(f"{x:g}" for x in GLOW_WEIGHTS)
    return (
        "format=gbrp,split[gl_a][gl_h];"
        f"[gl_h]{_bright(GLOW_THRESHOLD)},split=3[gl_s0][gl_s1][gl_s2];"
        f"{blurs};"
        f"[gl_b0][gl_b1][gl_b2]mix=inputs=3:weights='{w}':scale=1,format=gbrp[gl_l];"
        "[gl_a][gl_l]blend=all_mode=addition"
    )


def halation() -> str:
    return (
        "format=gbrp,split[hl_a][hl_h];"
        f"[hl_h]{_bright(HALATION_LEVEL)},gblur=sigma={HALATION_SIGMA},format=gbrp,"
        "colorchannelmixer=rr=1:gg=0.35:bb=0.2[hl_l];"
        f"[hl_a][hl_l]blend=all_mode=screen:all_opacity={HALATION_AMOUNT}"
    )


def grain() -> str:
    # OVERLAY の 4% ≒ 輝度に ±(0.04 × 255) の雑音
    return f"noise=c0s={round(GRAIN_OPACITY * 255)}:c0f=t+u,vignette=angle=PI/6"


PAPER_OPACITY = 0.10  # V9: 紙の質感（乗算）
PAPER_SHIFT = 0.02  # 2f ごとに画面の 2% 以内でずらす


def paper(width: int, height: int, fps: float = 30.0) -> str:
    """統合版 V9: 紙の質感（明るい灰色の雑音）を乗算・不透明度 10% で重ね、2f ごとにずらす。"""
    m = max(2, round(min(width, height) * PAPER_SHIFT))
    return (
        "null[v9a];"
        f"color=c=0xE6E6E6:s={width + m}x{height + m}:r={fps:g},format=gray,"
        "noise=alls=24,format=gbrp,"
        f"crop={width}:{height}:x='mod(floor(n/2)*37,{m})':y='mod(floor(n/2)*23,{m})'[v9t];"
        "[v9a]format=gbrp[v9b];"
        f"[v9b][v9t]blend=all_mode=multiply:all_opacity={PAPER_OPACITY}:shortest=1"
    )


def look_filter(name: str, width: int, height: int) -> str:
    """統合版 Y の仕上げ。riso（Y4）・synth（Y5）・pixel（Y6）・film（Y7）。"""
    k = height / 1080
    if name == "riso":  # 版ずれ（各色を ±3px）と粒（不透明度 12%）
        px = max(1, round(3 * k))
        return f"rgbashift=rh={px}:bh=-{px}:edge=wrap,noise=alls=12:allf=t"
    if name == "synth":  # 色収差（M9 の値）とグロー
        return f"{aberration(width, height)},{glow(height)}"
    if name == "pixel":  # 320×180 で描いてから最近傍で 6 倍。色は各チャンネル 4 段（64 色）に減らす
        sw, sh = max(2, _even(width / 6)), max(2, _even(height / 6))
        q = "lutrgb=r='floor(val/64)*85':g='floor(val/64)*85':b='floor(val/64)*85'"
        return f"scale={sw}:{sh}:flags=neighbor,{q},scale={width}:{height}:flags=neighbor"
    if name == "film":  # セピア（彩度 0 に #704214 を 18%）・グレイン 8%・周辺減光・±1px の揺れ
        j = max(1, round(k))
        return (
            # #704214 を不透明度 18% で重ねる = 各チャンネル val × 0.82 ＋ 色 × 0.18
            "hue=s=0,lutrgb=r='val*0.82+20.2':g='val*0.82+11.9':b='val*0.82+3.6',"
            f"noise=alls=8:allf=t,vignette=PI/5,"
            f"crop=iw-{2 * j}:ih-{2 * j}:{j}+{j}*sin(n*2.1):{j}+{j}*cos(n*1.7),"
            f"scale={width}:{height}"
        )
    return ""


def finish_chain(prof: DesignProfile | None, width: int, height: int) -> str:
    """映像に掛ける仕上げ（1 本のフィルターの連なり。ラベルは中で閉じる）。無ければ空。"""
    if prof is None:
        return ""
    parts: list[str] = []
    if prof.purpose in (Purpose.MV, Purpose.LYRIC):  # V9 テクスチャ（MV・リリック）
        parts.append(paper(width, height))
    if prof.allows(Component.GLOW):
        parts.append(glow(height))
    if prof.allows(Component.HALATION):
        parts.append(halation())
    if prof.allows(Component.GRAIN):
        parts.append(grain())
        parts.append(aberration(width, height))
    else:
        parts.append(DITHER)
    if prof.look:  # 統合版 Y1〜Y10 の仕上げ
        from video_automation.services.design.looks import LOOKS

        lk = LOOKS.get(prof.look)
        if lk and lk.finish:
            parts.append(look_filter(lk.finish, width, height))
    return ",".join(p for p in parts if p)


def applied(prof: DesignProfile | None) -> list[str]:
    """付けた仕上げの名前（書き出しの報告に使う）。"""
    if prof is None:
        return []
    names = []
    if prof.allows(Component.GLOW):
        names.append("グロー")
    if prof.allows(Component.HALATION):
        names.append("ハレーション")
    names.append("グレイン・周辺減光・色収差" if prof.allows(Component.GRAIN) else "縞の対策")
    return names
