"""統合版 段階5e: 歌詞の画面の組み方（U2〜U5・U21〜U22。MV・リリックで曲の方向性を選んだとき）。

- U21 要素の表: 画面は「軸ごとに値を 1 つ選んだ組み合わせ」。文字は役割（UR）ごとのかたまりで、
  かたまりごとに入れ物（UC）・置き場所（UP）・組み方（UL）・大きさ（US）・字形（UF）・質感（UG）・
  時間（UT）を 1 つずつ持つ。画面には絵の扱い（UH）・色（UK）・飾り（UD）を 1 つずつ
- U22 組み合わせの決まり: check() で検査する（1・2・3・4・7・12・16。6 は色を 3 つで持つ形で守る）
- 方向性 8 種（U1）は、A メロ・B メロ用とサビ用の 2 つの組み合わせの既定値（PRESETS）。
  1 曲を通して色・字形・絵の位置を固定し（決まり 17）、サビで大きさ・置き場所・組み方・時間を
  替える（決まり 7）
- 描画は Pillow。1 フレーズ（同じセクションの続く最大 4 行）を 1 枚の不透明な画の列にする。
  1920×1080 の px の値を、出力の高さに比例させて使う
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

# ---- U21 要素の表（番号 → (軸, 名前)） ------------------------------------------------------
ELEMENTS: dict[str, tuple[str, str]] = {
    "UR0": ("役割", "文字なし"), "UR1": ("役割", "字幕"), "UR2": ("役割", "見出し"),
    "UR3": ("役割", "模様"), "UR4": ("役割", "看板"), "UR5": ("役割", "クレジット"),
    "UC0": ("入れ物", "なし"), "UC1": ("入れ物", "帯"), "UC2": ("入れ物", "札"),
    "UC3": ("入れ物", "箱"), "UC4": ("入れ物", "窓"), "UC5": ("入れ物", "カード"),
    "UC6": ("入れ物", "小道具"), "UC7": ("入れ物", "吹き出し"), "UC8": ("入れ物", "斜めの札"),
    "UP1": ("置き場所", "絵の外"), "UP2": ("置き場所", "画面の端"),
    "UP3": ("置き場所", "絵のまわり"),
    "UP4": ("置き場所", "絵の前に重ねる"), "UP5": ("置き場所", "絵の後ろ"),
    "UP6": ("置き場所", "全面"),
    "UP7": ("置き場所", "下の中央"), "UP8": ("置き場所", "画面の中央"),
    "UP9": ("置き場所", "人物の近く"),
    "UP10": ("置き場所", "絵の列の間"), "UP12": ("置き場所", "下端からはみ出す"),
    "UL1": ("組み方", "横1行"), "UL2": ("組み方", "横2行"), "UL3": ("組み方", "縦1列"),
    "UL4": ("組み方", "縦の列を並べる"), "UL5": ("組み方", "1文字ずつ散らす"),
    "UL6": ("組み方", "幅いっぱいに均等"), "UL7": ("組み方", "円周"), "UL8": ("組み方", "2段の壁"),
    "UL9": ("組み方", "行の塊"), "UL10": ("組み方", "語の間に絵を挟む"),
    "UL11": ("組み方", "かぎ括弧で囲む（縦）"), "UL12": ("組み方", "記号を中心に四方へ置く"),
    "UL13": ("組み方", "傾けた大きな1語"), "UL14": ("組み方", "ふりがなを付ける"),
    "US1": ("大きさ", "最小"), "US2": ("大きさ", "字幕"), "US3": ("大きさ", "強い字幕"),
    "US4": ("大きさ", "見出し"), "US5": ("大きさ", "巨大"), "US6": ("大きさ", "壁"),
    "US7": ("大きさ", "1字ずつ変える"),
    "UF1": ("字形", "細い明朝"), "UF2": ("字形", "太い明朝"), "UF3": ("字形", "極太ゴシック"),
    "UF4": ("字形", "丸ゴシック"), "UF5": ("字形", "手書き"), "UF6": ("字形", "ポップ"),
    "UF7": ("字形", "筆文字"),
    "UG0": ("質感", "なし"), "UG1": ("質感", "フチ"), "UG2": ("質感", "箱の反転"),
    "UG3": ("質感", "線だけ"), "UG4": ("質感", "金のグラデーション"), "UG5": ("質感", "半透明"),
    "UG6": ("質感", "立体"), "UG7": ("質感", "落ち影"), "UG9": ("質感", "黒い棒で伏せる"),
    "UK1": ("色", "1色＋白（または黒）＋差し色1"), "UK2": ("色", "白黒＋差し色1"),
    "UK3": ("色", "2色を拍で入れ替える"), "UK4": ("色", "単色3色のカード"),
    "UK5": ("色", "絵の色のまま＋白"), "UK6": ("色", "同じ色相の明暗だけ"),
    "UH1": ("絵の扱い", "そのまま"), "UH2": ("絵の扱い", "白黒にする"),
    "UH3": ("絵の扱い", "影絵にする"),
    "UH4": ("絵の扱い", "単色に沈める"), "UH5": ("絵の扱い", "丸い窓で切る"),
    "UH6": ("絵の扱い", "枠に入れる"), "UH7": ("絵の扱い", "拡大してはみ出す"),
    "UH8": ("絵の扱い", "小さく置いて余白を作る"), "UH9": ("絵の扱い", "人物なし"),
    "UH10": ("絵の扱い", "増やして散らす"), "UH11": ("絵の扱い", "コマ割り"),
    "UH12": ("絵の扱い", "分割"),
    "UH13": ("絵の扱い", "同じ大きさで横一列に並べる"), "UH14": ("絵の扱い", "列の1枚だけ色を残す"),
    "UH15": ("絵の扱い", "白く飛ばす"),
    "UT1": ("時間", "1文字ずつ"), "UT2": ("時間", "1行ずつ"), "UT3": ("時間", "拍に1つ"),
    "UT4": ("時間", "2拍ごとに動きなしで入れ替え"), "UT5": ("時間", "積んで残す"),
    "UT6": ("時間", "流し続ける"), "UT7": ("時間", "1語ずつ場所を変える"),
    "UT8": ("時間", "先に薄く出して濃くする"), "UT9": ("時間", "題名を出したままにする"),
    "UD0": ("飾り", "なし"), "UD1": ("飾り", "二重の額縁"), "UD2": ("飾り", "ファインダーの角"),
    "UD3": ("飾り", "十字の線"), "UD4": ("飾り", "左右の黒帯"), "UD5": ("飾り", "粒を散らす"),
    "UD6": ("飾り", "同心の輪を背に"), "UD7": ("飾り", "×印を散らす"),
    "UD8": ("飾り", "斜めの色面"),
    "UD9": ("飾り", "上下の黒帯"),
}  # fmt: skip

# US の大きさ（1080p の px）。US7 は 1 字ずつ変える（最小 90・最大 280）
SIZE_PX = {"US1": 38, "US2": 46, "US3": 76, "US4": 170, "US5": 300, "US6": 590}
US7_MIN, US7_MAX = 90, 280
# UF → 書体（無ければ呼び出し側の代わりの書体）
FONT_OF = {
    "UF1": "Shippori Mincho Regular", "UF2": "Shippori Mincho ExtraBold",
    "UF3": "Dela Gothic One", "UF4": "Zen Maru Gothic Medium", "UF5": "Zen Kurenaido",
    "UF6": "RocknRoll One", "UF7": "Yuji Boku",
}  # fmt: skip
# 描けるもの（このモジュールで描き分ける値）。ほかの値は要素の表にだけある
DRAWN = {
    "UC0", "UC1", "UC2", "UC3", "UC5", "UP2", "UP3", "UP4", "UP5", "UP7", "UP8",
    "UL1", "UL2", "UL3", "UL4", "UL5", "UL6", "UL7", "UL8", "UL9",
    "US1", "US2", "US3", "US4", "US5", "US6", "US7", "UF1", "UF2", "UF3", "UF4", "UF5", "UF6",
    "UG0", "UG1", "UG3", "UG4", "UG5", "UK1", "UK2", "UK3", "UK5",
    "UH1", "UH2", "UH3", "UH4", "UH5", "UH7", "UH8", "UT1", "UT2", "UT3", "UT4", "UT5", "UT6",
    "UD0", "UD1", "UD3", "UK4",
}  # fmt: skip


@dataclass(frozen=True)
class Block:
    """文字の 1 かたまり（役割ごと）。px は大きさを US でなく直に決めるとき（1080p）。"""

    role: str  # UR1〜UR3
    uc: str = "UC0"
    up: str = "UP7"
    ul: str = "UL1"
    us: str = "US2"
    uf: str = "UF4"
    ug: str = "UG0"
    ut: str = "UT2"
    px: int | None = None
    tilt: float = 0.0  # 度（反時計回りが正）
    accent_last: bool = False  # 最後の行・列・札だけ差し色にする


@dataclass(frozen=True)
class Screen:
    """1 つの画面の組み合わせ。colors は（地, 文字, 差し色）。地が "" なら絵を全面に敷く（UK5）。"""

    blocks: tuple[Block, ...]
    uh: str = "UH1"
    uk: str = "UK1"
    ud: str = "UD0"
    colors: tuple[str, str, str] = ("#111111", "#FFFFFF", "#E0193A")
    pic_x: float = 0.5  # 絵の中心の x（画面の幅の比）。決まり 17 で 1 曲を通して固定
    flash: bool = False  # U5: 入りの頭に白の重ね（90%→0、3f）
    ground2: str = ""  # 地の下側のグラデーションの色（無ければ単色）


def size_px(b: Block) -> int:
    return b.px if b.px is not None else SIZE_PX.get(b.us, 46)


# ---- U22 組み合わせの決まり -----------------------------------------------------------------
def check(s: Screen, other: Screen | None = None) -> list[str]:
    """決まりに反する所（空なら合格）。other はもう一方の区間（A メロとサビ）の組み合わせ。"""
    bad: list[str] = []
    if len(s.blocks) > 3:
        bad.append("かたまりは最大 3 つ")
    for role in ("UR1", "UR2"):
        if sum(b.role == role for b in s.blocks) > 1:
            bad.append(f"{role} は 1 画面に最大 1 つ")
    read = [b for b in s.blocks if b.role == "UR1"]
    # UR3（模様）は見出しの大きさ（US4）以上のときだけ数える（円周 UL7 の文字は最大 96px のため）
    show = [b for b in s.blocks if b.role == "UR2" or (b.role == "UR3" and size_px(b) >= 170)]
    for r in read:  # 1: 読ませる文字と見せる文字の倍率は最小 3.5
        for v in show:
            if v.us != "US7" and size_px(v) / size_px(r) < 3.5 and s.uh != "UH9":
                bad.append("決まり1: 字幕と見出しの大きさの倍率が 3.5 未満")
    weak = s.uh in ("UH2", "UH3", "UH4")
    for b in s.blocks:  # 2: 絵の前に重ねるならフチか絵を弱める
        if b.up == "UP4" and b.ug != "UG1" and not weak and b.uc not in ("UC2", "UC3"):
            bad.append("決まり2: 絵の前の文字にフチも絵の弱めも無い")
        if b.us in ("US1", "US2") and b.up == "UP4" and b.uc == "UC0" and b.ug != "UG1":
            bad.append("決まり3: 絵の上の小さな文字に入れ物もフチも無い")
    if s.ud not in ("UD0", "UD1", "UD2", "UD3", "UD4", "UD5", "UD6", "UD7", "UD8", "UD9"):
        bad.append("決まり16: 飾りは 1 画面に 1 つ")
    if other is not None:  # 7: A メロ・B メロとサビは UC・UP・US・UL・UT の中で最小 2 つを変える
        a = {(b.role, k, getattr(b, k)) for b in s.blocks for k in ("uc", "up", "us", "ul", "ut")}
        c = {
            (b.role, k, getattr(b, k)) for b in other.blocks for k in ("uc", "up", "us", "ul", "ut")
        }
        changed = {k for _r, k, _v in a ^ c}
        if len(changed) < 2:
            bad.append("決まり7: A メロとサビで変わる軸が 2 つ未満")
        if s.pic_x != other.pic_x:
            bad.append("決まり17: 絵の位置を 1 曲の中で変えている")
    for b in s.blocks:
        if b.us in ("US5", "US6") and b.role != "UR3" and b.ul not in ("UL1", "UL3", "UL4"):
            bad.append("決まり4: US5 以上の読ませる文字の組み方")
    return bad


def clip_big(text: str, b: Block) -> str:
    """決まり4: US5 以上の見出し（UR2）は 4 字まで。"""
    if b.role == "UR2" and b.us in ("US5", "US6"):
        return "".join(ch for ch in text if not ch.isspace())[:4]
    return text


# ---- 方向性 8 種の既定（A メロ・B メロ用、サビ用） ------------------------------------------
_COOL = ("#12060A", "#F4EDEA", "#E0193A")
PRESETS: dict[str, tuple[Screen, Screen]] = {
    # ボカロ（かっこいい系）: 小さな縦書きを端に＋中央の題字 → サビは人物に重ねる巨大な縦書き（U11）
    "vocaloid_cool": (
        Screen(
            (
                Block("UR1", up="UP2", ul="UL3", us="US2", uf="UF2", ut="UT1"),
                Block("UR2", up="UP8", ul="UL1", us="US4", uf="UF2", ug="UG1", ut="UT1"),
            ),
            uh="UH1",
            colors=_COOL,
            ground2="#8E1024",
        ),
        Screen(
            (
                Block("UR1", up="UP2", ul="UL3", us="US2", uf="UF2", ut="UT2"),
                Block("UR2", up="UP4", ul="UL4", us="US5", uf="UF2", ut="UT4"),
            ),
            uh="UH4",
            colors=("#5A45B0", "#FFFFFF", "#FFE27A"),
        ),
    ),
    # ボカロ（かわいい系）: 余白の中の小さな歌詞（U20） → サビは絵のまわりに 1 文字ずつ散らす
    "vocaloid_cute": (
        Screen(
            (Block("UR1", up="UP3", ul="UL1", us="US1", uf="UF4", ut="UT2"),),
            uh="UH8",
            colors=("#F6BCD2", "#7A2E55", "#FFFFFF"),
        ),
        Screen(
            (
                Block("UR1", up="UP7", ul="UL1", us="US2", uf="UF6", ug="UG1", ut="UT2"),
                Block("UR2", up="UP3", ul="UL5", us="US7", uf="UF6", ug="UG1", ut="UT3"),
            ),
            uh="UH1",
            colors=("#FFD9E8", "#E8336D", "#FFFFFF"),
        ),
    ),
    # ボカロ（しっとり系）: 縦書きを右から積み、1 列だけ大きく → サビは額縁の中の行の塊
    "vocaloid_calm": (
        Screen(
            (
                Block("UR1", up="UP2", ul="UL4", us="US1", uf="UF1", ut="UT5"),
                Block("UR2", up="UP2", ul="UL3", us="US4", uf="UF1", ut="UT5", px=170),
            ),
            uh="UH8",
            colors=("#F4F4F2", "#2B2D33", "#7C8494"),
            pic_x=0.16,
        ),
        Screen(
            (
                Block("UR1", up="UP7", ul="UL1", us="US2", uf="UF1", ut="UT2"),
                Block("UR2", up="UP8", ul="UL9", us="US4", uf="UF1", ut="UT3", accent_last=True),
            ),
            uh="UH8",
            ud="UD1",
            colors=("#F4F4F2", "#2B2D33", "#C2444C"),
            pic_x=0.16,
        ),
    ),
    # アニソン（U5）: 絵＋下の字幕＋はみ出す手書き → サビは単色の全面＋巨大な 2 行＋絵を拡大
    "anison": (
        Screen(
            (
                Block("UR1", up="UP7", ul="UL1", us="US2", uf="UF4", ug="UG1", ut="UT2"),
                Block(
                    "UR3",
                    up="UP5",
                    ul="UL1",
                    us="US6",
                    uf="UF5",
                    ug="UG5",
                    ut="UT6",
                    px=620,
                    tilt=5.0,
                ),
            ),
            uh="UH1",
            colors=("#2E7BE0", "#FFFFFF", "#FFE600"),
            ground2="#DDF3FF",
            pic_x=0.68,
        ),
        Screen(
            (
                Block("UR1", up="UP7", ul="UL1", us="US2", uf="UF4", ug="UG1", ut="UT2"),
                Block(
                    "UR2",
                    up="UP2",
                    ul="UL1",
                    us="US5",
                    uf="UF5",
                    ut="UT3",
                    px=330,
                    tilt=4.0,
                    accent_last=True,
                ),
            ),
            uh="UH7",
            colors=("#E4212B", "#FFFFFF", "#FFE600"),
            pic_x=0.68,
            flash=True,
        ),
    ),
    # バラード: 背景つきの絵に右の縦書き（UK5） → サビは絵を沈めて巨大な縦の列を重ねる
    "ballad": (
        Screen(
            (
                Block("UR1", up="UP2", ul="UL4", us="US2", uf="UF5", ut="UT1", px=60),
                Block("UR2", up="UP2", ul="UL3", us="US4", uf="UF5", ut="UT1", px=210),
            ),
            uh="UH1",
            uk="UK5",
            colors=("", "#FFFFFF", "#FFFFFF"),
        ),
        Screen(
            (
                Block("UR1", up="UP7", ul="UL1", us="US2", uf="UF5", ug="UG1", ut="UT2"),
                Block("UR2", up="UP4", ul="UL4", us="US5", uf="UF5", ug="UG1", ut="UT4"),
            ),
            uh="UH4",
            uk="UK5",
            colors=("", "#FFFFFF", "#FFFFFF"),
        ),
    ),
    # ラップ: 黒い札の縦書きを積む（U17） → サビは壁の文字＋札の字幕、地と文字を拍で入れ替え
    "rap": (
        Screen(
            (
                Block(
                    "UR1",
                    uc="UC2",
                    up="UP2",
                    ul="UL4",
                    us="US2",
                    uf="UF2",
                    ut="UT5",
                    px=62,
                    accent_last=True,
                ),
            ),
            uh="UH2",
            uk="UK2",
            colors=("#C9C9C9", "#111111", "#D81E2C"),
            pic_x=0.74,
        ),
        Screen(
            (
                Block("UR1", uc="UC2", up="UP7", ul="UL1", us="US3", uf="UF3", ut="UT3", px=84),
                Block("UR3", up="UP5", ul="UL8", us="US6", uf="UF3", ut="UT3"),
            ),
            uh="UH1",
            uk="UK3",
            colors=("#EDE7DA", "#111111", "#FFE600"),
            pic_x=0.74,
        ),
    ),
    # ポップソング: 円の中の絵＋まわりを回る文字＋下の帯の字幕
    # → サビは 1 文字ずつ大きさを変えて全面に
    "pop": (
        Screen(
            (
                Block("UR1", uc="UC2", up="UP7", ul="UL1", us="US3", uf="UF6", ut="UT2", px=72),
                Block("UR3", up="UP3", ul="UL7", us="US3", uf="UF6", ut="UT6", px=84),
            ),
            uh="UH1",
            colors=("#FFC82E", "#111111", "#FF5A5F"),
        ),
        Screen(
            (
                Block("UR1", up="UP7", ul="UL1", us="US2", uf="UF1", ut="UT2", px=54),
                Block("UR2", up="UP5", ul="UL6", us="US7", uf="UF1", ut="UT1", accent_last=True),
            ),
            uh="UH1",
            colors=("#FFE9A6", "#3A3320", "#E23B3B"),
        ),
    ),
    # アイドルソング: 絵の後ろの大きな文字＋散らした札 → サビは金の飾り文字と額縁（U14）
    "idol": (
        Screen(
            (
                Block("UR1", uc="UC2", up="UP3", ul="UL5", us="US3", uf="UF6", ut="UT3"),
                Block("UR2", up="UP5", ul="UL1", us="US5", uf="UF6", ug="UG1", ut="UT1"),
            ),
            uh="UH1",
            colors=("#FFD3EA", "#FF5FA2", "#55C6F2"),
            ground2="#D6F1FF",
        ),
        Screen(
            (
                Block("UR1", up="UP7", ul="UL1", us="US2", uf="UF3", ug="UG4", ut="UT2", px=60),
                Block("UR2", up="UP5", ul="UL1", us="US5", uf="UF3", ug="UG4", ut="UT3", px=330),
            ),
            uh="UH1",
            ud="UD1",
            colors=("#150A0A", "#F2C94C", "#E2B64A"),
            ground2="#3A1A12",
        ),
    ),
}


DIRECTION_NAMES = {
    "vocaloid_cool": "ボカロ（かっこいい系）", "vocaloid_cute": "ボカロ（かわいい系）",
    "vocaloid_calm": "ボカロ（しっとり系）", "anison": "アニソン", "ballad": "バラード",
    "rap": "ラップ", "pop": "ポップソング", "idol": "アイドルソング",
}  # fmt: skip


# 組み合わせの例 U10・U12・U13・U15・U18（方向性の既定に足す、区間ごとの画面）
EXTRA: dict[str, Screen] = {
    # U10 飾り字幕: 下の 76px の字幕（フチ）＋上の線だけの題字。1 番の A メロ
    "U10": Screen((Block("UR1", up="UP7", ul="UL1", us="US3", uf="UF6", ug="UG1", ut="UT2"),
                   Block("UR2", up="UP5", ul="UL1", us="US5", uf="UF6", ug="UG3", ut="UT1")),
                  uh="UH1", colors=("#FFC82E", "#111111", "#FF5A5F")),
    # U12 枠の中の絵と 2 行の字幕（白黒の絵・差し色 1）。B メロ
    "U12": Screen((Block("UR1", up="UP7", ul="UL2", us="US2", uf="UF4", ut="UT2"),),
                  uh="UH2", uk="UK2", colors=("#F1F0EC", "#1E1E1E", "#35B89A")),
    # U13 交差する線と箱の中の縦書き → 端の巨大な縦書き。B メロ
    "U13": Screen((Block("UR1", uc="UC3", up="UP8", ul="UL4", us="US1", uf="UF2", ut="UT1",
                         px=36),
                   Block("UR2", up="UP2", ul="UL4", us="US5", uf="UF2", ug="UG1", ut="UT3",
                         accent_last=True)),
                  uh="UH1", ud="UD3", colors=_COOL),
    # U15 丸い窓の中の絵（十字の線）。2 回目からの A メロ
    "U15": Screen((Block("UR1", up="UP7", ul="UL1", us="US2", uf="UF4", ug="UG1", ut="UT2"),),
                  uh="UH5", ud="UD3", colors=("#0C0F16", "#FFFFFF", "#E9E4FF")),
    # U18 傾けた札を人物（影絵）のまわりに散らす。2 回目からのサビ
    "U18": Screen((Block("UR2", uc="UC2", up="UP3", ul="UL5", us="US3", uf="UF4", ut="UT3",
                         px=84),),
                  uh="UH3", uk="UK2", colors=("#0C0C0C", "#FFFFFF", "#BDBDBD")),
    # U9 下の帯に小さな歌詞（方向性を選んでいない曲で、絵が全面のとき。A メロ・B メロ）
    "U9": Screen((Block("UR1", uc="UC1", up="UP1", ul="UL2", us="US2", uf="UF5", ut="UT2"),),
                 uh="UH1", uk="UK5", colors=("#0E0E10", "#F3EFE6", "#F3EFE6")),
}  # fmt: skip
U9_SECTIONS = ("verse", "bridge")  # U9 を使うセクション（サビは動く文字のまま）
BAND_TOP = 830  # UC1: 絵を画面の上から 830px（77%）に収め、その下を帯にする

# 方向性 → (セクション, 何回目（0 から。None はすべて）) → 例の画面
SECTION_SCREENS: dict[str, list[tuple[str, int | None, str]]] = {
    "vocaloid_cool": [("verse", 1, "U15"), ("verse", 3, "U15"), ("bridge", None, "U13"),
                      ("chorus", 1, "U18"), ("chorus", 3, "U18")],
    "pop": [("verse", 0, "U10"), ("bridge", None, "U12")],
    "anison": [("bridge", None, "U13")],
}  # fmt: skip


def screen_for(direction: str, section: str, nth: int = 0) -> Screen:
    """方向性とセクション（M3）→ 画面の組み合わせ。サビ系はサビ用、ほかは A メロ用。
    SECTION_SCREENS に当たる区間（例: かっこいい系の B メロは U13）は、その例の画面にする。
    nth はそのセクションの何回目のフレーズか（サビは chorus_first も chorus として数える）。"""
    verse, chorus = PRESETS[direction]
    kind = "chorus" if section in ("chorus", "chorus_first") else section
    for sec, n, ex in SECTION_SCREENS.get(direction, []):
        if sec == kind and (n is None or n == nth):
            base = EXTRA[ex]
            # 決まり 17: 絵の位置は 1 曲で固定する（方向性の既定の位置に合わせる）
            return replace(base, pic_x=verse.pic_x)
    return chorus if section in ("chorus", "chorus_first", "drop") else verse


# ---- フレーズ -------------------------------------------------------------------------------
@dataclass
class Phrase:
    """同じセクションの続く行（最大 4 行）。

    frames は行ごとの (始め, 終わり)（フレーズの頭から）。"""

    section: str
    lines: list[str]
    frames: list[tuple[int, int]]
    start: int  # 曲の頭からのフレーム
    end: int
    key: int = 0  # U4 の鍵の行
    beats: list[int] = field(default_factory=list)  # フレーズの頭からの拍のフレーム


def phrases(
    texts: list[str], spans: list[tuple[int, int]], secs: list[str], beats: list[int],
    max_lines: int = 4, tail: int = 10,
) -> list[Phrase]:  # fmt: skip
    """行をフレーズにまとめる。U4 の鍵の行: サビは 1 行目、ほかは最も長い行（アクセントの
    強さを歌から取れないので、字数で代える）。"""
    out: list[Phrase] = []
    i = 0
    while i < len(texts):
        j = i + 1
        while (j < len(texts) and j - i < max_lines and secs[j] == secs[i]
               and spans[j][0] - spans[j - 1][1] < 45):  # fmt: skip
            j += 1
        s0, e0 = spans[i][0], spans[j - 1][1] + tail
        if j < len(texts):
            e0 = min(e0, spans[j][0])
        lines = texts[i:j]
        key = (
            0
            if secs[i] in ("chorus", "chorus_first", "drop")
            else max(range(len(lines)), key=lambda k: len(lines[k]))
        )
        out.append(
            Phrase(
                secs[i],
                lines,
                [(a - s0, b - s0) for a, b in spans[i:j]],
                s0,
                max(e0, s0 + 1),
                key,
                [b - s0 for b in beats if s0 <= b < e0],
            )
        )
        i = j
    return out


# ---- 描画の部品 ------------------------------------------------------------------------------
def _rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _lum(c: str) -> float:
    r, g, b = _rgb(c)
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


_ROT = set("ー〜…―（）「」『』【】〈〉《》()[]")
_SMALL = set("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮヵヶ")
_PUNCT = set("、。")


def _font(fonts: Callable[[str], str | None], uf: str, size: int) -> Any:
    from PIL import ImageFont

    p = fonts(FONT_OF.get(uf, "")) if FONT_OF.get(uf) else None
    if p and Path(p).is_file():
        return ImageFont.truetype(p, size)
    return ImageFont.load_default(size)


def _glyph(ch: str, f: Any, size: int, fill: tuple[int, int, int], stroke: int = 0,
           stroke_fill: tuple[int, int, int] = (0, 0, 0), outline_only: bool = False,
           gold: bool = False) -> Any:  # fmt: skip
    """1 文字の透過の画（size×size の枠の中央）。"""
    from PIL import Image, ImageDraw

    pad = stroke + 4
    im = Image.new("RGBA", (size + 2 * pad, size + 2 * pad), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = (im.width / 2, im.height / 2)
    if outline_only:
        lw = max(2, round(size * 0.012))
        d.text(c, ch, font=f, fill=(0, 0, 0, 0), anchor="mm", stroke_width=lw,
               stroke_fill=(*fill, 255))  # fmt: skip
        return im
    d.text(c, ch, font=f, fill=(*fill, 255), anchor="mm", stroke_width=stroke,
           stroke_fill=(*stroke_fill, 255))  # fmt: skip
    if gold:  # UG4: 上から #FFF3B0 → #F2C94C（38%）→ #B8791A（62%）→ #FFE58A
        stops = [(0.0, "#FFF3B0"), (0.38, "#F2C94C"), (0.62, "#B8791A"), (1.0, "#FFE58A")]
        grad = Image.new("RGBA", im.size)
        gd = ImageDraw.Draw(grad)
        for y in range(im.height):
            u = y / max(1, im.height - 1)
            for (u0, c0), (u1, c1) in zip(stops, stops[1:], strict=False):
                if u0 <= u <= u1:
                    k = (u - u0) / (u1 - u0)
                    a, b = _rgb(c0), _rgb(c1)
                    gd.line(
                        [(0, y), (im.width, y)],
                        fill=tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3)) + (255,),
                    )
                    break
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).text(c, ch, font=f, fill=255, anchor="mm")
        im.paste(grad, (0, 0), mask)
    return im


def _vertical(ch: str, g: Any, size: int) -> tuple[Any, float, float]:
    """縦書きの 1 字（T11 の置き方）。

    戻り値は (画, x のずれ, y のずれ)（文字の大きさに対する比）。"""
    if ch in _ROT:
        return g.rotate(-90, expand=False), 0.0, 0.0
    if ch in _SMALL:
        return g, 0.1, -0.1
    if ch in _PUNCT:
        return g, 0.6, -0.6
    return g, 0.0, 0.0


@dataclass
class Item:
    """画面の中の 1 つの部品（1 文字・1 行・1 枚の札）。"""

    im: Any
    x: float  # 中心
    y: float
    t_in: int
    t_out: int | None = None  # None ならフレーズの終わりまで
    anim: str = "fade"  # fade・pop・cut・slide_up・scale_y
    rot: float = 0.0
    alpha: float = 1.0
    move: tuple[float, float] = (0.0, 0.0)  # 1 フレームあたりの移動（UT6）
    ring: tuple[float, float, float, float] | None = None  # (中心x, 中心y, 半径, 角度) UL7
    alt: Any = None  # UK3 の入れ替えた色の画


def _ease_out(u: float) -> float:
    u = min(max(u, 0.0), 1.0)
    return 1 - (1 - u) ** 3


class Builder:
    """1 フレーズの画面を組む（静止の地＋部品）。"""

    def __init__(self, scr: Screen, ph: Phrase, pic: Any, w: int, h: int,
                 fonts: Callable[[str], str | None], fps: float, seed: int) -> None:  # fmt: skip
        self.s, self.ph, self.pic, self.w, self.h = scr, ph, pic, w, h
        self.fonts, self.fps = fonts, fps
        self.k = h / 1080
        self.rnd = random.Random(seed)
        self.items: list[Item] = []
        self.pic_box = (w * scr.pic_x, h * 0.5, 0.0, 0.0)  # 中心x, 中心y, 幅, 高さ
        ground, ink, accent = scr.colors
        self.ink = _rgb(ink)
        self.accent = _rgb(accent)
        if scr.uk == "UK5" or not ground:
            self.ground = (0, 0, 0)
        else:
            self.ground = _rgb(ground)

    # 地と絵（UH）・飾り（UD）
    def background(self, swap: bool = False) -> Any:
        from PIL import Image, ImageDraw, ImageEnhance, ImageOps

        w, h, k, s = self.w, self.h, self.k, self.s
        ground = self.ink if swap else self.ground
        bg = Image.new("RGBA", (w, h), (*ground, 255))
        if s.ground2 and not swap:
            g2 = _rgb(s.ground2)
            grad = Image.new("RGBA", (w, h))
            gd = ImageDraw.Draw(grad)
            for y in range(h):
                u = y / max(1, h - 1)
                gd.line([(0, y), (w, y)], fill=tuple(round(ground[i] + (g2[i] - ground[i]) * u)
                                                     for i in range(3)) + (255,))  # fmt: skip
            bg = grad
        pic = self.pic.convert("RGBA") if self.pic is not None else None
        if pic is None:
            return bg
        if any(b.uc == "UC1" for b in s.blocks):  # U9: 絵を上の 830px に、その下を帯に
            top = round(BAND_TOP * k)
            band = Image.new("RGBA", (w, h), (*_rgb(s.colors[0]), 255))
            band.alpha_composite(ImageOps.fit(pic.convert("RGB"), (w, top)).convert("RGBA"))
            self.pic_box = (w * 0.5, top / 2, w, top)
            return band
        has_alpha = pic.getchannel("A").getextrema()[0] < 250
        if s.uk == "UK5" and has_alpha:  # 背景の無い絵（立ち絵）: 暗い地に人物を左下 380px
            self.ground = (20, 10, 30)
            bg = Image.new("RGBA", (w, h), (*self.ground, 255))
            hp = (380 if s.uh != "UH4" else 830) * k
            pw0 = max(1, round(pic.width * hp / pic.height))
            p2 = pic.resize((pw0, round(hp)))
            if s.uh == "UH4":
                g = ImageOps.grayscale(p2.convert("RGB")).convert("RGBA")
                g.putalpha(p2.getchannel("A").point(lambda v: v // 2))
                p2 = g
            cx = 60 * k + pw0 / 2
            self.pic_box = (cx, h - hp / 2, pw0, hp)
            self._pic_layer = (p2, round(cx - pw0 / 2), round(h - hp))
            return bg
        if s.uk == "UK5":  # 絵を全面に敷き、右へ向けて暗くする（バラード）
            full = ImageOps.fit(pic.convert("RGB"), (w, h)).convert("RGBA")
            dark = Image.new("RGBA", (w, h), (20, 10, 30, 0))
            mask = (
                Image.linear_gradient("L")
                .rotate(90)
                .resize((w, h))
                .point(lambda v: round(v * 0.45))
            )
            dark.putalpha(mask)
            full.alpha_composite(dark)
            if s.uh == "UH4":
                full = ImageOps.grayscale(full.convert("RGB")).convert("RGBA")
                full = Image.blend(Image.new("RGBA", (w, h), (0, 0, 0, 255)), full, 0.5)
            self.pic_box = (w * 0.5, h * 0.5, w, h)
            return full
        hp = {"UH1": 700, "UH2": 760, "UH3": 860, "UH4": 830, "UH5": 800, "UH7": 1250,
              "UH8": 330}.get(s.uh, 700) * k  # fmt: skip
        if s.uh == "UH5":  # 丸い窓（直径 800px）
            dia = round(800 * k)
            sq = ImageOps.fit(pic.convert("RGB"), (dia, dia)).convert("RGBA")
            m = Image.new("L", (dia, dia), 0)
            ImageDraw.Draw(m).ellipse((0, 0, dia - 1, dia - 1), fill=255)
            sq.putalpha(m)
            ImageDraw.Draw(sq).ellipse((0, 0, dia - 1, dia - 1), outline=(*self.ink, 255),
                                       width=max(2, round(6 * k)))  # fmt: skip
            pic, hp = sq, dia
        scale = hp / pic.height
        pw, ph = max(1, round(pic.width * scale)), max(1, round(pic.height * scale))
        if not has_alpha and s.uh not in ("UH5",) and pw > w * 0.9:
            pw, ph = round(w * 0.9), round(w * 0.9 * pic.height / pic.width)
        pic = pic.resize((pw, ph))
        if s.uh in ("UH2", "UH4"):
            gray = ImageOps.grayscale(pic.convert("RGB"))
            gray = ImageEnhance.Contrast(gray).enhance(1.2).convert("RGBA")
            gray.putalpha(pic.getchannel("A"))
            pic = gray
            if s.uh == "UH4":
                pic.putalpha(pic.getchannel("A").point(lambda v: v // 2))
        elif s.uh == "UH3":
            col = (
                (255, 255, 255)
                if _lum("#{:02X}{:02X}{:02X}".format(*ground)) < 0.5
                else (17, 17, 17)
            )
            sil = Image.new("RGBA", pic.size, (*col, 255))
            sil.putalpha(pic.getchannel("A"))
            pic = sil
        cx = w * s.pic_x
        cy = h * (0.62 if s.uh == "UH8" else 0.5) + (h * 0.12 if s.uh == "UH7" else 0)
        if s.uh == "UH8" and s.pic_x < 0.3:
            cy = h - ph / 2 - 40 * k  # 左下
        self.pic_box = (cx, cy, pw, ph)
        if s.ud == "UD3":  # 十字の線
            d = ImageDraw.Draw(bg)
            col = (*self.ink, round(255 * 0.6))
            d.line([(cx, 0), (cx, h)], fill=col, width=max(1, round(2 * k)))
            d.line([(0, cy), (w, cy)], fill=col, width=max(1, round(2 * k)))
        self._pic_layer = (pic, round(cx - pw / 2), round(cy - ph / 2))
        return bg

    def decor(self, im: Any) -> None:
        from PIL import ImageDraw

        if self.s.ud == "UD1":  # 二重の額縁（26px に 4px、46px に 2px）
            d = ImageDraw.Draw(im)
            k = self.k
            for off, lw in ((26, 4), (46, 2)):
                o = round(off * k)
                d.rectangle((o, o, self.w - o, self.h - o), outline=(*self.accent, 255),
                            width=max(1, round(lw * k)))  # fmt: skip

    # 文字
    def _glyph(self, b: Block, ch: str, size: int, color: tuple[int, int, int]) -> Any:
        f = _font(self.fonts, b.uf, size)
        stroke = 0
        if b.ug == "UG1":
            stroke = max(6, round(size * (0.16 if b.role == "UR1" else 0.05)))
        sf = self.ground if self.s.uk != "UK5" else (40, 10, 20)
        return _glyph(ch, f, size, color, stroke, sf, outline_only=b.ug == "UG3",
                      gold=b.ug == "UG4")  # fmt: skip

    def _line_img(self, b: Block, text: str, size: int, color: tuple[int, int, int],
                  vertical: bool = False, track: float = 0.0) -> Any:  # fmt: skip
        """1 行（横か縦）を 1 枚の画に。"""
        from PIL import Image

        gl = [self._glyph(b, ch, size, color) for ch in text if not ch.isspace()]
        if not gl:
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        g0 = gl[0]
        step = size * (1.0 + track)
        if vertical:
            im = Image.new(
                "RGBA", (g0.width, round(step * (len(gl) - 1) + g0.height)), (0, 0, 0, 0)
            )
            for i, (ch, g) in enumerate(zip([c for c in text if not c.isspace()], gl, strict=True)):
                g2, dx, dy = _vertical(ch, g, size)
                im.alpha_composite(g2, (round(dx * size), max(0, round(i * step + dy * size))))
        else:
            im = Image.new(
                "RGBA", (round(step * (len(gl) - 1) + g0.width), g0.height), (0, 0, 0, 0)
            )
            for i, g in enumerate(gl):
                im.alpha_composite(g, (round(i * step), 0))
        if b.ug == "UG5":
            im.putalpha(im.getchannel("A").point(lambda v: round(v * 0.55)))
        return im

    def _tag(self, im: Any, size: int, fill: tuple[int, int, int], box: bool = False) -> Any:
        """UC2 の札（余白は上 12%・左右 22%・下 20%）・UC3 の箱（線は 2%）。"""
        from PIL import Image, ImageDraw

        t, lr, bt = round(size * 0.12), round(size * 0.22), round(size * 0.20)
        out = Image.new("RGBA", (im.width + 2 * lr, im.height + t + bt), (0, 0, 0, 0))
        d = ImageDraw.Draw(out)
        if box:
            d.rectangle((0, 0, out.width - 1, out.height - 1), outline=(*fill, 255),
                        width=max(3, round(size * 0.02)))  # fmt: skip
        else:
            d.rectangle((0, 0, out.width - 1, out.height - 1), fill=(*fill, 255))
        out.alpha_composite(im, (lr, t))
        return out

    def add_block(self, b: Block) -> None:
        ph, k, w, h = self.ph, self.k, self.w, self.h
        size = round(size_px(b) * k)
        cx, cy, pw, phh = self.pic_box
        lines = ph.lines if b.role != "UR2" else [ph.lines[ph.key]]
        if b.role == "UR2":
            lines = [clip_big(lines[0], b)]
        if b.role == "UR3":
            lines = [" ".join(ph.lines)]
        if b.role == "UR2" and b.ul == "UL1":  # 横の見出しは画面の幅の 90% に収める
            size = min(size, round(w * 0.9 / max(1, len(lines[0].replace(" ", "")))))
        ink = self.ink
        tagged = b.uc in ("UC2", "UC3")
        txt_col = self.ground if b.uc == "UC2" else ink
        # 組み方ごとに部品を作る
        if b.ul == "UL2" and b.role == "UR1":  # 横 2 行: 2 行ずつ組み、行間は文字の 1.3 倍
            for i in range(0, len(ph.lines), 2):
                pair = ph.lines[i : i + 2]
                trk = 0.10 if b.uc == "UC1" else 0.0  # U9 の字間 +10%
                ims = [self._line_img(b, t, size, ink, track=trk) for t in pair]
                for j, im in enumerate(ims):
                    if im.width > w * 0.94:
                        r = w * 0.94 / im.width
                        ims[j] = im.resize(
                            (max(1, round(im.width * r)), max(1, round(im.height * r)))
                        )
                a = ph.frames[i][0]
                nxt = ph.frames[i + 2][0] if i + 2 < len(ph.frames) else None
                for j, im in enumerate(ims):
                    if b.uc == "UC1":  # 帯の中: 1 行は上端から 52px、2 行は 22px
                        top = BAND_TOP * k + (52 if len(ims) == 1 else 22) * k
                        y = top + size * 1.3 * j + size / 2
                    else:
                        y = h - 56 * k - size * 1.3 * (len(ims) - 1 - j) - im.height / 2
                    self.items.append(Item(im, w / 2, y, a, nxt, "fade"))
            return
        if b.ul == "UL1" and b.role == "UR1":  # 1 行ずつ（UT2）か 1 行の札（UT3 は拍に 1 枚）
            for i, (txt, (a, _e)) in enumerate(zip(ph.lines, ph.frames, strict=True)):
                col = self.accent if (b.accent_last and i == len(ph.lines) - 1) else txt_col
                im = self._line_img(b, txt, size, col)
                if tagged:
                    face = ink if b.uc == "UC2" else ink
                    if b.uc == "UC2" and self.s.uk in ("UK3", "UK1") and i % 2:
                        face = self.accent
                    im = self._tag(im, size, face, box=b.uc == "UC3")
                    if b.uc == "UC2" and i % 2 and self.s.uk == "UK3":
                        im = self._tag(self._line_img(b, txt, size, ink), size, self.accent)
                if im.width > w * 0.94:
                    r = w * 0.94 / im.width
                    im = im.resize((max(1, round(im.width * r)), max(1, round(im.height * r))))
                x, y = self._place(b, im, i)
                if b.uc == "UC2" and b.ut == "UT3":  # 札は左下と右下に交互
                    x = w * 0.3 if i % 2 == 0 else w * 0.7
                nxt = ph.frames[i + 1][0] if i + 1 < len(ph.frames) else None
                self.items.append(
                    Item(
                        im,
                        x,
                        y,
                        a,
                        nxt,
                        "slide_up" if tagged else "fade",
                        rot=b.tilt * (1 if i % 2 == 0 else -1) if tagged else 0,
                    )
                )
            return
        if b.ul in ("UL1",):  # 見出し・模様の横 1 行
            txt = lines[0]
            im = self._line_img(b, txt, size, self.accent if b.accent_last else ink)
            if b.role == "UR3" and b.ut == "UT6":  # 流し続ける（左へ毎秒 90px）
                x, y = self._place(b, im, 0)
                self.items.append(Item(im, x, y, 0, None, "fade", rot=b.tilt,
                                       move=(-90 * k / self.fps, 0)))  # fmt: skip
                return
            if b.ut == "UT1":
                self._per_char(b, txt, size, ink, vertical=False)
                return
            x, y = self._place(b, im, 0)
            t0 = ph.frames[ph.key][0] if b.role == "UR2" else 0
            self.items.append(Item(im, x, y, t0, None, "pop", rot=b.tilt))
            return
        if b.ul == "UL3":  # 縦 1 列（字幕は行ごとに左右の端で交互、見出しは 1 列）
            if b.role == "UR1":
                xs = [150, 1720, 270, 1600]
                for i, (txt, (a, e)) in enumerate(zip(ph.lines, ph.frames, strict=True)):
                    im = self._line_img(b, txt[:10], size, ink, vertical=True, track=0.18)
                    x = xs[i % 4] * k if b.up == "UP2" else cx
                    y = 90 * k + im.height / 2
                    if b.ut == "UT1":
                        self._per_char(
                            b,
                            txt[:10],
                            size,
                            ink,
                            vertical=True,
                            x=x,
                            y0=90 * k,
                            t0=a,
                            track=0.18,
                            t_out=a + round(2.3 * self.fps) + 10,
                        )
                    else:
                        self.items.append(Item(im, x, y, a, e + 6, "fade"))
                return
            txt = lines[0][:12]
            im = self._line_img(b, txt, size, self.accent if b.accent_last else ink, vertical=True,
                                track=0.04)  # fmt: skip
            x = (w - 110 * k - im.width / 2) if b.up == "UP2" else cx
            if b.up == "UP2" and self.s.pic_x > 0.5:
                x = 110 * k + im.width / 2
            if b.up == "UP2" and any(
                o.role == "UR1" and o.up == "UP2" and o.ul == "UL4" for o in self.s.blocks
            ):  # 字幕の列と重ねない
                x = w * (0.6 if self.s.pic_x <= 0.5 else 0.4)
            y = max(im.height / 2 + 40 * k, h / 2)
            self.items.append(Item(im, x, min(y, h - im.height / 2), ph.frames[ph.key][0], None,
                                   "fade"))  # fmt: skip
            return
        if b.ul == "UL4":  # 縦の列を並べる（右から左へ、上端を列ごとにずらす）
            gap = size * 1.45
            if b.us in ("US5", "US6"):
                cols = [clip_big(x, b) or x[:4] for x in ph.lines][:3]
            else:
                cols = [x[:12] for x in ph.lines][:5]
            right = self.s.pic_x <= 0.5
            x0 = (w - 120 * k) if right else (120 * k)
            if b.up == "UP8":  # 画面の中央に列の塊を置く（右から左へ読む）
                x0 = w / 2 + gap * (len(cols) - 1) / 2 + size / 2
                right = True
            for i, txt in enumerate(cols):
                col = (
                    self.accent
                    if (b.accent_last and i == len(cols) - 1)
                    else (txt_col if tagged else ink)
                )
                im = self._line_img(b, txt, size, col, vertical=True,
                                    track=0.18 if size < 60 * k else 0.04)  # fmt: skip
                if b.uc == "UC3":  # 箱（線の枠）
                    im = self._tag(im, size, ink, box=True)
                elif tagged:
                    face = self.accent if (b.accent_last and i == len(cols) - 1) else ink
                    im = self._tag(self._line_img(b, txt, size, self.ground if face == ink else
                                                  (255, 255, 255), vertical=True, track=0.08),
                                   size, face)  # fmt: skip
                step = max(gap, im.width * 1.25) if tagged else gap
                x = x0 - i * step - im.width / 2 if right else x0 + i * step + im.width / 2
                top = (90 if i % 2 == 0 else 150) * k
                y = top + im.height / 2
                if b.us in ("US5",):
                    y = h / 2 + (i % 2) * 60 * k
                    if b.up == "UP2" and b.role == "UR2":  # 巨大な列は左右の端に交互
                        x = (w - 110 * k - im.width / 2) if i % 2 == 0 else 110 * k + im.width / 2
                a = ph.frames[min(i, len(ph.frames) - 1)][0]
                anim = "scale_y" if tagged else ("cut" if b.ut == "UT4" else "fade")
                t_out = None
                if b.ut == "UT4" and i + 1 < len(cols):
                    t_out = ph.frames[min(i + 1, len(ph.frames) - 1)][0]
                self.items.append(Item(im, x, y, a, t_out, anim))
            return
        if b.ul == "UL5":  # 1 文字ずつ絵のまわりに散らす（傾きは交互に最大 ±14°）
            chars = [c for c in "".join(ph.lines) if not c.isspace()][:12]
            rx, ry = max(pw, 300 * k) * 0.62 + size * 0.6, max(phh, 300 * k) * 0.5 + size * 0.3
            beat = self._beat_frames()
            for i, ch in enumerate(chars):
                sz = size if b.us != "US7" else round(self._us7(i) * k * 0.8)
                col = ink if i % 2 == 0 else self.accent
                g = self._glyph(b, ch, sz, col)
                if b.uc == "UC2":
                    g = self._tag(self._glyph(b, ch, sz, self.ground if i % 2 == 0 else ink), sz,
                                  ink if i % 2 == 0 else self.accent)  # fmt: skip
                ang = -math.pi / 2 + 2 * math.pi * i / max(1, len(chars))
                x = cx + rx * math.cos(ang)
                y = cy + ry * math.sin(ang)
                t0 = beat[i] if i < len(beat) else i * 6
                self.items.append(Item(g, x, y, t0, None, "pop", rot=14 if i % 2 == 0 else -14))
            return
        if b.ul == "UL6":  # 幅いっぱいに均等な 2 行（1 文字ずつ大きさを変える）
            rows = ph.lines[:2] if len(ph.lines) > 1 else [ph.lines[0], ph.lines[0]]
            ys = [90 * k, 560 * k]
            red = None
            for r, txt in enumerate(rows):
                chars = [c for c in txt if not c.isspace()][:10]
                if not chars:
                    continue
                span = (w - 120 * k) / len(chars)
                for i, ch in enumerate(chars):
                    sz = min(round(self._us7(i + r) * k), round(span * 1.05))
                    is_red = red is None and r == 0 and i == len(chars) - 1 and b.accent_last
                    g = self._glyph(b, ch, sz, self.accent if is_red else ink)
                    if is_red:
                        red = g
                        g = self._tag(g, sz, self.accent, box=True)
                    x = 60 * k + span * (i + 0.5)
                    y = ys[r] + sz / 2
                    a = ph.frames[min(r, len(ph.frames) - 1)][0] + i * 3
                    self.items.append(Item(g, x, y, a, None, "cut"))
            return
        if b.ul == "UL7":  # 円周（絵の高さの 50%＋70px の半径）を回る
            chars = [c for c in "".join(ph.lines) if not c.isspace()] or ["・"]
            rad = max(phh, 300 * k) * 0.5 + 70 * k
            n = min(27, max(8, round(2 * math.pi * rad / (size * 1.1))))
            for i in range(n):
                g = self._glyph(b, chars[i % len(chars)], size, ink)
                self.items.append(Item(g, cx, cy, 0, None, "fade",
                                       ring=(cx, cy, rad, 2 * math.pi * i / n)))  # fmt: skip
            return
        if b.ul == "UL8":  # 2 段の壁（行送り 90%・字間 −4%）、拍ごとに 1 字ずつ送る
            chars = [c for c in "".join(ph.lines) if not c.isspace()] or ["・"]
            for r in range(2):
                txt = "".join(chars[(r * 5 + j) % len(chars)] for j in range(7))
                im = self._line_img(b, txt, size, ink, track=-0.04)
                alt = self._line_img(b, txt, size, self.ground, track=-0.04)
                y = h * 0.5 + (r - 0.5) * size * 0.9
                self.items.append(
                    Item(
                        im,
                        w / 2 - size * 0.4 * r,
                        y,
                        0,
                        None,
                        "kick",
                        alt=alt,
                    )
                )
            return
        if b.ul == "UL9":  # 行の塊（3〜4 行を同じ幅にして中央揃え、2 拍に 1 行）
            rows = ph.lines[:4]
            width = (900 if self.pic is not None else 1300) * k
            ys: list[tuple[Any, float]] = []
            total = 0.0
            for i, txt in enumerate(rows):
                col = self.accent if (b.accent_last and i == 1) else ink
                base = self._line_img(b, txt, 100, col)
                r = width / max(1, base.width)
                sz = max(round(38 * k), min(round(100 * r), round(260 * k)))
                im = self._line_img(b, txt, sz, col)
                ys.append((im, sz))
                total += sz * 1.12
            y = h / 2 - total / 2
            x = (
                w - 120 * k - width / 2
                if self.s.pic_x < 0.5
                else (w / 2 if b.up == "UP8" else 120 * k + width / 2)
            )
            for i, (im, sz) in enumerate(ys):
                a = ph.frames[min(i, len(ph.frames) - 1)][0]
                self.items.append(Item(im, x, y + sz * 0.56, a, None, "slide_up"))
                y += sz * 1.12

    def _place(self, b: Block, im: Any, i: int) -> tuple[float, float]:
        k, w, h = self.k, self.w, self.h
        cx, cy, pw, phh = self.pic_box
        if b.up == "UP7":
            return w / 2, h - 56 * k - im.height / 2
        if b.up == "UP8":
            return w / 2, h / 2
        if b.up == "UP3":  # 絵のまわり（右上寄り）
            return min(w - im.width / 2 - 20 * k, cx + pw * 0.5 + im.width * 0.3), max(
                im.height / 2 + 20 * k, cy - phh * 0.45
            )
        if b.up == "UP5":  # 絵の後ろ（上寄り）
            return w / 2 if self.s.pic_x == 0.5 else w * (1 - self.s.pic_x), max(
                im.height / 2 - 60 * k, cy - phh * 0.35
            )
        if b.up == "UP2":
            left = self.s.pic_x > 0.5
            x = 110 * k + im.width / 2 if left else w - 110 * k - im.width / 2
            return x, h * (0.3 + 0.18 * (i % 3))
        return cx, cy

    def _us7(self, i: int) -> int:
        """US7: 最小 90・最大 280、隣と同じ大きさにしない。"""
        vals = [180, 110, 260, 140, 220, 95, 280, 130, 200, 160]
        return vals[i % len(vals)]

    def _beat_len(self) -> int:
        b = self.ph.beats
        return (b[1] - b[0]) if len(b) > 1 else round(self.fps / 2)

    def _beat_frames(self) -> list[int]:
        """拍に 1 つ（散らす文字は半拍に 1 つ）。拍が無ければ 6f おき。"""
        b = self.ph.beats
        if len(b) < 2:
            return [i * 6 for i in range(64)]
        half = max(1, (b[1] - b[0]) // 2)
        return [b[0] + i * half for i in range(64)]

    def _per_char(self, b: Block, txt: str, size: int, col: tuple[int, int, int], vertical: bool,
                  x: float | None = None, y0: float | None = None, t0: int | None = None,
                  track: float = 0.0, t_out: int | None = None) -> None:  # fmt: skip
        """UT1: 1 文字ずつ 2f ずらし、各 6f。"""
        w, h = self.w, self.h
        chars = [c for c in txt if not c.isspace()]
        step = size * (1 + track)
        if t0 is None:
            t0 = self.ph.frames[self.ph.key][0] if b.role == "UR2" else 0
        if vertical:
            x = x if x is not None else w / 2
            y = (y0 if y0 is not None else h / 2 - step * len(chars) / 2) + size / 2
        else:
            x0, y = (
                (w / 2 - step * (len(chars) - 1) / 2, h / 2)
                if b.up in ("UP8", "UP4")
                else (w / 2 - step * (len(chars) - 1) / 2, h * 0.28)
            )
        for i, ch in enumerate(chars):
            g = self._glyph(b, ch, size, col)
            if vertical:
                g, dx, dy = _vertical(ch, g, size)
                pos = (x + dx * size, y + i * step + dy * size)
            else:
                pos = (x0 + i * step, y)
            self.items.append(Item(g, pos[0], pos[1], t0 + i * 2, t_out, "fade"))

    def build(self) -> list[Block]:
        """部品を作り、部品ごとのかたまりを返す（items と同じ並び）。"""
        owner: list[Block] = []
        for b in self.s.blocks:
            before = len(self.items)
            self.add_block(b)
            owner += [b] * (len(self.items) - before)
        return owner


def frames(
    scr: Screen, ph: Phrase, pic: Any, w: int, h: int,
    fonts: Callable[[str], str | None], fps: float, seed: int = 0,
) -> Iterator[Any]:  # fmt: skip
    """1 フレーズの画面（不透明な RGBA の画の列、ph.end − ph.start フレーム）。"""
    from PIL import Image

    bd = Builder(scr, ph, pic, w, h, fonts, fps, seed)
    bg = bd.background()
    bg2 = bd.background(swap=True) if scr.uk == "UK3" else None
    pic_layer = getattr(bd, "_pic_layer", None)
    owner = bd.build()
    n = max(1, ph.end - ph.start)
    beats = ph.beats
    behind = {id(it) for b, it in zip(owner, bd.items, strict=True)
              if b.up == "UP5" or b.role == "UR3"}  # 絵の後ろに置く文字  # fmt: skip
    out_start = n - 10  # フレーズの終わりで全部を 10f で消す（UT5 と見出し）
    layers = [it for it in bd.items if id(it) in behind]
    # 読ませる字幕（UR1）は、見出し・模様の前に描く（PC の確認で、バラードのサビの巨大な列が
    # 下の字幕に重なって読めなかった）
    sub_ids = {id(it) for b, it in zip(owner, bd.items, strict=True) if b.role == "UR1"}
    # 下の字幕（UP7）に重なる見出し・模様の列は、字幕の上端の 8px 上で切る（端からはみ出す
    # 巨大な列でも、読ませる行を隠さない）
    subs = [
        (it.x - it.im.width / 2, it.x + it.im.width / 2, it.y - it.im.height / 2)
        for b, it in zip(owner, bd.items, strict=True)
        if b.role == "UR1" and b.up == "UP7"
    ]
    if subs:
        x0 = min(a for a, _b, _t in subs)
        x1 = max(b for _a, b, _t in subs)
        top = min(t for _a, _b, t in subs) - 8 * bd.k
        for b, it in zip(owner, bd.items, strict=True):
            if b.role == "UR1" or id(it) in behind:
                continue
            left, right = it.x - it.im.width / 2, it.x + it.im.width / 2
            bottom = it.y + it.im.height / 2
            if right > x0 and left < x1 and bottom > top and it.y - it.im.height / 2 < top:
                keep = max(1, round(top - (it.y - it.im.height / 2)))
                it.y -= (it.im.height - keep) / 2
                it.im = it.im.crop((0, 0, it.im.width, keep))
    front = [it for it in bd.items if id(it) not in behind and id(it) not in sub_ids]
    front += [it for it in bd.items if id(it) not in behind and id(it) in sub_ids]
    k = bd.k

    def state(it: Item, t: int, swap: bool) -> tuple[Any, ...] | None:
        """部品のその時刻の (画, x, y, 不透明度, sx, sy)。見えないなら None。"""
        if t < it.t_in:
            return None
        age = t - it.t_in
        a = it.alpha
        sx = sy = 1.0
        dx = dy = 0.0
        if it.anim == "fade":
            a *= _ease_out(age / 6)
        elif it.anim == "pop":
            u = age / 8
            if u < 0.6:
                sx = sy = 1.15 * _ease_out(u / 0.6)
            elif u < 1:
                sx = sy = 1.15 - 0.15 * _ease_out((u - 0.6) / 0.4)
            a *= min(1.0, age / 3 + 0.01)
        elif it.anim == "slide_up":
            dy = 24 * k * (1 - _ease_out(age / 6))
            a *= _ease_out(age / 6)
        elif it.anim == "scale_y":
            sy = max(0.02, _ease_out(age / 6))
        elif it.anim == "kick" and beats:  # 拍の頭で x −120px→0 を 6f
            last = max((x for x in beats if x <= t), default=-99)
            if 0 <= t - last < 6:
                dx = -120 * k * (1 - _ease_out((t - last) / 6))
        if it.t_out is not None and t >= it.t_out:
            a *= max(0.0, 1 - (t - it.t_out) / 5)
        if t >= out_start:
            a *= max(0.0, (n - t) / 10)
        if a <= 0.01:
            return None
        x, y = it.x + it.move[0] * t + dx, it.y + it.move[1] * t + dy
        if it.ring is not None:
            rcx, rcy, rad, ang = it.ring
            ang += math.radians(24) * t / fps  # 毎秒 24°
            x, y = rcx + rad * math.cos(ang), rcy + rad * math.sin(ang)
        src = it.alt if (swap and it.alt is not None) else it.im
        return (id(src), round(x), round(y), round(a, 3), round(sx, 3), round(sy, 3), src, it.rot)

    rotated: dict[tuple[Any, ...], Any] = {}

    def draw(im: Any, st: tuple[Any, ...]) -> None:
        _i, x, y, a, sx, sy, src, rot = st
        if sx != 1.0 or sy != 1.0:
            src = src.resize((max(1, round(src.width * sx)), max(1, round(src.height * sy))))
        if rot:
            ck = (id(src), rot, src.size)
            if ck not in rotated:
                rotated[ck] = src.rotate(rot, expand=True, resample=Image.Resampling.BICUBIC)
            src = rotated[ck]
        if a < 1.0:
            src = src.copy()
            src.putalpha(src.getchannel("A").point(lambda v, a_=a: round(v * a_)))
        im.alpha_composite(src, (round(x - src.width / 2), round(y - src.height / 2)))

    prev_key: tuple[Any, ...] | None = None
    prev: Any = None
    for t in range(n):
        swap = bool(bg2 is not None and beats and sum(1 for x in beats if x <= t) % 2 == 1)
        py_off = 0
        if pic_layer is not None and scr.uh == "UH1" and beats:  # 拍で小さく沈む（12px・6f）
            last = max((x for x in beats if x <= t), default=-99)
            if 0 <= t - last < 6:
                py_off = round(12 * k * (1 - (t - last) / 6))
        sb = [state(it, t, swap) for it in layers]
        sf = [state(it, t, swap) for it in front]
        flash = scr.flash and t < 3
        key = (
            swap,
            py_off,
            flash and t,
            tuple(x[:6] if x else None for x in sb),
            tuple(x[:6] if x else None for x in sf),
        )
        if key == prev_key and prev is not None:
            yield prev  # 変わらないフレームは前の画をそのまま使う
            continue
        im = (bg2 if swap else bg).copy()
        for st in sb:
            if st is not None:
                draw(im, st)
        if pic_layer is not None:
            p, px, py = pic_layer
            im.alpha_composite(p, (px, py + py_off))
        for st in sf:
            if st is not None:
                draw(im, st)
        bd.decor(im)
        if flash:  # U5: 白の重ね 90%→0
            wh = Image.new("RGBA", (w, h), (255, 255, 255, round(255 * 0.9 * (1 - t / 3))))
            im.alpha_composite(wh)
        prev_key, prev = key, im
        yield im


# ---- U16 単色のカードと 1 語（ボカロ（しっとり系）・バラード） --------------------------------
U16_DIRECTIONS = ("vocaloid_calm", "ballad")
U16_COLORS = (("#8E2F35", "#F3E9D8"), ("#F3E9D8", "#243A4A"), ("#243A4A", "#F3E9D8"))
U16_LEN = 18  # 行の終わりの前に見せるフレーム数（最後の語を歌っている間）
_SEP = (" ", "\u3000", "、", "，", ",")


def _is_kanji_kata(ch: str) -> bool:
    return "\u4e00" <= ch <= "\u9fff" or "\u30a1" <= ch <= "\u30fa" or ch in "々〆"


def last_word(line: str) -> str:
    """行の最後の語。空白・読点があれば最後の区切りの後ろ。無ければ、最後の漢字・カタカナの
    続き（とその後ろのひらがな）を語とみなす（「光の方へ」→「方へ」）。漢字・カタカナが無い行は
    行の全体。句読点・記号は除く。"""
    t = line.strip().rstrip("。．.！!？?〜～ー…")
    cut = max(t.rfind(x) for x in _SEP)
    if cut >= 0:
        return t[cut + 1 :].strip()
    i = len(t)
    while i > 0 and not _is_kanji_kata(t[i - 1]):
        i -= 1
    if i == 0:
        return t
    while i > 0 and _is_kanji_kata(t[i - 1]):
        i -= 1
    return t[i:]


def word_card(word: str, idx: int, w: int, h: int, fonts: Callable[[str], str | None],
              n: int, vertical: bool = False) -> Iterator[Any]:  # fmt: skip
    """U16: 単色の全面に 1 語（1 字 300px・2 字 220px、Shippori Mincho ExtraBold）を
    scale 1.15→1・10f で置く。入りは右から左へ進む斜めの境目（傾きは画面の幅の 30%）、8f
    （easeOutExpo）。vertical は間奏の頭の 40px の縦書き 1 列（1 文字 3f ずらし）。"""
    from PIL import Image, ImageDraw

    k = h / 1080
    ground, ink = (_rgb(c) for c in U16_COLORS[idx % len(U16_COLORS)])
    size = round((40 if vertical else (300 if len(word) <= 1 else 220)) * k)
    f = _font(fonts, "UF2", size)
    glyphs = [_glyph(ch, f, size, ink) for ch in word if not ch.isspace()]
    base = Image.new("RGBA", (w, h), (*ground, 255))
    for t in range(n):
        im = base.copy()
        if vertical:
            y0 = h / 2 - size * len(glyphs) / 2
            for i, g in enumerate(glyphs):
                if t >= i * 3:
                    im.alpha_composite(
                        g,
                        (round(w / 2 - g.width / 2), round(y0 + i * size - (g.height - size) / 2)),
                    )
        elif glyphs:
            u = min(1.0, t / 10)
            sc = 1.15 - 0.15 * (1 - (1 - u) ** 3)
            row = Image.new("RGBA", (sum(g.width for g in glyphs), glyphs[0].height), (0, 0, 0, 0))
            x = 0
            for g in glyphs:
                row.alpha_composite(g, (x, 0))
                x += g.width
            row = row.resize((max(1, round(row.width * sc)), max(1, round(row.height * sc))))
            im.alpha_composite(row, (round(w / 2 - row.width / 2), round(h / 2 - row.height / 2)))
        if t < 8:  # 右から左へ進む斜めの境目（境目より右だけ見せ、左は透明）
            u = 1 - 2 ** (-10 * (t + 1) / 8)
            edge = w * (1.3 - 1.3 * u)
            m = Image.new("L", (w, h), 0)
            ImageDraw.Draw(m).polygon([(edge, 0), (w * 2, 0), (w * 2, h), (edge - w * 0.3, h)],
                                      fill=255)  # fmt: skip
            im.putalpha(m)
        yield im


def presets_ok() -> dict[str, list[str]]:
    """方向性 8 種の既定が決まりを満たすか（テスト用）。"""
    out = {}
    for name, (v, c) in PRESETS.items():
        out[name] = check(v) + check(c) + check(v, c)
    for name, scr in EXTRA.items():
        out[name] = check(scr)
    return out


__all__ = [
    "ELEMENTS", "DRAWN", "Block", "Screen", "PRESETS", "Phrase", "check", "phrases", "frames",
    "screen_for", "presets_ok", "replace", "DIRECTION_NAMES",
]  # fmt: skip
