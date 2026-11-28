"""文章の指示だけで動画を作る（つくる → 構成の確認 → 制作中 → できあがり）。

動画編集に関わったことがない人が、Blender のアドオンから文章を書くだけで動画を作れるようにする。
1. plan: 用途を判定し、プロジェクト・台本の設定・話し手（声と立ち絵）を決めて「構成」を返す。
   ここではタイムラインを変えず、LLM も使わない（すぐ返る）
2. run: 構成の確認で人が 1 回承認したら、台本 → 音声 → 立ち絵 → 字幕 → 演出 → 検査 → 書き出し
   を続けて行う。承認したのは人なので、各段は人の操作（origin=user）として行う
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from video_automation.app.logging import get_logger
from video_automation.database.models import Project
from video_automation.schemas.animation import ClipAnimation, Easing, Keyframe
from video_automation.schemas.asset_source import MusicGenerateRequest
from video_automation.schemas.build import (
    BuildChange,
    BuildCharacter,
    BuildPlan,
    BuildRequest,
    BuildRunRequest,
    PackChoice,
    ReadinessItem,
)
from video_automation.schemas.design import (
    ClassifyRequest,
    Component,
    Family,
    ProjectDesign,
    Purpose,
)
from video_automation.schemas.direction import DirectionApplyRequest, DirectionPlanRequest
from video_automation.schemas.provider import ChatMessage, TaskType
from video_automation.schemas.script_studio import (
    CharacterIn,
    DialogueInput,
    DialogueLine,
    ScriptGenerateRequest,
    ScriptMode,
    ScriptSettings,
    ScriptStudioCreate,
    ScriptUpdate,
    VoiceSettings,
)
from video_automation.schemas.subtitle import SubtitleGenerateRequest, SubtitleSyncRequest
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
    RenderProfileName,
    RenderRequest,
    RenderStatus,
    SetClipAnimation,
    TrackKind,
)
from video_automation.schemas.voice import ReadingCheckRequest
from video_automation.services.build import audio_bed, bgm_prompts, recording_reader
from video_automation.services.design.profiles import PROFILES
from video_automation.services.direction import mv_bake
from video_automation.services.direction import mv_shots as mv
from video_automation.services.direction import purpose_parts as pp
from video_automation.services.direction import slides as sl
from video_automation.services.direction.service import MARK_TRACK, native_transform
from video_automation.services.rendering import lyric_screen as ls_
from video_automation.services.rendering import sync as sync_audio
from video_automation.services.rendering.beats import detect_file
from video_automation.services.rendering.lyrics import sections as lyric_sections
from video_automation.services.rendering.render_service import FILTER_SCRIPT_THRESHOLD
from video_automation.services.transcribe.service import TranscribeRequest

if TYPE_CHECKING:
    from video_automation.app.bootstrap import AppContainer

log = get_logger("build")

# 用途ごとの話し手（VOICEVOX の話者名, 役割）。掛け合いが合う用途は 2 人
_DUO = [
    ("四国めたん", "解説役（要点を分かりやすく説明する）"),
    ("ずんだもん", "聞き手（素朴な疑問を言い、驚く）"),
]
CAST: dict[Purpose, list[tuple[str, str]]] = {
    Purpose.EXPLAINER: _DUO,
    Purpose.TUTORIAL: _DUO,
    Purpose.SHORT: _DUO,
    Purpose.RANKING: _DUO,
    Purpose.GAME: _DUO,
    Purpose.KIDS: [("ずんだもん", "案内役（やさしく話す）"), ("春日部つむぎ", "聞き手")],
    Purpose.PODCAST: [("春日部つむぎ", "進行役"), ("玄野武宏", "ゲスト")],
    Purpose.INTERVIEW: [("春日部つむぎ", "聞き手"), ("玄野武宏", "話し手")],
    Purpose.STORY: [("冥鳴ひまり", "語り手")],
    Purpose.DOCUMENTARY: [("青山龍星", "ナレーター")],
    Purpose.NEWS: [("青山龍星", "キャスター")],
}
_NARRATOR = [("四国めたん", "ナレーター（落ち着いて説明する）")]
# ゲーム実況の掛け合い（play_lines があるとき）: (表示名, 役割, 既定の声)
GAME_DUO = [
    (
        "Claude",
        "実況と機能の解説役（このゲームを遊んだ本人。遊びながら仕組みを説明する）",
        "四国めたん",
    ),
    ("Qwen", "質問とガヤ役（素朴な疑問を言い、驚き、茶々を入れる。説明はしない）", "ずんだもん"),
]
# 曲（音源）が要る用途。文章だけでは作れない
NEEDS_MUSIC = {Purpose.MV, Purpose.LYRIC}
SHORT_PURPOSES = {Purpose.SHORT, Purpose.PROMO, Purpose.TRAILER, Purpose.PRODUCT}

STEPS = [  # 実際に行う順
    ("script", "台本を作る"),
    ("voice", "音声を作る"),
    ("subtitles", "字幕を作る"),
    ("tachie", "立ち絵を置く"),
    ("recording", "画面録画を置く"),
    ("direction", "画面の演出を付ける"),
    ("check", "デザインを検査する"),
    ("render", "書き出す"),
]


MUSIC_NAME = "曲: "  # 構成で取り込んだ曲の素材の名前の頭
MUSIC_TRACK = "曲"
BGM_TRACK = "BGM"
SFX_TRACK = "効果音"
# 曲から作るとき（MV・リリック）の手順。台本・声の代わりに、歌の時刻を文字起こしで取る
MV_STEPS = [
    ("lyrics", "歌詞と時刻を合わせる"),
    ("music", "曲を置く"),
    ("direction", "画面の演出を付ける"),
    ("check", "デザインを検査する"),
    ("render", "書き出す"),
]
MV_IMAGE_NAME = "MVの絵: "  # 構成で取り込んだ MV のショットの絵（統合版 V1）
MV_SHOT_TRACK = "MV・ショット"
MV_MOTIF_TRACKS = ("MV・形（白）", "MV・形（黒）")  # 統合版 V11
MV_INK_TRACK = "MV・インク"  # 統合版 V6
MV_VIS_TRACK = "MV・音の視覚化"  # 統合版 M10・M11
MV_KEY_TRACK = "MV・キーの一字"  # 統合版 T26
MV_T29_TRACK = "MV・文字の絵"  # 統合版 T29
MV_U_TRACK = "MV・歌詞の画面"  # 統合版 U2〜U5（曲の方向性を選んだとき）
MV_U16_TRACK = "MV・歌詞のカード"  # 統合版 U16（しっとり系・バラードの短い語）
X4_TRACK = "演出・擬音"  # 統合版 X4
MV_X7_TRACK = "MV・落ちサビの粒"  # 統合版 X7
MV_BAR_TRACKS = ("MV・黒帯（上）", "MV・黒帯（下）")  # 統合版 V10
RECORDING_NAME = "画面録画: "  # 構成で取り込んだ画面録画の素材の名前の頭
RECORDING_TRACK = "画面録画"
FACECAM_NAME = "顔の映像: "  # 統合版 P6: ゲーム実況のワイプの素材の名前の頭
WIPE_TRACK = "ワイプ"
MAX_REC_MARKS = 60  # 統合版 N12・N13（録画）: OCR する文の数の上限
# 録画を置く枠（画面比）: 上 100px は見出しの帯、下は字幕。立ち絵があれば左右に各 300px 空ける
REC_CY = 0.44
REC_H = 0.66
REC_W = 0.9
REC_W_TACHIE = 0.69
# ゲーム実況: プレイ録画の音（BGM・効果音）を声の下に小さく残す（約 −12 dB）。寄りは作らない
GAME_REC_VOLUME = 0.25


class BuildError(Exception):
    """利用者に見せる失敗（ジョブの error に本文を付ける）。"""

    public_message = True


_BIG = re.compile(r"(文字|字幕|テロップ|字|カード|見出し)[^。\n]{0,12}?(大き|でか|太く|読みやすく)")
_SMALL = re.compile(r"(文字|字幕|テロップ|字|カード|見出し)[^。\n]{0,12}?(小さ|控えめ)")
_MORE = re.compile(r"(もっと|かなり|すごく|とても|もう少しだけ|少しだけ|ちょっと)")


FIT_TOLERANCE = 1.08  # 目標の長さをこれ以上超えたら話す速さで合わせる
MAX_SPEED_UP = 1.2  # 速くしすぎると聞き取りにくい


def text_size_factor(instruction: str) -> float | None:
    """手直しの文章から、文字の大きさの変え方（倍率）を読む。触れていなければ None。"""
    t = instruction or ""
    m = _MORE.search(t)
    strong = bool(m) and m.group(1) in ("もっと", "かなり", "すごく", "とても")
    light = bool(m) and m.group(1) in ("もう少しだけ", "少しだけ", "ちょっと")
    if _BIG.search(t):
        return 1.35 if strong else 1.1 if light else 1.2
    if _SMALL.search(t):
        return 0.75 if strong else 0.92 if light else 0.85
    return None


def _title(instruction: str) -> str:
    t = instruction.strip().splitlines()[0]
    for suf in ("を作って", "を作ってください", "を作りたい", "を作る", "の動画", "動画"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return (t.strip(" 　。") or "新しい動画")[:40]


def _is_opaque(path: Path) -> bool:
    """透明の部分が無い画像（全面の絵）か。読めなければ False。"""
    try:
        from PIL import Image

        with Image.open(path) as im:
            if im.mode not in ("RGBA", "LA", "P"):
                return True
            return im.convert("RGBA").getchannel("A").getextrema()[0] >= 250
    except Exception:  # noqa: BLE001
        return False


def _song_title(instruction: str, music: Path) -> str:
    """MV・リリックの題名: 指示の文に「」か『』で囲んだ曲名があればそれ、無ければ曲の
    ファイル名（拡張子を除き、_ と - を空白に）。「この曲の MV」を題名にしない。"""
    m = re.search(r"[「『]([^」』]{1,40})[」』]", instruction)
    if m and m.group(1).strip():
        return m.group(1).strip()
    stem = re.sub(r"[_\-]+", " ", music.stem).strip()
    return (stem or "新しい曲")[:40]


VERTICAL_STRUCTURE = (
    "1 文目でいちばん伝えたい結論を言う（理由や前置きは 2 文目から）。"
    "冒頭は 1 文を短く、2 行に収まる長さにする"
)


AUTO_STRUCTURES = (VERTICAL_STRUCTURE, pp.RANKING_STRUCTURE, pp.TRAILER_STRUCTURE)


def purpose_structure(purpose: Purpose, portrait: bool) -> str:
    """台本の構成の指示（自動で付けるもの）。縦型（S5）・ランキング（P5）・予告編（P8）。"""
    parts = [VERTICAL_STRUCTURE] if portrait else []
    if purpose == Purpose.RANKING:
        parts.append(pp.RANKING_STRUCTURE)
    elif purpose == Purpose.TRAILER:
        parts.append(pp.TRAILER_STRUCTURE)
    return "\n".join(parts)


def _ink_overlay(
    path: Path, prev: Any, fps: float, shape: str | None, edge: str, seed: int, w: int, h: int
) -> list[Any]:
    """V6: 前のショットの最後に見えていた画に穴をあける（V11 の面なら、重なった白と黒の形も）。"""
    from PIL import Image

    with Image.open(path) as im:
        img = im.convert("RGBA").resize((w, h))
    img = mv.camera_view(img, prev.shot, prev.end - prev.start, fps)
    if shape:
        img.alpha_composite(mv.motif_image(shape, mv.MOTIF_WHITE, w, h))
        img.alpha_composite(mv.motif_image(shape, mv.MOTIF_BLACK, w, h, mirror=True))
    return mv.ink_frames(img, edge, seed)


def _bar_png(out: Path, w: int, h: int, bar: int, side: str) -> None:
    """V10 の黒帯（画面の大きさの透過 PNG。上か下に帯）。"""
    from PIL import Image

    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    im.paste((0, 0, 0, 255), (0, 0, w, bar) if side == "top" else (0, h - bar, w, h))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)


async def _no_progress(_x: float) -> None:
    return None


def skipped_notes(skipped: list[str]) -> list[str]:
    """演出で外したものの理由を、同じ理由ごとにまとめて伝える（実機: 素材の無い画像や表示時間の
    足りないカードまで「用途の型に合わない演出」と書いていた）。"""
    if not skipped:
        return []
    reasons: dict[str, int] = {}
    for x in skipped:
        r = x.split(": ", 1)[-1]  # 頭の発話番号（「3: 」）を外す
        r = re.sub(r"「[^」]*」|（[^）]*）|\d+", "…", r)  # 語・数を伏せて同じ理由をまとめる
        reasons[r] = reasons.get(r, 0) + 1
    top = sorted(reasons.items(), key=lambda kv: -kv[1])[:3]
    detail = "、".join(f"{r}（{n} 件）" if n > 1 else r for r, n in top)
    return [f"演出 {len(skipped)} 個を外しました: {detail}"]


class BuildService:
    def __init__(self, c: AppContainer) -> None:
        self.c = c

    # ---------- 準備の確認 ----------
    async def readiness(self) -> list[ReadinessItem]:
        """作るのに要るもの（声・AI・FFmpeg・Blender）があるか。"""
        c = self.c
        out: list[ReadinessItem] = []
        n = 0
        for st in await c.voice.engines(refresh=True):
            if st.available:
                try:
                    n += len(await c.voice.speakers(st.engine_id))
                except Exception:  # noqa: BLE001 - 話者が取れないエンジンは数えない
                    continue
        out.append(
            ReadinessItem(
                key="voice",
                label="音声（VOICEVOX）",
                ok=n > 0,
                required=True,
                detail=f"話者 {n} 人" if n else "動いていません",
            )
        )
        models = [m for m in await c.providers.list_models()]
        out.append(
            ReadinessItem(
                key="llm",
                label="文章を作る AI（ローカル LLM）",
                ok=bool(models),
                required=True,
                detail=f"モデル {len(models)} 個" if models else "モデルが見つかりません",
            )
        )
        out.append(
            ReadinessItem(
                key="ffmpeg", label="FFmpeg（動画の変換）", ok=c.ffmpeg.available, required=True
            )
        )
        out.append(
            ReadinessItem(
                key="blender",
                label="Blender（画面演出）",
                ok=c.blender.available,
                required=False,
                detail=""
                if c.blender.available
                else "無くても作れます（凝った画面演出は付きません）",
            )
        )
        return out

    async def _style_ids(self) -> dict[str, int]:
        """話者名 → ノーマル（無ければ最初の話し声）のスタイル。"""
        sps = []
        for st in await self.c.voice.engines(refresh=False):
            if st.available:
                try:
                    sps += await self.c.voice.speakers(st.engine_id)
                except Exception:  # noqa: BLE001
                    continue
        out: dict[str, int] = {}
        for sp in sps:
            talk = [s for s in sp.styles if s.is_talk]
            if talk and sp.name not in out:
                normal = next((s for s in talk if s.name == "ノーマル"), talk[0])
                out[sp.name] = normal.id
        return out

    def _pack_for(self, name: str) -> str | None:
        for p in self.c.characters.packs():
            if name and (name in str(p.get("name", "")) or name in p["id"]):
                return str(p["id"])
        return None

    # ---------- 1. 構成 ----------
    async def plan(self, req: BuildRequest) -> BuildPlan:
        c = self.c
        rec_path = None
        rec_sec = None
        if req.screen_recording.strip():
            rec_path = self._allowed_file(req.screen_recording.strip(), "画面録画")
            info = await c.ffmpeg.probe(rec_path)
            if not info.has_video:
                raise BuildError("画面録画のファイルに映像がありません")
            rec_sec = info.duration_sec
        cam_path = None
        if req.facecam.strip():
            if rec_path is None:
                raise BuildError("顔の映像（ワイプ）は、ゲームの画面録画と一緒に指定してください")
            cam_path = self._allowed_file(req.facecam.strip(), "顔の映像")
            if not (await c.ffmpeg.probe(cam_path)).has_video:
                raise BuildError("顔の映像のファイルに映像がありません")
        music_path = None
        music_sec = None
        if req.music.strip():
            music_path = self._allowed_file(req.music.strip(), "曲")
            info = await c.ffmpeg.probe(music_path)
            if not info.has_audio:
                raise BuildError("曲のファイルに音声がありません")
            music_sec = info.duration_sec
        slides_path = None
        if req.slides.strip():
            slides_path = self._allowed_file(req.slides.strip(), "スライド")
            if slides_path.suffix.lower() != ".pdf":
                raise BuildError("スライドは PDF のファイルを指定してください")
        vertical = {"auto": None, "landscape": False, "vertical": True}[req.orientation]
        cls = c.design.classify(
            ClassifyRequest(text=req.instruction, has_script=False, vertical=vertical)
        )
        purpose = cls.purpose
        if rec_path is not None and purpose != Purpose.GAME:  # 画面録画がある = 操作説明
            purpose = Purpose.TUTORIAL  # ただしゲーム実況（プレイ録画）はゲーム実況のまま
        if slides_path is not None and music_path is None:  # スライドがある = 講義
            purpose = Purpose.LECTURE
            vertical = False
        if music_path is not None and purpose not in NEEDS_MUSIC:  # 曲がある = MV
            purpose = Purpose.LYRIC if req.lyrics.strip() else Purpose.MV
        prof = PROFILES[purpose]
        portrait = vertical if vertical is not None else prof.orientation == "vertical"
        w, h = (1080, 1920) if portrait else (1920, 1080)
        notes: list[str] = []
        if cls.note:
            notes.append(cls.note)
        dur = req.duration_sec or (45 if purpose in SHORT_PURPOSES else 120)
        if purpose == Purpose.LECTURE and dur > sl.SPLIT_SEC:
            notes.append(
                f"講義が {dur // 60} 分あります。6 分を超えると集中が続きにくいので、"
                f"{-(-dur // sl.SPLIT_SEC)} 本に分けることを勧めます"
            )
        if music_sec:  # 曲の長さで決まる
            dur = int(min(1800, max(15, round(music_sec))))
        if rec_sec and not req.duration_sec:  # 録画の長さに合わせて話す
            dur = int(min(1800, max(15, round(rec_sec))))
        styles = await self._style_ids()
        duo = bool(req.play_lines)  # ゲーム実況の掛け合い（1 人目のセリフは遊んだ AI が書く）
        if duo:
            purpose = Purpose.GAME
            prof = PROFILES[purpose]
        cast = [(n, "話し手") for n in req.characters] or CAST.get(purpose, _NARRATOR)
        if music_path is not None:  # 曲から作るときは話し手はいない
            cast = []
        chars: list[BuildCharacter] = []
        if duo:
            for i, (label, role, voice0) in enumerate(GAME_DUO):
                voice = req.characters[i] if i < len(req.characters) else voice0
                label = req.cast[i] if i < len(req.cast) and req.cast[i].strip() else label
                sid = styles.get(voice)
                if styles and sid is None:
                    notes.append(f"「{voice}」の声が見つからないため、使える声で代わりに作ります")
                    voice, sid = list(styles.items())[i % len(styles)]
                chars.append(
                    BuildCharacter(
                        name=label, role=role, voice_style_id=sid, tachie_pack=self._pack_for(voice)
                    )
                )
            if len({x.name for x in chars}) < 2:
                raise BuildError("掛け合いの 2 人の名前が同じです")
        for name, role in [] if duo else cast:
            sid = styles.get(name)
            if styles and sid is None:
                notes.append(f"「{name}」の声が見つからないため、使える声で代わりに作ります")
                name, sid = next(iter(styles.items()))
            chars.append(
                BuildCharacter(
                    name=name, role=role, voice_style_id=sid, tachie_pack=self._pack_for(name)
                )
            )
        # 同じ話者が 2 回にならないように
        uniq: dict[str, BuildCharacter] = {}
        for x in chars:
            uniq.setdefault(x.name, x)
        chars = list(uniq.values())
        title = _title(req.instruction)
        if music_path is not None:  # 曲から作るときは、指示の文ではなく曲の名前を題名にする
            title = _song_title(req.instruction, music_path)
        async with c.db.session_factory() as s:
            p = Project(name=title, width=w, height=h, fps_num=30, fps_den=1)
            s.add(p)
            await s.flush()
            pid = p.id
            await s.commit()
        await c.design.set(pid, ProjectDesign(purpose=purpose))
        if req.lyric_direction:  # 統合版 U1
            await c.design.set(pid, ProjectDesign(lyric_direction=req.lyric_direction))
        if music_path is not None:
            await c.timeline.import_asset(
                AssetImportRequest(
                    project_id=pid, path=str(music_path), name=f"{MUSIC_NAME}{music_path.name}"
                )
            )
            if not req.lyrics.strip():
                notes.append(
                    "歌詞が無いので、歌声を文字起こしして字幕にします（聞き取りの誤りが出ることがあります）"
                )
        if music_path is not None and req.image.strip():  # 統合版 V1: MV のショットの絵
            img_path = self._allowed_file(req.image.strip(), "絵")
            await c.timeline.import_asset(
                AssetImportRequest(
                    project_id=pid, path=str(img_path), name=f"{MV_IMAGE_NAME}{img_path.name}"
                )
            )
            notes.append("絵から 8 種のショットを作り、曲の拍に合わせて切り替えます")
        elif music_path is not None and req.placeholder:  # 統合版 U8: 仮の絵
            ph = self._placeholder_image(music_path.name)
            if ph is not None:
                await c.timeline.import_asset(
                    AssetImportRequest(
                        project_id=pid, path=str(ph), name=f"{MV_IMAGE_NAME}仮の絵 {ph.name}"
                    )
                )
                notes.append(
                    f"絵が無いので、仮の絵（{ph.name}）でショットを作ります。"
                    "本番では使いたい絵を指定してください"
                )
        reference = req.lyrics if music_path is not None else req.reference
        if slides_path is not None:
            text, snotes = await self._import_slides(pid, slides_path, w / h)
            notes += snotes
            if text:
                reference = (
                    "スライドの順に 1 枚ずつ説明する。スライドの文字:\n" + text + "\n\n" + reference
                )[:20000]
        if cam_path is not None:
            await c.timeline.import_asset(
                AssetImportRequest(
                    project_id=pid, path=str(cam_path), name=f"{FACECAM_NAME}{cam_path.name}"
                )
            )
        if rec_path is not None:
            await c.timeline.import_asset(
                AssetImportRequest(
                    project_id=pid, path=str(rec_path), name=f"{RECORDING_NAME}{rec_path.name}"
                )
            )
            try:
                await c.llm.resolve(TaskType.IMAGE_UNDERSTANDING)
                notes.append(
                    "録画の場面を画像の分かる AI に読ませて台本の参考にします。"
                    "作りたいものの文章に操作の手順も書くと、さらに合います"
                )
            except Exception:  # noqa: BLE001 - 画像の分かるモデルが無い
                notes.append(
                    "録画の中身は AI には見えません。作りたいものの文章に操作の手順を書くと、"
                    "台本が録画に合います"
                )
        detail = await c.studio.create(
            ScriptStudioCreate(
                project_id=pid,
                title=title,
                # 曲から作るときは AI に台本を書かせない（歌詞の台本は文字起こしで作る）
                mode=ScriptMode.MANUAL
                if music_path is not None
                else (ScriptMode.USER_ONE_SIDE if duo else ScriptMode.FULL_AI),
                settings=ScriptSettings(
                    topic=req.instruction[:500],
                    purpose=prof.label,
                    target_duration_sec=dur,
                    # 統合版 S5: 縦型は最初の 1 文で結論（いちばん伝えたいこと）を言う
                    structure=purpose_structure(prof.purpose, portrait)
                    if music_path is None
                    else "",
                    # 曲から作るときは、貼られた歌詞を台本の参考に置いて書き出しまで使う
                    reference=reference,
                ),
                characters=[
                    CharacterIn(
                        name=x.name,
                        role=x.role,
                        voice=VoiceSettings(style_id=x.voice_style_id),
                        tachie_pack=x.tachie_pack,
                        is_user=duo and k == 0,
                    )
                    for k, x in enumerate(chars)
                ],
            )
        )
        if duo:  # 遊んだ AI のセリフを、言う時刻つきで固定して入れる
            lines = sorted(req.play_lines, key=lambda x: x.at_sec)
            await c.studio.add_dialogue(
                detail.id,
                DialogueInput(
                    lines=[
                        DialogueLine(text=x.text, speaker=chars[0].name, at_sec=x.at_sec)
                        for x in lines
                    ]
                ),
            )
            notes.append(
                f"{chars[0].name}のセリフ {len(lines)} 件を録画の時刻に合わせて置き、"
                f"{chars[1].name}のセリフを AI が作ります"
            )
        return await self._view(pid, detail.id, notes=notes, uncertain=cls.needs_confirmation)

    async def _view(
        self, project_id: str, script_id: str, notes: list[str], uncertain: bool = False
    ) -> BuildPlan:
        """プロジェクトと台本の今の設定から「構成」を作る（構成の確認で見せる内容）。"""
        c = self.c
        async with c.db.session_factory() as s:
            p = await s.get(Project, project_id)
            if p is None:
                raise BuildError("プロジェクトが見つかりません")
            w, h, title = p.width, p.height, p.name
        design = await c.design.get(project_id)
        purpose = Purpose(design.purpose) if design.purpose else Purpose.EXPLAINER
        prof = PROFILES[purpose]
        detail = await c.studio.detail(script_id)
        if detail.project_id != project_id:
            raise BuildError("台本がこのプロジェクトのものではありません")
        chars = [
            BuildCharacter(
                name=ch.name,
                role=ch.role,
                voice_style_id=ch.voice.style_id,
                tachie_pack=ch.tachie_pack,
            )
            for ch in detail.characters
        ]
        notes = list(notes)
        rec = await self._recording(project_id)
        ready = await self.readiness()
        can_run = all(r.ok for r in ready if r.required)
        song = await self._asset(project_id, MUSIC_NAME, AssetKind.AUDIO)
        if song is not None:
            # 曲から作るときは声を使わないので、VOICEVOX は無くてよい
            for r in ready:
                if r.key == "voice":
                    r.required = False
            can_run = all(r.ok for r in ready if r.required)
            t = c.transcriber.status()
            ready.append(
                ReadinessItem(
                    key="transcribe",
                    label="文字起こし（歌の時刻を取る）",
                    ok=bool(t.get("available")),
                    required=True,
                    detail="" if t.get("available") else "faster-whisper のモデルが見つかりません",
                )
            )
            can_run = can_run and bool(t.get("available"))
            steps = [label for _, label in MV_STEPS]
        else:
            if purpose in NEEDS_MUSIC:
                notes.append(
                    f"「{prof.label}」は曲のファイルが要ります。「曲」の欄で曲を選んでください"
                )
                can_run = False
            steps = [
                label
                for key, label in STEPS
                if (key != "tachie" or any(x.tachie_pack for x in chars))
                and (key != "recording" or rec is not None)
            ]
        packs = [
            PackChoice(id=str(x["id"]), name=str(x.get("name") or x["id"]))
            for x in c.characters.packs()
        ]
        return BuildPlan(
            project_id=project_id,
            script_id=script_id,
            title=title,
            purpose=purpose.value,
            purpose_label=prof.label,
            purpose_uncertain=uncertain,
            width=w,
            height=h,
            duration_sec=detail.settings.target_duration_sec or 0,
            characters=chars,
            steps=steps,
            notes=notes,
            readiness=ready,
            can_run=can_run,
            packs=packs,
        )

    async def change(self, project_id: str, req: BuildChange) -> BuildPlan:
        """構成の確認で、用途・長さ・立ち絵を変える（まだ何も作らない）。"""
        c = self.c
        detail = await c.studio.detail(req.script_id)
        if detail.project_id != project_id:
            raise BuildError("台本がこのプロジェクトのものではありません")
        notes: list[str] = []
        settings = detail.settings.model_copy()
        if req.purpose is not None:
            try:
                purpose = Purpose(req.purpose)
            except ValueError as exc:
                raise BuildError(f"知らない用途です: {req.purpose}") from exc
            prof = PROFILES[purpose]
            await c.design.set(project_id, ProjectDesign(purpose=purpose))
            settings.purpose = prof.label
            # 縦長が決まりの用途に変えたら縦長に、縦長から横長の用途に変えたら横長にする
            async with c.db.session_factory() as s:
                p = await s.get(Project, project_id)
                assert p is not None
                portrait = p.height > p.width
                if prof.orientation == "vertical" and not portrait:
                    p.width, p.height = 1080, 1920
                elif prof.orientation == "landscape" and portrait:
                    p.width, p.height = 1920, 1080
                portrait = p.height > p.width
                await s.commit()
            # 統合版 S5: 縦型は最初の 1 文で結論を言う（向きが変わったら付け外しする）
            if all(x in AUTO_STRUCTURES for x in settings.structure.split("\n") if x):
                settings.structure = purpose_structure(prof.purpose, portrait)
            notes.append(f"用途を「{prof.label}」にしました")
        if req.duration_sec is not None:
            settings.target_duration_sec = req.duration_sec
        chars = None
        if req.tachie is not None:
            known = {str(x["id"]) for x in c.characters.packs()}
            chars = []
            for ch in detail.characters:
                ci = CharacterIn.model_validate(ch.model_dump())
                if ch.name in req.tachie:
                    pack = req.tachie[ch.name] or None
                    if pack is not None and pack not in known:
                        raise BuildError(f"立ち絵「{pack}」が見つかりません")
                    ci.tachie_pack = pack
                chars.append(ci)
        await c.studio.update(req.script_id, ScriptUpdate(settings=settings, characters=chars))
        return await self._view(project_id, req.script_id, notes=notes)

    async def _base_subtitle_ratio(self, project_id: str) -> float:
        from video_automation.schemas.subtitle import SubtitleStyle
        from video_automation.services.subtitle.subtitle_service import style_from_profile

        prof = await self.c.design.profile_for(project_id)
        return (style_from_profile(prof) if prof else SubtitleStyle()).font_size_ratio

    async def _resize_text(self, project_id: str, k: float) -> str:
        """字幕の文字の大きさを k 倍にして保存する。

        カード・テロップは _text_scale で同じ割合にする。"""
        c = self.c
        st = await c.subtitles.project_style(project_id)
        base = await self._base_subtitle_ratio(project_id)
        ratio = min(base * 1.5, max(base * 0.7, st.font_size_ratio * k))
        size = None if st.font_size is None else max(8, min(400, round(st.font_size * k)))
        await c.subtitles.set_project_style(
            project_id,
            st.model_copy(update={"font_size_ratio": round(ratio, 4), "font_size": size}),
        )
        word = "大きく" if k > 1 else "小さく"
        return f"文字を{word}しました（字幕・カード・テロップ、元の {round(ratio / base * 100)}%）"

    async def _text_scale(self, project_id: str) -> float:
        """カード・テロップの文字の倍率（字幕を手直しで変えた割合に合わせる）。"""
        try:
            st = await self.c.subtitles.project_style(project_id)
            base = await self._base_subtitle_ratio(project_id)
            return round(min(1.5, max(0.7, st.font_size_ratio / base)), 3)
        except Exception:  # noqa: BLE001 - 分からなければ元の大きさ
            return 1.0

    async def _fit_voice_length(self, sid: str, notes: list[str]) -> bool:
        """声が目標の長さを大きく超えたら、話す速さを上げて作り直す（最大 1.2 倍）。

        台本の長さは文字数から見積もるが、ささやき等のスタイルは話すのが遅く、実機で
        50 秒の指定が 60 秒になっていた。戻り値は作り直したか。"""
        from sqlalchemy import select

        from video_automation.database.models import ScriptCharacter, ScriptSegment

        c = self.c
        d = await c.studio.detail(sid)
        target = d.settings.target_duration_sec
        ends = [x.end_frame for x in d.segments if x.end_frame]
        if not target or not ends:
            return False
        actual = max(ends) / 30
        if actual <= target * FIT_TOLERANCE:
            return False
        # 間（発話の間）は速さで縮まないので、話している時間だけを縮める
        talk = sum((x.end_frame or 0) - (x.start_frame or 0) for x in d.segments) / 30
        gaps = max(0.0, actual - talk)
        room = max(target - gaps, talk / MAX_SPEED_UP)
        speed = round(min(MAX_SPEED_UP, talk / room), 3)
        async with c.db.session_factory() as s:
            chars = (
                await s.execute(select(ScriptCharacter).where(ScriptCharacter.script_id == sid))
            ).scalars()
            for ch in chars:
                v = dict(ch.voice or {})
                v["speed_scale"] = round(min(2.0, float(v.get("speed_scale") or 1.0) * speed), 3)
                ch.voice = v
            segs = (
                await s.execute(select(ScriptSegment).where(ScriptSegment.script_id == sid))
            ).scalars()
            for x in segs:
                x.audio_file_id, x.start_frame, x.end_frame = None, None, None
            await s.commit()
        notes.append(
            f"声が目標より長かった（{round(actual)} 秒 / 目標 {round(target)} 秒）ため、"
            f"話す速さを {speed} 倍にして作り直しました"
        )
        return True

    async def _script_to_tachie(
        self,
        sid: str,
        has_tachie: bool,
        redo: str,
        at: Any,
        notes: list[str],
        model_key: str | None = None,
    ) -> None:
        """台本 → 音声 → 字幕 → 立ち絵（redo は直したいこと。台本の生成に渡す）。"""
        c = self.c
        await at("script")
        if redo:
            # 作り直すときは、前の台本を見せて直したいことに沿って書き直させる
            old = await c.studio.detail(sid)
            if old.segments:
                tail = f"\n\nこの台本の直したいこと: {redo[:600]}"
                prev = "\n".join(f"{x.speaker}: {x.text}" for x in old.segments)
                prev = prev[: max(0, 2000 - len(tail) - 12)]
                redo = f"前に作った台本:\n{prev}{tail}"
        try:
            d = await c.studio.generate(
                sid, ScriptGenerateRequest(instruction=redo[:2000], model_key=model_key)
            )
        except Exception as exc:  # noqa: BLE001
            raise BuildError(f"台本を作れませんでした: {type(exc).__name__}") from exc
        notes += list(getattr(d, "notes", []) or [])[:5]
        # 解説の内容の誤り（「コンセントを抜くと電気が貯まる」など）を直してから声にする
        fact = await c.studio.fact_check(sid)
        notes += fact
        if fact:  # 直したセリフの前後で反応が合わなくなる（「半分！？」が残る）ので推敲し直す
            notes += await c.studio.polish(sid)

        await at("voice")
        # 声にする前に、読み間違い（英語・専門用語など）を AI に確かめさせて読みのルールにする
        try:
            chk = await c.readings.check_script(sid, ReadingCheckRequest(apply=True))
            if chk.issues:
                words = "、".join(f"{x.surface}→{x.reading}" for x in chk.issues[:5])
                notes.append(f"読み間違いを {len(chk.issues)} 語直しました: {words}")
        except Exception as exc:  # noqa: BLE001 - 読みの確認ができなくても声は作る
            log.info("build_reading_check_skipped", error=type(exc).__name__)

        async def vp(v: float) -> None:
            await at("voice", v)

        try:
            await c.subtitles.generate_voices(sid, False, vp)
        except Exception as exc:  # noqa: BLE001
            raise BuildError(
                f"音声を作れませんでした（VOICEVOX を確かめてください）: {type(exc).__name__}"
            ) from exc

        await at("subtitles")
        await c.subtitles.generate(SubtitleGenerateRequest(script_id=sid))
        await c.subtitles.synchronize(SubtitleSyncRequest(script_id=sid))
        if await self._fit_voice_length(sid, notes):
            await c.subtitles.generate_voices(sid, False, vp)
            await c.subtitles.generate(SubtitleGenerateRequest(script_id=sid))
            await c.subtitles.synchronize(SubtitleSyncRequest(script_id=sid))

        if has_tachie:
            await at("tachie")
            try:
                await c.characters.apply(sid)
            except Exception as exc:  # noqa: BLE001 - 立ち絵なしで続ける
                notes.append(
                    f"立ち絵を置けませんでした（{type(exc).__name__}）。立ち絵なしで続けます"
                )

    async def _read_recording(self, sid: str, rec: AssetRead, at: Any) -> list[str]:
        """画面録画の場面を画像の分かる AI に読ませ、台本の参考資料に足す。"""
        c = self.c
        await at("script")
        d0 = await c.studio.detail(sid)
        if recording_reader.PLAY_LOG_HEAD in (d0.settings.reference or ""):
            # 遊んだ AI のプレイ記録がある: 画面から読み取るより正確なので、それを使う
            return ["プレイ記録を台本の参考にしました（画面からの読み取りは省きました）"]
        game = await self._is_game(sid)
        try:
            text = await recording_reader.describe(
                c.llm, c.ffmpeg, c.settings.data_dir / rec.path, rec.duration_sec or 0.0, game=game
            )
        except Exception as exc:  # noqa: BLE001 - 画像の分かるモデルが無くても台本は作る
            log.info("recording_read_skipped", error=type(exc).__name__, detail=str(exc)[-300:])
            return ["画面録画を読めるAIが無いため、録画の中身は台本に使っていません"]
        if not text:
            return []
        d = await c.studio.detail(sid)
        head = "画面録画の内容（AIが画面から読み取った場面。時刻は録画の頭から）:\n"
        old = d.settings.reference
        if head in old:  # 作り直すときは前に読み取った分を置き換える
            old = old.split(head)[0].rstrip()
        ref = (old + "\n\n" if old else "") + head + text
        await c.studio.update(
            sid, ScriptUpdate(settings=d.settings.model_copy(update={"reference": ref[:20000]}))
        )
        return [f"画面録画の {text.count(chr(10)) + 1} 場面を読み取り、台本の参考にしました"]

    async def _anchor_lag(self, project_id: str, sid: str, limit_sec: float = 3.0) -> list[str]:
        """時刻つきのセリフが、置きたい時刻より大きく遅れていないか（遅れは前のセリフが長いため）。"""
        d = await self.c.studio.detail(sid)
        tl = await self.c.timeline.get_timeline(project_id)
        fps = tl.fps_num / tl.fps_den
        late = [
            (x.start_frame / fps - x.anchor_sec, x.text)
            for x in d.segments
            if x.anchor_sec is not None
            and x.start_frame is not None
            and x.start_frame / fps - x.anchor_sec > limit_sec
        ]
        if not late:
            return []
        worst = max(late)
        return [
            f"時刻より {limit_sec:.0f} 秒以上遅れたセリフが {len(late)} 件あります"
            f"（最大 {worst[0]:.1f} 秒: 「{worst[1][:20]}」）。前のセリフを短くすると合います"
        ]

    async def _is_game(self, sid: str) -> bool:
        """台本のプロジェクトの用途がゲーム実況か。"""
        try:
            pid = (await self.c.studio.detail(sid)).project_id
            design = await self.c.design.get(pid)
            return design.purpose is not None and str(design.purpose) == Purpose.GAME.value
        except Exception:  # noqa: BLE001 - 分からなければ操作説明として扱う
            return False

    PLACEHOLDER_EXT = (".png", ".jpg", ".jpeg")

    def _placeholder_image(self, key: str) -> Path | None:
        """統合版 U8: data/placeholders に利用者が置いた仮の絵から 1 枚を決める。

        曲の名前で選ぶので、同じ曲なら同じ絵になる（作り直しても絵が変わらない）。
        絵は自動では取ってこない（素材サイトの規約の確認は利用者がする）。"""
        root = Path(self.c.settings.data_dir) / "placeholders"
        if not root.is_dir():
            return None
        files = sorted(
            p for p in root.iterdir() if p.is_file() and p.suffix.lower() in self.PLACEHOLDER_EXT
        )
        if not files:
            return None
        h = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16)
        return files[h % len(files)]

    def _allowed_file(self, path: str, label: str) -> Path:
        """利用者が渡したファイル（取り込みを許可したフォルダの中だけ）。"""
        p = Path(path)
        if not p.is_absolute() or not self.c.timeline._allowed(p):
            raise BuildError(
                f"{label}は、取り込みを許可したフォルダ（ビデオ・ミュージック・デスクトップ・"
                "ダウンロード等）に置いてください"
            )
        if not p.is_file():
            raise BuildError(f"{label}のファイルが見つかりません")
        return p

    async def _recording(self, project_id: str) -> AssetRead | None:
        """構成で取り込んだ画面録画（無ければ None）。"""
        return await self._asset(project_id, RECORDING_NAME, AssetKind.VIDEO)

    async def _asset(self, project_id: str, prefix: str, kind: AssetKind) -> AssetRead | None:
        """構成で取り込んだ素材（名前の頭で見分ける。無ければ None）。"""
        tl = await self.c.timeline.get_timeline(project_id)
        hits = [
            a
            for a in tl.assets
            if a.kind == kind and a.source == "import" and a.name.startswith(prefix)
        ]
        return hits[-1] if hits else None

    async def _place_recording(
        self, project_id: str, sid: str, rec: AssetRead, at: Any
    ) -> list[str]:
        """画面録画を操作箇所へ寄った映像にして、中央の枠（左右は立ち絵の余白）に置く。"""
        c = self.c
        notes: list[str] = []

        async def zp(v: float) -> None:
            await at("recording", v * 0.9)

        game = await self._is_game(sid)
        if game:  # ゲーム実況: プレイ画面全体を見せる（操作箇所へは寄らない）
            zoomed = rec
        else:
            try:
                zoomed = await c.screencast.zoom(project_id, rec.id, None, 1.8, zp)
                rep = c.screencast.reports.get(zoomed.id, {})
                notes += list(rep.get("notes", []))
                if rep.get("pii"):
                    notes.append(
                        f"録画の個人情報らしい文字を {len(rep['pii'])} か所ぼかしました"
                        "（録画の素材の .finish.json で確かめてください）"
                    )
            except Exception as exc:  # noqa: BLE001 - 寄れなくても録画はそのまま置く
                notes.append(f"操作箇所へのズームを作れませんでした（{type(exc).__name__}）")
                zoomed = rec
        tl = await c.timeline.get_timeline(project_id)
        p = tl
        fps = tl.fps_num / tl.fps_den
        # 話（音声）の長さ。録画はこの長さに合わせる（足りなければ最後の画面を止める）
        ends = [x.end_frame for x in (await c.studio.detail(sid)).segments if x.end_frame]
        total = max(ends) if ends else tl.duration_frames
        cmds: list[Any] = []
        track = next((t for t in tl.tracks if t.name == RECORDING_TRACK), None)
        if track is not None:
            cmds += [DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == track.id]
            tid = track.id
        else:
            tid = str(uuid4())
            cmds.append(AddTrack(kind=TrackKind.VIDEO, name=RECORDING_TRACK, track_id=tid))
        tachie = any(t.name == "立ち絵" for t in tl.tracks)
        transform = native_transform(
            zoomed.width or p.width,
            zoomed.height or p.height,
            p.width,
            p.height,
            cx=0.5,
            cy=REC_CY,
            max_w=REC_W_TACHIE if tachie else REC_W,
            max_h=REC_H,
        )
        rec_frames = max(1, int((zoomed.duration_sec or 0) * fps))
        length = min(rec_frames, total) if total else rec_frames
        cmds.append(
            AddClip(
                track_id=tid,
                asset_id=zoomed.id,
                start_frame=0,
                duration_frames=length,
                transform=transform,
                # 録画の音は使わない（話すのはキャラクター）。ゲーム実況はゲームの音を小さく残す
                volume=GAME_REC_VOLUME if game else 0.0,
                fade_in_frames=min(10, length // 4),
            )
        )
        if total > rec_frames + round(fps * 0.5):
            # 話の方が長いときは、録画の最後の画面を止めて出し続ける
            still = await self._last_frame(project_id, zoomed)
            cmds.append(
                AddClip(
                    track_id=tid,
                    asset_id=still.id,
                    start_frame=rec_frames,
                    duration_frames=total - rec_frames,
                    transform=native_transform(
                        still.width or p.width,
                        still.height or p.height,
                        p.width,
                        p.height,
                        cx=0.5,
                        cy=REC_CY,
                        max_w=REC_W_TACHIE if tachie else REC_W,
                        max_h=REC_H,
                    ),
                )
            )
            notes.append(
                f"話の方が録画より {round((total - rec_frames) / fps)} 秒長いため、"
                "録画の最後の画面を止めて出しています"
            )
        elif rec_frames > total + round(fps * 0.5):
            notes.append(
                f"録画の最後の {round((rec_frames - total) / fps)} 秒は、"
                "話が終わったため使っていません"
            )
        if not game and zoomed is not rec:
            n_marks = await self._recording_marks(
                project_id, sid, zoomed, transform, length, fps, tl, cmds
            )
            if n_marks:
                notes.append(
                    f"録画の中の話している語を {n_marks} か所、丸（小さい字は虫めがね）で示しました"
                )
        await c.timeline.submit(
            project_id,
            CommandRequest(command=Batch(commands=cmds), description="画面録画を置く"),
        )
        return notes

    async def _raise_track(self, project_id: str, name: str) -> None:
        """名前のトラックを最も上に描く（演出が後からトラックを足しても隠れないように）。"""
        tl = await self.c.timeline.get_timeline(project_id)
        t = next((x for x in tl.tracks if x.name == name), None)
        if t is None:
            return
        await self.c.timeline.submit(
            project_id,
            CommandRequest(
                command=MoveTrack(track_id=t.id, index=1000),
                origin=CommandOrigin.USER,
                description=f"{name}を最も上に",
            ),
        )

    async def _place_wipe(self, project_id: str, sid: str, rec: AssetRead) -> list[str]:
        """統合版 P6: ゲーム実況で顔の映像があれば、録画の HUD を避けた隅にワイプで置く
        （画面の面積の 18%・白の縁・入り出 12f）。立ち絵があるときは、立ち絵の無い上の隅から
        選ぶ。"""
        from video_automation.services.rendering import wipe

        c = self.c
        cam = await self._asset(project_id, FACECAM_NAME, AssetKind.VIDEO)
        if cam is None or not await self._is_game(sid):
            return []
        tl = await c.timeline.get_timeline(project_id)
        w, h, fps = tl.width, tl.height, tl.fps_num / tl.fps_den
        src = c.settings.data_dir / rec.path
        dur = max(0.5, float(rec.duration_sec or 1.0))
        raw = await c.ffmpeg.run_capture(
            ["-i", str(src), "-vf",
             f"fps={wipe.SAMPLES / dur:.5f},scale={wipe.SAMPLE_W}:{wipe.SAMPLE_H},format=gray",
             "-frames:v", str(wipe.SAMPLES), "-f", "rawvideo", "-"]
        )  # fmt: skip
        frames = wipe.frames_from_raw(raw)
        tachie = any(t.name == "立ち絵" for t in tl.tracks)
        allowed = ("top_right", "top_left") if tachie else wipe.CORNERS
        if len(frames) >= 2:
            corner, scores = wipe.pick_corner(wipe.hud_mask(frames), allowed)
        else:
            corner, scores = allowed[0], {}
        x0, y0, x1, y1 = wipe.box(corner, w, h)
        bw, bh = x1 - x0, y1 - y0
        b = max(2, round(h * wipe.BORDER))
        ends = [x.end_frame for x in (await c.studio.detail(sid)).segments if x.end_frame]
        total = max(ends) if ends else tl.duration_frames
        out = (
            c.settings.data_dir / "projects" / project_id / "assets" / f"wipe_{uuid4().hex[:8]}.mp4"
        )
        await c.ffmpeg.run(
            ["-y", "-i", str(c.settings.data_dir / cam.path), "-t", f"{total / fps:.3f}", "-vf",
             f"scale={bw - 2 * b}:{bh - 2 * b}:force_original_aspect_ratio=increase,"
             f"crop={bw - 2 * b}:{bh - 2 * b},pad={bw}:{bh}:{b}:{b}:white,fps={fps:.5f},"
             "format=yuv420p", "-an", "-c:v", "libx264", "-preset", "veryfast", str(out)]
        )  # fmt: skip
        a = AssetRead.model_validate(
            await c.timeline.register_generated(
                project_id, out, AssetKind.VIDEO, "build", f"ワイプ（P6・{corner}）"
            )
        )
        n = max(1, min(total, int(float(a.duration_sec or 0) * fps) or total))
        cmds: list[Any] = []
        track = next((t for t in tl.tracks if t.name == WIPE_TRACK), None)
        if track is not None:
            cmds += [DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == track.id]
            tid = track.id
        else:
            tid = str(uuid4())
            cmds.append(AddTrack(kind=TrackKind.VIDEO, name=WIPE_TRACK, track_id=tid))
        cmds.append(
            AddClip(
                track_id=tid, asset_id=a.id, start_frame=0, duration_frames=n,
                transform=native_transform(bw, bh, w, h, cx=(x0 + x1) / 2 / w,
                                           cy=(y0 + y1) / 2 / h, max_h=1.0, max_w=1.0),
                volume=0.0, fade_in_frames=wipe.FADE, fade_out_frames=wipe.FADE,
            )
        )  # fmt: skip
        await c.timeline.submit(
            project_id, CommandRequest(command=Batch(commands=cmds), description="ワイプを置く")
        )
        names = {
            "bottom_right": "右下",
            "bottom_left": "左下",
            "top_right": "右上",
            "top_left": "左上",
        }
        hud = f"（HUD との重なり {scores.get(corner, 0.0):.0%}）" if scores else ""
        return [f"顔の映像を{names[corner]}のワイプ（画面の 18%）に置きました{hud}"]

    async def _recording_marks(
        self,
        project_id: str,
        sid: str,
        zoomed: AssetRead,
        transform: ClipTransform,
        length: int,
        fps: float,
        tl: Any,
        cmds: list[Any],
    ) -> int:
        """統合版 N12・N13（画面録画）: 各文の頭から 6f 後の録画の画面を OCR し、その文が
        話している語に手描きの丸（語が小さければ虫めがね）を、録画と同じ置き方で重ねる。
        録画は寄った後の映像なので、カメラは付けない（文の途中で寄りが変わると印がずれる）。"""
        from PIL import Image

        from video_automation.integrations.ocr.engine import OcrUnavailable
        from video_automation.services.rendering import n_marks as nm

        c = self.c
        ocr = c.screencast.ocr
        if ocr is None or not zoomed.width or not zoomed.height:
            return 0
        segs = [
            x
            for x in (await c.studio.detail(sid)).segments
            if x.start_frame is not None and x.end_frame and x.start_frame + nm.N12_DRAW < length
        ][:MAX_REC_MARKS]
        if not segs:
            return 0
        work = c.settings.data_dir / "projects" / project_id / "assets" / f"recmk_{uuid4().hex[:8]}"
        work.mkdir(parents=True, exist_ok=True)
        src = c.settings.data_dir / zoomed.path
        imgs: list[Path] = []
        for i, x in enumerate(segs):
            out = work / f"f{i:03d}.png"
            t = ((x.start_frame or 0) + 6) / fps
            await c.ffmpeg.run(
                ["-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", str(out)]
            )  # fmt: skip
            imgs.append(out)
        try:
            results = await asyncio.to_thread(ocr.read, imgs)
        except OcrUnavailable:
            await asyncio.to_thread(shutil.rmtree, work, True)
            return 0
        prof = await c.design.profile_for(project_id) if c.design else None
        accent = (prof or PROFILES[Purpose.TUTORIAL]).palette.accent
        cw, ch = tl.width, tl.height
        zw, zh = zoomed.width, zoomed.height
        k_disp = min(cw / zw, ch / zh) * (transform.scale or 1.0)  # 録画の画素 → 出力の画素
        scale = min(1.0, 960 / zw)
        ow, oh = max(2, round(zw * scale)), max(2, round(zh * scale))
        px_per_out = scale / k_disp
        mark_t = next((t for t in tl.tracks if t.name == MARK_TRACK), None)
        if mark_t is not None:
            cmds.extend(DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == mark_t.id)
            mark_tid = mark_t.id
        else:
            mark_tid = str(uuid4())
            cmds.append(AddTrack(kind=TrackKind.VIDEO, name=MARK_TRACK, track_id=mark_tid))
        made = 0
        for i, (x, res) in enumerate(zip(segs, results, strict=False)):
            if not res.width or not res.lines:
                continue
            box = sl.word_box(res, sl.tokens(x.text))
            if box is None:
                continue
            f0 = x.start_frame or 0
            n = min(x.end_frame or f0, length) - f0
            if n < nm.N12_DRAW:
                continue
            sx, sy = ow / res.width, oh / res.height
            b = (box[0] * sx, box[1] * sy, box[2] * sx, box[3] * sy)
            if box[3] / res.height * zh * k_disp / ch < nm.N13_MIN:
                frame_im = await asyncio.to_thread(
                    lambda p_=imgs[i]: Image.open(p_).convert("RGBA").resize((ow, oh))
                )
                lens = await asyncio.to_thread(
                    nm.magnifier, frame_im, b, nm.N13_DIAM * ch * px_per_out, px_per_out
                )
                gen = nm.magnifier_frames((ow, oh), lens, b, n)
                name = "虫めがね（N13・録画）"
            else:
                gen = nm.circle_frames((ow, oh), b, n, accent, px_per_out, i)
                name = "手描きの丸（N12・録画）"
            a = await self._frames_mov(project_id, gen, fps, name)
            cmds.append(
                AddClip(track_id=mark_tid, asset_id=a.id, start_frame=f0, duration_frames=n,
                        clip_id=str(uuid4()), transform=transform)
            )  # fmt: skip
            made += 1
        await asyncio.to_thread(shutil.rmtree, work, True)
        return made

    def _slides_dir(self, project_id: str) -> Path:
        return self.c.settings.data_dir / "projects" / project_id / "slides"

    async def _import_slides(
        self, project_id: str, pdf: Path, aspect: float
    ) -> tuple[str, list[str]]:
        """統合版 P3: PDF をページの画像（2倍の解像度）とOCRの結果にし、スライドの文字を返す。"""
        from dataclasses import asdict

        from video_automation.integrations.ocr.engine import OcrUnavailable
        from video_automation.integrations.ocr.pdf import render_pages

        c = self.c
        d = self._slides_dir(project_id)
        try:
            pages = await asyncio.to_thread(render_pages, pdf, d)
        except Exception as exc:  # noqa: BLE001 - 読めない PDF
            raise BuildError(
                f"スライドの PDF を画像にできませんでした: {type(exc).__name__}"
            ) from exc
        for pg in pages:
            await asyncio.to_thread(sl.fit_aspect, pg, aspect)
        notes = [f"スライド {len(pages)} 枚を、話に合わせて切り替えます"]
        ocr = c.screencast.ocr
        results: list[Any] = []
        if ocr is None:
            notes.append("文字の読み取り（OCR）が使えないため、スライドの行へ寄る演出を省きます")
        else:
            try:
                results = await asyncio.to_thread(ocr.read, pages)
            except OcrUnavailable as exc:
                notes.append(f"スライドの文字を読めませんでした（{exc}）。行へ寄る演出を省きます")
        (d / "ocr.json").write_text(
            json.dumps([asdict(r) for r in results], ensure_ascii=False), encoding="utf-8"
        )
        for pg in pages:
            await c.timeline.import_asset(
                AssetImportRequest(
                    project_id=project_id, path=str(pg), name=f"{sl.SLIDE_NAME}{pg.name}"
                )
            )
        text = "\n".join(f"[{i + 1}] {r.text.replace(chr(10), ' ')}" for i, r in enumerate(results))
        return text, notes

    async def _place_slides(self, project_id: str, sid: str) -> list[str]:
        """統合版 P3: スライドを話す順に切り替え、話している行へ寄り、箇条書きを 1 項目ずつ出す。"""
        from video_automation.integrations.ocr.engine import OcrLine, OcrResult, OcrWord

        c = self.c
        tl = await c.timeline.get_timeline(project_id)
        assets = sorted(
            (
                a
                for a in tl.assets
                if a.kind == AssetKind.IMAGE and a.name.startswith(sl.SLIDE_NAME)
            ),
            key=lambda a: a.name,
        )
        if not assets:
            return []
        d = self._slides_dir(project_id)
        try:
            raw = json.loads((d / "ocr.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = []
        pages = [
            OcrResult(
                r["width"], r["height"],
                [OcrLine([OcrWord(**w) for w in ln["words"]]) for ln in r["lines"]],
            )
            for r in raw
        ]  # fmt: skip
        pages += [OcrResult(0, 0) for _ in range(len(assets) - len(pages))]
        segs = [x for x in (await c.studio.detail(sid)).segments if x.start_frame is not None]
        fps = tl.fps_num / tl.fps_den
        total = max((x.end_frame or 0 for x in segs), default=0) or tl.duration_frames
        if not segs or not total:
            return []
        toks = [sl.tokens(x.text) for x in segs]
        frames = [(x.start_frame or 0, x.end_frame or 0) for x in segs]
        page_of = sl.assign(toks, pages[: len(assets)])
        shots = sl.plan_shots(page_of, frames, pages, toks, total)
        cmds: list[Any] = []
        track = next((t for t in tl.tracks if t.name == RECORDING_TRACK), None)
        if track is not None:
            cmds += [DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == track.id]
            tid = track.id
        else:
            # 画面録画と同じトラックに置く（演出は録画と同じく主役を隠さない）
            tid = str(uuid4())
            cmds.append(AddTrack(kind=TrackKind.VIDEO, name=RECORDING_TRACK, track_id=tid))
        # 統合版 N12・N13: 話している語の手描きの丸・虫めがね（スライドの上のトラック）
        mark_t = next((t for t in tl.tracks if t.name == MARK_TRACK), None)
        if mark_t is not None:
            cmds += [DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == mark_t.id]
            mark_tid = mark_t.id
        else:
            mark_tid = str(uuid4())
            cmds.append(AddTrack(kind=TrackKind.VIDEO, name=MARK_TRACK, track_id=mark_tid))
        marks = 0
        # 箇条書きを隠した画像（同じ状態は 1 枚にまとめる）
        made: dict[tuple[int, tuple[int, ...]], AssetRead] = {}
        zooms = 0
        for k, sh in enumerate(shots):
            src = assets[sh.page]
            key = (sh.page, tuple(sh.hide))
            if not sh.hide:
                a = src
            elif key in made:
                a = made[key]
            else:
                path = d / f"p{sh.page + 1:03d}_h{'_'.join(map(str, sh.hide))}.png"
                await asyncio.to_thread(
                    sl.cover, c.settings.data_dir / src.path, path, pages[sh.page].lines, sh.hide
                )
                a = AssetRead.model_validate(
                    await c.timeline.register_generated(
                        project_id, path, AssetKind.IMAGE, "build", f"{src.name}（箇条書きを隠す）"
                    )
                )
                made[key] = a
            cid = str(uuid4())
            cmds.append(
                AddClip(
                    track_id=tid,
                    asset_id=a.id,
                    start_frame=sh.start,
                    duration_frames=sh.end - sh.start,
                    clip_id=cid,
                    transform=ClipTransform(fit=FitMode.FIT),
                    fade_in_frames=min(8, (sh.end - sh.start) // 4) if k == 0 else 0,
                )
            )
            keys = self._slide_keys(sh, frames, toks, page_of, pages[sh.page], fps)
            if keys:
                zooms += 1
                cmds.append(SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=keys)))
            marks += await self._slide_marks(
                project_id, sh, frames, toks, page_of, pages[sh.page], keys, fps,
                c.settings.data_dir / src.path, mark_tid, cmds, (tl.width, tl.height),
            )  # fmt: skip
        await c.timeline.submit(
            project_id,
            CommandRequest(command=Batch(commands=cmds), description="スライドを置く"),
        )
        notes = await self._small_tachie(project_id)
        log.info("slides_placed", shots=len(shots), zooms=zooms, marks=marks)
        return notes

    async def _slide_marks(
        self,
        project_id: str,
        sh: Any,
        frames: list[tuple[int, int]],
        toks: list[set[str]],
        page_of: list[int],
        res: Any,
        keys: list[Keyframe],
        fps: float,
        slide_path: Path,
        tid: str,
        cmds: list[Any],
        canvas: tuple[int, int],
    ) -> int:
        """統合版 N12・N13: その文が話している語を、スライドの上に手描きの丸（小さい語は虫めがね）で
        示す。画はスライドと同じ縦横比で、スライドと同じ置き方・カメラで重ねる（寄っても合う）。"""
        from PIL import Image

        from video_automation.services.animation.motion import _values_at
        from video_automation.services.rendering import n_marks as nm

        if not res.width or not res.lines:
            return 0
        c = self.c
        prof = await c.design.profile_for(project_id) if c.design else None
        accent = (prof or PROFILES[Purpose.LECTURE]).palette.accent
        cw, ch = canvas
        k_fit = min(cw / res.width, ch / res.height)  # スライドの画素 → 出力の画素
        scale = min(1.0, 960 / res.width)  # 重ねる画は幅 960px までに縮める
        ow, oh = max(2, round(res.width * scale)), max(2, round(res.height * scale))
        px_per_out = scale / k_fit
        cam = ClipAnimation(keyframes=keys) if keys else None
        ffps = Fraction(fps).limit_denominator(1001)
        slide_img: Any = None
        made = 0
        for i, (f0, f1) in enumerate(frames):
            if page_of[i] != sh.page or not (sh.start <= f0 < sh.end):
                continue
            box = sl.word_box(res, toks[i])
            if box is None:
                continue
            n = min(f1, sh.end) - f0
            if n < nm.N12_DRAW:
                continue
            bx, by, bw, bh = (v * scale for v in box)
            small = box[3] * k_fit / ch < nm.N13_MIN
            if small:
                if slide_img is None:
                    slide_img = await asyncio.to_thread(
                        lambda: Image.open(slide_path).convert("RGBA").resize((ow, oh))
                    )
                lens = await asyncio.to_thread(
                    nm.magnifier, slide_img, (bx, by, bw, bh), nm.N13_DIAM * ch * px_per_out,
                    px_per_out,
                )  # fmt: skip
                gen = nm.magnifier_frames((ow, oh), lens, (bx, by, bw, bh), n)
                name = "虫めがね（N13）"
            else:
                gen = nm.circle_frames((ow, oh), (bx, by, bw, bh), n, accent, px_per_out, i)
                name = "手描きの丸（N12）"
            a = await self._frames_mov(project_id, gen, fps, name)
            cid = str(uuid4())
            cmds.append(
                AddClip(track_id=tid, asset_id=a.id, start_frame=f0, duration_frames=n,
                        clip_id=cid, transform=ClipTransform(fit=FitMode.FIT))
            )  # fmt: skip
            if cam is not None:  # スライドのカメラの、この区間の値（2 フレームごと・直線でつなぐ）
                sub: list[Keyframe] = []
                prev: dict[str, float] | None = None
                last_f = -1
                for f in [*range(0, n, 2), n - 1]:
                    v = _values_at(cam, f0 - sh.start + f, ffps)
                    if prev is not None and v == prev:
                        continue
                    if prev is not None and last_f != f - 2 and f - 2 > sub[-1].frame:
                        sub.append(Keyframe(frame=f - 2, easing=Easing.LINEAR, **prev))  # type: ignore[arg-type]
                    sub.append(Keyframe(frame=f, easing=Easing.LINEAR, **v))  # type: ignore[arg-type]
                    prev, last_f = v, f
                if sub:
                    cmds.append(
                        SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=sub))
                    )
            made += 1
        return made

    async def _frames_mov(self, project_id: str, frames: Any, fps: float, name: str) -> AssetRead:
        """透過の画の列（生成器でもよい）を PNG の mov にして素材にする（最後に透明の 1 枚）。"""
        return await self.c.direction._overlay(project_id, frames, name, fps)

    @staticmethod
    def _slide_keys(
        sh: Any,
        frames: list[tuple[int, int]],
        toks: list[set[str]],
        page_of: list[int],
        res: Any,
        fps: float,
    ) -> list[Keyframe]:
        """そのクリップの間に話す文ごとに、一致する行へ寄る（無ければ全体に戻す）。"""
        if not res.width or not res.lines:
            return []
        targets: list[tuple[int, tuple[float, float] | None]] = []
        for i, (f0, _) in enumerate(frames):
            if page_of[i] != sh.page or not (sh.start <= f0 < sh.end):
                continue
            j = sl.best_line(res, toks[i])
            if j is None:
                targets.append((f0 - sh.start, None))
            else:
                x, y, w, h = res.lines[j].box
                targets.append((f0 - sh.start, ((x + w / 2) / res.width, (y + h / 2) / res.height)))
        if not any(t for _, t in targets):
            return []
        dur = sh.end - sh.start
        n = sl.ZOOM_FRAMES
        keys: dict[int, Keyframe] = {0: Keyframe(frame=0, zoom=1.0, pan_x=0.0, pan_y=0.0)}
        cur: tuple[float, float, float] = (1.0, 0.0, 0.0)
        for f, t in targets:
            nxt = (1.0, 0.0, 0.0) if t is None else (sl.ZOOM, *sl.pan_for(*t))
            if nxt == cur:
                continue
            f = max(f, max(keys) + 1)
            if f + n >= dur:
                break
            z0, x0, y0 = cur
            keys[f] = Keyframe(frame=f, zoom=z0, pan_x=x0, pan_y=y0, easing=Easing.CUBIC_IN_OUT)
            z1, x1, y1 = nxt
            keys[f + n] = Keyframe(frame=f + n, zoom=z1, pan_x=x1, pan_y=y1)
            cur = nxt
        return [keys[k] for k in sorted(keys)] if len(keys) > 1 else []

    async def _small_tachie(self, project_id: str) -> list[str]:
        """統合版 P3: 立ち絵を画面の面積の 8% で右下に出す。"""
        from video_automation.schemas.timeline import SetClipProps
        from video_automation.services.characters.overlay import layout

        c = self.c
        tl = await c.timeline.get_timeline(project_id)
        track = next((t for t in tl.tracks if t.name == "立ち絵"), None)
        clips = [x for x in tl.clips if track is not None and x.track_id == track.id]
        if not clips:
            return []
        band, ch, _ = layout(tl.width, tl.height)
        tr = sl.tachie_transform(tl.width, tl.height, band * tl.height, ch * tl.height)
        cmds: list[Any] = [
            SetClipProps(
                clip_id=x.id,
                transform=ClipTransform(fit=FitMode.FIT, scale=tr["scale"], x=tr["x"], y=tr["y"]),
            )
            for x in clips
        ]
        await c.timeline.submit(
            project_id,
            CommandRequest(command=Batch(commands=cmds), description="立ち絵を右下に小さく"),
        )
        return []

    async def _last_frame(self, project_id: str, a: AssetRead) -> AssetRead:
        c = self.c
        src = c.settings.data_dir / a.path
        out = (
            c.settings.data_dir
            / "projects"
            / project_id
            / "assets"
            / f"rec_last_{uuid4().hex[:8]}.png"
        )
        await c.ffmpeg.run(
            ["-sseof", "-0.2", "-i", str(src), "-frames:v", "1", "-update", "1", str(out)]
        )
        return AssetRead.model_validate(
            await c.timeline.register_generated(
                project_id, out, AssetKind.IMAGE, "build", "画面録画の最後の画面"
            )
        )

    async def _run_music(
        self, project_id: str, detail: Any, song: AssetRead, req: BuildRunRequest, progress: Any
    ) -> dict[str, Any]:
        """曲から作る（MV・リリック）: 歌の時刻を文字起こしで取り、曲を置き、演出して書き出す。"""
        c = self.c
        labels = dict(MV_STEPS)
        keys = [k for k, _ in MV_STEPS]
        notes: list[str] = []
        if req.keep_script:  # 見た目だけ作り直す: 前に合わせた歌詞の台本を使う
            keys = [k for k in keys if k in ("direction", "check", "render")]

        async def at(key: str, frac: float = 0.0) -> None:
            i = keys.index(key)
            await progress((i + frac) / len(keys), f"{labels[key]}（{i + 1}/{len(keys)}）")

        sid = await self._lyrics_script(project_id, detail.id)
        if not req.keep_script or sid is None:
            await at("lyrics")

            async def tp(v: float) -> None:
                await at("lyrics", v)

            try:
                r = await c.transcriber.transcribe(
                    TranscribeRequest(
                        project_id=project_id,
                        asset_id=song.id,
                        lyrics=detail.settings.reference or None,
                        title=detail.title[:255],  # 題名は動画の題名になる
                    ),
                    tp,
                )
            except Exception as exc:  # noqa: BLE001
                raise BuildError(f"歌の時刻を取れませんでした: {exc}") from exc
            sid = r.script_id
            if r.weak_lines:
                notes.append(f"歌詞の {len(r.weak_lines)} 行は歌声と合わせきれず、時刻が推定です")
            await at("music")
            await self._place_music(project_id, song)
        await at("direction")
        if req.keep_script and (k := text_size_factor(req.instruction)) is not None:
            notes.append(await self._resize_text(project_id, k))
        text_scale = await self._text_scale(project_id)
        plan = (
            await c.direction.plan(
                DirectionPlanRequest(
                    script_id=sid,
                    use_llm=False,  # 曲の演出は画面演出（文字の動き・背景）が担う
                    instruction=req.instruction if req.keep_script else "",
                )
            )
        ).plan
        res = await c.direction.apply(
            DirectionApplyRequest(script_id=sid, plan=plan, text_scale=text_scale)
        )
        notes += skipped_notes(list(getattr(res, "skipped", []) or []))
        image = await self._asset(project_id, MV_IMAGE_NAME, AssetKind.IMAGE)
        if image is not None:
            try:
                notes += await self._mv_shots(project_id, sid, song, image)
            except Exception as exc:  # noqa: BLE001 - ショットが作れなくても書き出す
                log.warning("mv_shots_failed", error=type(exc).__name__, detail=str(exc)[:300])
                notes.append(f"MV のショットを作れませんでした（{type(exc).__name__}）")
        await at("check")
        rreq = RenderRequest(
            project_id=project_id,
            script_id=sid,
            rich_direction=req.rich_direction,
            profile=await self._render_profile(project_id),
        )
        return await self._check_and_render(rreq, at, notes, progress)

    async def _mv_shots(
        self, project_id: str, sid: str, song: AssetRead, image: AssetRead
    ) -> list[str]:
        """統合版 V1・V2・V8: 1 枚の絵から 8 種のショットを作り、拍に合わせて並べる。"""
        c = self.c
        tl = await c.timeline.get_timeline(project_id)
        fps = tl.fps_num / tl.fps_den
        w, h = tl.width, tl.height
        song_path = c.settings.data_dir / song.path
        bt = await detect_file(c.ffmpeg, song_path)
        if bt.confidence < 0.2 or len(bt.times) < 8:
            return ["曲の拍がはっきりしないので、MV のショットは作りませんでした"]
        cues = [x for x in await c.subtitles.cues(sid) if x.start_frame is not None]
        if not cues:
            return []
        secs = lyric_sections([x.text for x in cues])
        spans = mv.line_spans(
            [(x.start_frame or 0) / fps for x in cues],
            [(x.end_frame or x.start_frame or 0) / fps for x in cues],
            secs,
            4 * 60.0 / max(1.0, bt.bpm),
        )
        total_sec = song.duration_sec or tl.duration_frames / fps
        cuts = mv.plan_cuts(bt.times, spans, total_sec, fps)
        wave_ = await sync_audio.load(c.ffmpeg, song_path)
        peaks = sync_audio.hits(wave_, sync_audio.SR, fps, bt.times).peaks
        cuts = mv.insert_flashes(cuts, peaks, fps)
        if not cuts:
            return []
        # 統合版 V11: 各サビの直前の 1 小節を主色の面にし、白と黒の形を置く
        bars = mv.motif_bars(spans, bt.times, fps)
        shape = await self._motif_shape([x.text for x in cues]) if bars else "circle"
        # 統合版 V7: B メロの最後の小節にグリッド、2 番の A メロに画面内の画面、1 番の A メロの
        # 最後にカード（V11 の小節とは重ねない）
        cuts, v7 = mv.apply_v7(cuts, spans, bt.times, fps, [(a, b) for a, _, b in bars])
        cuts = mv.insert_planes(cuts, bars)
        from video_automation.services.rendering.lyrics import DIRECTIONS, direction_genre

        dsg = await c.design.get(project_id)
        genre = direction_genre(dsg.lyric_direction or None, dsg.genre) or "pop"
        main, ink = mv.V12_MAIN.get(genre, mv.V12_MAIN["pop"])
        if dsg.lyric_direction in DIRECTIONS:  # 統合版 U1: 方向性の主色と文字の色
            main, ink = DIRECTIONS[dsg.lyric_direction][4:6]
        work = c.settings.data_dir / "projects" / project_id / "assets"
        src = c.settings.data_dir / image.path
        files: dict[tuple[str, int], Path] = {}
        cut_keys: list[tuple[str, int]] = []
        n_flash = 0
        cmds: list[Any] = []
        order = [t.name for t in tl.tracks]
        tracks = {t.name: t for t in tl.tracks}

        def track(name: str, after: str | None) -> str:
            """名前のトラックを空にして使う。無ければ after（無ければ 演出・背景）の上に作る。"""
            if name in tracks:
                tid_ = tracks[name].id
                cmds.extend(DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == tid_)
                return tid_
            tid_ = str(uuid4())
            cmds.append(AddTrack(kind=TrackKind.VIDEO, name=name, track_id=tid_))
            base = after if after in order else "演出・背景"
            at_ = order.index(base) + 1 if base in order else 0
            cmds.append(MoveTrack(track_id=tid_, index=at_))
            order.insert(at_, name)
            return tid_

        tid = track(MV_SHOT_TRACK, None)
        white_tid = track(MV_MOTIF_TRACKS[0], MV_SHOT_TRACK)
        black_tid = track(MV_MOTIF_TRACKS[1], MV_MOTIF_TRACKS[0])
        ink_tid = track(MV_INK_TRACK, MV_MOTIF_TRACKS[1])

        async def asset(out: Path, label: str) -> AssetRead:
            return AssetRead.model_validate(
                await c.timeline.register_generated(
                    project_id, out, AssetKind.IMAGE, "build", label
                )
            )

        for cut in cuts:
            k = 0
            if cut.shot == "flash":
                k, n_flash = n_flash % 2, n_flash + 1
            key = (cut.shot, k)
            cut_keys.append(key)
            if key not in files:
                out = work / f"mv_{cut.shot}_{k}_{uuid4().hex[:6]}.png"
                await asyncio.to_thread(mv.save_shot, out, src, cut.shot, w, h, main, ink, k)
                files[key] = out
        # カットを 1 本の動画に焼いて置く（カットごとのクリップにすると、書き出しで
        # クリップの数だけ入力を開き、4 分の曲ではメモリが 10GB を超える）
        baked = await self._bake_shots(project_id, work, cuts, [files[k] for k in cut_keys], tl)
        cmds.append(
            AddClip(
                track_id=tid,
                asset_id=baked.id,
                start_frame=cuts[0].start,
                duration_frames=cuts[-1].end - cuts[0].start,
                clip_id=str(uuid4()),
                transform=ClipTransform(fit=FitMode.FILL),
            )
        )
        # V11 の形（白は左から、黒は左右の鏡像で右から。小節の最後の拍で中央に重なる）
        if bars:
            shapes = {}
            for color, mirror in ((mv.MOTIF_WHITE, False), (mv.MOTIF_BLACK, True)):
                out = work / f"mv_motif_{shape}_{int(mirror)}_{uuid4().hex[:6]}.png"
                img_ = await asyncio.to_thread(mv.motif_image, shape, color, w, h, mirror)
                await asyncio.to_thread(img_.save, out)
                shapes[mirror] = await asset(out, f"MV の{mv.MOTIF_LABEL[shape]}")
            for f0, f3, f4 in bars:
                for mirror, tid_, side in ((False, white_tid, -1), (True, black_tid, 1)):
                    cid = str(uuid4())
                    cmds.append(
                        AddClip(
                            track_id=tid_,
                            asset_id=shapes[mirror].id,
                            start_frame=f0,
                            duration_frames=f4 - f0,
                            clip_id=cid,
                            transform=ClipTransform(fit=FitMode.FILL),
                        )
                    )
                    cmds.append(
                        SetClipAnimation(
                            clip_id=cid,
                            animation=ClipAnimation(keyframes=mv.motif_keys(f0, f3, f4, side)),
                        )
                    )
        # 統合版 V6: 各サビの最初のカットで、前のショットにインクの穴をあけて次を現す
        entries = mv.chorus_entries(cuts, spans, bt.times, fps)
        for seed, j in enumerate(entries):
            prev, cur = cuts[j - 1], cuts[j]
            frames = await asyncio.to_thread(
                _ink_overlay,
                files[cut_keys[j - 1]],
                prev,
                fps,
                shape if prev.shot == "plane" else None,
                ink,
                seed,
                w,
                h,
            )
            # 10 枚を 1 本の透過の動画（PNG の mov）にまとめる（1 フレームずつのクリップにすると
            # 書き出しの入力が増え、メモリが足りなくなる）
            tmp = work / f"mv_ink_{seed}_{uuid4().hex[:6]}"
            tmp.mkdir(parents=True, exist_ok=True)
            # 最後に透明の 1 枚を足す（動画の長さの丸めで 10 フレームに 1 つ足りなくならないように）
            for i, fr in enumerate([*frames, frames[-1].copy()]):
                if i == len(frames):
                    fr.putalpha(0)
                await asyncio.to_thread(fr.save, tmp / f"{i:02d}.png")
            out = work / f"{tmp.name}.mov"
            await c.ffmpeg.run(
                [
                    "-y",
                    "-framerate",
                    f"{tl.fps_num}/{tl.fps_den}",
                    "-i",
                    str(tmp / "%02d.png"),
                    "-c:v",
                    "png",
                    "-pix_fmt",
                    "rgba",
                    str(out),
                ]
            )
            await asyncio.to_thread(shutil.rmtree, tmp, True)
            a_ = AssetRead.model_validate(
                await c.timeline.register_generated(
                    project_id, out, AssetKind.VIDEO, "build", "MV のインクの転換"
                )
            )
            cmds.append(
                AddClip(
                    track_id=ink_tid,
                    asset_id=a_.id,
                    start_frame=cur.start,
                    duration_frames=len(frames),
                    clip_id=str(uuid4()),
                    transform=ClipTransform(fit=FitMode.FILL),
                )
            )
        # 統合版 M11（イントロの拍の輪）・M10（2 回目以降の間奏のスペクトラム）
        from video_automation.services.direction import mv_audio_vis as av

        vis_tid = track(MV_VIS_TRACK, MV_INK_TRACK)
        n_vis = 0
        intro_end = spans[0][0]
        if intro_end > 1.0:
            fr = await asyncio.to_thread(av.ring_frames, bt.times, intro_end, fps, w, h, ink)
            a_ = await self._alpha_mov(project_id, work, fr, fps, "拍の輪（M11）")
            cmds.append(AddClip(track_id=vis_tid, asset_id=a_.id, start_frame=0,
                                duration_frames=len(fr), clip_id=str(uuid4())))  # fmt: skip
            n_vis += 1
        gaps = av.interludes(spans)[1:]
        if gaps:
            lev = await asyncio.to_thread(av.band_levels, wave_, sync_audio.SR, fps)
            for a0, b0 in gaps:
                fr = await asyncio.to_thread(av.spectrum_frames, lev, a0, b0, fps, w, h, ink)
                if not fr:
                    continue
                a_ = await self._alpha_mov(project_id, work, fr, fps, "スペクトラム（M10）")
                cmds.append(AddClip(track_id=vis_tid, asset_id=a_.id, start_frame=round(a0 * fps),
                                    duration_frames=len(fr), clip_id=str(uuid4())))  # fmt: skip
                n_vis += 1
        # 統合版 T29: 1 曲で最初の間奏の最初の 1 小節、そのときのショットを直前の歌詞の文字で描く
        all_gaps = av.interludes(spans)
        n_t29 = 0
        if all_gaps:
            g0 = all_gaps[0][0]
            f0 = round(g0 * fps)
            n29 = min(round(4 * 60.0 / max(1.0, bt.bpm) * fps), round((all_gaps[0][1] - g0) * fps))
            prev_i = max((i for i, sp in enumerate(spans) if sp[1] <= g0 + 1e-6), default=-1)
            jc = next((j for j, x in enumerate(cuts) if x.start <= f0 < x.end), None)
            if n29 > av.T29_OUT + 6 and prev_i >= 0 and jc is not None:
                from PIL import Image as _Image

                shot_im = await asyncio.to_thread(_Image.open, files[cut_keys[jc]])
                fr = await asyncio.to_thread(
                    av.text_art_frames, shot_im, cues[prev_i].text, n29, w, h, main, ink,
                    c.titles.font_path("Noto Sans CJK JP Black"),
                )  # fmt: skip
                a_ = await self._alpha_mov(project_id, work, fr, fps, "文字で絵を作る（T29）")
                t29_tid = track(MV_T29_TRACK, MV_VIS_TRACK)
                cmds.append(AddClip(track_id=t29_tid, asset_id=a_.id, start_frame=f0,
                                    duration_frames=len(fr), clip_id=str(uuid4())))  # fmt: skip
                n_t29 = 1
        if not n_t29 and MV_T29_TRACK in tracks:
            track(MV_T29_TRACK, None)  # 前に作った T29 を消す
        # 統合版 T26: サビの間、サビで最も多い漢字 1 字を背景に巨大に置く
        ch_spans = [(a0, b0) for a0, b0, s_ in spans if s_ in ("chorus", "chorus_first")]
        ch_texts = [x.text for x, (_a, _b, s_) in zip(cues, spans, strict=False)
                    if s_ in ("chorus", "chorus_first")]  # fmt: skip
        kc = av.key_char(ch_texts) if ch_spans else None
        if kc:
            img = await asyncio.to_thread(
                av.key_char_image, kc, w, h, c.titles.font_path("Noto Sans CJK JP Black"), ink
            )
            p_ = work / f"t26_{uuid4().hex[:8]}.png"
            await asyncio.to_thread(img.save, p_)
            a_ = await asset(p_, f"キーの一字（T26）「{kc}」")
            key_tid = track(MV_KEY_TRACK, MV_SHOT_TRACK)
            # 続くサビの行は 1 つの区間にまとめる（同じトラックで重ねない）
            merged: list[list[float]] = []
            for a0, b0 in ch_spans:
                if merged and a0 - merged[-1][1] < 0.5:
                    merged[-1][1] = max(merged[-1][1], b0)
                else:
                    merged.append([a0, b0])
            for a0, b0 in merged:
                f0, f1 = round(a0 * fps), round(b0 * fps)
                if f1 > f0:
                    cmds.append(AddClip(track_id=key_tid, asset_id=a_.id, start_frame=f0,
                                        duration_frames=f1 - f0, clip_id=str(uuid4())))  # fmt: skip
        # 統合版 X7: 落ちサビにジャンルの粒（150f で繰り返す粒を並べる）
        drops = [(a0, b0) for a0, b0, s_ in spans if s_ == "drop"]
        if drops:
            from video_automation.services.direction import effects as fx

            fr = await asyncio.to_thread(fx.drop_particles, genre, w, h, main)
            a_ = await self._alpha_mov(project_id, work, fr, fps, f"落ちサビの粒（X7・{genre}）")
            x7_tid = track(MV_X7_TRACK, MV_VIS_TRACK)
            f0, f1 = round(drops[0][0] * fps), round(drops[-1][1] * fps)
            pos = f0
            while pos < f1:
                n = min(len(fr), f1 - pos)
                cmds.append(AddClip(track_id=x7_tid, asset_id=a_.id, start_frame=pos,
                                    duration_frames=n, clip_id=str(uuid4())))  # fmt: skip
                pos += n
        # 統合版 U2〜U5: 曲の方向性を選んでいれば、歌の区間をフレーズごとの歌詞の画面にする。
        # U9: 選んでいなくても、絵が全面（透明の無い画像）なら A メロ・B メロを帯の画面にする
        n_u, u_spans = 0, []
        opaque = await asyncio.to_thread(_is_opaque, src)
        if dsg.lyric_direction in ls_.PRESETS:
            n_u, u_spans = await self._lyric_screens(
                project_id, dsg.lyric_direction, cues, secs, bt, src, fps, w, h, cmds, track
            )
        elif opaque:
            n_u, u_spans = await self._lyric_screens(
                project_id, "", cues, secs, bt, src, fps, w, h, cmds, track
            )
        elif MV_U_TRACK in tracks:
            track(MV_U_TRACK, None)  # 前に作った歌詞の画面を消す
        # 統合版 U16: しっとり系・バラードは、最後の語が 2 字以下の行と間奏の頭に単色のカード
        n_card = 0
        if dsg.lyric_direction in ls_.U16_DIRECTIONS:
            n_card = await self._word_cards(project_id, cues, fps, w, h, cmds, track)
        elif MV_U16_TRACK in tracks:
            track(MV_U16_TRACK, None)
        # 統合版 V10: イントロは 2.39:1 の黒帯、最初のサビの頭で 12f で開く。落ちサビで閉じる
        await self._letterbox(
            project_id, tl, spans, total_sec, work, cmds, [(a / fps, b / fps) for a, b in u_spans]
        )
        await c.timeline.submit(
            project_id,
            CommandRequest(
                command=Batch(commands=cmds), origin=CommandOrigin.USER, description="MV のショット"
            ),
        )
        used = {"beat_cut": len(cuts)}
        if entries:
            used["transition_shape"] = len(entries)
        await c.design.record(
            project_id,
            "mv_shots",
            {
                "used": used,
                "cuts": len(cuts),
                "ink": len(entries),
                "motif": len(bars),
                "v7": v7,
                "lyric_screens": n_u,
                "lyric_spans": u_spans,  # 歌詞の画面で歌詞を描いた範囲（Blender の歌詞を出さない）
                "word_cards": n_card,
            },
        )
        notes = [
            f"絵から {len(files)} 種のショットを作り、"
            f"拍に合わせて {len(cuts)} カットに切り替えました"
        ]
        if entries:
            notes.append(f"サビの頭 {len(entries)} か所をインクの転換にしました")
        if n_vis:
            notes.append("イントロに拍の輪、2 回目からの間奏にスペクトラムを入れました")
        if n_t29:
            notes.append("最初の間奏の頭の 1 小節を、直前の歌詞の文字で描いた絵にしました")
        if n_u and dsg.lyric_direction in ls_.PRESETS:
            notes.append(
                f"歌の区間を、方向性「{ls_.DIRECTION_NAMES[dsg.lyric_direction]}」の歌詞の画面"
                f" {n_u} 枚にしました（A メロ・B メロとサビで組み方を替えています）"
            )
        elif n_u:
            notes.append(
                f"A メロ・B メロ {n_u} か所を、絵の下の帯に小さな歌詞を出す画面にしました（U9）"
            )
        if n_card:
            notes.append(f"短い語で終わる行と間奏の頭 {n_card} か所に、単色のカードをはさみました")
        v7_text = [
            t
            for t, k in (
                (f"グリッド {v7['grid']} か所", v7["grid"]),
                ("2 番の A メロに画面内の画面", v7["pip"]),
                ("1 番の A メロの最後にカード", v7["cards"]),
            )
            if k
        ]
        if v7_text:
            notes.append("、".join(v7_text) + "を入れました")
        if bars:
            notes.append(
                f"サビの前の {len(bars)} 小節に、白と黒の{mv.MOTIF_LABEL[shape]}を置きました"
            )
        return notes

    async def _lyric_screens(
        self,
        project_id: str,
        direction: str,
        cues: list[Any],
        secs: list[str],
        bt: Any,
        src: Path,
        fps: float,
        w: int,
        h: int,
        cmds: list[Any],
        track: Any,
    ) -> tuple[int, list[list[int]]]:
        """統合版 U2〜U5: 歌の区間を、フレーズ（同じセクションの続く最大 4 行）ごとの歌詞の画面
        （不透明の画の列）にして、ショットの上に置く。イントロ・間奏は文字なし（UR0）のまま、
        今のショットを見せる。歌詞は書き出しの Blender の歌詞を出さない（render が記録を見る）。"""
        from PIL import Image

        c = self.c
        spans = [((x.start_frame or 0), (x.end_frame or x.start_frame or 0)) for x in cues]
        beats = [round(t * fps) for t in bt.times]
        phs = ls_.phrases([x.text for x in cues], spans, secs, beats)
        if not direction:  # U9: A メロ・B メロだけ
            phs = [x for x in phs if x.section in ls_.U9_SECTIONS]
        if not phs:
            return 0, []
        pic = await asyncio.to_thread(lambda: Image.open(src).convert("RGBA"))
        tid = track(MV_U_TRACK, MV_MOTIF_TRACKS[1])

        def fonts(name: str) -> str | None:
            return c.titles.font_path(name)

        made = 0
        covered: list[list[int]] = []
        seen: dict[str, int] = {}
        for i, ph in enumerate(phs):
            kind = "chorus" if ph.section == "chorus_first" else ph.section
            nth = seen.get(kind, 0)
            seen[kind] = nth + 1
            scr = ls_.screen_for(direction, ph.section, nth) if direction else ls_.EXTRA["U9"]
            gen = ls_.frames(scr, ph, pic, w, h, ls_.fonts_for(direction, fonts), fps, seed=i)
            label = ls_.DIRECTION_NAMES.get(direction, direction) if direction else "U9"
            a = await c.direction._overlay(project_id, gen, f"歌詞の画面（{label}）", fps)
            cmds.append(
                AddClip(track_id=tid, asset_id=a.id, start_frame=ph.start,
                        duration_frames=ph.end - ph.start, clip_id=str(uuid4()))
            )  # fmt: skip
            covered.append([ph.start, ph.end])
            made += 1
        return made, covered

    async def _word_cards(
        self, project_id: str, cues: list[Any], fps: float, w: int, h: int, cmds: list[Any],
        track: Any,
    ) -> int:  # fmt: skip
        """統合版 U16: 最後の語が 2 字以下の行の終わり（18f）と、間奏の頭（2 秒）に単色のカード。
        間奏は、前の行の終わりから次の行まで 6 秒以上あいた所（最初の行の前は除く）。
        間奏の頭には曲の題名（プロジェクトの名前）を 40px の縦書きで出す。"""
        c = self.c
        tid = track(MV_U16_TRACK, MV_U_TRACK)

        def fonts(name: str) -> str | None:
            return c.titles.font_path(name)

        async with c.db.session_factory() as s:
            p = await s.get(Project, project_id)
            title = (p.name if p else "")[:12]
        made = 0
        timed = [x for x in cues if x.start_frame is not None]
        for i, x in enumerate(timed):
            st, en = int(x.start_frame or 0), int(x.end_frame or x.start_frame or 0)
            word = ls_.last_word(x.text)
            if 0 < len(word) <= 2 and en - st > ls_.U16_LEN + 6:
                n = ls_.U16_LEN
                gen = ls_.word_card(word, made, w, h, fonts, n)
                a = await c.direction._overlay(project_id, gen, f"単色のカード「{word}」", fps)
                cmds.append(AddClip(track_id=tid, asset_id=a.id, start_frame=en - n,
                                    duration_frames=n, clip_id=str(uuid4())))  # fmt: skip
                made += 1
            nxt = timed[i + 1] if i + 1 < len(timed) else None
            gap = (int(nxt.start_frame or 0) - en) if nxt is not None else 0
            if title and nxt is not None and gap >= round(6 * fps):
                n = round(2 * fps)
                gen = ls_.word_card(title, made, w, h, fonts, n, vertical=True)
                a = await c.direction._overlay(project_id, gen, "単色のカード（間奏の頭）", fps)
                cmds.append(AddClip(track_id=tid, asset_id=a.id, start_frame=en + round(0.5 * fps),
                                    duration_frames=n, clip_id=str(uuid4())))  # fmt: skip
                made += 1
        return made

    async def _onomatopoeia(
        self,
        project_id: str,
        tl: Any,
        pops: list[int],
        total: int,
        prof: Any,
        cmds: list[Any],
        work: Path,
    ) -> list[str]:
        """統合版 X4: 効果音（ポン）を鳴らす所に「ポン！」の吹き出し（右上寄り、24f）。"""
        from video_automation.services.direction import effects as fx

        c = self.c
        fps = tl.fps_num / tl.fps_den
        font = c.titles.font_path("M PLUS Rounded 1c ExtraBold") or c.titles.font_path(None)
        fr = await asyncio.to_thread(
            fx.onomatopoeia, fx.ONOMATOPOEIA["pop"], tl.height, prof.palette.accent, font
        )
        a_ = await self._alpha_mov(project_id, work, fr, fps, "擬音（X4）「ポン！」")
        tr = next((t for t in tl.tracks if t.name == X4_TRACK), None)
        if tr is not None:
            cmds.extend(DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == tr.id)
            tid = tr.id
        else:
            tid = str(uuid4())
            cmds.append(AddTrack(kind=TrackKind.VIDEO, name=X4_TRACK, track_id=tid))
            cmds.append(MoveTrack(track_id=tid, index=1000))
        bw, bh = fr[0].size
        tf = native_transform(bw, bh, tl.width, tl.height, cx=0.78, cy=0.22, max_w=1.0)
        last, n = -(10**9), 0
        for at in pops:
            if at < last or at + len(fr) > total:
                continue
            cmds.append(
                AddClip(track_id=tid, asset_id=a_.id, start_frame=at, duration_frames=len(fr),
                        clip_id=str(uuid4()), transform=tf)
            )  # fmt: skip
            last, n = at + len(fr), n + 1
        return [f"効果音の所に擬音を {n} か所出しました"] if n else []

    async def _alpha_mov(
        self, project_id: str, work: Path, frames: list[Any], fps: float, label: str
    ) -> AssetRead:
        """透過の画の列を 1 本の動画（PNG の mov）にする（最後に透明の 1 枚を足す）。"""
        from PIL import Image

        c = self.c
        tmp = work / f"vis_{uuid4().hex[:8]}"
        tmp.mkdir(parents=True, exist_ok=True)
        for i, fr in enumerate([*frames, Image.new("RGBA", frames[0].size, (0, 0, 0, 0))]):
            await asyncio.to_thread(fr.save, tmp / f"{i:05d}.png")
        out = work / f"{tmp.name}.mov"
        await c.ffmpeg.run(
            ["-y", "-framerate", f"{fps:.6f}", "-i", str(tmp / "%05d.png"), "-c:v", "png",
             "-pix_fmt", "rgba", str(out)]
        )  # fmt: skip
        await asyncio.to_thread(shutil.rmtree, tmp, True)
        return AssetRead.model_validate(
            await c.timeline.register_generated(project_id, out, AssetKind.VIDEO, "build", label)
        )

    async def _bake_shots(
        self, project_id: str, work: Path, cuts: list[Any], paths: list[Path], tl: Any
    ) -> AssetRead:
        """MV のカット（絵とカメラ）を 40 カットずつ書き出してつなぎ、1 本の動画にする。"""
        c = self.c
        fps_f = Fraction(tl.fps_num, tl.fps_den)
        fps = float(fps_f)
        items = [
            (p, x.end - x.start, mv.camera(x.shot, x.end - x.start, fps))
            for p, x in zip(paths, cuts, strict=True)
        ]
        tmp = work / f"mv_bake_{uuid4().hex[:6]}"
        tmp.mkdir(parents=True, exist_ok=True)
        parts = []
        try:
            spans = mv_bake.groups(len(items))
            for gi, (a, b) in enumerate(spans):
                part = tmp / f"part_{gi:03d}.mp4"
                last = 1 if b == len(items) else 0
                args = mv_bake.group_args(items[a:b], fps_f, tl.width, tl.height, part, last)
                k = args.index("-filter_complex")
                if (
                    len(args[k + 1]) > FILTER_SCRIPT_THRESHOLD
                ):  # Windows のコマンド行は 32767 字まで
                    gfile = tmp / f"part_{gi:03d}.filtergraph.txt"
                    gfile.write_text(args[k + 1], encoding="utf-8")
                    args[k : k + 2] = await c.ffmpeg.filter_script_args(str(gfile))
                await c.ffmpeg.run(["-y", *args])
                parts.append(part)
            lst = tmp / "list.txt"
            lst.write_text(mv_bake.concat_list(parts), encoding="utf-8")
            out = work / f"mv_shots_{uuid4().hex[:6]}.mp4"
            await c.ffmpeg.run(
                ["-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(out)]
            )
        finally:
            await asyncio.to_thread(shutil.rmtree, tmp, True)
        return AssetRead.model_validate(
            await c.timeline.register_generated(
                project_id, out, AssetKind.VIDEO, "build", f"MV のショット（{len(cuts)} カット）"
            )
        )

    async def _motif_shape(self, texts: list[str]) -> str:
        """統合版 V11: 歌詞から、意味が対になる 2 語と形を LLM に選ばせる。選べないときは円。"""
        body = "\n".join(t for t in texts if t.strip())[:4000]
        if not body:
            return "circle"
        schema = {
            "type": "object",
            "properties": {
                "pair": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 2,
                    "maxItems": 2,
                },
                "shape": {"type": "string", "enum": list(mv.MOTIF_SHAPES)},
            },
            "required": ["pair", "shape"],
        }
        msgs = [
            ChatMessage(
                role="system",
                content=(
                    "あなたは MV の演出家です。歌詞から、意味が対になる 2 語"
                    "（光と影、昼と夜 など）を 1 組だけ選び、その対を表す形を "
                    "circle・star・heart・diamond・triangle・cross の 6 つから 1 つ選びます。"
                    'JSON {"pair": ["語1", "語2"], "shape": "..."} だけを出します。'
                ),
            ),
            ChatMessage(role="user", content="歌詞:\n" + body),
        ]
        try:
            res = await self.c.llm.complete_json(
                TaskType.EDIT_PLAN, msgs, schema, temperature=0.3, retries=1
            )
            shape = str(res.data.get("shape", ""))
        except Exception as exc:  # noqa: BLE001 - 形が選べなくても円で出す
            log.info("motif_shape_fallback", error=type(exc).__name__)
            return "circle"
        return shape if shape in mv.MOTIF_SHAPES else "circle"

    async def _letterbox(
        self,
        project_id: str,
        tl: Any,
        spans: list[tuple[float, float, str]],
        total_sec: float,
        work: Path,
        cmds: list[Any],
        avoid: list[tuple[float, float]] | None = None,
    ) -> None:
        c = self.c
        w, h = tl.width, tl.height
        fps = tl.fps_num / tl.fps_den
        bar = mv.letterbox_bar(w, h)
        boxes = mv.letterbox_spans(spans, total_sec) if bar > 0 and w > h else []
        boxes = mv.letterbox_minus(
            boxes, avoid or [], mv.LETTERBOX_FRAMES / fps
        )  # 歌詞の画面には重ねない
        if not boxes:
            return
        bars = {}
        for side in ("top", "bottom"):
            out = work / f"mv_bar_{side}_{uuid4().hex[:6]}.png"
            await asyncio.to_thread(_bar_png, out, w, h, bar, side)
            bars[side] = AssetRead.model_validate(
                await c.timeline.register_generated(
                    project_id, out, AssetKind.IMAGE, "build", "MV の黒帯"
                )
            )
        # 上と下の帯は同じ時間に出るので、別のトラックに置く（同じトラックでは重ねられない）
        tracks = {t.name: t for t in tl.tracks}
        bids: dict[str, str] = {}
        for side, name in zip(("top", "bottom"), MV_BAR_TRACKS, strict=True):
            if name in tracks:
                bids[side] = tracks[name].id
                cmds += [DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == bids[side]]
            else:
                bids[side] = str(uuid4())
                cmds.append(AddTrack(kind=TrackKind.VIDEO, name=name, track_id=bids[side]))
                cmds.append(MoveTrack(track_id=bids[side], index=1000))
        lf = mv.LETTERBOX_FRAMES
        for a0, a1 in boxes:
            f0, f1 = round(a0 * fps), round(a1 * fps)
            if f1 - f0 < 2 * lf:
                continue
            for side, sign in (("top", -1), ("bottom", 1)):
                cid = str(uuid4())
                cmds.append(
                    AddClip(
                        track_id=bids[side],
                        asset_id=bars[side].id,
                        start_frame=f0,
                        duration_frames=f1 - f0 + lf,
                        clip_id=cid,
                        transform=ClipTransform(fit=FitMode.FILL),
                    )
                )
                off = sign * bar / h
                keys = [
                    Keyframe(frame=f1 - f0, y=0.0, easing=Easing.EXPO_OUT),
                    Keyframe(frame=f1 - f0 + lf - 1, y=off),
                ]
                if f0 > 0:  # 途中から閉じる（落ちサビ）
                    keys = [
                        Keyframe(frame=0, y=off, easing=Easing.EXPO_OUT),
                        Keyframe(frame=lf, y=0.0),
                        *keys,
                    ]
                cmds.append(SetClipAnimation(clip_id=cid, animation=ClipAnimation(keyframes=keys)))

    async def _render_profile(self, project_id: str) -> RenderProfileName:
        """プロジェクトの向きに合う書き出しの大きさ（実機: 縦型ショートが 1920×1080 で書き出され、
        縦の画面の配置が横長の画面に押し込まれていた）。"""
        async with self.c.db.session_factory() as s:
            p = await s.get(Project, project_id)
        portrait = p is not None and p.height > p.width
        return RenderProfileName.FHD_PORTRAIT if portrait else RenderProfileName.FHD_LANDSCAPE

    async def _check_and_render(
        self, rreq: RenderRequest, at: Any, notes: list[str], progress: Any
    ) -> dict[str, Any]:
        """デザインの検査 → 書き出し（終わるまで待つ）。"""
        c = self.c
        sid = rreq.script_id
        chk = await c.render.design_check(rreq)
        if chk is not None:
            notes += [f"検査: {v.message}" for v in chk.violations if v.severity == "warning"][:5]
            if not chk.ok:
                errs = "、".join(v.message for v in chk.violations if v.severity == "error")[:300]
                raise BuildError(f"デザインの検査で止まりました: {errs}")

        await at("render")
        r = await c.render.request(rreq)
        while True:
            r = await c.render.get(r.id)
            if r.status in (RenderStatus.SUCCEEDED, RenderStatus.FAILED, RenderStatus.CANCELLED):
                break
            job = c.jobs.get(r.job_id) if r.job_id else None
            await at("render", job.progress if job else 0.0)
            await asyncio.sleep(1.0)
        if r.status != RenderStatus.SUCCEEDED or not r.output_path:
            raise BuildError(f"書き出しに失敗しました: {r.error or r.status}")
        job = c.jobs.get(r.job_id) if r.job_id else None
        if job is not None and isinstance(job.result, dict):
            notes += [f"検査: {w}" for w in job.result.get("warnings") or []]
        video = str((c.settings.data_dir / r.output_path).resolve())
        thumbs: list[str] = []
        if c.thumbnails is not None:  # 統合版 S4: サムネイルを 3 案（作れなくても動画は渡す）
            try:
                thumbs = await c.thumbnails.make(rreq.project_id, notes)
            except Exception as exc:  # noqa: BLE001
                log.warning("thumbnails_failed", error=str(exc)[:300])
                notes.append(f"サムネイルを作れませんでした（{type(exc).__name__}）")
        await progress(1.0, "できあがりました")
        return {
            "render_id": r.id,
            "video": video,
            "thumbnails": thumbs,
            "notes": notes,
            "script_id": sid,
        }

    async def _lyrics_script(self, project_id: str, plan_sid: str) -> str | None:
        """前に文字起こしで作った歌詞の台本（新しいもの）。"""
        async with self.c.db.session_factory() as s:
            from sqlalchemy import select

            from video_automation.database.models import Script

            # 構成で作った台本（歌詞の参考を持つ空の台本）以外の、新しい台本
            q = (
                select(Script.id)
                .where(Script.project_id == project_id, Script.id != plan_sid)
                .order_by(Script.created_at.desc())
            )
            return (await s.execute(q)).scalars().first()

    async def _place_narration(self, project_id: str, sid: str) -> list[str]:
        """台本の声をタイムラインに置く。置けなければ書き出しを止める（声の無い動画を作らない）。"""
        try:
            await self.c.timeline.from_script(project_id, sid)
        except Exception as exc:  # noqa: BLE001
            raise BuildError(
                f"キャラクターの声をタイムラインに置けませんでした: {type(exc).__name__}"
            ) from exc
        return []

    async def _place_audio(self, project_id: str, sid: str) -> list[str]:
        """BGM と効果音（場面の切り替えの「シュッ」・数値の強調の「ポン」）を置く。

        落ち着いた系（字幕動画・インタビュー等）は BGM も効果音も付けない。効果音の数は
        用途の型の上限まで。作り直すときは前に置いた BGM・効果音を外してから置く。"""
        c = self.c
        design = await c.design.get(project_id)
        purpose = Purpose(design.purpose) if design.purpose else Purpose.EXPLAINER
        prof = PROFILES[purpose]
        if prof.family == Family.CALM:
            return []
        tl = await c.timeline.get_timeline(project_id)
        fps = tl.fps_num / tl.fps_den
        segs = (await c.studio.detail(sid)).segments
        ends = [x.end_frame for x in segs if x.end_frame]
        speech = [
            (x.start_frame / fps, x.end_frame / fps)
            for x in segs
            if x.start_frame is not None and x.end_frame
        ]
        total = max([*ends, tl.duration_frames])
        if total <= 0:
            return []
        cmds: list[Any] = []
        tracks = {t.name: t for t in tl.tracks}
        tid: dict[str, str] = {}
        for name in (BGM_TRACK, SFX_TRACK):
            if name in tracks:
                cmds += [
                    DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == tracks[name].id
                ]
                tid[name] = tracks[name].id
            else:
                tid[name] = str(uuid4())
                cmds.append(AddTrack(kind=TrackKind.AUDIO, name=name, track_id=tid[name]))
        work = c.settings.data_dir / "projects" / project_id / "assets"

        async def register(path: Path, label: str) -> AssetRead:
            a = await c.timeline.register_generated(
                project_id, path, AssetKind.AUDIO, "build", label
            )
            return AssetRead.model_validate(a)

        learned = c.design.edits.overrides(purpose.value) if c.design.edits else {}
        bgm_ratio = learned.get("bgm", {}).get("volume", 1.0)
        series = await c.design.series_for(project_id)
        bgm: AssetRead | None = None
        if series is not None and series.bgm_path:  # 統合版 Q4: シリーズの BGM
            try:
                src = await c.timeline.import_asset(
                    AssetImportRequest(project_id=project_id, path=series.bgm_path)
                )
                bgm = await register(
                    await audio_bed.make_bgm_from(
                        c.ffmpeg,
                        c.settings.data_dir / src.path,
                        work / f"bgm_{uuid4().hex[:8]}.wav",
                        total / fps + 0.5,
                        speech,
                    ),
                    f"BGM（シリーズ「{series.name[:20]}」）",
                )
            except Exception as exc:  # noqa: BLE001 - 使えないときは自動の BGM にする
                log.warning("series_bgm_failed", error=type(exc).__name__)
        spec = bgm_prompts.bgm_spec(purpose)
        if bgm is None and spec is not None and c.music is not None:
            # 統合版 Q5: 用途ごとの caption と BPM で、ACE-Step に BGM を作らせる。
            # 動かないとき・失敗したときは、自動の和音にする
            try:
                if (await c.music.status()).available:
                    caption, bpm = spec
                    length = bgm_prompts.cut_length(total / fps, bpm)
                    made = await c.music.generate(
                        MusicGenerateRequest(
                            project_id=project_id,
                            prompt=caption,
                            bpm=bpm,
                            duration_sec=min(600.0, max(10.0, length + bgm_prompts.EXTRA_SEC)),
                            instrumental=True,
                        ),
                        _no_progress,
                    )
                    if made:
                        bgm = await register(
                            await audio_bed.make_bgm_from(
                                c.ffmpeg,
                                c.settings.data_dir / made[0].path,
                                work / f"bgm_{uuid4().hex[:8]}.wav",
                                length,
                                speech,
                                fade_sec=bgm_prompts.FADE_SEC,
                            ),
                            f"BGM（生成: {caption[:40]}・{bpm}BPM）",
                        )
            except Exception as exc:  # noqa: BLE001 - 作れなくても和音で続ける
                log.warning("bgm_generate_failed", error=type(exc).__name__)
        if bgm is None:
            bgm = await register(
                await audio_bed.make_bgm(
                    c.ffmpeg, work / f"bgm_{uuid4().hex[:8]}.wav", total / fps + 0.5, speech
                ),
                "BGM（自動で作った和音）",
            )
        cmds.append(
            AddClip(
                track_id=tid[BGM_TRACK],
                asset_id=bgm.id,
                start_frame=0,
                duration_frames=total,
                # 音量は BGM のファイルに焼いてある（声の区間で下げる）。利用者が承認した
                # 既定値の変更（統合版 W2）があれば、その比を掛ける
                volume=round(min(4.0, max(0.0, bgm_ratio)), 4),
            )
        )
        notes = ["BGM を付けました（声の間は小さく、台詞の間は少し上げる）"]
        if bgm_ratio != 1.0:
            notes.append(f"BGM の音量を、承認した既定（{bgm_ratio * 100:.0f}%）にしました")
        if c.design.edits is not None:
            c.design.edits.made(project_id, purpose.value, ["bgm"])
        # 効果音の時刻: カード（場面の切り替え）の出始め・「ポン」と出る数値のテロップ
        # (置く始まりのフレーム, 種類, 「シュッ」の立ち上がりのフレーム数)
        cue: list[tuple[int, str, int]] = []
        fig = tracks.get("演出・図")
        if fig is not None:
            roles = await c.design.clip_roles(project_id)
            clips = sorted(
                # 終了画面（統合版 S9）はフェードで出すので鳴らさない（E3）
                (x for x in tl.clips if x.track_id == fig.id and roles.get(x.id) != "end_screen"),
                key=lambda x: x.start_frame,
            )
            prev_end = -1
            for x in clips:
                if x.start_frame != prev_end:  # 同じカードの項目の切り替えは数えない
                    pre = x.animation.preset if x.animation else None
                    kind = audio_bed.whoosh_kind(pre)
                    if kind is not None:
                        lead = round(audio_bed.WHOOSH_LEAD_FRAMES[kind] * fps / 30)
                        n_in = round(
                            audio_bed.entrance_frames(pre, prof.motion.in_frames) * fps / 30
                        )
                        st = audio_bed.whoosh_start(x.start_frame, n_in, kind, fps)
                        cue.append((st, "whoosh", lead))
                prev_end = x.end_frame
        roles_all = await c.design.clip_roles(project_id)
        for x in tl.clips:  # 統合版 P5: 順位の数字の着地で「ポン」
            if roles_all.get(x.id) == "rank_number":
                cue.append((x.start_frame + round(fps * 0.25), "pop", 0))
        txt = tracks.get("演出・文字")
        if txt is not None:
            for x in tl.clips:
                anim = x.animation.preset if x.animation is not None else None
                if x.track_id == txt.id and anim in audio_bed.POP_PRESETS:
                    cue.append((x.start_frame + round(fps * 0.25), "pop", 0))  # 止まる瞬間
        limit = prof.allowance(Component.SFX)
        cue.sort()
        if limit == 0:
            cue = []
        elif limit > 0:
            cue = cue[:limit]
        if cue:
            whooshes: dict[int, AssetRead] = {}  # 立ち上がりの長さごとに 1 つ
            for lead in sorted({ld for _, k, ld in cue if k == "whoosh"}):
                whooshes[lead] = await register(
                    await audio_bed.make_whoosh(
                        c.ffmpeg, work / f"sfx_whoosh_{uuid4().hex[:6]}.wav", lead / fps
                    ),
                    "効果音（切り替え）",
                )
            pop = await register(
                await audio_bed.make_pop(c.ffmpeg, work / f"sfx_pop_{uuid4().hex[:6]}.wav"),
                "効果音（強調）",
            )
            last = -(10**9)
            pops: list[int] = []  # 統合版 X4: ゲーム実況・子ども向けは鳴らす所に擬音
            for start, kind, lead in cue:
                a = whooshes[lead] if kind == "whoosh" else pop
                length = max(1, int((a.duration_sec or 0.3) * fps))  # 素材の長さを超えない
                if start < last or start + length > total:  # 重なる・最後をはみ出す音は置かない
                    continue
                cmds.append(
                    AddClip(
                        track_id=tid[SFX_TRACK],
                        asset_id=a.id,
                        start_frame=start,
                        duration_frames=length,
                        volume=audio_bed.WHOOSH_VOLUME
                        if kind == "whoosh"
                        else audio_bed.POP_VOLUME,
                    )
                )
                last = start + length
                if kind == "pop":
                    pops.append(start)
            if pops and purpose in (Purpose.GAME, Purpose.KIDS):
                notes += await self._onomatopoeia(project_id, tl, pops, total, prof, cmds, work)
            n = sum(1 for x in cmds if isinstance(x, AddClip) and x.track_id == tid[SFX_TRACK])
            if n:
                await c.design.record(project_id, "build_audio", {"used": {"sfx": n}})
                notes.append(f"効果音を {n} か所に付けました")
        await c.timeline.submit(
            project_id, CommandRequest(command=Batch(commands=cmds), description="BGM と効果音")
        )
        return notes

    async def _place_music(self, project_id: str, song: AssetRead) -> None:
        """曲を音声のトラックに頭から置く（前に置いた曲は外す）。"""
        c = self.c
        tl = await c.timeline.get_timeline(project_id)
        cmds: list[Any] = []
        track = next((t for t in tl.tracks if t.name == MUSIC_TRACK), None)
        if track is not None:
            cmds += [DeleteClip(clip_id=x.id) for x in tl.clips if x.track_id == track.id]
            tid = track.id
        else:
            tid = str(uuid4())
            cmds.append(AddTrack(kind=TrackKind.AUDIO, name=MUSIC_TRACK, track_id=tid))
        cmds.append(AddClip(track_id=tid, asset_id=song.id, start_frame=0))
        await c.timeline.submit(
            project_id, CommandRequest(command=Batch(commands=cmds), description="曲を置く")
        )

    # ---------- 2. 続けて作る ----------
    async def run(self, project_id: str, req: BuildRunRequest, progress: Any) -> dict[str, Any]:
        c = self.c
        sid = req.script_id
        notes: list[str] = []
        detail = await c.studio.detail(sid)
        if detail.project_id != project_id:
            raise BuildError("台本がこのプロジェクトのものではありません")
        song = await self._asset(project_id, MUSIC_NAME, AssetKind.AUDIO)
        if song is not None:
            return await self._run_music(project_id, detail, song, req, progress)
        has_tachie = any(ch.tachie_pack for ch in detail.characters)
        rec = await self._recording(project_id)
        keys = [
            k
            for k, _ in STEPS
            if (k != "tachie" or has_tachie) and (k != "recording" or rec is not None)
        ]
        redo = req.instruction.strip()
        if req.keep_script:
            if not detail.segments:
                raise BuildError("台本がまだありません。先に作ってください")
            # 見た目だけ作り直す: 台本・音声・字幕・立ち絵はそのまま、演出から
            keys = [k for k in keys if k in ("direction", "check", "render")]
        labels = dict(STEPS)

        async def at(key: str, frac: float = 0.0) -> None:
            i = keys.index(key)
            await progress((i + frac) / len(keys), f"{labels[key]}（{i + 1}/{len(keys)}）")

        if not req.keep_script:
            if rec is not None:
                notes += await self._read_recording(sid, rec, at)
            await self._script_to_tachie(sid, has_tachie, redo, at, notes, req.script_model)
            if rec is not None:
                await at("recording")
                notes += await self._place_recording(project_id, sid, rec, at)
                notes += await self._place_wipe(project_id, sid, rec)
            notes += await self._anchor_lag(project_id, sid)
        if not req.keep_script:
            notes += await self._place_slides(project_id, sid)
        # キャラクターの声を「ナレーション」トラックに置く（立ち絵の動画には音が無い）
        notes += await self._place_narration(project_id, sid)

        await at("direction")
        if req.keep_script and (k := text_size_factor(redo)) is not None:
            notes.append(await self._resize_text(project_id, k))
        text_scale = await self._text_scale(project_id)
        plan = (
            await c.direction.plan(
                DirectionPlanRequest(
                    script_id=sid,
                    use_llm=req.use_llm_direction,
                    instruction=redo if req.keep_script else "",
                )
            )
        ).plan
        res = await c.direction.apply(
            DirectionApplyRequest(script_id=sid, plan=plan, text_scale=text_scale)
        )
        notes += skipped_notes(list(getattr(res, "skipped", []) or []))
        await self._raise_track(project_id, WIPE_TRACK)  # P6: 演出の後でワイプを最も上に
        try:
            notes += await self._place_audio(project_id, sid)
        except Exception as exc:  # noqa: BLE001 - BGM・効果音が無くても動画は作る
            log.warning("build_audio_failed", error=str(exc)[:300])
            notes.append(f"BGM・効果音を付けられませんでした（{type(exc).__name__}）")

        await at("check")
        rreq = RenderRequest(
            project_id=project_id,
            script_id=sid,
            rich_direction=req.rich_direction,
            profile=await self._render_profile(project_id),
        )
        return await self._check_and_render(rreq, at, notes, progress)
