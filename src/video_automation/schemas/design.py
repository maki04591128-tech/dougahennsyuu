"""動画の用途とデザインの型（色・書体・文字の大きさ・使ってよい部品・動きの強さ）。

用途ごとに「使ってよい部品」を決め、別の用途の部品（例: 字幕動画にモーショングラフィックスの
演出）が混ざったら書き出す前に止める。混ぜる指示が明示されたときだけ extras で例外を許す。
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_HEX = r"^#[0-9A-Fa-f]{6}$"


class Purpose(StrEnum):
    # 落ち着いた系（文字は読ませるためだけ・動きは最小）
    SUBTITLE = "subtitle"  # 字幕動画（会話・朗読以外の一般的な字幕付け）
    INTERVIEW = "interview"  # インタビュー・対談
    DOCUMENTARY = "documentary"  # ドキュメンタリー
    STORY = "story"  # 朗読・物語・オーディオドラマ
    PODCAST = "podcast"  # ラジオ・ポッドキャストの動画化
    # 情報系（図・要点で整理して伝える）
    EXPLAINER = "explainer"  # 解説（キャラの掛け合い・教養）
    LECTURE = "lecture"  # 講義・スライド
    TUTORIAL = "tutorial"  # 操作説明・ハウツー・画面録画
    RECIPE = "recipe"  # 料理・手順もの
    NEWS = "news"  # ニュース・情報番組風
    CORPORATE = "corporate"  # 会社紹介・採用・IR
    # 勢い系（テンポと強弱）
    SHORT = "short"  # 縦型ショート
    GAME = "game"  # ゲーム実況
    VLOG = "vlog"  # Vlog・日常
    RANKING = "ranking"  # ランキング・まとめ
    PRODUCT = "product"  # 商品紹介・レビュー
    PROMO = "promo"  # PR・CM・告知
    EVENT = "event"  # イベント・旅行のハイライト
    KIDS = "kids"  # 子ども向け
    # 表現系（世界観が主役）
    MV = "mv"  # ミュージックビデオ
    LYRIC = "lyric"  # リリックビデオ
    TRAILER = "trailer"  # 予告編・ティザー


class Family(StrEnum):
    CALM = "calm"
    INFO = "info"
    ENERGETIC = "energetic"
    EXPRESSIVE = "expressive"


class Component(StrEnum):
    """画面に出す部品・演出の種類。生成した要素にはこのどれかを付けて記録する。"""

    SUBTITLE = "subtitle"  # 発話の字幕
    SPEAKER_LABEL = "speaker_label"  # 字幕の上の話者名
    TITLE_CARD = "title_card"  # 冒頭の題名
    CARD = "card"  # 要点カード（見出し＋箇条書き）
    CHART = "chart"  # 図表・グラフ
    IMAGE = "image"  # 説明用の画像
    KEYWORD = "keyword"  # 画面上部の短い要点テロップ
    CHAPTER_LABEL = "chapter_label"  # 章ラベル
    LOWER_THIRD = "lower_third"  # 名前・肩書きの帯
    EMPHASIS_TEXT = "emphasis_text"  # 大きな一言の強調テロップ
    KINETIC_TEXT = "kinetic_text"  # 1 文字ずつ動く文字
    LYRIC_TEXT = "lyric_text"  # 歌詞
    BACKGROUND_MOTION = "background_motion"  # 背景がゆっくり動く・寄る
    TRANSITION_CUT = "transition_cut"  # カット・クロスフェード
    TRANSITION_SHAPE = "transition_shape"  # 図形・パネルの転換
    TRANSITION_WHIP = "transition_whip"  # ウィップパン・ズームブラー
    SHAKE_PUNCH = "shake_punch"  # 揺れ・ズームパンチ
    FLASH = "flash"  # フラッシュ
    GLITCH = "glitch"  # グリッチ・RGB ずらし・色収差
    BLOOM = "bloom"  # 光のにじみ・ディフュージョン
    LETTERBOX = "letterbox"  # 黒帯
    BEAT_CUT = "beat_cut"  # 拍に合わせたカット
    SFX = "sfx"  # 演出の効果音
    CHARACTER = "character"  # 立ち絵・キャラ
    # 段階5: 仕上げ（統合版 M9・L4・L5。使う用途は 2.3 の表）
    GRAIN = "grain"  # M9: グレイン・周辺減光・色収差
    GLOW = "glow"  # L4: 明るい所の光のにじみ
    HALATION = "halation"  # L5: 赤いにじみ（フィルムの質感）
    GLOSS = "gloss"  # L7: 題名を斜めの光の帯が 1 回通る
    PARALLAX = "parallax"  # M8: 切り抜いた絵を横に少し動かす 2.5D
    SUN_BEAMS = "sun_beams"  # L3: 題名からの光の筋
    LIQUID = "liquid"  # M6: メタボールの液体の背景


# 部品の許可: 0=使わない、-1=制限なし、n>0=1 本の動画で n 回まで
DENY, ANY = 0, -1


class Palette(BaseModel):
    """役割で決めた色。ベース 70%・メイン 25%・アクセント 5% を目安に使う。"""

    base: str = Field(pattern=_HEX)  # 背景（暗い側）
    base2: str = Field(pattern=_HEX)  # 背景のグラデーションの明るい側
    surface: str = Field(pattern=_HEX)  # カード・帯の面
    main: str = Field(pattern=_HEX)  # 見出し・線・主役の色
    accent: str = Field(pattern=_HEX)  # 強調（数値・結論）だけ
    text: str = Field(pattern=_HEX)
    text_muted: str = Field(pattern=_HEX)
    speakers: list[str] = Field(min_length=1, max_length=6)  # 話者の色（main / accent の明暗違い）
    chart: list[str] = Field(min_length=2, max_length=6)  # 図表の色（1 つ目が強調）

    @field_validator("speakers", "chart")
    @classmethod
    def _hexes(cls, v: list[str]) -> list[str]:
        for c in v:
            h = c.lstrip("#")
            if len(h) != 6 or any(x not in "0123456789abcdefABCDEF" for x in h):
                raise ValueError(f"bad color {c}")
        return [c.upper() for c in v]


class Typography(BaseModel):
    body_font: str = "Meiryo"  # 字幕・本文（ASS のフォント名）
    heading_font: str = "Meiryo"  # 見出し
    max_fonts: int = Field(default=2, ge=1, le=4)
    # 文字の大きさの段（本文 1 に対する比）。見出し・題名・注記はこの段だけを使う
    scale_ratio: float = Field(default=1.5, ge=1.1, le=2.0)


class SubtitleSpec(BaseModel):
    size_ratio: float = Field(ge=0.03, le=0.1)  # 文字の高さ（短辺比）
    size_range: tuple[float, float]  # 許す範囲
    band: str = Field(pattern=r"^(none|fitted|full)$")  # 帯: 無し / 文字の幅だけ / 画面幅
    max_chars_per_line: int = Field(ge=8, le=30)
    max_lines: int = Field(ge=1, le=3)
    chars_per_sec: float = Field(gt=0, le=12)  # 読める速さ（これより速い字幕は警告）
    # 音声に合わせた字幕の上限。音声と同時に聞けるので「読める速さ」より速くてよいが、
    # これを超えると音声があっても追えない
    speech_chars_per_sec: float = Field(default=12.0, gt=0, le=20)
    double_outline: bool = False  # 統合版 C1: 字幕の二重縁（用途は 2.2）


class SafeArea(BaseModel):
    """文字を置いてはいけない外周（画面比）。縦動画は UI の位置で上下右が広い。"""

    top: float = Field(ge=0, le=0.4)
    bottom: float = Field(ge=0, le=0.4)
    left: float = Field(ge=0, le=0.3)
    right: float = Field(ge=0, le=0.3)


class MotionSpec(BaseModel):
    """用途ごとの動きの値（統合版 2.1。30fps のフレーム数）。"""

    in_frames: int = Field(ge=0, le=60)  # 入り（A2。緩急は easeOutCubic）
    out_frames: int = Field(ge=0, le=60)  # 出（A2。緩急は easeInCubic）
    pop: str = Field(pattern=r"^(pop_in_soft|pop_in|pop_express)$")  # B2
    far_slide: bool  # B1: 画面の外からのスライドを使うか
    # E1: 切り替えの上限（1 分あたり）。None は上限なし、0 はカットだけ
    transitions_per_min: int | None
    # G2: 強い動き（行き過ぎのある pop・揺れ・ズームパンチ・フラッシュ）の上限。None は上限なし
    strong_per_min: int | None
    strong_total: int | None
    change_interval_sec: float | None  # Q2: 変化の間隔の最大。None は検査しない
    stepped: int = Field(default=0, ge=0, le=4)  # M5: コマ打ち（0 は使わない）
    motion_blur: bool = False  # E4


class DesignProfile(BaseModel):
    purpose: Purpose
    family: Family
    label: str  # 日本語の名前
    summary: str  # 主役・雰囲気
    orientation: str = Field(pattern=r"^(landscape|vertical|any)$")
    motion_level: int = Field(ge=0, le=4)  # 0=フェードのみ … 4=拍に同期・強い演出
    max_hues: int = Field(ge=1, le=6)  # 1 本の動画で使ってよい色相の数
    max_concurrent: int = Field(ge=1, le=6)  # 同時に出してよい要素（字幕を除く）
    palette: Palette
    typography: Typography
    subtitle: SubtitleSpec
    safe_area: SafeArea
    components: dict[Component, int]  # 許可（DENY / ANY / 回数）
    motion: MotionSpec
    # 背景だけ（図・カード・画像・題名が無い）時間の上限（秒）。None は映像そのものが主役で制限なし
    max_empty_sec: float | None = None
    keywords: list[str] = Field(default_factory=list)  # 指示文から用途を判定する語
    look: str | None = None  # 統合版 Y1〜Y10: 利用者が選んだ見た目の型（既定はなし）

    def allowance(self, c: Component) -> int:
        return self.components.get(c, DENY)

    def allows(self, c: Component) -> bool:
        return self.allowance(c) != DENY


class ProfileSummary(BaseModel):
    purpose: Purpose
    family: Family
    label: str
    summary: str
    orientation: str
    motion_level: int
    allowed: list[Component]
    limited: dict[Component, int]
    denied: list[Component]


class ClassifyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    has_music: bool | None = None  # 素材のヒント（分からなければ None）
    has_script: bool | None = None
    vertical: bool | None = None


class PurposeCandidate(BaseModel):
    purpose: Purpose
    score: float
    reasons: list[str] = Field(default_factory=list)


class ClassifyResult(BaseModel):
    purpose: Purpose
    confidence: float = Field(ge=0, le=1)
    candidates: list[PurposeCandidate]
    needs_confirmation: bool  # 確信が持てない。ユーザーに尋ねる
    # 「解説だけど MV っぽく」のように混ぜる指示が明示されたとき、許す部品の候補
    mixed_from: list[Purpose] = Field(default_factory=list)
    suggested_extras: list[Component] = Field(default_factory=list)
    note: str = ""


class ProjectDesign(BaseModel):
    purpose: Purpose | None = None  # None は未設定（書き出し前に決める）
    # 混ぜる指示が明示されたときだけ使う、用途の型では使わない部品の例外
    extras: list[Component] = Field(default_factory=list, max_length=12)
    series_id: str | None = Field(default=None, max_length=36)  # 統合版 Q4: シリーズの設定
    # 統合版 M3・M7: 曲のジャンル（MV・リリックの書体・質感）。None は未設定（ポップとして扱う）
    genre: Literal["ballad", "citypop", "rock", "edm", "vocaloid", "idol", "pop"] | None = None
    # 統合版 Q7: 聴覚補助の字幕（字幕動画・インタビュー・ニュースで選べる。既定はオフ）。
    # None は変えない（設定のときだけ使う）
    hearing_aid: bool | None = None
    # 統合版 Y1〜Y10: 見た目の型（用途ごとに選べるものだけ。""は「なし」に戻す。None は変えない）
    look: str | None = Field(default=None, pattern=r"^(|Y([1-9]|10))$")


SERIES_COLOR_KEYS = ("base", "base2", "surface", "main", "accent", "text", "text_muted")


class SeriesSettings(BaseModel):
    """統合版 Q4: シリーズの設定（色・書体・ロゴ・BGM・OP・ED・終了画面・サムネイルの型）。

    ファイルは利用者の PC の取り込みを許したフォルダの絶対パス（取り込みと同じ確認をする）。"""

    id: str = Field(default="", max_length=36)
    name: str = Field(min_length=1, max_length=60)
    colors: dict[str, str] = Field(default_factory=dict)  # 配色の役割（SERIES_COLOR_KEYS）→ 色
    heading_font: str = Field(default="", max_length=100)
    body_font: str = Field(default="", max_length=100)
    logo_path: str = Field(default="", max_length=1000)
    bgm_path: str = Field(default="", max_length=1000)
    op_path: str = Field(default="", max_length=1000)  # 3 秒。台本の 1 文目の発話の直後に置く
    ed_path: str = Field(default="", max_length=1000)  # 10 秒。最後の台詞の後に置く
    end_screen: bool = True  # 終了画面（S9）を使う用途で、終了画面を付けるか
    thumbnail_layout: str = Field(default="", max_length=20)  # S4 の型の名前（空はおすすめ順）

    @field_validator("colors")
    @classmethod
    def _colors(cls, v: dict[str, str]) -> dict[str, str]:
        out = {}
        for k, c in v.items():
            if k not in SERIES_COLOR_KEYS:
                raise ValueError(f"unknown color role {k}")
            h = c.lstrip("#")
            if len(h) != 6 or any(x not in "0123456789abcdefABCDEF" for x in h):
                raise ValueError(f"bad color {c}")
            out[k] = "#" + h.upper()
        return out


class Violation(BaseModel):
    code: str  # denied_component / over_limit / hue_count / font_count / subtitle_size / ...
    component: Component | None = None
    message: str
    severity: str = Field(default="error", pattern=r"^(error|warning)$")


class DesignCheck(BaseModel):
    purpose: Purpose | None
    ok: bool  # error が無い
    violations: list[Violation]
    used: dict[Component, int]  # 実際に使った部品と回数
