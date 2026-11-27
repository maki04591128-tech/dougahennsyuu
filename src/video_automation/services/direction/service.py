"""演出の自動化（演出プラン → 素材の生成 → タイムラインへの配置）。

画面の割り付け（16:9 / 9:16 共通の比率）:
- 上の帯（高さの 2.5%〜）: キーワードのテロップ（左寄せ）
- 中央の領域（13%〜71%）: カード・図表・画像（中央に 1 つ）
- 下: 字幕（字幕の帯と重ならない）

画面が空にならないよう、図・カードは次の図・カードが出るまで表示し続ける。
適用は 1 つの編集（Batch）としてまとめ、元に戻せる。LLM 経由（MCP）の適用は承認が必要。
"""

import asyncio
import json
import math
import random
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from video_automation.app.logging import get_logger
from video_automation.app.settings import Settings
from video_automation.database.database import Database
from video_automation.database.models import (
    Asset,
    AudioFile,
    Project,
    Script,
    ScriptCharacter,
    ScriptSegment,
)
from video_automation.integrations.ffmpeg.runner import FFmpeg
from video_automation.schemas.animation import (
    CardRequest,
    ClipAnimation,
    Easing,
    InfographicRequest,
    Keyframe,
    Transition,
    TransitionType,
)
from video_automation.schemas.design import Component, DesignProfile, Purpose
from video_automation.schemas.direction import (
    MAIN_KINDS,
    DirectionApplyRequest,
    DirectionApplyResult,
    DirectionPlan,
    DirectionPlanRequest,
    DirectionPlanResult,
    Shot,
    ShotKind,
)
from video_automation.schemas.provider import ChatMessage, TaskType
from video_automation.schemas.timeline import (
    AddClip,
    AddTrack,
    AssetImportRequest,
    AssetKind,
    AssetRead,
    Batch,
    ClipTransform,
    CommandOrigin,
    CommandRequest,
    DeleteClip,
    FitMode,
    MoveTrack,
    SetClipAnimation,
    TitleCardRequest,
    TrackKind,
)
from video_automation.services.animation.entrance import (
    ITEM_IN_PX,
    element_animation,
    slide_preset,
)
from video_automation.services.assets.look import match_file, palette_colors
from video_automation.services.design.edits import adjust_transform
from video_automation.services.design.guard import Placement, TextUse, is_dark
from video_automation.services.design.profiles import (
    END_SCREEN,
    LOWER_THIRD_PURPOSES,
    PROFILES,
    safe_area_for,
)
from video_automation.services.design.series import ED_SEC as SERIES_ED_SEC
from video_automation.services.design.series import OP_SEC as SERIES_OP_SEC
from video_automation.services.design.service import DesignService, manifest_record
from video_automation.services.direction import podcast
from video_automation.services.direction import prompts as plan_prompts
from video_automation.services.direction import purpose_parts as pp
from video_automation.services.direction.charts import anim_frames, chart_kind
from video_automation.services.direction.plan import (
    PLAN_SCHEMA,
    from_llm,
    normalize,
    prompt_lines,
    rule_plan,
)
from video_automation.services.llm_tasks.gateway import LLMError, LLMGateway
from video_automation.services.rendering import flower as fl
from video_automation.services.rendering import numbers, word_icon
from video_automation.services.rendering import t_extras as tx
from video_automation.services.rendering import text_scenes as tscn
from video_automation.services.rendering.card import card_layout, text_width
from video_automation.services.rendering.end_screen import END_SCREEN_SEC, draw_end_screen
from video_automation.services.rendering.lower_third import draw_lower_third
from video_automation.services.rendering.lower_third import schedule as lower_third_schedule
from video_automation.services.rendering.lower_third import show_sec as lt_show_sec
from video_automation.services.rendering.pictogram import pick_icon
from video_automation.services.rendering.purpose_cards import (
    draw_labels,
    draw_list_card,
    draw_rank_number,
)
from video_automation.services.rendering.title_card import TitleCardService
from video_automation.services.skills.library import SkillLibrary
from video_automation.services.timeline.timeline_service import (
    CommandError,
    TimelineNotFoundError,
    TimelineService,
)
from video_automation.services.voice.reading import emphasis_terms

log = get_logger("direction")

ZONE_TOP, ZONE_BOTTOM = 0.13, 0.71  # 図・カードの領域（高さ比）
# 縦長で立ち絵があるとき: 図・カードの領域の下端と、題名を上へずらす量（画面の高さ比）
V_ZONE_BOTTOM, V_TITLE_DY = 0.53, -0.1
# ケン・バーンズ（統合版 B3）: 拡大量 = 秒数 × 1.75%（8〜20%）。向きは順に繰り返す
KB_PER_SEC, KB_MIN, KB_MAX = 0.0175, 0.08, 0.20
KB_ORDER = ("ken_burns_in", "ken_burns_out", "pan_left", "pan_right")
STRIP_TOP = 0.025  # テロップの上端
SIDE = 0.035  # テロップの左右の余白（幅比）
# 立ち絵があるとき、図・カードを置ける横の範囲（幅比）。左右の端は立ち絵の列（layout-no-overlap）
TACHIE_MAIN_W = 0.56
_GAP_WORD = re.compile(r"[一-龥々ァ-ヴーA-Za-z0-9０-９]{2,10}")
_GAP_NUMBER = re.compile(
    r"約?[0-9０-９][0-9０-９.,/]*(?:[万億兆千]|[A-Za-z%％℃]+|倍|円|人|個|回|秒|分|時間|日|年)+"
)
_PARTICLES = set("がをにはのでともへやか")
_GAP_STOP = {"それ", "これ", "あれ", "ここ", "そこ", "本当", "今日", "大丈夫", "一緒", "次回"}


def gap_keyword(text: str, names: tuple[str, ...] = ()) -> str:
    """空いた時間に出すテロップの語。その発話の中の語だけを使う（統合版 Q2 の補い）。

    数値と単位（「約12万km」）、かぎかっこの語、無ければ漢字・カタカナの最も長い語
    （送り仮名が続く語「色合（わせ）」は語の途中なので使わない）。"""
    m = _GAP_NUMBER.search(text)
    if m:
        return m.group(0)[:12]
    terms = [t for t in emphasis_terms(text) if not re.search(r"\d", t)]
    if terms:
        return terms[0][:12]
    words = []
    for m in _GAP_WORD.finditer(text):
        w, nxt = m.group(0), text[m.end() : m.end() + 1]
        if w in _GAP_STOP or any(w in n or n in w for n in names):
            continue
        if nxt and "ぁ" <= nxt <= "ゟ" and nxt not in _PARTICLES:
            continue
        words.append(w)
    return max(words, key=len) if words else ""


PARALLAX_SHIFT = 0.03  # 統合版 M8: カメラの横の移動は最大で被写体の幅の 3%


def _has_alpha(path: Path) -> bool:
    """透明の部分がある画像（切り抜いた絵）か。"""
    try:
        from PIL import Image

        with Image.open(path) as im:
            if "A" not in im.getbands() and "transparency" not in im.info:
                return False
            lo = im.convert("RGBA").getchannel("A").getextrema()[0]
            return isinstance(lo, int | float) and lo < 250
    except OSError:
        return False


def with_parallax(anim: ClipAnimation | None, length: int, dx: float) -> ClipAnimation:
    """統合版 M8: 入り・出の動きに、x を −dx/2 → +dx/2（等速）で足す。"""
    keys = {k.frame: k.model_copy() for k in (anim.keyframes if anim else [])}
    for f, x in ((0, -dx / 2), (max(1, length - 1), dx / 2)):
        if f in keys:
            keys[f].x = round(x, 5)
        else:
            keys[f] = Keyframe(frame=f, x=round(x, 5), easing=Easing.LINEAR)
    out = sorted(keys.values(), key=lambda k: k.frame)
    data = anim.model_dump() if anim else {}
    data["keyframes"] = [k.model_dump() for k in out]
    return ClipAnimation.model_validate(data)


def names_of(lines: list[Any]) -> tuple[str, ...]:
    return tuple({x.speaker for x in lines if getattr(x, "speaker", "")})


TRACKS = ("演出・背景", "演出・図", "演出・文字")
# 使うときだけ作るトラック（同じトラックのクリップは重ねられないので、カードの新しい項目（D1）と
# 名前帯（P1）は別のトラックに置く）。いちばん上に重ねる
ITEM_TRACK, LOWER_THIRD_TRACK = "演出・項目", "演出・名前帯"
PART_TRACK, STEP_TRACK = "演出・部品", "演出・手順"  # 統合版 P4・P5・P8
SERIES_TRACK, LOGO_TRACK = "演出・シリーズ", "演出・ロゴ"  # 統合版 Q4
WAVE_TRACK = "演出・波形"  # 統合版 P2
CUT_TRACK = "演出・切り替え"  # 統合版 E6・E9・E10
FX_TRACK, EMBER_TRACK = "演出・効果", "演出・粒"  # 統合版 X1〜X6
CLOSE_TRACK, NEWS_TRACK, TICKER_TRACK = "演出・締め", "演出・ニュースの帯", "演出・ティッカー"
CHAPTER_TRACK = "演出・章"  # 統合版 H1・N6
DIAGRAM_TRACK = "演出・図解"  # 統合版 N1・N7・N8・N11
YEAR_TRACK = "演出・年号"  # 統合版 T43
PLACE_TRACK = "演出・場所"  # 統合版 H2
MARK_TRACK = "手描きの印"  # 統合版 N12・N13（スライドの上。build が置く）
CARD_MARK_TRACK = "演出・項目の印"  # 統合版 I1・N15
CALLOUT_TRACK = "演出・特長の線"  # 統合版 P9
MAP_TRACK = "演出・地図"  # 統合版 J1〜J3
STRIKE_TRACK = "演出・取り消し"  # 統合版 T36・T46
TEXT_SCENE_TRACK = "演出・文字だけの場面"  # 統合版 T30〜T34
J_PURPOSES = {Purpose.DOCUMENTARY, Purpose.NEWS, Purpose.EXPLAINER}
TITLE_BACK_TRACK, TITLE_BACK2_TRACK = "演出・題名の後ろ", "演出・題名の後ろ2"  # 統合版 T47
TITLE_RING_TRACK = "演出・題名の円周"  # 統合版 T50
BEHIND_TITLE = (TITLE_BACK_TRACK, TITLE_BACK2_TRACK, TITLE_RING_TRACK)  # 立ち絵と題名の下に置く
TITLE_ECHO_TRACK, TITLE_ECHO2_TRACK = "演出・題名の残響", "演出・題名の残響2"  # 統合版 T49
CRAWL_TRACK = "演出・奥へ流れる文字"  # 統合版 T44
# 統合版 段階5d: LLM の chart の種類 → 図、用途 → 使ってよい図
D_KIND = {
    "number": "D6", "share": "D7", "progress": "D12", "bars": "D3", "race": "D5",
    "pair": "D8", "radar": "D9", "flow": "D10", "funnel": "D11",
}  # fmt: skip
D_PURPOSES: dict[Purpose, set[str]] = {
    Purpose.EXPLAINER: {"D3", "D5", "D6", "D7", "D8", "D9", "D10", "D11", "D12"},
    Purpose.LECTURE: {"D6", "D12"},
    Purpose.NEWS: {"D5", "D7", "D8"},
    Purpose.CORPORATE: {"D6", "D11", "D12"},
    Purpose.PRODUCT: {"D6", "D9"},
    Purpose.PROMO: {"D6"},
    Purpose.RANKING: {"D5"},
}
T43_PURPOSES = {Purpose.DOCUMENTARY, Purpose.EXPLAINER, Purpose.LECTURE}
N6_PURPOSES = {Purpose.EXPLAINER, Purpose.LECTURE, Purpose.TUTORIAL}
H1_PURPOSES = {
    Purpose.DOCUMENTARY, Purpose.EXPLAINER, Purpose.LECTURE, Purpose.TUTORIAL, Purpose.GAME,
}  # fmt: skip
EXTRA_TRACKS = (
    *BEHIND_TITLE,
    TITLE_ECHO_TRACK,
    TITLE_ECHO2_TRACK,
    CRAWL_TRACK,
    PLACE_TRACK,
    YEAR_TRACK,
    CHAPTER_TRACK,
    DIAGRAM_TRACK,
    CLOSE_TRACK,
    NEWS_TRACK,
    TICKER_TRACK,
    CUT_TRACK,
    FX_TRACK,
    EMBER_TRACK,
    ITEM_TRACK,
    CARD_MARK_TRACK,
    CALLOUT_TRACK,
    MAP_TRACK,
    STRIKE_TRACK,
    TEXT_SCENE_TRACK,
    LOWER_THIRD_TRACK,
    PART_TRACK,
    STEP_TRACK,
    SERIES_TRACK,
    LOGO_TRACK,
    WAVE_TRACK,
)
TACHIE_TRACK = "立ち絵"  # characters.service.TRACK
RECORDING_TRACK = "画面録画"  # 画面録画を中央の枠に置くトラック（build.service）
CHAPTER_CARD_SEC = 2.4  # 画面録画があるときのカードの長さ（章カード: 出す・止める・消す）
BACKGROUNDS = {
    "navy": ("0x0B1D3A", "0x2A5A8A"),
    "charcoal": ("0x15171C", "0x3A3F4A"),
    "teal": ("0x062A2E", "0x1D6B6B"),
    "plum": ("0x1F0F2E", "0x5A2D6E"),
}
# 演出プランを作るときに参考にするスキル
SKILLS_FOR_PLAN = [
    "video-design-by-purpose",
    "advanced-motion-techniques",
    "layout-no-overlap",
    "blender-video-motion-design",
    "blender-rich-screen-direction",
    "screen-tutorial-direction",
]
# 画面録画を見せる用途（screen-tutorial-direction の既定の演出を優先して読ませる）
_SCREEN_PURPOSES = {"tutorial", "explainer"}
# 動く背景を「浮かぶ形」にする用途（それ以外は、箱の床が波打つアイソメトリック）
FLOATING_BG_PURPOSES = {"mv", "lyric", "trailer", "promo", "event", "product"}
CHART_MAX_SEC = 30.0
CARD_SCALE = 1.25  # 図・カード領域のカードの文字の大きさ（字幕より少し小さい程度）
HOLD_LINES = 2  # 図・カードを説明の範囲の後に出し続ける発話数の上限
REVEAL_MIN_SEC = 1.2  # カードの項目を順に出すとき、1 項目を出しておく最短の時間


