"""自由に使える書体の一覧と、手元のフォルダから書体のファイルを探す仕組み。

- 書体はリポジトリに入れず、`tools/fetch_fonts.py` が google/fonts（GitHub）から
  `data/fonts` に取ってくる。ライセンスの文（OFL.txt）も同じフォルダに書体ごとに置く
- CC0 の日本語の書体はほとんど無いので、商用でも動画に使え、改変・再配布もできる
  SIL Open Font License 1.1（OFL）の書体を集めた。OFL は書体のファイルを単体で売ることだけを禁じ、
  書体で作った動画・画像には条件が付かない
- 演出との組み合わせ（どこで使うか）は `use` に書く。統合版の書体の指定（U7・3節・Y10）が
  あるものはその書体、指定の無い見た目の型（Y1・Y3〜Y7・Y9）はこの一覧で決めた
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from video_automation.services.design.guard import font_key

GOOGLE_FONTS_RAW = "https://raw.githubusercontent.com/google/fonts/main/ofl"


@dataclass(frozen=True)
class FreeFont:
    family: str  # 書体名（太さを含む。演出の側はこの名前で探す）
    path: str  # google/fonts の ofl/ からのパス
    use: str  # 演出との組み合わせ
    license: str = "OFL-1.1"

    @property
    def file(self) -> str:
        return self.path.split("/")[-1]

    @property
    def folder(self) -> str:
        return self.path.split("/")[0]


FONTS: tuple[FreeFont, ...] = (
    # 統合版 U7（曲の方向性の書体）と U21 の字形 UF1〜UF7
    FreeFont("Shippori Mincho Regular", "shipporimincho/ShipporiMincho-Regular.ttf",
             "UF1 細い明朝（ボカロ（しっとり系）の歌詞、U19）"),
    FreeFont("Shippori Mincho SemiBold", "shipporimincho/ShipporiMincho-SemiBold.ttf",
             "UF2 の小さい列（ボカロ（かっこいい系）の歌詞の縦書き）"),
    FreeFont("Shippori Mincho ExtraBold", "shipporimincho/ShipporiMincho-ExtraBold.ttf",
             "UF2 太い明朝（題字・巨大な縦の列・U16 のカード）"),
    FreeFont("Dela Gothic One", "delagothicone/DelaGothicOne-Regular.ttf",
             "UF3 極太ゴシック（ラップ・金の飾り文字）、Y4 リソグラフの題名"),
    FreeFont("Zen Maru Gothic Medium", "zenmarugothic/ZenMaruGothic-Medium.ttf",
             "UF4 丸ゴシック（アニソンの字幕・札・窓の中の字幕）"),
    FreeFont("Zen Kurenaido", "zenkurenaido/ZenKurenaido-Regular.ttf",
             "UF5 手書き（バラード・U9 の帯の歌詞）"),
    FreeFont("Yusei Magic", "yuseimagic/YuseiMagic-Regular.ttf",
             "UF5 の太い手書き（アニソンのはみ出す手書き・サビ）"),
    FreeFont("RocknRoll One", "rocknrollone/RocknRollOne-Regular.ttf",
             "UF6 ポップ（ポップソング・飾り字幕）"),
    FreeFont("Hachi Maru Pop", "hachimarupop/HachiMaruPop-Regular.ttf",
             "UF6 の手描き風（ボカロ（かわいい系））"),
    FreeFont("Mochiy Pop One", "mochiypopone/MochiyPopOne-Regular.ttf",
             "UF6 の丸み（アイドルソング）"),
    FreeFont("Yuji Boku", "yujiboku/YujiBoku-Regular.ttf", "UF7 筆文字（見出し・題名だけ）"),
    # 見た目の型（Y）の題名の書体（統合版に指定が無いものをここで決めた）
    FreeFont("Klee One SemiBold", "kleeone/KleeOne-SemiBold.ttf",
             "Y1 黒板（教科書体に近い手書きで、チョークの板書に見せる）"),
    FreeFont("Kiwi Maru Medium", "kiwimaru/KiwiMaru-Medium.ttf",
             "Y3 紙の切り絵（やわらかい丸い明朝で、切り紙の面に合わせる）"),
    FreeFont("Rampart One", "rampartone/RampartOne-Regular.ttf",
             "Y5 シンセウェーブ（中抜きの立体の字形で、グラデーションと縁を受ける）"),
    FreeFont("DotGothic16", "dotgothic16/DotGothic16-Regular.ttf",
             "Y6 ピクセルアート（ドットの字形。320×180 に縮めても潰れない）"),
    FreeFont("Zen Antique", "zenantique/ZenAntique-Regular.ttf",
             "Y7 古い記録映像（活版の明朝。セピアと傷に合わせる）"),
    FreeFont("Reggae One", "reggaeone/ReggaeOne-Regular.ttf",
             "Y9 ハーフトーンのコミック（漫画の描き文字。黒の縁 6px に合わせる）"),
)  # fmt: skip

# 見た目の型 → 題名の書体（Y10 は統合版の指定どおり Zen Old Mincho。Y2・Y8 は用途の書体のまま）
LOOK_FONTS: dict[str, str] = {
    "Y1": "Klee One SemiBold",
    "Y3": "Kiwi Maru Medium",
    "Y4": "Dela Gothic One",
    "Y5": "Rampart One",
    "Y6": "DotGothic16",
    "Y7": "Zen Antique",
    "Y9": "Reggae One",
}

_EXT = (".ttf", ".otf", ".ttc")
_WEIGHT_ORDER = ("bold", "black", "extrabold", "semibold", "medium", "regular")


def find_font(family: str, dirs: list[Path]) -> str | None:
    """書体名（太さを含んでよい）→ dirs の中のファイル。名前がそのまま合うファイル（例:
    「Shippori Mincho ExtraBold」→ ShipporiMincho-ExtraBold.ttf）を先に、無ければ同じ書体の
    太さ違い（太字を優先）を返す。"""
    want = font_key(family)
    files = [f for d in dirs if d.is_dir() for f in sorted(d.iterdir()) if f.suffix.lower() in _EXT]
    for f in files:
        if font_key(f.stem) == want:
            return str(f)
    same = [f for f in files if font_key(f.stem.split("-")[0]) == want]
    if not same:
        return None

    def rank(f: Path) -> int:
        w = f.stem.split("-")[-1].lower()
        return _WEIGHT_ORDER.index(w) if w in _WEIGHT_ORDER else len(_WEIGHT_ORDER)

    return str(min(same, key=rank))


def missing(dirs: list[Path]) -> list[FreeFont]:
    """まだ手元に無い書体。"""
    have = {f.name for d in dirs if d.is_dir() for f in d.iterdir()}
    return [x for x in FONTS if x.file not in have]