def _json_items(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


@dataclass
class _Line:
    speaker: str
    text: str
    start: int
    end: int
    emotion: str = ""
    items: dict[str, Any] = field(default_factory=dict)  # 統合版 段階5d: LLM が付けた項目
    query: dict[str, Any] | None = None  # 音声のクエリ（統合版 T38 の最も強い句）


# 統合版 T38: 1 文に 1 つの強調（強調テロップを使う用途）
T38_PURPOSES = frozenset(
    {
        Purpose.EXPLAINER, Purpose.LECTURE, Purpose.TUTORIAL, Purpose.RECIPE,
        Purpose.CORPORATE, Purpose.SHORT, Purpose.GAME, Purpose.VLOG, Purpose.RANKING,
        Purpose.PRODUCT, Purpose.PROMO, Purpose.EVENT, Purpose.KIDS,
    }
)  # fmt: skip
T38_SCALE = 1.15
# 統合版 T35: 強調テロップの 2 桁以上の半角数字を回す用途
T35_PURPOSES = frozenset({Purpose.EXPLAINER, Purpose.LECTURE, Purpose.CORPORATE, Purpose.PRODUCT})
_T35_NUM = re.compile(r"[0-9]{2,}")
# 統合版 T28: 語が図に変わる用途と、強調テロップの入りの長さ（この後 12f 静止して変える）
T28_PURPOSES = frozenset({Purpose.EXPLAINER, Purpose.LECTURE, Purpose.KIDS})
T28_ENTER = 10
# 統合版 F3: 子ども向けで縁をふるわせるクリップ（役割）と、ずらす量（1080p の px）
F3_ROLES = frozenset({"card", "keyword"})
F3_PX = 1.5
F3_MAX = 40


def t38_hit(line_text: str, phrase: str, query: dict[str, Any] | None, strike: bool = True) -> bool:
    """統合版 T38: 強調テロップの文節が、その文のアクセントの最も強い句（Q8）を含むか。
    句はかなだけなので、文の全モーラに対する句の位置の割合を文字の位置に読み替えて、
    文節の文字の範囲と重なるかで決める。文節が文の中に無い・クエリが無いときは付けない。
    strike が真（T36・T46 を使う用途）なら、「A ではなく B」の文には付けない。"""
    if not query or not phrase:
        return False
    i = line_text.find(phrase)
    if i < 0 or (strike and tx.not_phrases(line_text)):
        return False
    try:
        from video_automation.schemas.voice import AudioQuery
        from video_automation.services.voice.timing import strongest_phrase_span

        span = strongest_phrase_span(AudioQuery.model_validate(query))
    except Exception:  # noqa: BLE001 - 読めないクエリは強調しない
        return False
    if span is None:
        return False
    n = len(line_text)
    a, b = span[0] * n, span[1] * n
    return a < i + len(phrase) and i < b


# 統合版 T16: 用途ごとの題名の入り（MV・リリックは V5 の曲名のまま。1 文字ずつの入りは、
# 改行の無い KINETIC_TITLE_MAX 字以下の題名だけ。それ以外は pop）
TITLE_ENTRANCE: dict[Purpose, str] = {
    Purpose.SUBTITLE: "fade",
    Purpose.INTERVIEW: "mask_rise",
    Purpose.DOCUMENTARY: "typewriter",
    Purpose.STORY: "stroke",
    Purpose.PODCAST: "fade",
    Purpose.EXPLAINER: "pop",
    Purpose.LECTURE: "stroke",
    Purpose.TUTORIAL: "typewriter",
    Purpose.RECIPE: "pop",
    Purpose.NEWS: "mask_rise",
    Purpose.CORPORATE: "tracking",
    Purpose.SHORT: "slam",
    Purpose.GAME: "decode",
    Purpose.VLOG: "slide",
    Purpose.RANKING: "flip3d",
    Purpose.PRODUCT: "tracking",
    Purpose.PROMO: "tracking",
    Purpose.EVENT: "slide",
    Purpose.KIDS: "stroke",
    Purpose.TRAILER: "decode",
}
PER_CHAR_TITLES = {"typewriter", "decode", "tracking", "flip3d", "mask_rise", "stroke"}


def title_entrance(prof: DesignProfile | None, title: str, kinetic: bool) -> str:
    """題名の入り（統合版 T16）。"""
    if prof is None or prof.purpose not in TITLE_ENTRANCE:
        return "bounce" if kinetic else "pop"
    a = TITLE_ENTRANCE[prof.purpose]
    if a in PER_CHAR_TITLES and ("\n" in title or len(title) > KINETIC_TITLE_MAX):
        return "pop"
    return a


from video_automation.services.direction import transitions as tz  # noqa: E402
from video_automation.services.rendering.backgrounds import PURPOSE_BACKGROUND  # noqa: E402

BACKGROUND_NAMES = {
    "R1": "オーロラ", "R2": "星空", "R3": "点と線の網", "R4": "等高線", "R5": "点の格子の波紋",
    "R6": "ボケの光", "R7": "メンフィス柄", "R8": "空と雲", "R9": "方眼", "R10": "紙",
    "R11": "アウトランの格子", "R12": "集中線", "R13": "ギンガム柄", "R14": "走る細い線",
    "R15": "斜めの帯", "R16": "メッシュグラデーション", "R17": "スタジオの床と光",
    "R18": "ゆっくり落ちる紙吹雪",
}  # fmt: skip
KINETIC_TITLE_MAX = 16  # 1 文字ずつ動かす題名の文字数の上限（長いと読み終わる前に消える）
_NUM = re.compile(r"[0-9０-９]")


def _has_number(text: str) -> bool:
    return bool(_NUM.search(text or ""))


def _emphasis_budget(prof: DesignProfile | None) -> int:
    """数値テロップを強調の動きで出してよい回数（用途の型の許可。動きの弱い型は 2 回まで）。"""
    if prof is None:
        return 2
    n = prof.allowance(Component.EMPHASIS_TEXT)
    if n == 0:
        return 0
    cap = 2 if prof.motion_level <= 1 else 99
    return cap if n < 0 else min(n, cap)


def _reveal_steps(shot: Shot, lines: list, start: int, end: int, min_len: int) -> list:
    """カードの本文を 1 項目ずつ出す区間 [(開始, 終了, 出す項目数)] を返す。

    項目が 2 つ以上で、見出しか要点・まとめの型のときだけ順に出す。各項目は説明している
    発話（from_line〜to_line）に合わせて出し、発話が足りなければ等分する。
    どの区間も min_len 未満になるなら順に出さず、全部を一度に出す（None）。
    """
    n = len(shot.body)
    if n < 2 or shot.style == "keyword" or end - start < min_len * n:
        return [(start, end, None)]
    lo, hi = shot.from_line, max(shot.from_line, shot.to_line or shot.from_line)
    starts = [ln.start for ln in lines[lo - 1 : hi]]
    if len(starts) >= n:
        # 発話を項目数で区切り、各区切りの最初の発話で次の項目を出す
        times = [starts[(k * len(starts)) // n] for k in range(n)]
    else:
        times = [start + (end - start) * k // n for k in range(n)]
    times[0] = start
    times = [max(start, min(t, end)) for t in times]
    bounds = [*times, end]
    if any(bounds[k + 1] - bounds[k] < min_len for k in range(n)):
        times = [start + (end - start) * k // n for k in range(n)]
        bounds = [*times, end]
    return [(bounds[k], bounds[k + 1], k + 1) for k in range(n)]


def _polaroid(src: Any, caption: str, h: int, box: float, font_path: str | None) -> Any:
    """統合版 N17: 写真に白い縁（上左右 2%・下 7%）を付け、下の縁に一言を書いた画。
    一言は M PLUS Rounded 1c / Medium・画面の高さの 4%・#333333（置いた後の大きさで）。"""
    from PIL import Image, ImageDraw, ImageFont

    iw, ih = src.size
    side, bottom = round(ih * 0.02), round(ih * 0.07)
    k = box * h / (ih + side + bottom)  # 置いた後の 1 画素 = 画面の何画素か
    size = max(10, round(0.04 * h / k))
    bottom = max(bottom, round(size * 1.6))
    out = Image.new("RGBA", (iw + 2 * side, ih + side + bottom), (255, 255, 255, 255))
    out.alpha_composite(src.convert("RGBA"), (side, side))
    font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default(size)
    d = ImageDraw.Draw(out)
    full = caption.strip()
    text = full
    while text and d.textlength(text + ("…" if text != full else ""), font=font) > iw:
        text = text[:-1]
    if text != full:
        text += "…"
    d.text((out.size[0] / 2, ih + side + bottom / 2), text, font=font, fill=(0x33, 0x33, 0x33),
           anchor="mm")  # fmt: skip
    return out


def _hex_rgb(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def pattern_background(out: Path, w: int, h: int, c0: str, c1: str, main: str) -> None:
    """模様つきの静止画の背景: 斜めのグラデーション＋柔らかい光の円＋細かい点の格子。

    平らな単色・グラデーションだけだと画面が止まって見えるので、目立たない奥行きを付ける。"""
    from PIL import Image, ImageDraw, ImageFilter

    a, b, m = _hex_rgb(c0), _hex_rgb(c1), _hex_rgb(main)
    small = Image.new("RGB", (64, 36) if w >= h else (36, 64))
    sw, sh = small.size
    px = small.load()
    assert px is not None
    for y in range(sh):
        for x in range(sw):
            u = (x / (sw - 1) + y / (sh - 1)) / 2
            px[x, y] = tuple(round(a[i] + (b[i] - a[i]) * u) for i in range(3))
    im = small.resize((w, h), Image.Resampling.BICUBIC).convert("RGBA")
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    r = round(max(w, h) * 0.32)
    for cx, cy, al in ((0.82, 0.18, 46), (0.12, 0.9, 34)):
        x, y = round(w * cx), round(h * cy)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(*m, al))
    glow = glow.filter(ImageFilter.GaussianBlur(r * 0.45))
    im.alpha_composite(glow)
    dots = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(dots)
    step = max(24, round(min(w, h) / 27))
    rad = max(1, step // 14)
    for y in range(step // 2, h, step):
        for x in range(step // 2, w, step):
            d.ellipse((x - rad, y - rad, x + rad, y + rad), fill=(255, 255, 255, 14))
    im.alpha_composite(dots)
    im.convert("RGB").save(out)


def shorten_animation(cmds: list[Any], clip_id: str | None, old_len: int, cut: int) -> None:
    """クリップを cut フレーム短くしたとき、出のキー（後ろ半分）を同じだけ前へ寄せる。

    入りのキー（前半分）はそのまま。キーがクリップの外に出ると命令が通らない。"""
    for k, c in enumerate(cmds):
        if not isinstance(c, SetClipAnimation) or c.clip_id != clip_id or c.animation is None:
            continue
        keys: list[Any] = []
        for kf in c.animation.keyframes:
            f = kf.frame if kf.frame < old_len / 2 else max(0, kf.frame - cut)
            if f < old_len - cut and (not keys or f > keys[-1].frame):
                keys.append(kf.model_copy(update={"frame": f}))
        anim = c.animation.model_copy(update={"keyframes": keys})
        cmds[k] = c.model_copy(update={"animation": anim})


def native_transform(
    aw: int,
    ah: int,
    cw: int,
    ch: int,
    *,
    cx: float,
    cy: float,
    max_h: float = 1.0,
    max_w: float = 0.92,
) -> ClipTransform:
    """素材をそのままの大きさ（はみ出すときは縮小）で、中心 (cx, cy)（画面比）に置く。"""
    s = max(aw / cw, ah / ch)
    k = native_scale(aw, ah, cw, ch, max_h, max_w)
    return ClipTransform(fit=FitMode.FIT, scale=round(s * k, 4), x=cx - 0.5, y=cy - 0.5)


def text_use(
    kind: str,
    req: CardRequest,
    a: AssetRead,
    cw: int,
    ch: int,
    start: float,
    end: float,
    max_h: float = 1.0,
    max_w: float = 0.92,
) -> TextUse:
    """カード・テロップの文字の、画面での大きさ（短辺比）と表示の秒（統合版 T39・T40・T52）。"""
    layout = card_layout(req, cw, ch)
    k = native_scale(a.width or layout.width, a.height or layout.height, cw, ch, max_h, max_w)
    unit = min(cw, ch)
    sizes = sorted({round(ln.size * k / unit, 4) for ln in layout.lines})
    chars = sum(len(ln.text) for ln in layout.lines)
    return TextUse(kind, sizes, chars, start, end)


def native_scale(
    aw: int, ah: int, cw: int, ch: int, max_h: float = 1.0, max_w: float = 0.92
) -> float:
    """native_transform で置いたときの、素材の画素に対する表示の倍率。"""
    return min(1.0, max_h * ch / ah, max_w * cw / aw)


def fit_to_profile(
    plan: DirectionPlan, prof: DesignProfile | None, extras: list[Component] | None = None
) -> tuple[DirectionPlan, list[str]]:
    """用途の型で使わない部品（題名・カード・図表・画像・テロップ）を外し、回数の上限で切る。"""
    if prof is None:
        return plan, []
    extras = extras or []
    kind_comp = {
        ShotKind.CARD: Component.CARD,
        ShotKind.CHART: Component.CHART,
        ShotKind.IMAGE: Component.IMAGE,
        ShotKind.KEYWORD: Component.KEYWORD,
    }
    notes: list[str] = []
    count: dict[Component, int] = {}
    shots = []
    for s in plan.shots:
        c = kind_comp[s.kind]
        limit = -1 if c in extras else prof.allowance(c)
        n = count.get(c, 0)
        if limit == 0 or (limit > 0 and n >= limit):
            why = "使いません" if limit == 0 else f"{limit} 回までです"
            notes.append(
                f"{s.from_line}: 「{prof.label}」では {s.kind.value} を{why}（外しました）"
            )
            continue
        count[c] = n + 1
        shots.append(s)
    update: dict[str, object] = {"shots": shots}
    if plan.title and not (prof.allows(Component.TITLE_CARD) or Component.TITLE_CARD in extras):
        update.update(title="", subtitle="")
        notes.append(f"「{prof.label}」では題名カードを使いません（外しました）")
    return plan.model_copy(update=update), notes


class DirectionService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        llm: LLMGateway,
        timeline: TimelineService,
        titles: TitleCardService,
        ffmpeg: FFmpeg,
        design: DesignService | None = None,
        skills: SkillLibrary | None = None,
    ) -> None:
        self.skills = skills
        self.design = design
        self.settings = settings
        self.db = db
        self.llm = llm
        self.timeline = timeline
        self.titles = titles
        self.ffmpeg = ffmpeg
        # 統合版 N16: 書類の画像を読む OCR（起動時に画面録画と同じものを差す。無ければ使わない）
        self.ocr_source: Any = lambda: None

    async def _podcast_waves(
        self, p: Project, script_id: str, total: int, work: Path
    ) -> AssetRead | None:
        """統合版 P2: 話者ごとの声を色分けした波形の動画（透過）を作って素材にする。"""
        from video_automation.database.models import AudioFile
        from video_automation.services.design.oklch import FRAME, derive

        async with self.db.session_factory() as s:
            rows = (
                await s.execute(
                    select(ScriptSegment.speaker, ScriptSegment.start_frame, AudioFile.path)
                    .join(AudioFile, AudioFile.id == ScriptSegment.audio_file_id)
                    .where(ScriptSegment.script_id == script_id)
                )
            ).all()
        chars = await self._characters(script_id)
        fps = Fraction(p.fps_num, p.fps_den)
        total_sec = float(total / fps)
        data = Path(self.settings.data_dir)
        by: dict[str, list[tuple[Path, float]]] = {}
        for spk, start, path in rows:
            if start is not None and (data / path).is_file():
                by.setdefault(spk or "", []).append((data / path, float(start / fps)))
        if not by:
            return None
        speakers = list(by)
        wav = work / f"wave_{uuid4().hex[:8]}.wav"
        peak = await asyncio.to_thread(
            podcast.speaker_tracks, [by[x] for x in speakers], total_sec, wav
        )
        palette = ("#7FB7E6", "#F4A259", "#8CCB9B")
        colors = [
            derive(chars.get(x, {}).get("color") or palette[i % 3], FRAME)
            for i, x in enumerate(speakers)
        ]
        mov = work / f"wave_{uuid4().hex[:8]}.mov"
        try:
            await self.ffmpeg.run(
                [
                    "-y",
                    *podcast.waveform_args(
                        wav,
                        peak,
                        colors,
                        p.width,
                        p.height,
                        f"{fps.numerator}/{fps.denominator}",
                        total_sec,
                        mov,
                    ),
                ]
            )
        finally:
            wav.unlink(missing_ok=True)
        asset = await self.timeline.register_generated(
            p.id, mov, AssetKind.VIDEO, "ffmpeg", "ポッドキャストの波形"
        )
        return AssetRead.model_validate(asset)

    async def _characters(self, script_id: str) -> dict[str, dict[str, str]]:
        """話者の名前 → 肩書き・色（名前帯に使う。統合版 P1）。"""
        async with self.db.session_factory() as s:
            rows = (
                await s.execute(
                    select(ScriptCharacter).where(ScriptCharacter.script_id == script_id)
                )
            ).scalars()
            return {r.name: {"job_title": r.job_title or "", "color": r.color or ""} for r in rows}

    async def _script(self, script_id: str) -> tuple[Project, Script, list[_Line]]:
        async with self.db.session_factory() as s:
            sc = await s.get(Script, script_id)
            if sc is None:
                raise TimelineNotFoundError(f"script {script_id}")
            p = await s.get(Project, sc.project_id)
            assert p is not None
            segs = (
                await s.execute(
                    select(ScriptSegment, AudioFile.query)
                    .outerjoin(AudioFile, AudioFile.id == ScriptSegment.audio_file_id)
                    .where(ScriptSegment.script_id == script_id)
                    .order_by(ScriptSegment.sequence)
                )
            ).all()
        lines = [
            _Line(
                x.speaker or "",
                x.text,
                x.start_frame or 0,
                x.end_frame or 0,
                x.emotion or "",
                _json_items(x.items),
                q if isinstance(q, dict) else None,
            )
            for x, q in segs
        ]
        return p, sc, lines

    async def _has_tachie(self, script_id: str) -> bool:
        """台本の登場キャラクターに立ち絵が付いているか（付いていれば左右の端は立ち絵の列）。"""
        async with self.db.session_factory() as s:
            row = (
                await s.execute(
                    select(ScriptCharacter.id)
                    .where(
                        ScriptCharacter.script_id == script_id,
                        ScriptCharacter.tachie_pack.is_not(None),
                    )
                    .limit(1)
                )
            ).first()
        return row is not None

    async def _matched_image(
        self,
        project_id: str,
        img: AssetRead,
        prof: DesignProfile,
        assets: dict[str, AssetRead],
    ) -> AssetRead:
        """画像の見た目を用途の配色に揃えた複製（統合版 Q3）。直す所が無ければ元のまま。"""
        dst = (
            Path(self.settings.data_dir)
            / "projects"
            / project_id
            / "assets"
            / f"look_{img.sha256[:16]}_{prof.purpose.value}.png"
        )
        rel = dst.resolve().relative_to(Path(self.settings.data_dir).resolve()).as_posix()
        for a in assets.values():  # 前に作った複製を使う
            if a.path == rel:
                return a
        try:
            src = self.timeline.asset_path(img)  # type: ignore[arg-type]
            res = await asyncio.to_thread(
                match_file, src, dst, palette_colors(prof.palette), img.name
            )
        except (OSError, ValueError) as exc:  # 読めない画像は元のまま使う
            log.info("look_match_failed", asset_id=img.id, error=type(exc).__name__)
            return img
        if not res.steps:
            dst.unlink(missing_ok=True)
            return img
        row = await self.timeline.register_generated(
            project_id, dst, AssetKind.IMAGE, "look", f"{img.name}（見た目を揃えた）"
        )
        out = AssetRead.model_validate(row)
        assets[out.id] = out
        log.info("look_matched", asset_id=img.id, steps=res.steps, individual=res.individual)
        return out

    # ---------- プラン ----------
    async def _skill_context(self, project_id: str) -> tuple[str, list[str]]:
        """演出の決まり（スキル）のうち、この用途に関係する節。"""
        if self.skills is None:
            return "", []
        prof = await self.design.profile_for(project_id) if self.design else None
        purpose = f"{prof.label} {prof.summary} " if prof else ""
        query = (
            f"{purpose}演出 テロップ カード 図表 題名 字幕 配置 動き "
            "別用途のデザインを混ぜない 背景だけの時間"
        )
        if prof and prof.purpose in _SCREEN_PURPOSES:
            # 画面録画の解説は、強化版の演出（ズーム・強調・章・音・立ち絵）を既定にする
            query += " 既定で入れる演出 画面録画 ズーム 章の切り替え 立ち絵 BGM 効果音"
        # 依頼文ではなく決まった問い合わせなので、しきい値は使わず上位の節を入れる
        ctx, picked = self.skills.context(
            query, names=SKILLS_FOR_PLAN, budget=self.settings.skills_budget_chars, min_score=0.0
        )
        return ctx, [f"{p.skill} / {p.path}" for p in picked]

    async def plan(self, req: DirectionPlanRequest) -> DirectionPlanResult:
        p, sc, lines = await self._script(req.script_id)
        texts = [x.text for x in lines]
        names = tuple({x.speaker for x in lines})
        if not texts:
            raise CommandError("script has no lines")
        title = sc.title or ""
        model_key = None
        used_skills: list[str] = []
        if req.use_llm:
            ctx, used_skills = await self._skill_context(p.id)
            prof = await self.design.profile_for(p.id) if self.design else None
            extras = (await self.design.get(p.id)).extras if self.design and prof else []
            body = f"タイトル案: {title or '（なし）'}\n台本:\n" + prompt_lines(
                [x.speaker for x in lines], texts
            )
            if req.instruction.strip():
                body += (
                    "\n\n演出への希望（作った人から。下の決まりに反しない範囲で従う）: "
                    + req.instruction.strip()
                )
            pp = plan_prompts.build(body, prof, extras, ctx)
            sent = (pp.rules, pp.examples)
            msgs = [
                ChatMessage(role="system", content=pp.system),
                ChatMessage(role="user", content=pp.user),
            ]
            res = await self.llm.complete_json(  # 形式が合わないときは 2 回まで直させる
                TaskType.EDIT_PLAN,
                msgs,
                PLAN_SCHEMA,
                model_key=req.model_key,
                temperature=0.3,
                retries=2,
            )
            model_key = res.model_key
            raw = from_llm(res.data)
            usable = plan_prompts.usable(prof, extras)
            if not usable:  # 題名だけの用途（字幕動画など）は演出が無いのが正しい
                raw.notes = [x for x in raw.notes if "演出がありませんでした" not in x]
            elif not raw.shots and "keyword" in usable:
                # AI が何も出さなかったときは規則の演出（数値のテロップ）で補う
                raw.shots = rule_plan(texts, title).shots
            plan = normalize(raw, texts, title, names, sent)
            # 図・カードが少なすぎる（同じカードが出続けて画面が止まる）ときは 1 回だけ直させる。
            # 目安は 4 発話に 1 つ（指示の「2〜4 発話に 1 つ」の下限）。使えない用途では直させない
            need = plan_prompts.need_main(prof, extras, len(texts))
            have = sum(1 for x in plan.shots if x.kind in MAIN_KINDS)
            if have < need:
                more = plan_prompts.retry_text(prof, extras, have, len(texts), need)
                try:
                    res2 = await self.llm.complete_json(
                        TaskType.EDIT_PLAN,
                        [
                            *msgs,
                            ChatMessage(
                                role="assistant", content=json.dumps(res.data, ensure_ascii=False)
                            ),
                            ChatMessage(role="user", content=more),
                        ],
                        PLAN_SCHEMA,
                        model_key=req.model_key,
                        temperature=0.3,
                        retries=1,
                    )
                    plan2 = normalize(from_llm(res2.data), texts, title, names, sent)
                    if sum(1 for x in plan2.shots if x.kind in MAIN_KINDS) > have:
                        plan = plan2
                except LLMError:
                    pass  # 直せなければ最初の案のまま
        else:
            plan = normalize(rule_plan(texts, title), texts, title, names)
        log.info(
            "direction_plan",
            script_id=req.script_id,
            shots=len(plan.shots),
            notes=len(plan.notes),
            model=model_key,
        )
        return DirectionPlanResult(
            plan=plan, model_key=model_key, lines=len(texts), skills=used_skills
        )

    # ---------- 適用 ----------
    @staticmethod
    def _fill_gaps(
        cmds: list[Any],
        kws: list[Shot],
        lines: list[_Line],
        prof: DesignProfile | None,
        fps: float,
        title_end: int,
        names: tuple[str, ...],
    ) -> list[Shot]:
        """画面が変わらない時間が用途の「変化の間隔の最大」（統合版 Q2）を超える所に、
        その発話の語のテロップを足す（実機: 縦型ショートで 8 秒間、画面が変わらなかった）。

        語はその発話から取る（数値と単位・かぎかっこの語、無ければ漢字・カタカナの最も長い語）。
        台本に無い語は作らない。"""
        if prof is None or prof.motion.change_interval_sec is None:
            return []
        if not prof.allows(Component.KEYWORD):
            return []
        limit = round(prof.motion.change_interval_sec * fps)
        times = {c.start_frame for c in cmds if isinstance(c, AddClip)} | {
            lines[k.from_line - 1].start for k in kws
        }
        times.add(title_end)
        out: list[Shot] = []
        last = title_end
        taken = {k.from_line for k in kws}
        for i, ln in enumerate(lines, 1):
            nearby = [t for t in times if last < t <= ln.start]
            if nearby:
                last = max(nearby)
            if ln.start - last < limit * 0.6 or i in taken:
                continue
            word = gap_keyword(ln.text, names)
            if word:
                out.append(Shot(kind=ShotKind.KEYWORD, from_line=i, text=word))
                last = ln.start
        return out

    async def _background(
        self, project_id: str, style: str, w: int, h: int, prof: DesignProfile | None = None
    ) -> AssetRead:
        """背景。単色・平らなグラデーションだけにはしない（advanced-motion-techniques）。

        動きが許される用途は Blender の 3D の動く背景（箱の床が波打つ／形が浮かぶ）。
        Blender が無い・失敗したとき、動きが許されない用途は、模様つきの静止画にする。"""
        c0, c1 = BACKGROUNDS.get(style, BACKGROUNDS["navy"])
        main, accent = "#3BB8DB", "#FFC752"
        if prof is not None:  # 用途の型の背景（暗い側→明るい側のグラデーション）
            c0 = "0x" + prof.palette.base.lstrip("#")
            c1 = "0x" + prof.palette.base2.lstrip("#")
            main, accent = prof.palette.main, prof.palette.accent
            style = f"{prof.purpose.value}"
        if prof is not None and prof.purpose.value in PURPOSE_BACKGROUND:
            try:  # 統合版 段階5d: 用途ごとの背景 R1〜R18
                return await self._r_background(project_id, prof, w, h)
            except Exception as exc:  # noqa: BLE001 - 今の背景で続ける
                log.warning("r_background_failed", error=str(exc)[:300])
        moving = prof is None or prof.allows(Component.BACKGROUND_MOTION)
        if moving and self.titles.blender.available:
            loud = prof is not None and prof.purpose.value in FLOATING_BG_PURPOSES
            liquid = prof is not None and prof.allows(Component.LIQUID)  # 統合版 M6
            try:
                return await self.titles.motion_background(
                    project_id,
                    style="liquid" if liquid else "floating" if loud else "iso_blocks",
                    base="#" + c0[2:],
                    base2="#" + c1[2:],
                    main=main,
                    accent=accent,
                    name=f"動く背景（{style}）",
                )
            except Exception as exc:  # noqa: BLE001 - 静止画の背景で続ける
                log.warning("motion_background_failed", error=str(exc)[:300])
        out_dir = self.settings.data_dir / "projects" / project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"bg_{style}_{w}x{h}_p.png"
        if not out.is_file():
            pattern_background(out, w, h, "#" + c0[2:], "#" + c1[2:], main)
        a = await self.timeline.register_generated(
            project_id, out, AssetKind.IMAGE, "pillow", f"背景（{style}）"
        )
        return AssetRead.model_validate(a)

    async def _png(self, project_id: str, img: Any, name: str) -> AssetRead:
        out = (
            self.settings.data_dir / "projects" / project_id / "assets" / f"h_{uuid4().hex[:8]}.png"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(img.save, out)
        row = await self.timeline.register_generated(
            project_id, out, AssetKind.IMAGE, "pillow", name
        )
        return AssetRead.model_validate(row)

    async def _h_cards(
        self,
        project_id: str,
        prof: DesignProfile,
        plan: DirectionPlan,
        lines: list[Any],
        chapters: list[tuple[int, str]],
        cmds: list[Any],
        lane: Any,
        total: int,
        fps: float,
        w: int,
        h: int,
        font: str | None,
        ink: str,
    ) -> list[str]:
        """統合版 H1（章の題名）・H3（ニュース）・H4・H6（朗読・物語）・H5（締め）。"""
        from video_automation.services.rendering import h_cards as hc

        notes: list[str] = []
        pv = prof.purpose

        def anim(cid: str, keys: list[Keyframe]) -> None:
            cmds.append(SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=keys)))

        def clip(track: str, a: AssetRead, at: int, n: int, tf: ClipTransform) -> str:
            cid = str(uuid4())
            cmds.append(
                AddClip(track_id=track, asset_id=a.id, start_frame=at, duration_frames=n,
                        clip_id=cid, transform=tf)
            )  # fmt: skip
            return cid

        if pv == Purpose.STORY:  # H6: 「その頃」「一方」… で始まる文に中間字幕のカード
            mincho = self.titles.font_path("Zen Old Mincho") or font
            last_end = -1
            for ln in lines:
                if not hc.is_intertitle(ln.text) or ln.start < last_end:
                    continue
                n = min(round(hc.intertitle_sec(ln.text) * fps), max(1, total - ln.start))
                img = await asyncio.to_thread(hc.intertitle, w, h, ln.text.strip(), mincho)
                a = await self._png(project_id, img, "中間字幕（H6）")
                cid = clip(lane(CLOSE_TRACK), a, ln.start, n, ClipTransform(fit=FitMode.FIT))
                d8 = min(8, n // 2)
                anim(cid, [Keyframe(frame=0, opacity=0.0), Keyframe(frame=d8, opacity=1.0),
                           Keyframe(frame=max(d8 + 1, n - 1 - d8), opacity=1.0),
                           Keyframe(frame=max(d8 + 2, n - 1), opacity=0.0)])  # fmt: skip
                last_end = ln.start + n
            if last_end > 0:
                notes.append("中間字幕のカード（H6）の間も、その文の字幕は出ています")
            # H4: 最後にクレジットロール（終了画面のある用途では出さない）
            names = list(dict.fromkeys(f"VOICEVOX:{ln.speaker}" for ln in lines if ln.speaker))
            if names:
                img = await asyncio.to_thread(hc.credits, w, h, names, font, ink)
                roll, hold = hc.credit_frames(h, img.size[1], fps)
                n = roll + hold
                at = max(0, total - n)
                if last_end <= at:
                    a = await self._png(project_id, img, "クレジットロール（H4）")
                    sh = img.size[1] / h
                    tf = native_transform(w, img.size[1], w, h, cx=0.5, cy=0.5, max_w=1.0,
                                          max_h=sh + 0.01)  # fmt: skip
                    ln_ = min(n, total - at)
                    y0, y1 = 0.5 + sh / 2, -sh / 2
                    f1 = min(roll, ln_ - 1)  # クリップの中に収める（短いときは途中まで流す）
                    y_end = y0 + (y1 - y0) * f1 / max(1, roll)
                    cid = clip(lane(CLOSE_TRACK), a, at, ln_, tf)
                    if f1 > 0:
                        anim(cid, [Keyframe(frame=0, y=round(y0, 4), easing=Easing.LINEAR),
                                   Keyframe(frame=f1, y=round(y_end, 4))])  # fmt: skip
        toc_n = 0
        if pv in N6_PURPOSES and len(chapters) >= 3:  # N6: 2 章目からの頭に目次（2.0 秒）
            from video_automation.services.rendering import n_cards as nc

            toc_n = round(2.0 * fps)
            titles = [t for _a, t in chapters]
            for k, (at, _t) in enumerate(chapters):
                if k == 0 or at + toc_n > total:
                    continue
                img = await asyncio.to_thread(
                    nc.toc, w, h, titles, k, font, prof.palette.accent, ink, prof.palette.surface
                )
                a = await self._png(project_id, img, "目次（N6）")
                cid = clip(lane(CHAPTER_TRACK), a, at, toc_n, ClipTransform(fit=FitMode.FIT))
                anim(cid, [Keyframe(frame=0, opacity=0.0), Keyframe(frame=6, opacity=1.0),
                           Keyframe(frame=toc_n - 7, opacity=1.0),
                           Keyframe(frame=toc_n - 1, opacity=0.0)])  # fmt: skip
        if pv in H1_PURPOSES:  # H1: 2 章目からの頭に章の題名カード（2.5 秒、出は B4 の 6f）
            n = round(2.5 * fps)
            last = -1
            for k, (at0, text) in enumerate([c_ for c_ in chapters if c_[0] > 0], start=2):
                at = at0 + (toc_n if len(chapters) >= 3 else 0)  # N6 の後
                if at < last or at + n > total:
                    continue
                if pv == Purpose.GAME:  # T48: 題を縁だけで出してから、左から塗る
                    num, ttl, mask = await asyncio.to_thread(
                        hc.chapter_parts, w, h, k, text[:30], font, prof.palette.accent, ink
                    )
                    a = await self._overlay(
                        project_id,
                        tx.chapter_outline_fill(num, ttl, mask, prof.palette.accent, n, h),
                        f"章の題名（H1・T48）「{text[:12]}」",
                        fps,
                    )
                    cid = clip(lane(CHAPTER_TRACK), a, at, n, ClipTransform(fit=FitMode.FIT))
                    anim(cid, [Keyframe(frame=0, opacity=1.0),
                               Keyframe(frame=n - 7, opacity=1.0, scale=1.0,
                                        easing=Easing.CUBIC_IN),
                               Keyframe(frame=n - 1, opacity=0.0, scale=0.96)])  # fmt: skip
                    last = at + n
                    continue
                img = await asyncio.to_thread(
                    hc.chapter_card, w, h, k, text[:30], font, prof.palette.accent, ink
                )
                a = await self._png(project_id, img, f"章の題名（H1）「{text[:12]}」")
                cid = clip(lane(CHAPTER_TRACK), a, at, n, ClipTransform(fit=FitMode.FIT))
                anim(cid, [Keyframe(frame=0, opacity=0.0, y=0.02, easing=Easing.CUBIC_OUT),
                           Keyframe(frame=12, opacity=1.0, y=0.0),
                           Keyframe(frame=n - 7, opacity=1.0, scale=1.0, easing=Easing.CUBIC_IN),
                           Keyframe(frame=n - 1, opacity=0.0, scale=0.96)])  # fmt: skip
                last = at + n
        if pv in (Purpose.PROMO, Purpose.EVENT) and plan.title:  # H5: 最後の 3 秒に締めのカード
            n = round(3 * fps)
            at = total - n
            if at > 0:
                t_ = await self.titles.create(
                    TitleCardRequest(project_id=project_id, text=plan.title[:100],
                                     duration_sec=n / fps, animation="slam", fade_out_sec=0.0,
                                     text_color=ink, font=None)
                )  # fmt: skip
                clip(lane(CLOSE_TRACK), t_, at, n, ClipTransform())
            notes.append(
                "締めのカード（H5）のボタンと URL は、設定の項目がまだ無いため出していません"
            )
        if pv == Purpose.CORPORATE and plan.title:  # T41: 最後の 3 秒に一本の線で題名を描く
            from video_automation.services.rendering import line_title as lt

            n = round(3 * fps)
            at = total - n
            if at > 0:
                ov = await self._overlay(
                    project_id,
                    lt.line_title_frames(plan.title[:30], n, w, h, font, prof.palette.main, ink),
                    "一本の線で描く題名（T41）",
                    fps,
                )
                clip(lane(CLOSE_TRACK), ov, at, n, ClipTransform())
        if pv == Purpose.NEWS:  # H3: 下の見出しの帯（章で替える）とティッカー
            heads = chapters or [(0, plan.title or "ニュース")]
            if heads[0][0] > 0:
                heads.insert(0, (0, plan.title or heads[0][1]))
            band_t = lane(NEWS_TRACK)
            for i, (at, text) in enumerate(heads):
                end = heads[i + 1][0] if i + 1 < len(heads) else total
                if end <= at:
                    continue
                img = await asyncio.to_thread(
                    hc.news_band, w, h, text[:40], font, prof.palette.main
                )
                a = await self._png(project_id, img, "ニュースの帯（H3）")
                tf = native_transform(w, img.size[1], w, h, cx=0.5, cy=0.835, max_w=1.0)
                cid = clip(band_t, a, at, end - at, tf)
                if i:  # 帯の中で上へ送って替える（8f）
                    anim(cid, [Keyframe(frame=0, y=0.03, opacity=0.0, easing=Easing.CUBIC_OUT),
                               Keyframe(frame=8, y=0.0, opacity=1.0)])  # fmt: skip
            items = [t for _a, t in heads][:8]
            strip, period = await asyncio.to_thread(hc.ticker_strip, w, h, items, font)
            if period <= 1.4 * w:
                a = await self._png(project_id, strip, "ティッカー（H3）")
                pw = period / w
                tf = native_transform(strip.size[0], strip.size[1], w, h, cx=pw, cy=0.905,
                                      max_w=2 * pw + 0.01)  # fmt: skip
                loop = max(1, round(pw / hc.TICKER_SPEED * fps))
                tick_t = lane(TICKER_TRACK)
                pos = 0
                while pos < total:
                    n = min(loop, total - pos)
                    cid = clip(tick_t, a, pos, n, tf)
                    if n > 1:  # 最後のフレームの位置（次のクリップは 0 から流し直す）
                        x1 = round(-pw * (n - 1) / loop, 4)
                        anim(cid, [Keyframe(frame=0, x=0.0, easing=Easing.LINEAR),
                                   Keyframe(frame=n - 1, x=x1)])  # fmt: skip
                    pos += n
            notes.append(
                "ニュースの帯（H3）の上に字幕を移す処理はまだ無く、字幕と帯が重なることがあります"
            )
        return notes

    async def _roll_card(
        self, project_id: str, kreq: CardRequest, still: AssetRead, n: int, fps: float
    ) -> AssetRead:
        """統合版 T35: 強調テロップのカードを、数字が回ってから止まる画の列（n フレーム）にする。"""
        from PIL import Image

        text = kreq.heading
        k = numbers.roll_frames_count(text)
        work = (
            self.settings.data_dir / "projects" / project_id / "assets" / f"t35_{uuid4().hex[:8]}"
        )
        work.mkdir(parents=True, exist_ok=True)
        imgs: list[Any] = []
        try:
            shown: dict[str, Any] = {}
            for f in range(min(k, n)):
                t = numbers.roll_text(text, f)
                if t not in shown:
                    out = work / f"{len(shown):03d}.png"
                    await self.titles.card_png(kreq.model_copy(update={"heading": t}), out)
                    shown[t] = await asyncio.to_thread(
                        lambda p_=out: Image.open(p_).convert("RGBA")
                    )
                imgs.append(shown[t])
        except Exception:  # noqa: BLE001 - 回せないときは止まった数字のまま
            await asyncio.to_thread(shutil.rmtree, work, True)
            return still
        final = await asyncio.to_thread(
            lambda: Image.open(self.settings.data_dir / still.path).convert("RGBA")
        )
        size = final.size

        def gen() -> Any:
            for im in imgs:
                yield im if im.size == size else im.resize(size)
            for _ in range(n - len(imgs)):
                yield final

        a = await self._overlay(project_id, gen(), f"数字が回る（T35）「{text[:12]}」", fps)
        await asyncio.to_thread(shutil.rmtree, work, True)
        return a

    async def _morph_card(
        self,
        project_id: str,
        kreq: CardRequest,
        still: AssetRead,
        icon: str,
        n: int,
        fps: float,
        w: int,
        h: int,
        main: str,
    ) -> AssetRead:
        """統合版 T28: 強調テロップのカードの文字を、入りの後 12f 静止してから図に変える画の列。"""
        from PIL import Image

        layout = card_layout(kreq, w, h)
        if not layout.lines:
            return still
        x0 = min(ln.x for ln in layout.lines)
        y0 = min(ln.y for ln in layout.lines)
        x1 = max(ln.x + text_width(ln.text, ln.size) for ln in layout.lines)
        y1 = max(ln.y + round(ln.size * 1.15) for ln in layout.lines)
        size = max(ln.size for ln in layout.lines)
        card = await asyncio.to_thread(
            lambda: Image.open(self.settings.data_dir / still.path).convert("RGBA")
        )
        box = (round(x0), round(y0), min(card.width - 1, round(x1)), min(card.height - 1, y1))
        gen = word_icon.card_morph_frames(
            card, box, kreq.heading, icon, n, T28_ENTER, size,
            self.titles.font_path(kreq.font), main,
        )  # fmt: skip
        return await self._overlay(
            project_id, gen, f"語が図に変わる（T28）「{kreq.heading[:12]}」", fps
        )

    async def _f3_cards(
        self, project_id: str, cmds: list[Any], roles: dict[str, str], fps: float, h: int
    ) -> None:
        """統合版 F3（子ども向け）: カード・強調テロップの静止画を、3 コマごとに ±1.5px（1080p）の
        乱数でずらす画の列に替える（線の点ごとではなく、画の全体をずらして近似する）。"""
        from PIL import Image

        targets = [
            (i, c)
            for i, c in enumerate(cmds)
            if isinstance(c, AddClip) and roles.get(c.clip_id or "") in F3_ROLES
            and (c.duration_frames or 0) > 0
        ]  # fmt: skip
        if not targets:
            return
        ids = {c.asset_id for _i, c in targets}
        async with self.db.session_factory() as s:
            rows = (await s.execute(select(Asset).where(Asset.id.in_(ids)))).scalars().all()
            paths = {r.id: r.path for r in rows if r.kind == AssetKind.IMAGE.value}
        px = F3_PX * h / 1080
        made: dict[tuple[str, int], str] = {}
        for i, c in targets[:F3_MAX]:
            rel = paths.get(c.asset_id)
            if rel is None:
                continue
            n = c.duration_frames or 0
            key = (c.asset_id, n)
            if key not in made:
                im = await asyncio.to_thread(
                    lambda r_=rel: Image.open(self.settings.data_dir / r_).convert("RGBA")
                )
                pad = max(1, math.ceil(px))
                seed = sum(map(ord, c.asset_id))

                def gen(im_: Any = im, n_: int = n, pad_: int = pad, seed_: int = seed) -> Any:
                    from video_automation.services.rendering.n_marks import JITTER_STEP

                    cache: dict[int, Any] = {}
                    for f in range(n_):
                        k = f // JITTER_STEP
                        if k not in cache:
                            rnd = random.Random(seed_ * 7919 + k)
                            out = Image.new(
                                "RGBA", (im_.width + 2 * pad_, im_.height + 2 * pad_), (0, 0, 0, 0)
                            )
                            out.alpha_composite(
                                im_,
                                (round(pad_ + rnd.uniform(-px, px)),
                                 round(pad_ + rnd.uniform(-px, px))),
                            )  # fmt: skip
                            cache = {k: out}
                        yield cache[k]

                a = await self._overlay(project_id, gen(), "線のふるえ（F3）", fps)
                made[key] = a.id
            cmds[i] = c.model_copy(update={"asset_id": made[key]})

    async def _not_telops(
        self,
        project_id: str,
        prof: DesignProfile,
        lines: list[Any],
        cmds: list[Any],
        lane: Any,
        total: int,
        fps: float,
        w: int,
        h: int,
        top: float,
        ts: float,
        font: str | None,
        ink: str,
        panel: str,
    ) -> None:
        """統合版 T36（取り消し線で消す）・T46（打ち直すタイプライター）。
        1 文に 1 回、画面の上の中央に出す。"""
        size = max(12, round(min(w, h) * 0.078 * 0.62 * ts))
        retype = prof.purpose == Purpose.TUTORIAL
        track = None
        for ln in lines:
            ab = tx.not_phrases(ln.text)
            n = min(total, ln.end) - ln.start
            if ab is None or n < round(fps):
                continue
            make = tx.retype_frames if retype else tx.strike_frames
            (cw, ch), gen = make(ab[0], ab[1], font, size, ink, panel, n)
            name = "打ち直すタイプライター（T46）" if retype else "取り消し線（T36）"
            a = await self._overlay(project_id, gen, name, fps)
            track = track or lane(STRIKE_TRACK)
            k = native_scale(cw, ch, w, h)
            tf = native_transform(cw, ch, w, h, cx=0.5, cy=top + ch * k / 2 / h)
            cmds.append(AddClip(track_id=track, asset_id=a.id, start_frame=ln.start,
                                duration_frames=n, clip_id=str(uuid4()), transform=tf))  # fmt: skip

    async def _text_scenes(
        self,
        project_id: str,
        prof: DesignProfile,
        lines: list[Any],
        windows: list[tuple[Any, int, int]],
        shown: list[tuple[int, int]],
        title_end: int,
        total: int,
        cmds: list[Any],
        lane: Any,
        fps: float,
        w: int,
        h: int,
        font: str | None,
    ) -> list[tuple[int, int]]:
        """統合版 T30〜T34。文字だけの場面の区間を返す（字幕・強調テロップを出さない）。"""
        from video_automation.services.build.bgm_prompts import BGM

        kind = tscn.KIND[prof.purpose.value]
        busy = [*shown, (0, title_end)]
        rows = [(x.start, x.end, x.text) for x in lines]
        chapters = [st for s_, st, _en in windows if s_.kind in (ShotKind.CARD, ShotKind.CHART)]
        secs = tscn.pick_sections(rows, busy, total, fps, prof.purpose.value, chapters)
        if not secs:
            return []
        spec = BGM.get(prof.purpose)
        bpm = float(spec[1]) if spec else 120.0
        script = "".join(x.text for x in lines)
        track = lane(TEXT_SCENE_TRACK)
        out: list[tuple[int, int]] = []
        for i, j in secs:
            # 次の発話の頭まで（字幕は声より少し長く出るので、その分も覆う）
            nxt = lines[j].start if j < len(lines) else lines[j - 1].end
            start, end = lines[i].start, min(total, max(lines[j - 1].end, nxt))
            ph = [p_ for x in lines[i:j] for p_ in tscn.phrases(x.text, x.start, x.end)]
            if not ph or end - start < round(fps):
                continue
            gen = tscn.frames(kind, ph, start, end - start, w, h, font, prof.palette, fps, bpm,
                              script)  # fmt: skip
            a = await self._overlay(project_id, gen, f"文字だけの場面（{kind}）", fps)
            cmds.append(AddClip(track_id=track, asset_id=a.id, start_frame=start,
                                duration_frames=end - start, clip_id=str(uuid4()),
                                transform=ClipTransform(fit=FitMode.FIT)))  # fmt: skip
            out.append((start, end))
        return out

    async def _flower(
        self,
        project_id: str,
        text: str,
        emotion: str,
        color: str,
        start: int,
        end: int,
        cmds: list[Any],
        txt_t: str,
        w: int,
        h: int,
        fps: float,
        side: float,
        strip_top: float,
        ts: float,
        add: Any,
    ) -> None:
        """統合版 T42: リアクションの強調テロップを花字にする（強調テロップと同じ右上に置く）。
        色は話者の色から 3 節の作り方（OKLCH）で作る。大きさは強調テロップの文字の 1.4 倍。"""
        from video_automation.services.design.oklch import FRAME, OUTLINE, TEXT, derive

        col = fl.FlowerColors(
            edge=derive(color, OUTLINE),
            frame=derive(color, FRAME),
            light=derive(color, (0.88, 0.06)),
            text=derive(color, TEXT),
        )
        size = max(12, round(min(w, h) * 0.078 * 0.62 * ts * 1.4))
        font = self.titles.font_path("M PLUS Rounded 1c")
        (cw, ch), gen = fl.frames(text[:16], font, size, col, emotion, end - start, fps, h)
        a = await self._overlay(project_id, gen, f"花字（T42）「{text[:12]}」", fps)
        k = native_scale(cw, ch, w, h)
        # 余白を含む画なので、文字の部分が強調テロップと同じ右上に来るよう中心を置く
        kx = 1 - side - cw * k / 2 / w + size * 0.9 * k / w
        ky = strip_top + ch * k / 2 / h - size * 0.9 * k / h
        add(txt_t, a.id, start, end - start, role="keyword",
            transform=native_transform(cw, ch, w, h, cx=kx, cy=ky))  # fmt: skip

    async def _maps(
        self,
        project_id: str,
        prof: DesignProfile,
        lines: list[Any],
        cmds: list[Any],
        lane: Any,
        shown: list[tuple[int, int]],
        total: int,
        fps: float,
        w: int,
        h: int,
        box: float,
        cy: float,
    ) -> list[str]:
        """統合版 J1（国の塗り）・J2（地点へ寄る）・J3（経路の線）。地図は Natural Earth を初回に
        取得する（取れなければ出さない）。地名が地図に無ければ出さない。"""
        from video_automation.integrations.maps import natural_earth as ne
        from video_automation.services.rendering import maps as mp

        want = [x for x in lines if str(x.items.get("place") or "").strip()]
        if not want:
            return []
        world = await asyncio.to_thread(ne.ensure, self.settings.data_dir)
        if world is None:
            return ["地図のデータ（Natural Earth）を取得できなかったので、地図を出しませんでした"]
        pal = prof.palette
        mpal = mp.Palette(pal.base, pal.surface, pal.text_muted, pal.accent)
        fw, fh = max(2, round(w * box)) // 2 * 2, max(2, round(h * box)) // 2 * 2
        tf = ClipTransform(fit=FitMode.FIT, scale=box, y=cy - 0.5)
        used = list(shown)
        track = None
        notes: list[str] = []
        one = round(fps)
        for k_, ln in enumerate(want):
            place = str(ln.items["place"]).strip()
            nxt = want[k_ + 1].start if k_ + 1 < len(want) else total
            start, end = ln.start, min(total, ln.end + one, nxt)
            if end - start < one or any(a < end and start < b for a, b in used):
                continue
            got = await asyncio.to_thread(mp.frames_for, world, place, ln.text, fw, fh, mpal)
            if got is None:
                notes.append(f"地名「{place[:20]}」が地図に無いので、地図を出しませんでした")
                continue
            name, fr, still = got
            track = track or lane(MAP_TRACK)
            n_mov = min(len(fr), end - start)
            mov = await self._overlay(project_id, fr[:n_mov], f"{name}「{place[:12]}」", fps)
            c1 = str(uuid4())
            cmds.append(AddClip(track_id=track, asset_id=mov.id, start_frame=start,
                                duration_frames=n_mov, clip_id=c1, transform=tf))  # fmt: skip
            cmds.append(SetClipAnimation(clip_id=c1, animation=ClipAnimation(keyframes=[
                Keyframe(frame=0, opacity=0.0, easing=Easing.CUBIC_OUT),
                Keyframe(frame=min(6, n_mov - 1), opacity=1.0)])))  # fmt: skip
            if end - start > n_mov:
                b = await self._png(project_id, still.convert("RGBA"), f"{name}「{place[:12]}」")
                c2 = str(uuid4())
                m = end - start - n_mov
                cmds.append(AddClip(track_id=track, asset_id=b.id, start_frame=start + n_mov,
                                    duration_frames=m, clip_id=c2, transform=tf))  # fmt: skip
                if m > 8:
                    cmds.append(SetClipAnimation(clip_id=c2, animation=ClipAnimation(keyframes=[
                        Keyframe(frame=m - 7, opacity=1.0, easing=Easing.CUBIC_IN),
                        Keyframe(frame=m - 1, opacity=0.0)])))  # fmt: skip
            used.append((start, end))
        return notes

    async def _callouts(
        self,
        project_id: str,
        prof: DesignProfile,
        img: AssetRead,
        lines: list[Any],
        start: int,
        end: int,
        cmds: list[Any],
        lane: Any,
        w: int,
        h: int,
        box: float,
        cy: float,
        fps: float,
        font: str | None,
        ink: str,
    ) -> None:
        """統合版 P9: 商品の画像の区間で、LLM が発話に features（最大 3 つ）を付けたとき、
        画像の左上・右上・右下から外へ線を 8f で伸ばし、先に語を T10 で出す（話す間に 1 つずつ）。
        ケン・バーンズで少し寄るので、線は画像の置いた範囲の縁から始める（近似）。"""
        from video_automation.services.rendering import n_marks as nm

        ln = next((x for x in lines if start <= x.start < end and x.items.get("features")), None)
        if ln is None or not img.width or not img.height:
            return
        words = [str(x).strip()[:12] for x in ln.items["features"] if str(x).strip()][:3]
        if not words:
            return
        # 画像を置いた範囲（画面の box 倍の枠に収める。中心は (0.5, cy)）
        k = min(box * w / img.width, box * h / img.height)
        iw, ih = img.width * k, img.height * k
        rect = (w / 2 - iw / 2, cy * h - ih / 2, w / 2 + iw / 2, cy * h + ih / 2)
        span = max(1, ln.end - ln.start)
        times = [ln.start + span * i // len(words) for i in range(len(words))]
        events = [t for t in times if t < end]
        track = lane(CALLOUT_TRACK)
        n_anim = nm.P9_LINE + nm.P9_TEXT
        for i, at in enumerate(events):
            nxt = events[i + 1] if i + 1 < len(events) else end
            if nxt <= at:
                continue

            def layer(f: int, i: int = i, at: int = at) -> Any:
                shown: list[int | None] = [
                    (at + f - times[j]) if j <= i else None for j in range(len(words))
                ]
                return nm.callout_layer((w, h), rect, words, shown, prof.palette.main, ink, font)

            m = min(n_anim, nxt - at)
            mov = await self._overlay(project_id, (layer(f) for f in range(m)),
                                      "特長の引き出し線（P9）", fps)  # fmt: skip
            cmds.append(AddClip(track_id=track, asset_id=mov.id, start_frame=at,
                                duration_frames=m, clip_id=str(uuid4())))  # fmt: skip
            if nxt - at > m:
                still = await asyncio.to_thread(layer, 10_000)
                b = await self._png(project_id, still, "特長の引き出し線（P9）")
                cmds.append(
                    AddClip(track_id=track, asset_id=b.id, start_frame=at + m,
                            duration_frames=nxt - at - m, clip_id=str(uuid4()))
                )  # fmt: skip

    async def _before_after(
        self,
        project_id: str,
        a1: AssetRead,
        a2: AssetRead,
        start: int,
        mid: int,
        end: int,
        cmds: list[Any],
        fig_t: str,
        h: int,
        box: float,
        cy: float,
        fps: float,
        font: str | None,
        add: Any,
        animate: Any,
    ) -> None:
        """統合版 N5: 1 枚目を出し、2 枚目の区間の頭から境目を右端 → 左端へ動かして
        2 枚目を現す。"""
        from PIL import Image

        from video_automation.services.rendering import n_cards as nc

        def make() -> tuple[Any, list[Any], Any]:
            im1 = Image.open(self.settings.data_dir / a1.path).convert("RGBA")
            im2 = Image.open(self.settings.data_dir / a2.path).convert("RGBA")
            # 画の 1px = 画面の何 px か（box の高さに合わせて置くので）
            k = box * h / im1.size[1]
            frames_, still = nc.before_after_frames(im1, im2, h, font, 1 / k)
            return im1, frames_, still

        im1, frames_, still = await asyncio.to_thread(make)
        tf = ClipTransform(fit=FitMode.FIT, scale=box, y=cy - 0.5)
        first = mid - start
        if first > 0:
            cid = add(fig_t, a1.id, start, first, role="image", transform=tf)
            animate(cid, first, exit_=False)
        n_mov = min(len(frames_), end - mid)
        if n_mov > 0:
            mov = await self._overlay(project_id, frames_[:n_mov], "前後の比較（N5）", fps)
            add(fig_t, mov.id, mid, n_mov, role="image", transform=tf)
        if end - mid - n_mov > 0:
            b = await self._png(project_id, still, "前後の比較（N5）")
            cid = add(fig_t, b.id, mid + n_mov, end - mid - n_mov, role="image", transform=tf)
            animate(cid, end - mid - n_mov, None)
        del im1

    async def _document(
        self, project_id: str, img: AssetRead, said: str
    ) -> tuple[AssetRead, list[Keyframe]] | None:
        """統合版 N16: OCR の文字数が 120 字以上の画像（書類・記事）で、発話の語と一致する行が
        あれば、その行に T37 のマーカー（accent・不透明度 40%・文字の高さの 45%）、他の行を
        不透明度 55% にした画と、その行が中央に来るよう 1.00→1.25 に 24f で寄るカメラのキー。"""
        from PIL import Image, ImageDraw

        from video_automation.services.direction import slides as sl

        engine = self.ocr_source()
        if engine is None or not said.strip():
            return None
        path = self.settings.data_dir / img.path
        try:
            (res,) = await asyncio.to_thread(engine.read, [path])
        except Exception:  # noqa: BLE001 - 読めなければ普通の画像のまま
            return None
        if len(res.text.replace(" ", "")) < 120:
            return None
        j = sl.best_line(res, sl.tokens(said))
        if j is None:
            return None
        prof = await self.design.profile_for(project_id) if self.design else None
        accent = prof.palette.accent if prof else "#FFD966"

        def make() -> Any:
            im = Image.open(path).convert("RGBA")
            sx, sy = im.size[0] / max(1, res.width), im.size[1] / max(1, res.height)
            lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
            d = ImageDraw.Draw(lay)
            for k, ln in enumerate(res.lines):
                x, y, bw, bh = ln.box
                box = (x * sx, y * sy, (x + bw) * sx, (y + bh) * sy)
                if k == j:
                    top = box[3] - (box[3] - box[1]) * 0.45
                    r, g, b = _hex_rgb(accent)
                    d.rectangle((box[0], top, box[2], box[3]), fill=(r, g, b, round(255 * 0.4)))
                else:
                    bg = sl.background(im, (box[0], box[1], box[2] - box[0], box[3] - box[1]))
                    d.rectangle(box, fill=(*bg, round(255 * 0.45)))
            im.alpha_composite(lay)
            return im

        out = await asyncio.to_thread(make)
        a = await self._png(project_id, out, f"書類の強調（N16）{img.name[:20]}")
        x, y, bw, bh = res.lines[j].box
        px, py = sl.pan_for((x + bw / 2) / res.width, (y + bh / 2) / res.height)
        keys = [
            Keyframe(frame=0, zoom=1.0, pan_x=0.0, pan_y=0.0, easing=Easing.CUBIC_IN_OUT),
            Keyframe(frame=24, zoom=sl.ZOOM, pan_x=px, pan_y=py),
        ]
        return a, keys

    async def _photo_card(
        self, project_id: str, img: AssetRead, caption: str, h: int, box: float
    ) -> AssetRead:
        """統合版 N17: 写真に白い縁と一言を付けた画（_polaroid）の素材。"""
        from PIL import Image

        path = self.titles.font_path("M PLUS Rounded 1c Medium")

        def draw() -> Any:
            src = Image.open(self.settings.data_dir / img.path).convert("RGBA")
            return _polaroid(src, caption, h, box, path)

        out = await asyncio.to_thread(draw)
        return await self._png(project_id, out, f"写真と一言（N17）{img.name[:20]}")

    async def _sticker(self, project_id: str, img: AssetRead, h: int) -> AssetRead:
        """統合版 I3: 透過の画像の形に沿って白の縁（8px）と影を付けた画像。"""
        from PIL import Image, ImageFilter

        src = Path(self.settings.data_dir) / img.path
        im = await asyncio.to_thread(lambda: Image.open(src).convert("RGBA"))
        k = max(1.0, im.height / max(1, h))  # 画面に置いたときに縁が 8px になるよう、素材の画素で
        b = max(2, round(8 * k))
        pad = b * 3
        big = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
        big.alpha_composite(im, (pad, pad))
        a = big.getchannel("A").point(lambda v: 255 if v > 32 else 0)
        edge = a.filter(ImageFilter.MaxFilter(2 * b + 1))
        shadow = edge.filter(ImageFilter.GaussianBlur(b)).point(lambda v: round(v * 0.35))
        out = Image.new("RGBA", big.size, (0, 0, 0, 0))
        sh = Image.new("RGBA", big.size, (0, 0, 0, 255))
        sh.putalpha(shadow)
        out.alpha_composite(sh, (b // 2, b))
        white = Image.new("RGBA", big.size, (255, 255, 255, 255))
        white.putalpha(edge)
        out.alpha_composite(white)
        out.alpha_composite(big)
        dst = src.with_name(f"{src.stem}_sticker.png")
        await asyncio.to_thread(out.save, dst)
        row = await self.timeline.register_generated(
            project_id, dst, AssetKind.IMAGE, "pillow", f"{img.name}（ステッカー）"
        )
        return AssetRead.model_validate(row)

    async def _years(
        self,
        project_id: str,
        prof: DesignProfile,
        lines: list[Any],
        cmds: list[Any],
        lane: Any,
        total: int,
        fps: float,
        w: int,
        h: int,
        font: str | None,
        ink: str,
    ) -> None:
        """統合版 T43（ドキュメンタリー・解説・講義）。"""
        from video_automation.services.rendering import numbers as nb

        sa = safe_area_for(prof, w, h)
        first = True
        for ln in lines:
            year = nb.find_year(ln.text)
            n_all = min(ln.end, total) - ln.start
            if not year or n_all < 12:
                continue
            frames_ = await asyncio.to_thread(
                nb.year_frames, year, h, font, ink, prof.palette.base, not first
            )
            bw, bh = frames_[0].size
            tf = native_transform(bw, bh, w, h, cx=1 - sa.right - bw / w / 2,
                                  cy=sa.top + bh / h / 2, max_w=1.0)  # fmt: skip
            tid = lane(YEAR_TRACK)
            at = ln.start
            if len(frames_) > 1 and len(frames_) < n_all:  # 回る間（動画）→ 止まった後（静止画）
                a_ = await self._overlay(project_id, frames_, f"年号が回る（T43）{year}", fps)
                cmds.append(AddClip(track_id=tid, asset_id=a_.id, start_frame=at,
                                    duration_frames=len(frames_), clip_id=str(uuid4()),
                                    transform=tf))  # fmt: skip
                at += len(frames_)
            still = await self._png(project_id, frames_[-1], f"年号（T43）{year}")
            n = ln.start + n_all - at
            cid = str(uuid4())
            cmds.append(AddClip(track_id=tid, asset_id=still.id, start_frame=at,
                                duration_frames=n, clip_id=cid, transform=tf))  # fmt: skip
            keys = []
            if first:
                keys += [Keyframe(frame=0, opacity=0.0), Keyframe(frame=min(6, n - 1), opacity=1.0)]
            if n > 14:
                keys += [Keyframe(frame=n - 7, opacity=1.0), Keyframe(frame=n - 1, opacity=0.0)]
            if len(keys) >= 2 and len({k.frame for k in keys}) == len(keys):
                cmds.append(SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=keys)))
            first = False

    async def _n_cards(
        self,
        project_id: str,
        prof: DesignProfile,
        lines: list[Any],
        cmds: list[Any],
        lane: Any,
        total: int,
        fps: float,
        w: int,
        h: int,
        font: str | None,
        ink: str,
    ) -> list[str]:
        """統合版 N1（手順）・N7（定義）・N8（正誤）・N11（引用）。1 本のトラックに重ねずに置く。"""
        from video_automation.services.rendering import n_cards as nc

        pv = prof.purpose
        pal = prof.palette
        used: list[tuple[int, int]] = []
        notes: list[str] = []
        one_sec = round(fps)

        def free(a: int, b: int) -> bool:
            return 0 <= a < b <= total and not any(x < b and a < y for x, y in used)

        def put(a_: AssetRead, at: int, n: int, keys: list[Keyframe] | None) -> None:
            cid = str(uuid4())
            cmds.append(
                AddClip(track_id=lane(DIAGRAM_TRACK), asset_id=a_.id, start_frame=at,
                        duration_frames=n, clip_id=cid, transform=ClipTransform(fit=FitMode.FIT))
            )  # fmt: skip
            if keys:
                cmds.append(SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=keys)))
            used.append((at, at + n))

        def fade(n: int, slide: bool = False) -> list[Keyframe]:
            """入り（B1 のスライド・16f）と出（6f）。"""
            k0 = Keyframe(frame=0, opacity=0.0, x=-0.017 if slide else 0.0, easing=Easing.CUBIC_OUT)
            f1 = min(16, n // 3)
            return [
                k0,
                Keyframe(frame=f1, opacity=1.0, x=0.0),
                Keyframe(frame=max(f1 + 1, n - 7), opacity=1.0, x=0.0, easing=Easing.CUBIC_IN),
                Keyframe(frame=max(f1 + 2, n - 1), opacity=0.0, x=0.0),
            ]

        runs = (
            nc.step_runs([x.text for x in lines])
            if pv in (Purpose.EXPLAINER, Purpose.LECTURE)
            else []
        )
        run_starts = sorted(lines[r[0]].start for r in runs)
        for i, ln in enumerate(lines):
            # 後に手順の流れ（N1）があるときは、その始まりまでで消す
            nxt = next((x for x in run_starts if x > ln.start), total)
            end = min(total, ln.end + one_sec, nxt)
            if pv in (Purpose.EXPLAINER, Purpose.LECTURE, Purpose.KIDS) and (
                d_ := nc.definition(ln.text)
            ):  # N7
                if free(ln.start, end):
                    img = await asyncio.to_thread(nc.definition_card, w, h, d_[0], d_[1], font,
                                                  pal.accent, ink, pal.surface)  # fmt: skip
                    a_ = await self._png(project_id, img, f"用語の定義（N7）「{d_[0]}」")
                    put(a_, ln.start, end - ln.start, fade(end - ln.start, slide=True))
            elif pv == Purpose.EXPLAINER and i > 0 and nc.is_myth(ln.text):  # N8
                st = lines[i - 1].start
                if free(st, end) and ln.start - st >= 12:
                    for show, a0, b0 in ((False, st, ln.start + 12), (True, ln.start + 12, end)):
                        if b0 <= a0:
                            continue
                        img = await asyncio.to_thread(nc.myth_card, w, h, lines[i - 1].text,
                                                      ln.text, font, pal.accent, show)  # fmt: skip
                        a_ = await self._png(project_id, img, "正誤カード（N8）")
                        put(a_, a0, b0 - a0, None)
            elif pv in (Purpose.INTERVIEW, Purpose.DOCUMENTARY, Purpose.STORY) and (
                q := nc.quote(ln.text)
            ):  # N11
                if free(ln.start, end):
                    img = await asyncio.to_thread(nc.quote_card, w, h, q, ln.speaker, font,
                                                  pal.accent, ink)  # fmt: skip
                    a_ = await self._png(project_id, img, "引用カード（N11）")
                    put(a_, ln.start, end - ln.start, fade(end - ln.start))
        if runs:  # N1（後の 3 文以内に「繰り返」「循環」「サイクル」があれば N2 の円周）
            for run in runs:
                labels = [nc.first_phrase(lines[i].text) for i in run]
                near = range(run[-1] + 1, min(len(lines), run[-1] + 4))
                cyc = next(
                    (i for i in near if any(k_ in lines[i].text for k_ in nc.CYCLE_WORDS)), None
                )
                starts = [lines[i].start for i in run]
                end = min(total, lines[run[-1]].end + one_sec)
                if cyc is not None:  # N2: 「繰り返」の文の頭で、手順の箱を円周に並べ直す
                    c_st, c_end = lines[cyc].start, min(total, lines[cyc].end + one_sec)
                    end = min(end, c_st)
                    if free(c_st, c_end):
                        img = await asyncio.to_thread(nc.cycle, w, h, labels, font, pal.main,
                                                      pal.accent, ink, pal.surface)  # fmt: skip
                        put(await self._png(project_id, img, "循環の図（N2）"), c_st,
                            c_end - c_st, fade(c_end - c_st))  # fmt: skip
                if end <= starts[0] or not free(starts[0], end):
                    continue
                for k, st in enumerate(starts):
                    nx = starts[k + 1] if k + 1 < len(starts) else end
                    if nx <= st:
                        continue
                    img = await asyncio.to_thread(nc.steps, w, h, labels, k + 1, font, pal.main,
                                                  pal.accent, ink, pal.surface)  # fmt: skip
                    a_ = await self._png(project_id, img, "手順の流れ（N1）")
                    keys = None
                    if k == len(starts) - 1 and nx - st > 10:
                        keys = [Keyframe(frame=0, opacity=1.0),
                                Keyframe(frame=nx - st - 7, opacity=1.0, easing=Easing.CUBIC_IN),
                                Keyframe(frame=nx - st - 1, opacity=0.0)]  # fmt: skip
                    elif k == 0:
                        keys = [Keyframe(frame=0, opacity=0.0, easing=Easing.CUBIC_OUT),
                                Keyframe(frame=min(10, nx - st - 1), opacity=1.0)]  # fmt: skip
                    put(a_, st, nx - st, keys)
        # 統合版 段階5d: LLM が発話に付けた項目から出す部品（H2・N3・N10・P11）
        sa = safe_area_for(prof, w, h)
        placed_place: set[str] = set()
        last_tag: AddClip | None = None
        for i, ln in enumerate(lines):
            it = ln.items
            end = min(total, ln.end + one_sec)
            pl = str(it.get("place") or "").strip()
            if (
                pl
                and pv in (Purpose.DOCUMENTARY, Purpose.NEWS, Purpose.VLOG)
                and pl not in placed_place
            ):
                # H2: 左上に場所のタグ（4 秒。ピンが落ちる 8f）。前のタグがまだ出ていれば、
                # 次のタグの頭で前のタグを終える（同じトラックで重ねない）
                if (
                    last_tag is not None
                    and last_tag.start_frame + (last_tag.duration_frames or 0) > ln.start
                ):
                    old_n = last_tag.duration_frames or 0
                    # 同じ時刻に始まることは無い（発話の頭は 1 フレーム以上ずれる）
                    keep = max(1, ln.start - last_tag.start_frame)
                    cmds[cmds.index(last_tag)] = last_tag.model_copy(
                        update={"duration_frames": keep}
                    )
                    shorten_animation(cmds, last_tag.clip_id, old_n, old_n - keep)
                n = min(round(4 * fps), total - ln.start)
                img = await asyncio.to_thread(nc.place_tag, w, h, pl[:20], font, pal.accent, ink)
                a_ = await self._png(project_id, img, f"場所のタグ（H2）「{pl[:12]}」")
                bw_, bh_ = img.size
                tf = native_transform(bw_, bh_, w, h, cx=sa.left + bw_ / w / 2,
                                      cy=sa.top + bh_ / h / 2, max_w=1.0)  # fmt: skip
                cid = str(uuid4())
                last_tag = AddClip(track_id=lane(PLACE_TRACK), asset_id=a_.id,
                                   start_frame=ln.start, duration_frames=n, clip_id=cid,
                                   transform=tf)  # fmt: skip
                cmds.append(last_tag)
                if n > 14:
                    cmds.append(SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=[
                        Keyframe(frame=0, y=-20 / h, opacity=0.0, easing=Easing.BACK_OUT),
                        Keyframe(frame=8, y=0.0, opacity=1.0),
                        Keyframe(frame=n - 7, y=0.0, opacity=1.0),
                        Keyframe(frame=n - 1, y=0.0, opacity=0.0),
                    ])))  # fmt: skip
                placed_place.add(pl)
            vn = it.get("venn")
            if (pv in (Purpose.EXPLAINER, Purpose.LECTURE) and isinstance(vn, list) and len(vn) == 2
                    and any(k_ in ln.text for k_ in ("両方", "共通", "重なる"))
                    and free(ln.start, end)):  # fmt: skip
                img = await asyncio.to_thread(nc.venn, w, h, str(vn[0])[:8], str(vn[1])[:8], "",
                                              font, pal.chart[0], pal.chart[1 % len(pal.chart)],
                                              pal.accent, ink)  # fmt: skip
                a_ = await self._png(project_id, img, "ベン図（N3）")
                put(a_, ln.start, end - ln.start, fade(end - ln.start))
            if (
                pv in (Purpose.LECTURE, Purpose.SHORT, Purpose.KIDS)
                and i + 1 < len(lines)
                and ln.text.rstrip().endswith(("？", "?"))
                and lines[i + 1].items.get("answer")
            ):
                # N10: 問いのカード、右上に 3→2→1（1 つ 20f）、その後に答えの最初の文節
                ans = lines[i + 1]
                st, cd = ln.start, 20
                q_end = min(total, ans.end + one_sec)
                if free(st, q_end) and ans.start - st >= 3 * cd:
                    c0 = ans.start - 3 * cd
                    if c0 > st:
                        img = await asyncio.to_thread(nc.quiz, w, h, ln.text, None, "", font,
                                                      pal.accent, ink, pal.surface)  # fmt: skip
                        put(await self._png(project_id, img, "クイズ（N10）"), st, c0 - st, None)
                    for k_ in range(3):
                        img = await asyncio.to_thread(nc.quiz, w, h, ln.text, 3 - k_, "", font,
                                                      pal.accent, ink, pal.surface)  # fmt: skip
                        put(await self._png(project_id, img, "クイズ（N10）"), c0 + cd * k_, cd,
                            None)  # fmt: skip
                    img = await asyncio.to_thread(nc.quiz, w, h, ln.text, None,
                                                  nc.first_phrase(ans.text), font, pal.accent,
                                                  ink, pal.surface)  # fmt: skip
                    put(await self._png(project_id, img, "クイズ（N10）"), ans.start,
                        q_end - ans.start, None)  # fmt: skip
            ch_ = it.get("chart")
            if isinstance(ch_, dict) and free(ln.start, end):
                from video_automation.services.rendering import d_charts as dc

                did = D_KIND.get(str(ch_.get("kind")))
                if did and did in D_PURPOSES.get(pv, set()) and dc.valid(ch_):
                    pal_d = pal.model_dump()
                    if did == "D6":  # 大きな数字を回す（動画）→ 止まった後（静止画）
                        cap = nc.first_phrase(ln.text)
                        fr = await asyncio.to_thread(
                            dc.number_frames, ch_, w, h, font, pal.accent, ink, cap
                        )
                        n_mov = min(len(fr), end - ln.start)
                        put(await self._overlay(project_id, fr[:n_mov], "大きな数字（D6）", fps),
                            ln.start, n_mov, None)  # fmt: skip
                        if end - ln.start > n_mov:
                            put(await self._png(project_id, fr[-1], "大きな数字（D6）"),
                                ln.start + n_mov, end - ln.start - n_mov, None)  # fmt: skip
                    else:
                        img = await asyncio.to_thread(dc.draw, ch_, w, h, font, pal_d, ink)
                        a_ = await self._png(project_id, img, f"図表（{did}）")
                        put(a_, ln.start, end - ln.start, fade(end - ln.start))
            cp = it.get("compare")
            if pv == Purpose.PRODUCT and isinstance(cp, dict) and free(ln.start, end):
                names_ = [str(x) for x in (cp.get("names") or [])][:2]
                rows_ = [r for r in (cp.get("rows") or []) if isinstance(r, dict)][:5]
                if len(names_) == 2 and len(rows_) >= 3:  # P11 仕様の比較表
                    img = await asyncio.to_thread(nc.compare_table, w, h, names_, rows_, font,
                                                  pal.accent, ink, pal.surface)  # fmt: skip
                    a_ = await self._png(project_id, img, "仕様の比較表（P11）")
                    put(a_, ln.start, end - ln.start, fade(end - ln.start))
        pc = (
            nc.pros_cons_items([x.text for x in lines])
            if pv in (Purpose.EXPLAINER, Purpose.PRODUCT)
            else None
        )
        if pc:  # N9 長所と短所: 話す時刻に各列へ 1 項目ずつ足す
            pros_i, cons_i = pc
            order = sorted([(i, "p") for i in pros_i] + [(i, "c") for i in cons_i])
            st0, end0 = lines[order[0][0]].start, min(total, lines[order[-1][0]].end + one_sec)
            if free(st0, end0):
                for k_, (i, _side) in enumerate(order):
                    shown = [x for x, _ in order[: k_ + 1]]
                    p_ = [nc.first_phrase(lines[x].text) for x in pros_i if x in shown]
                    c_ = [nc.first_phrase(lines[x].text) for x in cons_i if x in shown]
                    a0 = lines[i].start
                    b0 = lines[order[k_ + 1][0]].start if k_ + 1 < len(order) else end0
                    if b0 <= a0:
                        continue
                    img = await asyncio.to_thread(nc.pros_cons, w, h, p_, c_, font, pal.main,
                                                  ink, pal.surface)  # fmt: skip
                    put(await self._png(project_id, img, "長所と短所（N9）"), a0, b0 - a0, None)
        if pv == Purpose.PRODUCT:  # P10 値下げ: 前の価格に線、後の価格を回し、止まったら X2
            from video_automation.services.direction import effects as fx
            from video_automation.services.rendering import numbers as nb

            for ln in lines:
                pr = nb.price_drop(ln.text)
                end = min(total, ln.end + one_sec)
                if not pr or not free(ln.start, end):
                    continue
                fr = await asyncio.to_thread(nb.price_frames, pr[0], pr[1], w, h, font, ink,
                                             pal.accent)  # fmt: skip
                star = await asyncio.to_thread(fx.sparkle, round(h * 0.16 * 0.6))
                spark = []  # 止まった時にきらめき（価格の右上）
                for s_ in star:
                    base_ = fr[-1].copy()
                    base_.alpha_composite(s_, (round(w * 0.62), round(h * 0.30)))
                    spark.append(base_)
                seq = fr + spark
                n_mov = min(len(seq), end - ln.start)
                a_ = await self._overlay(project_id, seq[:n_mov], "値下げ（P10）", fps)
                put(a_, ln.start, n_mov, None)
                if end - ln.start > n_mov:
                    still = await self._png(project_id, fr[-1], "値下げ（P10）")
                    put(still, ln.start + n_mov, end - ln.start - n_mov, None)
        if pv in (Purpose.DOCUMENTARY, Purpose.EXPLAINER, Purpose.LECTURE, Purpose.CORPORATE):
            # N4 年表: 年が 3 つ以上出たら、最後の文で並べる（章の区切りが無いので動画全体で 1 つ）
            from video_automation.services.rendering import numbers as nb

            ys0 = [(nb.find_year(x.text), x) for x in lines]
            ys = [(y_, x) for y_, x in ys0 if y_]
            if len({y_ for y_, _x in ys}) >= 3:
                items = [(y_, nc.first_phrase(x.text.split("年", 1)[-1] or x.text, 6))
                         for y_, x in ys][:8]  # fmt: skip
                last = lines[-1]
                end = min(total, last.end + one_sec)
                if free(last.start, end):
                    img = await asyncio.to_thread(nc.timeline, w, h, items, font, pal.main,
                                                  pal.accent, ink)  # fmt: skip
                    a_ = await self._png(project_id, img, "年表（N4）")
                    put(a_, last.start, end - last.start, fade(end - last.start))
        if used:
            notes.append("図解（N1・N7・N8・N11）は、出ている間も字幕を出しています")
        return notes

    async def _overlay(
        self, project_id: str, frames_: Iterable[Any], name: str, fps: float
    ) -> AssetRead:
        """透過の画の列を 1 本の動画（PNG の mov）にして素材にする（最後に透明の 1 枚を足す）。
        画の列は生成器でもよい（1 枚ずつ描いて書き出すので、長い列でもメモリに溜めない）。"""
        from PIL import Image

        out_dir = self.settings.data_dir / "projects" / project_id / "assets"
        work = out_dir / f"ov_{uuid4().hex[:8]}"
        work.mkdir(parents=True, exist_ok=True)

        def save_all() -> None:
            size = None
            i = 0
            for i, fr in enumerate(frames_):  # noqa: B007
                size = fr.size
                fr.save(work / f"{i:04d}.png")
            if size is None:
                shutil.rmtree(work, True)
                raise CommandError("no overlay frames")
            Image.new("RGBA", size, (0, 0, 0, 0)).save(work / f"{i + 1:04d}.png")

        await asyncio.to_thread(save_all)
        mov = work.with_suffix(".mov")
        await self.ffmpeg.run(
            ["-y", "-framerate", f"{fps:.6f}", "-i", str(work / "%04d.png"),
             "-c:v", "png", "-pix_fmt", "rgba", str(mov)]
        )  # fmt: skip
        await asyncio.to_thread(shutil.rmtree, work, True)
        row = await self.timeline.register_generated(
            project_id, mov, AssetKind.VIDEO, "pillow", name
        )
        return AssetRead.model_validate(row)

    async def _card_marks(
        self,
        project_id: str,
        prof: DesignProfile,
        body: list[str],
        lines: list[Any],
        steps: list[tuple[int, int, int | None]],
        creq: CardRequest,
        card: AssetRead,
        cmds: list[Any],
        lane: Any,
        w: int,
        h: int,
        cy: float,
        max_h: float,
        max_w: float,
        fps: float,
    ) -> None:
        """統合版 I1（項目の最初の文節が絵記号の語なら、頭に線の絵を 12f で描く）と
        N15（後の文が「できました」「完了」「終わり」で終わったら、その時に出ている最後の項目に
        チェックを 8f で描き、項目の文字を不透明度 55% に見せる）。

        印はカードと同じ大きさの透過の画に描き、カードと同じ置き方で 1 本のトラックに重ねる
        （印が増えるたびに、描く動き → 止め絵 の順に置く）。"""
        from PIL import Image, ImageDraw

        from video_automation.services.rendering import n_cards as nc
        from video_automation.services.rendering import n_marks as nm

        def _on(base: Any, pic: Any, pos: tuple[int, int]) -> Any:
            im = base.copy()
            im.alpha_composite(pic, pos)
            return im

        pv = prof.purpose
        i1 = pv in (Purpose.EXPLAINER, Purpose.LECTURE, Purpose.KIDS)
        n15 = pv in (Purpose.TUTORIAL, Purpose.RECIPE)
        cw, ch = card.width or 0, card.height or 0
        if not (i1 or n15) or not cw or not ch:
            return
        layout = card_layout(creq, w, h)
        end = steps[-1][1]
        if steps[0][2] is None:
            shown_at = [steps[0][0]] * len(body)
        else:
            shown_at = [steps[min(j, len(steps) - 1)][0] for j in range(len(body))]
        pal = prof.palette
        # (時刻, 描く画の列, 描いた後の画, 置く左上) の印の一覧
        marks: list[tuple[int, list[Any], tuple[int, int]]] = []
        dims: list[tuple[int, tuple[int, int, int, int]]] = []
        if i1:
            for j, item in enumerate(body):
                name = pick_icon(nc.first_phrase(item), [])
                head = nm.item_head(layout, j)
                if name is None or head is None:
                    continue
                hx, hy, side = head
                ln = next((x for x in layout.lines if x.item == j), None)
                lh = ln.size if ln else side
                right = (ln.x if ln else hx + side) - max(2, round(lh * 0.08))
                # 文字の 1.2 倍（カードからはみ出さない）
                size = max(8, min(round(lh * 1.2), right))
                fr = await asyncio.to_thread(nm.icon_draw_frames, name, size, pal.main)
                # 行頭記号を面の色で隠して、その場所に絵を描く（文字の左に、行の中央の高さで）
                cover = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                panel = (*_hex_rgb(creq.panel_color), 255)
                ImageDraw.Draw(cover).rectangle(
                    (hx - 1, hy - 1, hx + side + 1, hy + side + 1), fill=panel
                )
                pos = (right - size, round((ln.y if ln else hy) + lh / 2 - size / 2))
                fr = [_on(cover, f_, pos) for f_ in fr]
                marks.append((shown_at[j], fr, (0, 0)))
        if n15:
            done: set[int] = set()
            for ln_ in lines:
                if not (steps[0][0] <= ln_.start < end) or not nm.is_done(ln_.text):
                    continue
                jj = max((x for x in range(len(body)) if shown_at[x] <= ln_.start), default=None)
                head = nm.item_head(layout, jj) if jj is not None else None
                box = nm.item_box(layout, jj) if jj is not None else None
                if jj is None or jj in done or head is None or box is None:
                    continue
                done.add(jj)
                hx, hy, side = head
                size = max(8, round(side * 1.3))
                fr = await asyncio.to_thread(nm.check_frames, size, pal.accent)
                at = max(ln_.start, ln_.end - 1)
                marks.append(
                    (at, fr, (round(hx + side / 2 - size / 2), round(hy + side / 2 - size / 2)))
                )
                dims.append((at, box))
        marks = sorted((m for m in marks if m[0] < end), key=lambda m: m[0])
        if not marks:
            return
        dim_col = (*_hex_rgb(creq.panel_color), round(255 * nm.N15_DIM))

        def canvas(t: int, upto: int, anim_k: int | None) -> Any:
            """t の時点の印の画。upto 番目までは描き終えた画、upto 番目は anim_k 枚目。"""
            im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            for at, (bx, by, bw, bh) in dims:
                if at <= t:
                    d.rectangle((bx, by, bx + bw, by + bh), fill=dim_col)
            for k_, (_at, fr, pos) in enumerate(marks[: upto + 1]):
                pic = fr[min(anim_k, len(fr) - 1)] if k_ == upto and anim_k is not None else fr[-1]
                im.alpha_composite(pic, pos)
            return im

        tf = native_transform(cw, ch, w, h, cx=0.5, cy=cy, max_h=max_h, max_w=max_w)
        track = lane(CARD_MARK_TRACK)
        for k_, (at, fr, _pos) in enumerate(marks):
            nxt = marks[k_ + 1][0] if k_ + 1 < len(marks) else end
            if nxt <= at:
                continue
            n_anim = min(len(fr), nxt - at)
            seq = [canvas(at, k_, i) for i in range(n_anim)]
            a = await self._overlay(project_id, seq, "項目の印（I1・N15）", fps)
            cmds.append(AddClip(track_id=track, asset_id=a.id, start_frame=at,
                                duration_frames=n_anim, clip_id=str(uuid4()),
                                transform=tf))  # fmt: skip
            if nxt - at > n_anim:
                still = await self._png(project_id, canvas(at, k_, None), "項目の印（I1・N15）")
                cmds.append(AddClip(track_id=track, asset_id=still.id, start_frame=at + n_anim,
                                    duration_frames=nxt - at - n_anim, clip_id=str(uuid4()),
                                    transform=tf))  # fmt: skip

    async def _title_extras(
        self,
        project_id: str,
        prof: DesignProfile,
        title: str,
        cmds: list[Any],
        lane: Any,
        at: int,
        n: int,
        fps: float,
        w: int,
        h: int,
        font: str | None,
        ink: str,
        size: float,
        dy: float,
    ) -> None:
        """統合版 T47（ランキング・イベントの帯）・T49（縦型ショートの残響）・
        T50（イベントの円周）。
        題名を出している間（at から n フレーム）だけ置く。"""
        pv = prof.purpose
        pal = prof.palette
        if n <= 0 or not title.strip():
            return

        def add(track: str, a: AssetRead, start: int, length: int, tf: ClipTransform,
                keys: list[Keyframe]) -> None:  # fmt: skip
            cid = str(uuid4())
            cmds.append(AddClip(track_id=lane(track), asset_id=a.id, start_frame=start,
                                duration_frames=length, clip_id=cid, transform=tf))  # fmt: skip
            cmds.append(SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=keys)))

        sec = n / fps
        if pv in (Purpose.RANKING, Purpose.EVENT):  # T47: −8°（左へ）と ＋8°（右へ）の帯
            move = tx.BAND_SPEED * sec
            for k, (face, angle, y, sign) in enumerate(
                ((pal.accent, tx.BAND_ANGLE, -0.06, -1), (pal.main, -tx.BAND_ANGLE, 0.06, 1))
            ):
                img = await asyncio.to_thread(tx.title_band, w, h, title, font, face, ink, angle)
                a = await self._png(project_id, img, f"流れる帯（T47）{k + 1}")
                tf = native_transform(img.size[0], img.size[1], w, h, cx=0.5, cy=0.5 + dy + y,
                                      max_w=10.0)  # fmt: skip
                x0 = -sign * move / 2
                keys = [Keyframe(frame=0, opacity=0.0, x=x0, easing=Easing.LINEAR),
                        Keyframe(frame=min(6, n - 1), opacity=1.0,
                                 x=x0 + sign * move * min(6, n - 1) / n, easing=Easing.LINEAR),
                        Keyframe(frame=max(7, n - 7), opacity=1.0,
                                 x=x0 + sign * move * max(7, n - 7) / n, easing=Easing.LINEAR),
                        Keyframe(frame=n - 1, opacity=0.0, x=x0 + sign * move)]  # fmt: skip
                add(TITLE_BACK_TRACK if k == 0 else TITLE_BACK2_TRACK, a, at, n, tf, keys)
        if pv == Purpose.EVENT:  # T50: 画面の高さの 38% の円周に題名を並べ、20 秒で 1 周
            img = await asyncio.to_thread(tx.ring_text, round(h * 0.38), title, font,
                                          pal.text_muted, h)  # fmt: skip
            a = await self._png(project_id, img, "円周を回る文字（T50）")
            tf = native_transform(img.size[0], img.size[1], w, h, cx=0.5, cy=0.5 + dy,
                                  max_w=10.0, max_h=10.0)  # fmt: skip
            turn = 360.0 * sec / tx.RING_SEC
            keys = [Keyframe(frame=0, opacity=0.0, rotation=0.0, easing=Easing.LINEAR),
                    Keyframe(frame=min(6, n - 1), opacity=1.0,
                             rotation=turn * min(6, n - 1) / n, easing=Easing.LINEAR),
                    Keyframe(frame=max(7, n - 7), opacity=1.0,
                             rotation=turn * max(7, n - 7) / n, easing=Easing.LINEAR),
                    Keyframe(frame=n - 1, opacity=0.0, rotation=turn)]  # fmt: skip
            add(TITLE_RING_TRACK, a, at, n, tf, keys)
        if pv == Purpose.SHORT:  # T49: 上下に縁だけの文字を 2 段ずつ、題名に近い段から 4f ずらし
            for k in (1, 2):
                st = 4 * (k - 1)
                m = n - st
                if m < 8:
                    continue
                img = await asyncio.to_thread(tx.echo_pair, w, h, title, font, pal.text, size, k,
                                              dy)  # fmt: skip
                a = await self._png(project_id, img, f"段々の残響（T49）{k} 段目")
                keys = [Keyframe(frame=0, opacity=0.0, easing=Easing.CUBIC_OUT),
                        Keyframe(frame=6, opacity=1.0),
                        Keyframe(frame=max(7, m - 7), opacity=1.0, easing=Easing.CUBIC_IN),
                        Keyframe(frame=m - 1, opacity=0.0)]  # fmt: skip
                add(TITLE_ECHO_TRACK if k == 1 else TITLE_ECHO2_TRACK, a, at + st, m,
                    ClipTransform(fit=FitMode.FIT), keys)  # fmt: skip

    async def _effects(
        self,
        project_id: str,
        prof: DesignProfile,
        lines: list[Any],
        cmds: list[Any],
        lane: Any,
        t_at: int,
        title_end: int,
        total: int,
        fps: float,
        w: int,
        h: int,
    ) -> None:
        """統合版 X1（イベント）・X2（商品紹介・PR）・X3（驚きの発話）・X5・X6（予告編）。"""
        from video_automation.services.direction import effects as fx

        pv = prof.purpose
        placed: list[tuple[int, int]] = []

        async def put(frames_: list[Any], name: str, at: int, n: int, **kw: Any) -> None:
            if at < 0 or n <= 0 or any(a < at + n and at < b for a, b in placed):
                return  # 同じトラックで重ねない
            a = await self._overlay(project_id, frames_, name, fps)
            cmds.append(
                AddClip(
                    track_id=lane(FX_TRACK), asset_id=a.id, start_frame=at,
                    duration_frames=n, clip_id=str(uuid4()), **kw,
                )
            )  # fmt: skip
            placed.append((at, at + n))

        if pv == Purpose.EVENT and title_end > t_at:  # 題名の入り（slide 16f）の終わり
            fr = await asyncio.to_thread(fx.confetti, w, h, prof.palette.chart)
            await put(fr, "紙吹雪（X1）", t_at + 16, min(len(fr), total - t_at - 16))
        if pv in (Purpose.PRODUCT, Purpose.PROMO) and title_end > t_at:
            # 題名の右上（題名の箱の大きさは分からないので、中央の題名の右上のあたり）
            size = max(8, round(h * 0.08 * 0.6))
            fr = await asyncio.to_thread(fx.sparkle, size)
            n = len(fr)
            at = t_at + 18
            if at + n <= title_end:
                tf = ClipTransform(fit=FitMode.FIT, scale=round(2 * size / h, 4), x=0.28, y=-0.12)
                await put(fr, "きらめき（X2）", at, n, transform=tf)
        if pv == Purpose.TUTORIAL:  # I2: 「完了です」「できました」で終わる文の終わりに右上の輪
            sa = safe_area_for(prof, w, h)
            done: list[Any] = []
            for ln in lines:
                t = ln.text.rstrip("。！!　 ")
                if not t.endswith(("完了です", "できました")):
                    continue
                done = done or await asyncio.to_thread(fx.loading_done, h, prof.palette.accent, fps)
                bw = done[0].size[0]
                tf = native_transform(bw, bw, w, h, cx=1 - sa.right - bw / w / 2,
                                      cy=sa.top + bw / h / 2, max_w=1.0)  # fmt: skip
                at = max(0, ln.end - len(done) // 2)
                if at + len(done) <= total:
                    await put(done, "完了の印（I2）", at, len(done), transform=tf)
        if pv in (Purpose.SHORT, Purpose.GAME):  # 驚きの発話の頭（1 秒に最大 1 回）
            last = -(10**9)
            fl: list[Any] = []
            for ln in lines:
                if ln.emotion == "surprise" and ln.start - last >= round(fps):
                    fl = fl or await asyncio.to_thread(fx.focus_lines, w, h)
                    await put(fl, "集中線（X3）", ln.start, len(fl))
                    last = ln.start
        if pv == Purpose.TRAILER:
            if title_end > t_at:  # 題名の後ろの煙（5 秒で繰り返す。題名の間だけ）
                fr = await asyncio.to_thread(fx.smoke, w, h)
                a = await self._overlay(project_id, fr, "煙（X5）", fps)
                pos = t_at
                while pos < title_end:
                    n = min(len(fr), title_end - pos)
                    cmds.append(
                        AddClip(track_id=lane(FX_TRACK), asset_id=a.id, start_frame=pos,
                                duration_frames=n, clip_id=str(uuid4()))
                    )  # fmt: skip
                    placed.append((pos, pos + n))
                    pos += n
            durs = [max(1, x.end - x.start) / fps for x in lines]
            act = pp.acts(durs) if lines else []
            if 3 in act:  # 第 3 幕の全体に残り火
                s3 = lines[act.index(3)].start
                e3 = lines[-1].end
                fr = await asyncio.to_thread(fx.embers, w, h)
                a = await self._overlay(project_id, fr, "残り火（X6）", fps)
                pos = s3
                while pos < e3:
                    n = min(len(fr), e3 - pos)
                    if any(x < pos + n and pos < y for x, y in placed):
                        break
                    cmds.append(
                        AddClip(track_id=lane(EMBER_TRACK), asset_id=a.id, start_frame=pos,
                                duration_frames=n, clip_id=str(uuid4()))
                    )  # fmt: skip
                    pos += n

    async def _cut_transitions(
        self,
        project_id: str,
        prof: DesignProfile,
        cmds: list[Any],
        fig_t: str,
        total: int,
        fps: float,
        w: int,
        h: int,
        lane: Any,
        txt_t: str | None = None,
        keywords: set[str] | None = None,
    ) -> list[str]:
        """統合版 E6〜E13。cmds の図のトラックのクリップの境目に切り替えを足す。"""
        kind = tz.PURPOSE_TRANSITION[prof.purpose.value]
        if prof.look == "Y10":  # 統合版 Y10: 墨と和紙は切り替えを墨の転換に
            kind = "Y10"
        figs = [c for c in cmds if isinstance(c, AddClip) and c.track_id == fig_t]
        ends = {c.start_frame + (c.duration_frames or 0) for c in figs}
        heads = {c.start_frame: c for c in figs if c.start_frame in ends}
        cuts = tz.pick_cuts(list(heads), fps, total, prof.purpose.value)
        if not cuts:
            return []

        def enter(b: int, name: str, n: int) -> None:
            cid = heads[b].clip_id
            if cid:
                cmds.append(
                    SetClipAnimation(
                        clip_id=cid,
                        transition=Transition(type=TransitionType(name), duration_frames=n),
                    )
                )

        if (
            kind == "E13"
        ):  # マッチカット: 境目にかかる強調テロップを 8f 持ち越す。無ければディゾルブ
            texts = [c for c in cmds if isinstance(c, AddClip) and c.track_id == txt_t]
            kws = [c for c in texts if c.clip_id in (keywords or set())]
            near = round(0.5 * fps)
            for b in cuts:
                hold = b + tz.MATCH_HOLD
                cand = [
                    c
                    for c in kws
                    if c.start_frame < b <= c.start_frame + (c.duration_frames or 0) + near
                    and c.start_frame + (c.duration_frames or 0) <= hold
                ]
                pick = max(cand, key=lambda c: c.start_frame) if cand else None
                if pick is not None:
                    p_end = pick.start_frame + (pick.duration_frames or 0)
                    busy = any(
                        c is not pick
                        and c.start_frame < hold
                        and p_end < c.start_frame + (c.duration_frames or 0)
                        for c in texts
                    )
                    if not busy and hold <= total:
                        old = pick.duration_frames or 0
                        i = cmds.index(pick)
                        cmds[i] = pick.model_copy(
                            update={"duration_frames": hold - pick.start_frame}
                        )
                        shorten_animation(cmds, pick.clip_id, old, old - (hold - pick.start_frame))
                        continue
                enter(b, "dissolve", 8)
            return []
        if kind in tz.ENTRANCE:
            name, n = tz.ENTRANCE[kind]
            for b in cuts:
                enter(b, name, n)
            if kind != "E11":
                return []
        n, at = tz.OVERLAY[kind], tz.CUT_AT[kind]
        pal = prof.palette
        cols = [pal.main, pal.accent, pal.chart[1] if len(pal.chart) > 1 else pal.base2]
        if kind == "E11":
            cols = [pal.surface]
        out_dir = self.settings.data_dir / "projects" / project_id / "assets"
        out_dir.mkdir(parents=True, exist_ok=True)
        tid = None
        last_end = -1
        for k, b in enumerate(cuts):
            start = b - at
            if start < 0 or start + n > total or start < last_end:
                continue
            frames_ = await asyncio.to_thread(tz.overlay_frames, kind, w, h, cols, k)
            work = out_dir / f"cut_{kind}_{uuid4().hex[:8]}"
            work.mkdir(parents=True, exist_ok=True)
            # 最後に透明の 1 枚を足す（動画の長さの丸めで 1 フレーム足りなくならないように）
            from PIL import Image

            for i, fr in enumerate([*frames_, Image.new("RGBA", (w, h), (0, 0, 0, 0))]):
                await asyncio.to_thread(fr.save, work / f"{i:03d}.png")
            mov = work.with_suffix(".mov")
            await self.ffmpeg.run(
                ["-y", "-framerate", f"{fps:.6f}", "-i", str(work / "%03d.png"),
                 "-c:v", "png", "-pix_fmt", "rgba", str(mov)]
            )  # fmt: skip
            await asyncio.to_thread(shutil.rmtree, work, True)
            row = await self.timeline.register_generated(
                project_id, mov, AssetKind.VIDEO, "pillow", f"切り替え（{kind}）"
            )
            tid = tid or lane(CUT_TRACK)
            cmds.append(
                AddClip(
                    track_id=tid,
                    asset_id=row.id,
                    start_frame=start,
                    duration_frames=n,
                    clip_id=str(uuid4()),
                )
            )
            last_end = start + n
        return []

    async def _r_background(
        self, project_id: str, prof: DesignProfile, w: int, h: int
    ) -> AssetRead:
        """統合版 R1〜R18（24 秒で継ぎ目なく繰り返す動画。R10 は静止画）。
        同じ設定なら作り直さない。"""
        import hashlib

        from video_automation.services.rendering import backgrounds as rb

        style = PURPOSE_BACKGROUND[prof.purpose.value]
        if prof.look:  # 統合版 Y: 見た目の型の背景
            from video_automation.services.design.looks import LOOKS

            lk = LOOKS.get(prof.look)
            style = lk.background if lk and lk.background else style
        pal = prof.palette.model_dump()
        p = await self.timeline.get_timeline(project_id)
        fps = p.fps_num / p.fps_den
        key = hashlib.sha1(f"{style}|{w}x{h}|{fps}|{sorted(pal.items())}".encode()).hexdigest()[:10]
        out_dir = self.settings.data_dir / "projects" / project_id / "assets"
        out = out_dir / f"bg_{style}_{key}.mp4"
        ff = self.ffmpeg.ffmpeg
        if not ff:
            raise CommandError("ffmpeg not found")
        made = out.with_suffix(".png") if style in rb.STATIC else out
        if not made.is_file():
            made = await asyncio.to_thread(rb.render, ff, style, out, (w, h), fps, pal)
        kind = AssetKind.IMAGE if made.suffix == ".png" else AssetKind.VIDEO
        row = await self.timeline.register_generated(
            project_id, made, kind, "pillow", f"背景（{style} {BACKGROUND_NAMES[style]}）"
        )
        return AssetRead.model_validate(row)

    async def apply(
        self, req: DirectionApplyRequest, origin: CommandOrigin = CommandOrigin.USER
    ) -> DirectionApplyResult:
        p, _, lines = await self._script(req.script_id)
        if not lines or any(x.end <= x.start for x in lines):
            raise CommandError("generate voices and synchronize subtitles first")
        plan = normalize(req.plan, [x.text for x in lines], "", tuple({x.speaker for x in lines}))
        ts = req.text_scale
        prof = await self.design.profile_for(p.id) if self.design else None
        extras = (await self.design.get(p.id)).extras if self.design and prof else []
        plan, dropped = fit_to_profile(plan, prof, extras)
        w, h = p.width, p.height
        # 色・余白は用途の型から（型が無いときは従来の値）
        accent = prof.palette.main if prof else plan.accent_color
        emphasis = prof.palette.accent if prof else plan.accent_color
        panel = prof.palette.surface if prof else "#0F1B2D"
        ink = prof.palette.text if prof else "#FFFFFF"
        chart_colors = prof.palette.chart if prof else [accent, "#FFB74D", "#81C784", "#F06292"]
        muted = prof.palette.text_muted if prof else "#6F829E"
        heading_font = prof.typography.heading_font if prof else None
        # 題名の縁取り: 暗い文字なら背景の明るい色で縁取る
        title_outline = prof.palette.base if prof and is_dark(prof.palette.text) else "#000000"
        sa = safe_area_for(prof, w, h)  # 統合版 S5: 縦型は用途によらず縦型ショートの値
        strip_top = max(STRIP_TOP, sa.top) if prof or h > w else STRIP_TOP
        side = max(SIDE, sa.right) if prof or h > w else SIDE
        # 横長で立ち絵があるときは、図・カードを中央の列に収める（左右の端は立ち絵の列）
        tachie = w >= h and await self._has_tachie(req.script_id)
        main_w = TACHIE_MAIN_W if tachie else 0.92
        # 縦長で立ち絵があるときは、字幕が立ち絵の上（画面の下から約 37〜45%）に出る。
        # 題名・カードをその上に収める（実機: 題名の下の一言が字幕に重なった）
        v_tachie = h > w and await self._has_tachie(req.script_id)
        title_dy = V_TITLE_DY if v_tachie else 0.0
        zone_bottom = V_ZONE_BOTTOM if v_tachie else ZONE_BOTTOM
        used: dict[Component, int] = {}
        boxes: list[Placement] = []
        texts: list[TextUse] = []

        def use(c: Component, n: int = 1) -> None:
            used[c] = used.get(c, 0) + n

        fps = p.fps_num / p.fps_den
        ffps = Fraction(p.fps_num, p.fps_den)
        # 用途ごとの動きの値（統合版 2.1）。型が無いときは解説の値
        mspec = prof.motion if prof else PROFILES[Purpose.EXPLAINER].motion

        def animate(clip_id: str, length: int, entrance: str | None = "fade", **kw: Any) -> None:
            """入り（フェード・pop・スライド）と出（不透明度 1→0）を付ける（統合版 A2・B1・B2）。"""
            anim = element_animation(length, mspec, ffps, entrance=entrance, **kw)
            if anim is not None:
                cmds.append(SetClipAnimation(clip_id=clip_id, animation=anim))

        kb_index = 0  # 連続する画像のケン・バーンズの向き（B3）
        n_sticker = 0  # 統合版 I3: 何枚目のステッカーか（傾きの向き）
        n_photo = 0  # 統合版 N17: 何枚目の写真か（傾きの向き）

        def ken_burns(clip_id: str, a: AssetRead, length: int, box: float) -> None:
            """画像のケン・バーンズ（統合版 B3）。拡大量 = 秒数 × 1.75%（最小 8%・最大 20%）。
            元画像の幅を超えて拡大しない。向きは in → out → 左へパン → 右へパン の順に繰り返す。"""
            nonlocal kb_index
            amount = min(max(sec(length) * KB_PER_SEC, KB_MIN), KB_MAX)
            if a.width and a.height:
                disp_w = min(box * w, box * h * a.width / a.height)
                amount = min(amount, a.width / max(disp_w, 1.0) - 1.0)
            if amount <= 0.005:
                return
            name = KB_ORDER[kb_index % len(KB_ORDER)]
            kb_index += 1
            full = 0.2 if name.startswith("pan_") else 0.15  # プリセットの intensity 1 の拡大量
            cmds.append(
                SetClipAnimation(clip_id=clip_id, preset=name, intensity=round(amount / full, 4))
            )

        def parallax_dx(a: AssetRead, box: float) -> float:
            """切り抜いた絵なら、横に動かす量（画面の幅に対する比）。そうでなければ 0。"""
            if not (a.width and a.height) or not _has_alpha(Path(self.settings.data_dir) / a.path):
                return 0.0
            disp_w = min(box * w, box * h * a.width / a.height)
            return PARALLAX_SHIFT * disp_w / w

        tl = await self.timeline.get_timeline(p.id)
        total = max(tl.duration_frames, lines[-1].end)
        assets = {a.id: a for a in tl.assets}
        skipped: list[str] = list(plan.notes) + dropped

        def frames(sec: float) -> int:
            return round(sec * fps)

        def media_frames(a: AssetRead, fallback: int) -> int:
            """素材の長さ（フレーム）。タイムラインの判定と同じく切り捨て。"""
            if not a.duration_sec:
                return fallback
            return max(1, math.floor(a.duration_sec * fps + 1e-6))

        def sec(n: int) -> float:
            return n / fps

        cmds: list[Any] = []
        track: dict[str, str] = {}
        for t in tl.tracks:
            if (
                t.name in (*TRACKS, *EXTRA_TRACKS)
                and t.kind == TrackKind.VIDEO
                and t.name not in track
            ):
                track[t.name] = t.id
        # 以前の演出は外して作り直す（同じトラックを使う）
        for c in tl.clips:
            if c.track_id in track.values():
                cmds.append(DeleteClip(clip_id=c.id))
        for name in TRACKS:
            if name not in track:
                track[name] = str(uuid4())
                cmds.append(AddTrack(kind=TrackKind.VIDEO, name=name, track_id=track[name]))
        bg_t, fig_t, txt_t = (track[n] for n in TRACKS)

        def lane(name: str) -> str:
            """使うときだけ作るトラック（いちばん上）。"""
            if name not in track:
                track[name] = str(uuid4())
                cmds.append(AddTrack(kind=TrackKind.VIDEO, name=name, track_id=track[name]))
                cmds.append(MoveTrack(track_id=track[name], index=1000))
            return track[name]

        # 重なり順: 背景 → 図・カード → 立ち絵 → 文字（立ち絵を先に置いていても背景で隠さない）
        tachie = next(
            (t for t in tl.tracks if t.name == TACHIE_TRACK and t.kind == TrackKind.VIDEO), None
        )
        rec_t = next(
            (t for t in tl.tracks if t.name == RECORDING_TRACK and t.kind == TrackKind.VIDEO), None
        )
        # 画面録画が主役のときは、図・カードで録画を長く隠さない（章カードとして短く出す）
        recording = rec_t is not None and any(c.track_id == rec_t.id for c in tl.clips)
        if tachie is not None or rec_t is not None:
            # 背景 → 画面録画 → 図・カード → 立ち絵 → 文字（後から足した背景で隠さない）
            mark_t = next(
                (t for t in tl.tracks if t.name == MARK_TRACK and t.kind == TrackKind.VIDEO), None
            )
            order = [
                rec_t.id if rec_t else None,
                mark_t.id if mark_t else None,
                fig_t,
                tachie.id if tachie else None,
                txt_t,
            ]
            for tid in order:
                if tid is not None:
                    cmds.append(MoveTrack(track_id=tid, index=1000))
            for name in EXTRA_TRACKS:  # 前に作った重ねるトラックは、その上に
                if name in track:
                    cmds.append(MoveTrack(track_id=track[name], index=1000))
        clips = 0

        roles: dict[str, str] = {}  # クリップ → 部品（利用者の直しの記録に使う。統合版 W2）
        learned = (
            self.design.edits.overrides(prof.purpose.value)
            if self.design is not None and self.design.edits is not None and prof is not None
            else {}
        )

        def add(
            track_id: str, asset_id: str, start: int, length: int, role: str = "", **kw: Any
        ) -> str:
            nonlocal clips
            clip_id = str(uuid4())
            if role:
                roles[clip_id] = role
                if learned.get(role):  # 利用者が承認した既定値の変更（位置・大きさ）
                    kw["transform"] = adjust_transform(
                        kw.get("transform") or ClipTransform(), learned[role]
                    )
            cmds.append(
                AddClip(
                    track_id=track_id,
                    asset_id=asset_id,
                    start_frame=start,
                    duration_frames=length,
                    clip_id=clip_id,
                    **kw,
                )
            )
            clips += 1
            return clip_id

        # 統合版 S9: 最後の 15 秒を終了画面にする（横長の動画で、終了画面を使う用途だけ）
        series = await self.design.series_for(p.id) if self.design else None
        es = (
            frames(END_SCREEN_SEC)
            if prof is not None
            and prof.purpose in END_SCREEN
            and w >= h
            and not recording
            and (series is None or series.end_screen)
            else 0
        )

        async def series_asset(path: str, label: str) -> AssetRead | None:
            """シリーズの素材を取り込む（同じファイルは前に取り込んだものを使う）。"""
            name = f"シリーズの{label}（{Path(path).name}）"[:200]
            for a in assets.values():
                if a.name == name:
                    return a
            try:
                return await self.timeline.import_asset(
                    AssetImportRequest(project_id=p.id, path=path, name=name)
                )
            except Exception as exc:  # noqa: BLE001 - 置けない素材は外して知らせる
                skipped.append(f"シリーズの{label}を置けませんでした（{type(exc).__name__}）")
                return None

        # 統合版 Q4: シリーズの ED（10 秒。最後の台詞の後、終了画面の前）
        ed_asset = await series_asset(series.ed_path, "ED") if series and series.ed_path else None
        ed = (
            min(frames(SERIES_ED_SEC), media_frames(ed_asset, frames(SERIES_ED_SEC)))
            if ed_asset
            else 0
        )
        trailer = prof is not None and prof.purpose == Purpose.TRAILER
        # 統合版 P8: 予告編は題名を第 3 幕の後（台詞の後）に出し、最後に日付のカード
        tr_date = pp.date_text([x.text for x in lines]) if trailer else ""
        tr_title = frames(pp.TRAILER_TITLE_SEC) if trailer and plan.title else 0
        tr_tail = tr_title + (frames(pp.TRAILER_DATE_SEC) if tr_date else 0)
        bg_total = total + ed + es + tr_tail
        # 背景（ゆっくり寄る）
        bg = await self._background(p.id, plan.background, w, h, prof)
        if bg.kind == AssetKind.VIDEO:
            # 動く背景は継ぎ目なく繰り返せる長さなので、並べて全体を覆う
            loop = max(1, media_frames(bg, bg_total))
            pos = 0
            while pos < bg_total:
                add(bg_t, bg.id, pos, min(loop, bg_total - pos), role="background")
                pos += loop
            use(Component.BACKGROUND_MOTION)
        else:
            cid = add(bg_t, bg.id, 0, bg_total, role="background")
            still = bg.name.startswith("背景（R10")  # 統合版 R10 紙: 動かさない
            if not still and (prof is None or prof.allows(Component.BACKGROUND_MOTION)):
                cmds.append(SetClipAnimation(clip_id=cid, preset="ken_burns_in", intensity=0.4))
                use(Component.BACKGROUND_MOTION)

        # タイトル（最初の発話の間。短すぎるときは 3 秒）
        title_end = 0
        t_at = total + ed if tr_title else 0  # 題名を置く始まり（予告編は台詞の後）
        if plan.title:
            use(Component.TITLE_CARD)
            title_end = min(total, max(lines[0].end, frames(3.0)))
            if tr_title:
                title_end = tr_title
            if prof is not None and prof.allows(Component.LYRIC_TEXT):
                # 歌詞が出る型（MV・リリック）は、最初の歌詞が出る前に題名を消す
                # （前奏が長い曲で題名と歌詞が重ならないように。短くても 2 秒は出す）
                title_end = min(total, max(frames(2.0), min(title_end, lines[0].start)))
            dur = min(sec(title_end), 30.0)
            # 冒頭の題名だけは 1 文字ずつ跳ねて出す（情報系では 1 本に 1 回。短い 1 行の題名のみ）
            kinetic = (
                prof is not None
                and prof.allows(Component.KINETIC_TEXT)
                and "\n" not in plan.title
                and len(plan.title) <= KINETIC_TITLE_MAX
            )
            if kinetic:
                use(Component.KINETIC_TEXT)
            gloss = prof is not None and prof.allows(Component.GLOSS)  # 統合版 L7
            if gloss:
                use(Component.GLOSS)
            beams = prof is not None and prof.allows(Component.SUN_BEAMS)  # 統合版 L3
            if beams:
                use(Component.SUN_BEAMS)
            t_size = min(0.95, main_w * (0.85 if tachie else 0.95) / 0.8 * ts)
            t45 = (
                tx.rotating_words(plan.title[:100])
                if prof is not None and prof.purpose == Purpose.PROMO
                else None
            )
            if t45 is not None:  # 統合版 T45: 「／」で区切った語を同じ位置で入れ替える
                t_asset = await self._overlay(
                    p.id,
                    tx.rotating_frames(w, h, t45[0], t45[1], max(1, round(dur * fps)),
                                       self.titles.font_path(heading_font), ink,
                                       prof.palette.accent if prof else accent, t_size),
                    f"回る語の見出し（T45）「{plan.title[:20]}」",
                    fps,
                )  # fmt: skip
            else:
                t_asset = await self.titles.create(
                    TitleCardRequest(
                        project_id=p.id,
                        text=plan.title[:100],
                        duration_sec=dur,
                        animation=title_entrance(prof, plan.title, kinetic),  # type: ignore[arg-type]
                        # 出は direction 側で付ける（不透明度 1→0 と 0.96 倍への縮小。統合版 A2）
                        fade_out_sec=0.0,
                        # 立ち絵があるときは、題名を中央の列（立ち絵の間）に収める。
                        # 題名の幅は画面幅の 80%×size（title_card.py）。題名は立ち絵の頭と同じ
                        # 高さに出るので、縁取りの分も含めて列の 85% に収める（実機: 18 文字の
                        # 題名がずんだもんの頭に重なった）
                        size=t_size,
                        position="center",
                        text_color=ink,
                        outline_color=title_outline,
                        font=heading_font,
                        gloss=gloss,
                        sun_beams=beams,
                        solid3d=prof is not None and prof.purpose == Purpose.TRAILER,  # T24
                        vertical=prof is not None and prof.look == "Y10",  # T11（墨と和紙）
                        # L6: ドキュメンタリー・予告編の題名は焦点を奥から文字へ送る
                        rack_focus=prof is not None
                        and prof.purpose in (Purpose.DOCUMENTARY, Purpose.TRAILER),
                    )
                )
            t_len = min(title_end, media_frames(t_asset, title_end))
            t_cid = add(
                txt_t,
                t_asset.id,
                t_at,
                t_len,
                role="title_card",
                **({"transform": ClipTransform(y=title_dy)} if title_dy else {}),
            )
            animate(t_cid, t_len, None, title_exit=True)
            if prof is not None:  # 統合版 T47・T49・T50: 題名のまわりの文字
                await self._title_extras(
                    p.id, prof, plan.title[:100], cmds, lane, t_at, t_len, fps, w, h,
                    self.titles.font_path(heading_font), ink, t_size, title_dy,
                )  # fmt: skip
            if plan.subtitle:
                sreq = CardRequest(
                    project_id=p.id,
                    heading=plan.subtitle,
                    style="keyword",
                    accent_color=emphasis,
                    panel_color=panel,
                    text_color=ink,
                    font=heading_font,
                    scale=round(0.62 * ts, 3),
                )
                sub = await self.titles.card(sreq)
                st = min(frames(0.6), max(0, title_end - 15))
                texts.append(
                    text_use("subtitle_card", sreq, sub, w, h, sec(st), sec(title_end), 1.0, main_w)
                )
                cid = add(
                    fig_t,
                    sub.id,
                    t_at + st,
                    max(1, title_end - st),
                    role="title_card",
                    transform=native_transform(
                        sub.width or 1,
                        sub.height or 1,
                        w,
                        h,
                        cx=0.5,
                        cy=0.6 + title_dy,
                        max_w=main_w,
                    ),
                )
                animate(cid, max(1, title_end - st), slide_preset("up", mspec))
            if tr_title:
                title_end = 0  # 冒頭は題名で塞がない（図・カードは最初から置ける）

        # 図・カード・画像（次が出るまで表示し続ける）
        # 置けない画像（素材が未選択）は、表示時間を決める前に外す。後で外すと、その画像の
        # 時間（最後の図なら動画の終わりまで）が背景だけの空白になる
        mains = []
        for s in plan.shots:
            if s.kind not in MAIN_KINDS:
                continue
            if recording and s.kind != ShotKind.CARD:
                skipped.append(f"{s.from_line}: 画面録画を隠さないよう {s.kind.value} を外しました")
                continue
            if s.kind == ShotKind.IMAGE:
                img = assets.get(s.asset_id or "")
                if img is None or img.kind != AssetKind.IMAGE:
                    q = f"（検索語: {s.query}）" if s.query else ""
                    skipped.append(f"{s.from_line}: 画像の素材が未選択のため外しました{q}")
                    continue
            mains.append(s)
        zone_cy = (ZONE_TOP + zone_bottom) / 2
        zone_h = zone_bottom - ZONE_TOP
        windows: list[tuple[Shot, int, int]] = []
        for i, s in enumerate(mains):
            start = max(lines[s.from_line - 1].start, title_end)
            if i + 1 < len(mains):
                # 次の図・カードまで表示する。ただし説明している範囲（to_line）の
                # 2 発話先までに留める（話題が変わった後に古い図を出し続けない）
                hold = lines[min(len(lines), (s.to_line or s.from_line) + HOLD_LINES) - 1].end
                nxt = lines[mains[i + 1].from_line - 1].start
                if prof is not None and prof.max_empty_sec is not None:
                    # 型に「背景だけの時間の上限」があれば、次までの空きがそれ以下になるよう延ばす
                    hold = max(hold, nxt - frames(prof.max_empty_sec))
                end = min(nxt, hold)
            else:
                end = total
            end = max(end, title_end)
            if recording:  # 章カード: 短く出して録画に戻す
                end = min(end, start + frames(CHAPTER_CARD_SEC))
            if end - start < frames(0.5):
                skipped.append(f"{s.from_line}: 表示時間が短すぎる {s.kind.value} を外しました")
                continue
            windows.append((s, start, end))
        shown: list[tuple[int, int]] = []  # 実際に図・カード・画像を出した区間
        # 統合版 N5: 「以前は…今は」の文の区間に続く 2 枚の画像は、前後の比較にまとめる
        pair_of: dict[int, int] = {}
        if prof is not None and prof.purpose in (Purpose.EXPLAINER, Purpose.TUTORIAL,
                                                 Purpose.PRODUCT):  # fmt: skip
            from video_automation.services.rendering import n_cards as nc

            for i_ in range(len(windows) - 1):
                (sh1, w_st, _w_e1), (sh2, _w_s2, w_e2) = windows[i_], windows[i_ + 1]
                if sh1.kind != ShotKind.IMAGE or sh2.kind != ShotKind.IMAGE:
                    continue
                if i_ - 1 in pair_of:  # 前の組の 2 枚目は使わない
                    continue
                spoken = [x for x in lines if w_st <= x.start < w_e2]
                if any(nc.is_before_after(x.text) for x in spoken):
                    pair_of[i_] = i_ + 1
        skip_win = set(pair_of.values())
        for w_i, (s, start, end) in enumerate(windows):
            length = end - start
            if w_i in skip_win:
                continue
            if w_i in pair_of:
                sh2, mid, end2 = windows[pair_of[w_i]]
                img1 = assets[s.asset_id or ""]
                img2 = assets[sh2.asset_id or ""]
                use(Component.IMAGE)
                box = min(zone_h, main_w)
                await self._before_after(
                    p.id, img1, img2, start, mid, end2, cmds, fig_t, h, box, zone_cy, fps,
                    self.titles.font_path(heading_font), add, animate,
                )  # fmt: skip
                shown.append((start, end2))
                continue
            if s.kind == ShotKind.CARD:
                use(Component.CARD)
                steps = _reveal_steps(s, lines, start, end, frames(REVEAL_MIN_SEC))
                # 見出しの語に合う絵記号を添える（合うものが無ければ付けない）
                icon = pick_icon(s.heading, s.body)
                last = len(steps) - 1
                for k, (t0, t1, reveal) in enumerate(steps):
                    creq = CardRequest(
                        project_id=p.id,
                        heading=s.heading,
                        body=s.body,
                        style=s.style,
                        accent_color=accent,
                        panel_color=panel,
                        text_color=ink,
                        font=heading_font,
                        scale=round(min(2.0, CARD_SCALE * ts), 3),
                        reveal=reveal,
                        # 録画の上に出す章カードは、後ろの細かい画面が透けないよう濃く
                        panel_opacity=0.96 if recording else 0.88,
                        icon=icon,
                        # 縦長では横幅いっぱいに使う（実機: 既定の 62% では「パズルゲー／
                        # ムのコツ」「先に置／く」と語の途中で折り返した）
                        width_ratio=main_w if h > w else 0.62,
                    )
                    # 統合版 D1: 2 項目目からは、新しい項目を別の絵で重ねて下から出す
                    new_item = reveal - 1 if reveal is not None and k > 0 else None
                    if new_item is not None:
                        creq = creq.model_copy(update={"hide_item": new_item})
                    a = await self.titles.card(creq)
                    if k == last:  # 文字の大きさと表示の長さ（統合版 T39・T40・T52）
                        texts.append(
                            text_use(
                                "card", creq, a, w, h, sec(steps[0][0]), sec(t1), zone_h, main_w
                            )
                        )
                    # 順に出すときは同じ大きさの絵を切り替えるだけ。出入りの動きは最初と最後のみ
                    cid = add(
                        fig_t,
                        a.id,
                        t0,
                        t1 - t0,
                        role="card",
                        transform=native_transform(
                            a.width or 1,
                            a.height or 1,
                            w,
                            h,
                            cx=0.5,
                            cy=zone_cy,
                            max_h=zone_h,
                            max_w=main_w,
                        ),
                    )
                    # 入りは用途の pop（B2）、出は最後の項目だけ
                    if k == 0 or k == last:
                        animate(cid, t1 - t0, mspec.pop if k == 0 else None, exit_=k == last)
                    if new_item is not None:
                        item = await self.titles.card(
                            creq.model_copy(update={"hide_item": None, "only_item": new_item})
                        )
                        iid = add(
                            lane(ITEM_TRACK),
                            item.id,
                            t0,
                            t1 - t0,
                            role="card",
                            transform=native_transform(
                                item.width or 1,
                                item.height or 1,
                                w,
                                h,
                                cx=0.5,
                                cy=zone_cy,
                                max_h=zone_h,
                                max_w=main_w,
                            ),
                        )
                        dy = ITEM_IN_PX / 1080  # 1080p で 16px（画面の高さに対する比）
                        animate(iid, t1 - t0, "item", exit_=k == last, item_dy=dy)
                if prof is not None and s.body:  # 統合版 I1・N15: 項目の頭の印
                    full = creq.model_copy(update={"reveal": None, "hide_item": None})
                    await self._card_marks(
                        p.id, prof, s.body, lines, steps, full, a, cmds, lane, w, h,
                        zone_cy, zone_h, main_w, fps,
                    )  # fmt: skip
                shown.append((start, end))
            elif s.kind == ShotKind.CHART:
                use(Component.CHART)
                said = "".join(x.text for x in lines[s.from_line - 1 : s.to_line or s.from_line])
                ckind = chart_kind(s, said)  # 統合版 S10: データの形から図を選ぶ
                anim = anim_frames(ckind, len(s.values))
                a = await self.titles.infographic(
                    InfographicRequest(
                        project_id=p.id,
                        kind=ckind,
                        title=s.heading,
                        labels=s.labels,
                        values=s.values,
                        unit=s.unit,
                        decimals=0 if all(float(v).is_integer() for v in s.values) else 1,
                        duration_sec=min(CHART_MAX_SEC, sec(length)),
                        animation_sec=float(anim / ffps),
                        # 表示区間が長さの上限を超えるときは、止まった後半を繰り返して埋める
                        # ので、素材自体は消えない（最後のクリップで消す）
                        fade_out_sec=0.0 if sec(length) > CHART_MAX_SEC else 0.3,
                        highlight=s.highlight if ckind == "bar" else None,
                        colors=chart_colors,
                        muted_color=muted,
                        font=heading_font,
                        text_color=ink,
                        box_color=panel,
                    )
                )
                media = media_frames(a, length)
                shown_len = min(length, media)
                tf = ClipTransform(fit=FitMode.FIT, scale=min(zone_h, main_w), y=zone_cy - 0.5)
                # 動き終わった後（止まっている部分）。区間が素材より長ければ、ここを繰り返す
                still_from = min(media - 1, anim + 2)
                seg = media - still_from
                fill = length > shown_len and seg >= frames(1.0)
                cid = add(fig_t, a.id, start, shown_len, role="chart", transform=tf)
                # 繰り返せないほど短いときだけ、急に消さずに消す
                animate(cid, shown_len, exit_=shown_len < length and not fill)
                shown_end = start + shown_len
                if fill:
                    pos, stop = start + shown_len, start + length
                    while pos < stop:
                        n = min(seg, stop - pos)
                        cid = add(
                            fig_t,
                            a.id,
                            pos,
                            n,
                            role="chart",
                            source_in_frame=still_from,
                            transform=tf,
                        )
                        if pos + n >= stop:
                            animate(cid, n, None)
                        pos += n
                    shown_end = stop
                shown.append((start, shown_end))
            else:  # IMAGE
                img = assets[s.asset_id or ""]  # 置けるものだけ mains に残している
                cap = img.caption  # 見た目を合わせた画は別の素材になるので、先に取っておく
                doc_keys: list[Keyframe] | None = None
                if prof is not None and prof.purpose in (Purpose.DOCUMENTARY, Purpose.NEWS):
                    said_ = " ".join(x.text for x in lines if start <= x.start < end)
                    got_doc = await self._document(p.id, img, said_)
                    if got_doc is not None:  # 書類は見た目を揃えない（文字の色が変わる）
                        img, doc_keys = got_doc
                if req.match_look and doc_keys is None and prof is not None:
                    img = await self._matched_image(p.id, img, prof, assets)
                use(Component.IMAGE)
                box = min(zone_h, main_w)
                sticker = (
                    prof is not None
                    and prof.purpose in (Purpose.VLOG, Purpose.SHORT)
                    and parallax_dx(img, box) > 0
                )
                if sticker:  # 統合版 I3: 切り抜きの画像は白の縁と影を付けたステッカーにする
                    img = await self._sticker(p.id, img, h)
                photo = (
                    prof is not None
                    and prof.purpose in (Purpose.DOCUMENTARY, Purpose.STORY, Purpose.VLOG)
                    and bool(cap)
                    and not sticker
                )
                if photo:  # 統合版 N17: 白い縁と一言を付けた写真にする
                    img = await self._photo_card(p.id, img, cap or "", h, box)
                if doc_keys is not None:  # 統合版 N16: 書類の一致した行に寄って印を付ける
                    box = 0.70
                cid = add(
                    fig_t,
                    img.id,
                    start,
                    length,
                    role="image",
                    transform=ClipTransform(fit=FitMode.FIT, scale=box, y=zone_cy - 0.5),
                )
                dx = parallax_dx(img, box) if prof is not None else 0.0
                if doc_keys is not None:
                    cmds.append(
                        SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=doc_keys))
                    )
                elif photo and length > 12:
                    # 統合版 N17: 回転 −3°（次は ＋2°、以後交互）、上から y −6%→0・
                    # scale 1.08→1.00 を 12f（easeOutCubic）
                    n_photo += 1
                    rot = -3.0 if n_photo % 2 else 2.0
                    keys = [
                        Keyframe(frame=0, y=-0.06, scale=1.08, rotation=rot, opacity=0.0,
                                 easing=Easing.CUBIC_OUT),
                        Keyframe(frame=12, y=0.0, scale=1.0, rotation=rot, opacity=1.0),
                        Keyframe(frame=length - 7, opacity=1.0, rotation=rot,
                                 easing=Easing.CUBIC_IN),
                        Keyframe(frame=length - 1, opacity=0.0, rotation=rot),
                    ]  # fmt: skip
                    cmds.append(
                        SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=keys))
                    )
                elif sticker and length > 6:
                    # 統合版 I3: scale 1.3→1.0・回転 +10°→（奇数番目 −6°、偶数番目 ＋6°）を 5f
                    n_sticker += 1
                    rot = -6.0 if n_sticker % 2 else 6.0
                    keys = [
                        Keyframe(frame=0, scale=1.3, rotation=10.0, easing=Easing.CUBIC_OUT),
                        Keyframe(frame=5, scale=1.0, rotation=rot),
                    ]
                    cmds.append(
                        SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=keys))
                    )
                elif dx and prof is not None and prof.allows(Component.PARALLAX):
                    # 統合版 M8: 切り抜いた絵（透明の部分がある）は、ケン・バーンズの代わりに
                    # 絵を横へ被写体の幅の 3% だけ動かし、後ろの背景との間に奥行きを出す
                    use(Component.PARALLAX)
                    anim_ = element_animation(length, mspec, ffps, entrance="fade")
                    cmds.append(
                        SetClipAnimation(clip_id=cid, animation=with_parallax(anim_, length, dx))
                    )
                else:
                    animate(cid, length)
                    ken_burns(cid, img, length, box)
                if prof is not None and prof.purpose == Purpose.PRODUCT:  # 統合版 P9
                    await self._callouts(
                        p.id, prof, img, lines, start, end, cmds, lane, w, h, box, zone_cy, fps,
                        self.titles.font_path(heading_font), ink,
                    )  # fmt: skip
                shown.append((start, end))

        # 統合版 T30・T32・T33・T34: 素材の無い 3 秒以上の台詞の区間を文字だけの場面にする
        no_subs: list[tuple[int, int]] = []
        if prof is not None and prof.purpose.value in tscn.KIND:
            no_subs = await self._text_scenes(
                p.id, prof, lines, windows, shown, title_end, total, cmds, lane, fps, w, h,
                self.titles.font_path(heading_font),
            )  # fmt: skip
            shown += no_subs

        # 統合版 J1〜J3: LLM が place を付けた発話の間、図の枠に地図（図・カードの無い所だけ）
        if prof is not None and prof.purpose in J_PURPOSES:
            skipped += await self._maps(
                p.id, prof, lines, cmds, lane, shown, total, fps, w, h, min(zone_h, main_w),
                zone_cy,
            )  # fmt: skip

        # テロップ（その発話の間。重なる分は前を短くする）
        kws = [s for s in plan.shots if s.kind == ShotKind.KEYWORD]
        kws += self._fill_gaps(cmds, kws, lines, prof, fps, title_end, names_of(lines))
        kws.sort(key=lambda x: x.from_line)
        emphasis_left = _emphasis_budget(prof)
        spans: list[tuple[Shot, int, int]] = []
        for s in kws:
            start = max(lines[s.from_line - 1].start + frames(0.15), title_end)
            end = lines[(s.to_line or s.from_line) - 1].end
            if any(a_ < end and start < b_ for a_, b_ in no_subs):
                continue  # 文字だけの場面では強調テロップを出さない
            if spans and spans[-1][2] > start:
                prev = spans[-1]
                spans[-1] = (prev[0], prev[1], start)
            spans.append((s, start, end))
        flower_at: list[int] = []  # 統合版 T42: 花字を出した時刻（1 分に 3 回まで）
        chars_t42: dict[str, dict[str, str]] | None = None
        for s, start, end in spans:
            if end - start < frames(0.6):
                continue
            use(Component.KEYWORD)
            ln_k = lines[s.from_line - 1]
            if (
                prof is not None
                and prof.purpose in (Purpose.GAME, Purpose.VLOG, Purpose.SHORT)
                and ln_k.emotion in fl.EMOTIONS
                and sum(1 for t_ in flower_at if start - t_ < frames(60)) < 3
                and end - start > fl.OUT + 8
            ):
                if chars_t42 is None:
                    chars_t42 = await self._characters(req.script_id)
                names_ = list(dict.fromkeys(x.speaker for x in lines))
                spk_col = (chars_t42.get(ln_k.speaker, {}).get("color") or
                           prof.palette.speakers[names_.index(ln_k.speaker)
                                                 % len(prof.palette.speakers)])  # fmt: skip
                await self._flower(
                    p.id, s.text, ln_k.emotion, spk_col, start, end, cmds, txt_t, w, h, fps,
                    side, strip_top, ts, add,
                )  # fmt: skip
                flower_at.append(start)
                use(Component.EMPHASIS_TEXT)
                continue
            hit38 = (
                prof is not None
                and prof.purpose in T38_PURPOSES
                and t38_hit(
                    ln_k.text, s.text, ln_k.query,
                    prof.purpose in (Purpose.EXPLAINER, Purpose.LECTURE, Purpose.CORPORATE,
                                     Purpose.TUTORIAL),
                )
            )  # fmt: skip
            kreq = CardRequest(
                project_id=p.id,
                heading=s.text,
                style="keyword",
                accent_color=emphasis,
                panel_color=panel,
                text_color=emphasis if hit38 else ink,
                font=heading_font,
                scale=round(0.62 * ts * (T38_SCALE if hit38 else 1.0), 3),
            )
            a = await self.titles.card(kreq)
            if (
                prof is not None
                and prof.purpose in T35_PURPOSES
                and _T35_NUM.search(s.text)
                and numbers.find_year(s.text) is None
            ):  # 統合版 T35: 強調テロップの 2 桁以上の数字を回す
                a = await self._roll_card(p.id, kreq, a, end - start, fps)
            elif (
                prof is not None
                and prof.purpose in T28_PURPOSES
                and (ic := word_icon.icon_for(s.text)) is not None
                and end - start > T28_ENTER + word_icon.HOLD + word_icon.MORPH + 6
            ):  # 統合版 T28: 強調テロップの語が図に変わる
                a = await self._morph_card(p.id, kreq, a, ic, end - start, fps, w, h,
                                           prof.palette.main)  # fmt: skip
            texts.append(text_use("keyword", kreq, a, w, h, sec(start), sec(end)))
            aw, ah = a.width or 1, a.height or 1
            # 右上に置く（左上は章の見出しの場所。layout-no-overlap の「補助」の枠）
            k = native_scale(aw, ah, w, h)
            kx, ky = 1 - side - aw * k / 2 / w, strip_top + ah * k / 2 / h
            boxes.append(
                Placement(
                    Component.KEYWORD,
                    kx - aw * k / 2 / w,
                    ky - ah * k / 2 / h,
                    kx + aw * k / 2 / w,
                    ky + ah * k / 2 / h,
                )
            )
            cid = add(
                txt_t,
                a.id,
                start,
                end - start,
                role="keyword",
                transform=native_transform(aw, ah, w, h, cx=kx, cy=ky),
            )
            # 数値の要点は「ポンと飛び出す」動きで強調する（喋りテロップの動き。情報系は 1 本で
            # 1〜2 回まで）。それ以外は横からの登場（B1）
            if _has_number(s.text) and emphasis_left > 0:
                emphasis_left -= 1
                use(Component.EMPHASIS_TEXT)
                animate(cid, end - start, mspec.pop)
            else:
                animate(cid, end - start, slide_preset("right", mspec))

        # 統合版 T36（解説・講義・会社紹介）・T46（操作説明）: 「A ではなく B」の A を消して B に
        if prof is not None and prof.purpose in (
            Purpose.EXPLAINER, Purpose.LECTURE, Purpose.CORPORATE, Purpose.TUTORIAL,
        ):  # fmt: skip
            await self._not_telops(
                p.id, prof, lines, cmds, lane, total, fps, w, h, strip_top, ts,
                self.titles.font_path(heading_font), ink, panel,
            )  # fmt: skip

        # 統合版 P1: 名前帯（インタビュー・ドキュメンタリー・ニュース・会社紹介）
        if (
            prof is not None
            and prof.purpose in LOWER_THIRD_PURPOSES
            and (prof.allows(Component.LOWER_THIRD) or Component.LOWER_THIRD in extras)
        ):
            chars = await self._characters(req.script_id)
            vertical = h > w
            bottom = 0.60 if vertical else 0.70  # 字幕の上（縦型は下 35% の安全範囲の上）
            left = safe_area_for(prof, w, h).left
            names = list(dict.fromkeys(x.speaker for x in lines if x.speaker))
            sched = lower_third_schedule([(x.speaker, x.start, x.end) for x in lines], float(fps))
            for k_lt, (spk, at) in enumerate(sched):
                # 次の話者の名前帯が出たら、前の帯は消す（同じ所に重ねない）
                nxt = sched[k_lt + 1][1] if k_lt + 1 < len(sched) else total
                # 題名が出ている間・消える動きの間に重ねない（実機: 同時に 3 つ動いて止まった）
                at = max(at, title_end + frames(0.2)) if at < title_end + frames(0.2) else at
                if nxt - at < frames(1.5):
                    continue
                lt_limit = prof.allowance(Component.LOWER_THIRD)
                if at >= total - frames(1.0) or (
                    0 < lt_limit <= used.get(Component.LOWER_THIRD, 0)
                ):
                    continue  # 終わり間際、または用途の回数の上限
                info = chars.get(spk, {})
                n = min(frames(lt_show_sec(spk, info.get("job_title", ""))), nxt - at)
                color = (
                    info.get("color")
                    or (prof.palette.speakers[names.index(spk) % len(prof.palette.speakers)])
                )
                out = (
                    Path(self.settings.data_dir)
                    / "projects"
                    / p.id
                    / "assets"
                    / f"lower_third_{uuid4().hex[:8]}.png"
                )
                await asyncio.to_thread(
                    draw_lower_third,
                    out,
                    h,
                    spk,
                    info.get("job_title", ""),
                    self.titles.font_path(heading_font),
                    ink,
                    color,
                    panel,
                )
                row = await self.timeline.register_generated(
                    p.id, out, AssetKind.IMAGE, "pillow", f"名前帯「{spk[:20]}」"
                )
                aw, ah = row.width or 1, row.height or 1
                cid = add(
                    lane(LOWER_THIRD_TRACK),
                    row.id,
                    at,
                    n,
                    role="lower_third",
                    transform=native_transform(
                        aw, ah, w, h, cx=left + aw / w / 2, cy=bottom - ah / h / 2
                    ),
                )
                use(Component.LOWER_THIRD)
                animate(cid, n, slide_preset("left", mspec))
        texts_all = [x.text for x in lines]
        part_dir = Path(self.settings.data_dir) / "projects" / p.id / "assets"
        font = self.titles.font_path(heading_font)
        line_col = prof.palette.main if prof else accent

        async def png(label: str, fn: Any, *args: Any) -> Any:
            out = part_dir / f"part_{uuid4().hex[:8]}.png"
            await asyncio.to_thread(fn, out, *args)
            return await self.timeline.register_generated(
                p.id, out, AssetKind.IMAGE, "pillow", label
            )

        def place(row: Any, cx: float, cy: float, max_w: float = 0.92) -> ClipTransform:
            return native_transform(
                row.width or 1, row.height or 1, w, h, cx=cx, cy=cy, max_w=max_w
            )

        def corner(row: Any, left_x: float, top_y: float) -> ClipTransform:
            """左上の角を (left_x, top_y) に合わせる（画面に対する比）。"""
            aw, ah = row.width or 1, row.height or 1
            return native_transform(aw, ah, w, h, cx=left_x + aw / w / 2, cy=top_y + ah / h / 2)

        # 統合版 P2: ポッドキャストの波形（話者ごとに、話している人の声だけを話者の色で）
        if prof is not None and prof.purpose == Purpose.PODCAST:
            wave_ = await self._podcast_waves(p, req.script_id, total, part_dir)
            if wave_ is not None:
                add(
                    lane(WAVE_TRACK),
                    wave_.id,
                    0,
                    total,
                    role="podcast_wave",
                    transform=place(wave_, 0.5, podcast.CENTER_Y),
                )

        # 統合版 P4: 料理の材料（「材料名 分量」2 秒・STEP n・最後に材料の一覧）
        if prof is not None and prof.purpose == Purpose.RECIPE:
            ings = pp.ingredients(texts_all)
            list_at = max(0, total - frames(pp.INGREDIENT_LIST_SEC)) if ings else total
            by_line: dict[int, list[pp.Ingredient]] = {}
            for g in ings:
                by_line.setdefault(g.line, []).append(g)
            starts = sorted(lines[i].start for i in by_line)
            for i, group in sorted(by_line.items()):
                at = lines[i].start
                nxt = next((s for s in starts if s > at), list_at)
                n = min(frames(pp.INGREDIENT_SEC), nxt - at, list_at - at)
                if n < frames(0.5):
                    continue
                row = await png(
                    f"材料「{group[0].name[:10]}」",
                    draw_labels,
                    h,
                    [g.label for g in group[: pp.INGREDIENT_MAX]],
                    font,
                    ink,
                    line_col,
                    panel,
                )
                cid = add(
                    lane(PART_TRACK),
                    row.id,
                    at,
                    n,
                    role="recipe_ingredient",
                    transform=corner(row, sa.left, 0.70 - (row.height or 1) / h),
                )
                animate(cid, n, slide_preset("left", mspec))
            stp = pp.steps(texts_all)
            for k, (i, num) in enumerate(stp):
                at = lines[i].start
                end = lines[stp[k + 1][0]].start if k + 1 < len(stp) else total
                if end - at < frames(1.0):
                    continue
                row = await png(
                    f"STEP {num}", draw_labels, h, [f"STEP {num}"], font, ink, line_col, panel
                )
                cid = add(
                    lane(STEP_TRACK),
                    row.id,
                    at,
                    end - at,
                    role="recipe_step",
                    transform=corner(row, sa.left, max(sa.top, 0.05)),
                )
                use(Component.CHAPTER_LABEL)
                animate(cid, end - at, slide_preset("left", mspec))
            if ings and total - list_at >= frames(2.0):
                row = await png(
                    "材料の一覧",
                    draw_list_card,
                    w,
                    h,
                    pp.servings(texts_all),
                    [g.label for g in ings],
                    font,
                    ink,
                    emphasis,
                    panel,
                )
                cid = add(
                    lane(PART_TRACK),
                    row.id,
                    list_at,
                    total - list_at,
                    role="recipe_list",
                    transform=place(row, 0.5, 0.42, main_w),
                )
                use(Component.CARD)
                animate(cid, total - list_at)

        # 統合版 P5: ランキング（大きな数字 → 解説のカード → ここまでの一覧のボード）
        if prof is not None and prof.purpose == Purpose.RANKING:
            rks = pp.ranks(texts_all)
            for k, r in enumerate(rks):
                at = lines[r.line].start
                end = lines[rks[k + 1].line].start if k + 1 < len(rks) else total
                n = min(frames(pp.RANK_NUMBER_SEC), end - at)
                if n < frames(0.5):
                    continue
                row = await png(
                    f"第{r.number}位",
                    draw_rank_number,
                    h,
                    r.number,
                    font,
                    ink,
                    emphasis,
                    title_outline,
                )
                cid = add(
                    lane(PART_TRACK),
                    row.id,
                    at,
                    n,
                    role="rank_number",
                    transform=place(row, 0.5, 0.40),
                )
                animate(cid, n, mspec.pop)
                # 数字 → 解説のカードの順に出す（同じ所に重ねない）: 数字の間に始まるカード・図・
                # 項目は、数字が消えてから出す
                delay_ids = {track[x] for x in (ITEM_TRACK,) if x in track} | {fig_t}
                for k_c, c in enumerate(cmds):
                    if (
                        isinstance(c, AddClip)
                        and c.track_id in delay_ids
                        and c.duration_frames is not None
                        and at <= c.start_frame < at + n
                        and c.start_frame + c.duration_frames > at + n + frames(0.5)
                    ):
                        cut = at + n - c.start_frame
                        cmds[k_c] = c.model_copy(
                            update={
                                "start_frame": at + n,
                                "duration_frames": c.duration_frames - cut,
                            }
                        )
                        shorten_animation(cmds, c.clip_id, c.duration_frames, cut)
                b_at = max(at + n, end - frames(pp.RANK_BOARD_SEC))
                if end - b_at < frames(1.5):
                    continue
                # ボードの間はカードを出さない（ボードと同時に消えると、同時に動く要素が 3 つ
                # になった。実機）。カードはボードが出る所で終える
                for k_c, c in enumerate(cmds):
                    if (
                        isinstance(c, AddClip)
                        and c.track_id in delay_ids
                        and c.duration_frames is not None
                        and c.start_frame < b_at < c.start_frame + c.duration_frames
                        and b_at - c.start_frame >= frames(0.5)
                    ):
                        cut = c.start_frame + c.duration_frames - b_at
                        cmds[k_c] = c.model_copy(update={"duration_frames": b_at - c.start_frame})
                        shorten_animation(cmds, c.clip_id, c.duration_frames, cut)
                board = [f"第{x.number}位　{x.name}".rstrip() for x in rks[: k + 1]]
                row = await png(
                    "ここまでの順位",
                    draw_list_card,
                    w,
                    h,
                    "ここまでの順位",
                    board,
                    font,
                    ink,
                    emphasis,
                    panel,
                    k,
                )
                cid = add(
                    lane(PART_TRACK),
                    row.id,
                    b_at,
                    end - b_at,
                    role="rank_board",
                    transform=place(row, 0.5, 0.45, main_w),
                )
                use(Component.CARD)
                animate(cid, end - b_at)

        # 統合版 P8: 予告編の日付のカード（題名の後。台本に日付があるときだけ）
        if tr_date:
            at = total + ed + tr_title
            n = frames(pp.TRAILER_DATE_SEC)
            row = await png("日付", draw_labels, h, [tr_date], font, ink, line_col, panel)
            cid = add(
                lane(PART_TRACK), row.id, at, n, role="trailer_date", transform=place(row, 0.5, 0.5)
            )
            animate(cid, n)
        # 統合版 Q4: シリーズの OP（3 秒。台本の 1 文目の発話が終わった直後）・ED・ロゴ
        if series is not None:
            if series.op_path and (op := await series_asset(series.op_path, "OP")):
                at = lines[0].end
                n = min(frames(SERIES_OP_SEC), media_frames(op, frames(SERIES_OP_SEC)))
                if at + n <= total:
                    cid = add(lane(SERIES_TRACK), op.id, at, n, role="series_op")
                    animate(cid, n)
            if ed_asset is not None and ed:
                cid = add(lane(SERIES_TRACK), ed_asset.id, total, ed, role="series_ed")
                animate(cid, ed)
            if series.logo_path and (logo := await series_asset(series.logo_path, "ロゴ")):
                lw = 0.10  # 幅は画面の 10%（右上。安全範囲の内側）
                lh = lw * w / h * (logo.height or 1) / max(1, logo.width or 1)
                cid = add(
                    lane(LOGO_TRACK),
                    logo.id,
                    0,
                    total,
                    role="series_logo",
                    transform=native_transform(
                        logo.width or 1,
                        logo.height or 1,
                        w,
                        h,
                        cx=1 - sa.right - lw / 2,
                        cy=sa.top + lh / 2,
                        max_w=lw,
                        max_h=0.12,
                    ),
                )
        # 統合版 X1・X2・X3・X5・X6: 効果と粒
        if prof is not None:
            # 予告編の題名は台詞の後（t_at から tr_title フレーム）。他の用途は 0〜title_end
            t_end = t_at + tr_title if tr_title else title_end
            await self._effects(p.id, prof, lines, cmds, lane, t_at, t_end, total, fps, w, h)
            if prof.purpose in (Purpose.TRAILER, Purpose.STORY) and lines:  # 統合版 T44
                ln0 = lines[0]
                n0 = min(total, ln0.end) - ln0.start
                if n0 > 6:
                    a0 = await self._overlay(
                        p.id,
                        tx.crawl_frames(w, h, ln0.text, self.titles.font_path(heading_font),
                                        ink, n0, fps),
                        "奥へ流れる文字（T44）",
                        fps,
                    )  # fmt: skip
                    c0 = str(uuid4())
                    cmds.append(AddClip(track_id=lane(CRAWL_TRACK), asset_id=a0.id,
                                        start_frame=ln0.start, duration_frames=n0, clip_id=c0,
                                        transform=ClipTransform(fit=FitMode.FIT)))  # fmt: skip

        # 統合版 H3・H4・H6: ニュースの帯・クレジットロール・中間字幕のカード
        if prof is not None:
            chap = [
                (start, (s.heading or "").strip())
                for s, start, _end in windows
                if s.kind in (ShotKind.CARD, ShotKind.CHART) and (s.heading or "").strip()
            ]
            font = self.titles.font_path(heading_font)
            skipped += await self._h_cards(
                p.id, prof, plan, lines, chap, cmds, lane, total, fps, w, h, font, ink
            )

        # 統合版 T43: 台詞の年号を右上に（2 回目からは前の年から回す）
        if prof is not None and prof.purpose in T43_PURPOSES:
            await self._years(p.id, prof, lines, cmds, lane, total, fps, w, h,
                              self.titles.font_path(heading_font), ink)  # fmt: skip

        # 統合版 N1・N7・N8・N11: 台本の語だけで出せる図解
        if prof is not None:
            skipped += await self._n_cards(
                p.id, prof, lines, cmds, lane, total, fps, w, h,
                self.titles.font_path(heading_font), ink,
            )  # fmt: skip

        # 統合版 E6〜E10: 図・カードのショットが替わる所に、用途の切り替えの型を付ける
        if prof is not None and prof.purpose.value in tz.PURPOSE_TRANSITION:
            kw_ids = {cid for cid, r in roles.items() if r == "keyword"}
            skipped += await self._cut_transitions(
                p.id, prof, cmds, fig_t, total, fps, w, h, lane, txt_t, kw_ids
            )

        if es:  # 終了画面（まとめの文字と、YouTube の要素を置く場所の目安の枠）
            out = (
                Path(self.settings.data_dir)
                / "projects"
                / p.id
                / "assets"
                / f"end_screen_{uuid4().hex[:8]}.png"
            )
            await asyncio.to_thread(
                draw_end_screen,
                out,
                w,
                h,
                plan.title or "",
                plan.subtitle,
                self.titles.font_path(heading_font),
                ink,
                accent,
            )
            row = await self.timeline.register_generated(
                p.id, out, AssetKind.IMAGE, "pillow", "終了画面"
            )
            cid = add(fig_t, row.id, total + ed, es, role="end_screen")
            animate(cid, es)
        if any(x in track for x in BEHIND_TITLE):  # 統合版 T47・T50: 立ち絵と題名の下へ
            for name in BEHIND_TITLE:
                if name in track:
                    cmds.append(MoveTrack(track_id=track[name], index=1000))
            for t_ in tl.tracks:
                if t_.name == TACHIE_TRACK and t_.kind == TrackKind.VIDEO:
                    cmds.append(MoveTrack(track_id=t_.id, index=1000))
            cmds.append(MoveTrack(track_id=txt_t, index=1000))
            for name in EXTRA_TRACKS:
                if name in track and name not in BEHIND_TITLE:
                    cmds.append(MoveTrack(track_id=track[name], index=1000))
        if prof is not None and lines:
            from video_automation.services.rendering import h_cards as hc_

            if prof.purpose == Purpose.STORY:  # H6: 中間字幕のカードの文の字幕は出さない
                no_subs += [(x.start, x.end) for x in lines if hc_.is_intertitle(x.text)]
            if prof.purpose in (Purpose.TRAILER, Purpose.STORY):  # T44: 最初の台詞
                no_subs.append((lines[0].start, lines[0].end))
        if prof is not None and prof.purpose == Purpose.KIDS:  # 統合版 F3: カード・図の縁のふるえ
            await self._f3_cards(p.id, cmds, roles, fps, h)
        res = await self.timeline.submit(
            p.id,
            CommandRequest(
                command=Batch(commands=cmds),
                origin=origin,
                description="演出プランを適用",
            ),
        )
        # 背景だけ（題名・図・カード・画像が無い）区間
        covered = sorted([(0, title_end)] + shown)
        empty: list[tuple[float, float]] = []
        cur = 0
        for w0, w1 in covered:
            if w0 > cur:
                empty.append((cur / fps, w0 / fps))
            cur = max(cur, w1)
        if cur < total:
            empty.append((cur / fps, total / fps))
        if recording:  # 画面録画が出ている間は背景だけにならない
            empty = []
        if self.design is not None:  # 書き出し前の検査のために、作った要素を記録する
            colors = [accent, emphasis, panel, ink]
            if used.get(Component.CHART):
                colors += chart_colors
            fonts = [Path(f).stem for f in [self.titles.font_path(heading_font)] if f]
            # 書き出し時の画面演出（rich_direction）が使う、題名と章の区切り
            outline = {
                "end_screen": round((total + ed) / fps, 3) if es else None,
                "title": plan.title,
                "catch": plan.subtitle,
                "chapters": [
                    [start, (s.heading or "").strip()]
                    for s, start, _end in windows
                    if s.kind in (ShotKind.CARD, ShotKind.CHART) and (s.heading or "").strip()
                ],
                # 字幕を出さない区間（文字だけの場面 T30〜T34・中間字幕 H6・奥へ流れる文字 T44）
                "no_subtitles": [[a_, b_] for a_, b_ in no_subs],
            }
            await self.design.record(
                p.id,
                "direction",
                manifest_record(
                    used, colors, fonts, boxes, empty, outline=outline, texts=texts, clips=roles
                ),
            )
            if self.design.edits is not None and prof is not None:  # 直さなかった本も数える（W2）
                self.design.edits.made(p.id, prof.purpose.value, list(roles.values()))
        log.info("direction_applied", project_id=p.id, clips=clips, skipped=len(skipped))
        return DirectionApplyResult(
            command_id=res.id, status=res.status, clips=clips, skipped=skipped
        )
