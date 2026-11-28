"""動画自動化ブリッジ: Blender の中だけで、文章から動画を作る。

サイドバー（N キー）の「動画自動化」タブ、「動画をつくる」パネル:
- はじめに … 必要なもの（声・AI・FFmpeg）を確かめる。アプリが動いていなければ起動する
- つくる … 作りたいものを文章で書く（長さ・向きは任意）
- 構成の確認 … 用途・尺・話し手・手順を見て、1 回承認すると最後まで続けて作る
- 制作中 … 進み具合。止めることもできる
- できあがり … 動画をシーケンサーに読み込む。もう一本つくる
「くわしい設定」の中に、従来の画面（1 手順ずつ実行・素材ごとの読み込み）を残している。

従来の画面（くわしい設定 → 従来の画面）:
1. 接続 … 同じ PC で動いているアプリ（既定 http://127.0.0.1:8765）につなぐ
2. 作るもの … 作りたいものを文章で書くと用途（字幕動画・解説・MV など）を判定し、
   その用途のデザインの型をプロジェクトに設定する
3. 作る手順 … 音声 → 字幕 → 演出 → 検査 → 書き出しを、1 つずつまたは続けて実行
4. 読み込み … 書き出した動画を今のシーンの空いているチャンネルに置く。
   または、タイムラインを素材ごと（音声・映像・画像・字幕）のストリップとして置き、
   Blender で細かく直す

通信や待ち時間のかかる処理は別スレッドで行い、Blender の画面は止めない。
bpy の操作はタイマー（メインスレッド）からだけ行う。
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time
from typing import Any

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
    StringProperty,
)

from . import setup_assist as sa
from .client import (
    DEFAULT_SERVER,
    STEP_LABELS,
    STEPS,
    BridgeError,
    Client,
    app_candidates,
    engine_command,
    find_app_dir,
    plan_strips,
    run_step,
)

# 画面の候補（EnumProperty の items はコールバックで返す。参照を保持しないと文字化けする）
_cache: dict[str, list[tuple[str, str, str]]] = {
    "projects": [],
    "scripts": [],
    "purposes": [],
    "packs": [("NONE", "立ち絵なし", "")],
}
MAX_CAST = 4  # 構成の確認で立ち絵を選べる話し手の数
_notes: list[str] = []  # 検査・演出の補足（画面に並べる）
_jobs: queue.Queue[tuple[str, Any]] = queue.Queue()
_ready: list[dict[str, Any]] = []  # 必要なものの確認結果（はじめに）
_engine: dict[str, Any] = {"proc": None}  # このアドオンが起動したアプリ
_busy = threading.Event()


def _items(key: str, empty: str):
    def f(_self, _ctx):
        return _cache[key] or [("", empty, "")]

    return f


class VA_Settings(bpy.types.PropertyGroup):
    server: StringProperty(name="接続先", default=DEFAULT_SERVER)  # type: ignore[valid-type]
    project: EnumProperty(  # type: ignore[valid-type]
        name="プロジェクト",
        items=_items("projects", "（未接続）"),
        update=lambda s, c: _on_project(s, c),
    )
    script: EnumProperty(name="台本", items=_items("scripts", "（台本なし）"))  # type: ignore[valid-type]
    instruction: StringProperty(  # type: ignore[valid-type]
        name="作りたいもの", description="例: ウルツァイト窒化ホウ素の解説動画を作って"
    )
    purpose: EnumProperty(name="用途", items=_items("purposes", "（未取得）"))  # type: ignore[valid-type]
    use_llm: BoolProperty(  # type: ignore[valid-type]
        name="AIで演出案を作る", default=True, description="オフは規則だけ（題名と数値のテロップ）"
    )
    status: StringProperty(name="状態", default="未接続")  # type: ignore[valid-type]
    video: StringProperty(name="書き出した動画", subtype="FILE_PATH")  # type: ignore[valid-type]
    with_subtitles: BoolProperty(  # type: ignore[valid-type]
        name="字幕も置く", default=True, description="台本の字幕をテキストのストリップで置く"
    )
    # ---- 動画をつくる ----
    screen: EnumProperty(  # type: ignore[valid-type]
        items=[
            ("START", "はじめに", ""),
            ("CREATE", "つくる", ""),
            ("REVIEW", "構成の確認", ""),
            ("WORKING", "制作中", ""),
            ("DONE", "できあがり", ""),
        ],
        default="START",
    )
    length: EnumProperty(  # type: ignore[valid-type]
        name="長さ",
        items=[
            ("0", "おまかせ", "用途から決めます（ショートは 45 秒、ほかは 2 分）"),
            ("30", "30 秒", ""),
            ("60", "1 分", ""),
            ("120", "2 分", ""),
            ("300", "5 分", ""),
        ],
    )
    orientation: EnumProperty(  # type: ignore[valid-type]
        name="向き",
        items=[
            ("auto", "おまかせ", "用途から決めます"),
            ("landscape", "横長（16:9）", ""),
            ("vertical", "縦長（9:16・ショート）", ""),
        ],
    )
    reference: PointerProperty(  # type: ignore[valid-type]
        name="参考資料",
        type=bpy.types.Text,
        description="テキストエディタに貼った資料を台本の参考にします（任意）",
    )
    recording: StringProperty(  # type: ignore[valid-type]
        name="画面録画",
        subtype="FILE_PATH",
        description=(
            "操作説明を作るときの画面録画（任意）。指定すると、録画を中央に置いて操作している所へ"
            "寄ります。ビデオ・デスクトップ・ダウンロードのフォルダに置いてください"
        ),
    )
    facecam: StringProperty(  # type: ignore[valid-type]
        name="顔の映像",
        subtype="FILE_PATH",
        description=(
            "ゲーム実況のワイプに入れる顔の映像（任意）。録画の HUD を避けた隅に、画面の 18% で"
            "置きます"
        ),
    )
    music: StringProperty(  # type: ignore[valid-type]
        name="曲",
        subtype="FILE_PATH",
        description=(
            "MV・リリックビデオを作るときの曲（任意）。ミュージック・デスクトップ・ダウンロードの"
            "フォルダに置いてください"
        ),
    )
    lyrics: PointerProperty(  # type: ignore[valid-type]
        name="歌詞",
        type=bpy.types.Text,
        description="テキストエディタに貼った歌詞（1 行 1 字幕）。無ければ歌声を文字起こしします",
    )
    mv_image: StringProperty(  # type: ignore[valid-type]
        name="絵",
        subtype="FILE_PATH",
        description=(
            "MV のショットを作る 1 枚の絵（任意）。透明の部分があれば切り抜きとして使います。"
            "ピクチャ・デスクトップ・ダウンロードのフォルダに置いてください"
        ),
    )
    lyric_direction: EnumProperty(  # type: ignore[valid-type]
        name="曲の方向性",
        description="選ぶと、歌の区間をフレーズごとの「歌詞の画面」で見せます（統合版 U1）",
        items=[
            ("AUTO", "おまかせ", "歌詞は動く文字で出します"),
            ("vocaloid_cool", "ボカロ（かっこいい系）", ""),
            ("vocaloid_cute", "ボカロ（かわいい系）", ""),
            ("vocaloid_calm", "ボカロ（しっとり系）", ""),
            ("anison", "アニソン", ""),
            ("ballad", "バラード", ""),
            ("rap", "ラップ", ""),
            ("pop", "ポップソング", ""),
            ("idol", "アイドルソング", ""),
        ],
    )
    plan_json: StringProperty(default="")  # type: ignore[valid-type]
    job_id: StringProperty(default="")  # type: ignore[valid-type]
    progress: FloatProperty(default=0.0, min=0.0, max=1.0)  # type: ignore[valid-type]
    step: StringProperty(default="")  # type: ignore[valid-type]
    # ---- 構成の確認で変える ----
    re_purpose: EnumProperty(name="用途", items=_items("purposes", "（未取得）"))  # type: ignore[valid-type]
    re_length: EnumProperty(  # type: ignore[valid-type]
        name="長さ",
        items=[
            ("KEEP", "そのまま", ""),
            ("30", "30 秒", ""),
            ("60", "1 分", ""),
            ("120", "2 分", ""),
            ("300", "5 分", ""),
        ],
    )
    tachie0: EnumProperty(name="立ち絵", items=_items("packs", "立ち絵なし"))  # type: ignore[valid-type]
    tachie1: EnumProperty(name="立ち絵", items=_items("packs", "立ち絵なし"))  # type: ignore[valid-type]
    tachie2: EnumProperty(name="立ち絵", items=_items("packs", "立ち絵なし"))  # type: ignore[valid-type]
    tachie3: EnumProperty(name="立ち絵", items=_items("packs", "立ち絵なし"))  # type: ignore[valid-type]
    # ---- できあがった後の手直し ----
    fix_text: StringProperty(  # type: ignore[valid-type]
        name="直したいこと",
        description="例: もっと短く／ずんだもんの説明を増やして／カードを減らして",
    )
    auto_import: BoolProperty(  # type: ignore[valid-type]
        name="できたら読み込む", default=True, description="できた動画をシーケンサーに置きます"
    )


def _on_project(st, _ctx) -> None:
    _load_scripts(st)


def _settings(ctx=None) -> "VA_Settings":
    return (ctx or bpy.context).scene.va_bridge


def _client(st: "VA_Settings") -> Client:
    return Client(st.server)


def _redraw() -> None:
    for w in bpy.context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == "SEQUENCE_EDITOR":
                a.tag_redraw()


def _drain() -> float | None:
    """別スレッドからの結果を画面に反映する（タイマー）。"""
    st = _settings()
    try:
        while True:
            kind, val = _jobs.get_nowait()
            if kind == "status":
                st.status = val
            elif kind == "notes":
                _notes[:] = list(val)[:20]
            elif kind == "video":
                st.video = val or ""
            elif kind == "cache":
                key, items = val
                _cache[key] = items
            elif kind == "ready":
                _ready[:] = list(val)
            elif kind == "plan":
                st.plan_json = json.dumps(val, ensure_ascii=False)
                _fill_review(st, val)
                st.screen = "REVIEW"
            elif kind == "progress":
                st.progress, st.step = val
            elif kind == "screen":
                st.screen = val
            elif kind == "done":
                st.video = val.get("video") or ""
                _notes[:] = list(val.get("notes") or [])[:20]
                st.screen = "DONE"
                st.job_id = ""
                if st.auto_import and st.video:
                    try:
                        bpy.ops.va_bridge.import_video()
                    except RuntimeError as e:
                        st.status = f"読み込めませんでした: {e}"
    except queue.Empty:
        pass
    _redraw()
    return 0.3 if _busy.is_set() or not _jobs.empty() else None


def _fill_review(st, plan: dict[str, Any]) -> None:
    """構成の確認の選択肢を、届いた構成の値に合わせる（メインスレッドで呼ぶ）。"""
    packs = [(p["id"], p["name"], "") for p in plan.get("packs") or []]
    _cache["packs"] = [("NONE", "立ち絵なし", ""), *packs]
    if any(k == plan.get("purpose") for k, _, _ in _cache["purposes"]):
        st.re_purpose = plan["purpose"]
    st.re_length = "KEEP"
    ids = {k for k, _, _ in _cache["packs"]}
    for i, ch in enumerate((plan.get("characters") or [])[:MAX_CAST]):
        pack = ch.get("tachie_pack") or "NONE"
        setattr(st, f"tachie{i}", pack if pack in ids else "NONE")


def _background(fn, *args) -> bool:
    """fn を別スレッドで実行する。実行中なら断る。"""
    if _busy.is_set():
        return False
    _busy.set()

    def run():
        try:
            fn(*args)
        except BridgeError as e:
            _jobs.put(("status", f"エラー: {e}"))
        except Exception as e:  # noqa: BLE001
            _jobs.put(("status", f"エラー: {type(e).__name__}: {e}"))
        finally:
            _busy.clear()

    threading.Thread(target=run, daemon=True).start()
    if not bpy.app.timers.is_registered(_drain):
        bpy.app.timers.register(_drain, first_interval=0.2)
    return True


def _load_scripts(st: "VA_Settings") -> None:
    if not st.project:
        return
    try:
        rows = _client(st).scripts(st.project)
    except BridgeError as e:
        st.status = f"エラー: {e}"
        return
    _cache["scripts"] = [(r["id"], r.get("title") or r["id"][:8], "") for r in rows]


# ---------------------------------------------------------------- 操作
class VA_OT_connect(bpy.types.Operator):
    bl_idname = "va_bridge.connect"
    bl_label = "接続"
    bl_description = "動画自動化アプリにつなぎ、プロジェクトと用途の一覧を読み込みます"

    def execute(self, context):
        st = _settings(context)
        try:
            c = _client(st)
            h = c.health()
            projects = c.projects()
            profiles = c.profiles()
        except BridgeError as e:
            st.status = f"エラー: {e}"
            return {"CANCELLED"}
        _cache["projects"] = [(p["id"], p["name"], "") for p in projects]
        _cache["purposes"] = [(p["purpose"], p["label"], p.get("summary", "")) for p in profiles]
        st.status = f"接続しました（{h.get('version', '')}・プロジェクト {len(projects)} 件）"
        if projects:
            st.project = projects[-1]["id"]
            _load_scripts(st)
        return {"FINISHED"}


class VA_OT_classify(bpy.types.Operator):
    bl_idname = "va_bridge.classify"
    bl_label = "用途を判定"
    bl_description = "作りたいものの文章から用途（デザインの型）を判定します"

    def execute(self, context):
        st = _settings(context)
        if not st.instruction.strip():
            st.status = "作りたいものを書いてください"
            return {"CANCELLED"}
        try:
            r = _client(st).classify(st.instruction, bool(st.script))
        except BridgeError as e:
            st.status = f"エラー: {e}"
            return {"CANCELLED"}
        st.purpose = r["purpose"]
        label = {k: v for k, v, _ in _cache["purposes"]}
        top = r.get("candidates", [])[:3]
        cands = "・".join(label.get(x["purpose"], x["purpose"]) for x in top)
        ask = "（確認してください）" if r.get("needs_confirmation") else ""
        st.status = f"用途: {label.get(r['purpose'], r['purpose'])}{ask}　候補: {cands}"
        _notes[:] = [r["note"]] if r.get("note") else []
        return {"FINISHED"}


class VA_OT_set_purpose(bpy.types.Operator):
    bl_idname = "va_bridge.set_purpose"
    bl_label = "この用途にする"
    bl_description = (
        "選んだ用途のデザインの型をプロジェクトに設定します（別用途の演出は使われません）"
    )

    def execute(self, context):
        st = _settings(context)
        if not st.project or not st.purpose:
            st.status = "プロジェクトと用途を選んでください"
            return {"CANCELLED"}
        try:
            _client(st).set_purpose(st.project, st.purpose)
        except BridgeError as e:
            st.status = f"エラー: {e}"
            return {"CANCELLED"}
        st.status = f"用途を「{st.purpose}」にしました"
        return {"FINISHED"}


class VA_OT_run(bpy.types.Operator):
    bl_idname = "va_bridge.run"
    bl_label = "実行"
    bl_description = "作る手順を実行します（画面は止まりません）"

    step: EnumProperty(  # type: ignore[valid-type]
        items=[(s, STEP_LABELS[s], "") for s in STEPS] + [("all", "すべて続けて", "")]
    )

    def execute(self, context):
        st = _settings(context)
        if not st.project or not st.script:
            st.status = "プロジェクトと台本を選んでください"
            return {"CANCELLED"}
        steps = list(STEPS) if self.step == "all" else [self.step]
        server, pid, sid, use_llm = st.server, st.project, st.script, st.use_llm

        def work():
            c = Client(server)
            for s in steps:
                _jobs.put(("status", f"{STEP_LABELS[s]}…"))
                r = run_step(c, s, pid, sid, use_llm, lambda m: _jobs.put(("status", m)))
                _jobs.put(("status", r["message"]))
                if r.get("notes"):
                    _jobs.put(("notes", r["notes"]))
                if r.get("video"):
                    _jobs.put(("video", r["video"]))

        if not _background(work):
            st.status = "ほかの処理が実行中です"
            return {"CANCELLED"}
        return {"FINISHED"}


class VA_OT_import_video(bpy.types.Operator):
    bl_idname = "va_bridge.import_video"
    bl_label = "動画を読み込む"
    bl_description = "書き出した動画（音声つき）を今のシーンの空いているチャンネルに置きます"

    def execute(self, context):
        st = _settings(context)
        path = bpy.path.abspath(st.video)
        if not path:
            st.status = "先に書き出してください"
            return {"CANCELLED"}
        scene = context.scene
        se = scene.sequence_editor or scene.sequence_editor_create()
        empty = len(se.strips_all) == 0
        used = {s.channel for s in se.strips_all}
        ch = next(i for i in range(1, 128) if i not in used and i + 1 not in used)
        start = scene.frame_current
        try:
            mov = se.strips.new_movie("自動化_動画", path, ch + 1, start, fit_method="FIT")
            fps = float(getattr(mov, "fps", 0) or 0)
            note = ""
            el = mov.elements[0] if len(mov.elements) else None
            if empty and el is not None and el.orig_width and el.orig_height:
                r = scene.render
                if (r.resolution_x, r.resolution_y) != (el.orig_width, el.orig_height):
                    # 空のシーンは動画の解像度に合わせる（拡大・切り取りを防ぐ）
                    se.strips.remove(mov)
                    r.resolution_x, r.resolution_y = el.orig_width, el.orig_height
                    mov = se.strips.new_movie("自動化_動画", path, ch + 1, start, fit_method="FIT")
                    note += f"・解像度を {el.orig_width}x{el.orig_height} にしました"
            scene_fps = scene.render.fps / scene.render.fps_base
            if fps > 0 and abs(fps - scene_fps) > 0.01:
                if empty:
                    # 空のシーンは動画のフレームレートに合わせる（音と映像の長さを揃える）
                    se.strips.remove(mov)
                    scene.render.fps, scene.render.fps_base = _fps_pair(fps)
                    mov = se.strips.new_movie("自動化_動画", path, ch + 1, start, fit_method="FIT")
                    note += f"・シーンを {fps:g} fps にしました"
                else:
                    note += f"・注意: 動画 {fps:g} fps とシーン {scene_fps:g} fps が違います"
            snd = se.strips.new_sound("自動化_音声", path, ch, start)
        except RuntimeError as e:
            st.status = f"読み込めません: {e}"
            return {"CANCELLED"}
        end = max(_end(mov), _end(snd))
        if scene.frame_end < end:
            scene.frame_end = end - 1
        st.status = f"チャンネル {ch}〜{ch + 1} に読み込みました{note}"
        st.status += _show_in_sequencer(context, scene)
        return {"FINISHED"}


class VA_OT_import_timeline(bpy.types.Operator):
    bl_idname = "va_bridge.import_timeline"
    bl_label = "素材ごとに読み込む"
    bl_description = (
        "アプリのタイムラインを、音声・映像・画像・字幕のストリップとして今のシーンに置きます。"
        "演出の動きは再現しません"
    )

    def execute(self, context):
        st = _settings(context)
        if not st.project:
            st.status = "先に接続してプロジェクトを選んでください"
            return {"CANCELLED"}
        try:
            tl = _client(st).timeline_for_blender(st.project, st.script or None)
        except BridgeError as e:
            st.status = str(e)
            return {"CANCELLED"}
        plan = plan_strips(tl, subtitles=st.with_subtitles)
        res = place_strips(context.scene, plan)
        _notes[:] = res["notes"]
        st.status = res["message"] + _show_in_sequencer(context, context.scene)
        return {"FINISHED"}


def _show_in_sequencer(context, scene) -> str:
    """Blender 5 はシーケンサーに表示するシーンを作業画面ごとに選ぶ。未選択なら今のシーンにする。"""
    ws = getattr(context, "workspace", None)
    if ws is None or not hasattr(ws, "sequencer_scene"):
        return ""  # 4.x は今のシーンがそのまま表示される
    if ws.sequencer_scene is None:
        ws.sequencer_scene = scene
        return ""
    if ws.sequencer_scene != scene:
        return f"（シーケンサーには「{ws.sequencer_scene.name}」が表示されています）"
    return ""


def _set_range(strip, start: int, end: int) -> None:
    """表示する範囲（1 始まり、end は含まない）を決める（5.3 は left/right_handle）。"""
    if hasattr(strip, "left_handle"):
        strip.left_handle, strip.right_handle = start, end
    else:
        strip.frame_final_start, strip.frame_final_end = start, end


def _key(strip, prop: str, frames_values) -> None:
    for f, v in frames_values:
        setattr(strip, prop, v)
        strip.keyframe_insert(prop, frame=f)


def place_strips(scene, plan: dict) -> dict:
    """plan_strips の結果をシーンに置く。空のシーンは解像度・fps を合わせる。"""
    se = scene.sequence_editor or scene.sequence_editor_create()
    empty = len(se.strips_all) == 0
    notes = list(plan["notes"])
    r = scene.render
    if empty:
        r.resolution_x, r.resolution_y = plan["width"], plan["height"]
        r.fps, r.fps_base = plan["fps_num"], plan["fps_den"]
        origin = scene.frame_start
    else:
        if (r.resolution_x, r.resolution_y) != (plan["width"], plan["height"]):
            notes.append(
                f"注意: シーンの解像度がプロジェクト（{plan['width']}x{plan['height']}）と違います"
            )
        if abs(r.fps / r.fps_base - plan["fps_num"] / plan["fps_den"]) > 0.01:
            notes.append("注意: シーンのフレームレートがプロジェクトと違います（位置がずれます）")
        origin = scene.frame_current
    used = [s.channel for s in se.strips_all]
    shift = max(used) if used else 0
    lowest_visual = min(
        (s["channel"] for s in plan["strips"] if s["type"] in ("movie", "image")), default=0
    )
    n = 0
    failed = 0
    for sp in plan["strips"]:
        ch = sp["channel"] + shift
        s0, s1 = origin + sp["start"], origin + sp["end"]
        if s1 <= s0:
            continue
        try:
            if sp["type"] == "sound":
                st = se.strips.new_sound(sp["name"], sp["path"], ch, s0 - sp["source_in"])
                _set_range(st, s0, s1)
                v = sp["volume"]
                st.volume = v
                if sp["fade_in"]:
                    _key(st, "volume", [(s0, 0.0), (s0 + sp["fade_in"], v)])
                if sp["fade_out"]:
                    _key(st, "volume", [(s1 - sp["fade_out"], v), (s1, 0.0)])
            elif sp["type"] in ("movie", "image"):
                if sp["type"] == "movie":
                    st = se.strips.new_movie(
                        sp["name"], sp["path"], ch, s0 - sp["source_in"], fit_method=sp["fit"]
                    )
                else:
                    st = se.strips.new_image(sp["name"], sp["path"], ch, s0, fit_method=sp["fit"])
                _set_range(st, s0, s1)
                t = st.transform
                t.scale_x *= sp["scale"]
                t.scale_y *= sp["scale"]
                t.offset_x, t.offset_y = sp["offset_x"], sp["offset_y"]
                if sp["channel"] > lowest_visual:
                    st.blend_type = "ALPHA_OVER"
                a = sp["opacity"]
                st.blend_alpha = a
                if sp["fade_in"]:
                    _key(st, "blend_alpha", [(s0, 0.0), (s0 + sp["fade_in"], a)])
                if sp["fade_out"]:
                    _key(st, "blend_alpha", [(s1 - sp["fade_out"], a), (s1, 0.0)])
            elif sp["type"] == "text":
                try:
                    st = se.strips.new_effect(sp["name"], "TEXT", ch, s0, length=s1 - s0)
                except TypeError:  # 4.x は frame_end
                    st = se.strips.new_effect(sp["name"], "TEXT", ch, s0, frame_end=s1)
                _set_range(st, s0, s1)
                st.text = sp["text"]
                st.font_size = sp["font_size"]
                st.location = (0.5, 0.08)
                st.anchor_x, st.anchor_y = "CENTER", "BOTTOM"
                st.alignment_x = "CENTER"
                st.use_outline = True
                st.outline_width = 0.15
                st.wrap_width = 0.9
            else:
                continue
            st.mute = bool(sp.get("mute"))
            n += 1
        except (RuntimeError, TypeError, AttributeError) as e:
            failed += 1
            if failed <= 3:
                notes.append(f"置けませんでした: {sp.get('name')}（{e}）")
    end = origin + plan["frames"]
    if scene.frame_end < end - 1:
        scene.frame_end = end - 1
    lo, hi = plan["channels"]
    msg = f"{n} 個のストリップをチャンネル {lo + shift}〜{hi + shift} に置きました"
    if failed:
        msg += f"（{failed} 個は失敗）"
    return {"message": msg, "notes": notes, "placed": n, "failed": failed}


def _fps_pair(fps: float) -> tuple[int, float]:
    """29.97 などは fps=30, fps_base=1.001 で表す。"""
    for n in (24, 30, 60, 120):
        if abs(fps - n / 1.001) < 0.01:
            return n, 1.001
    return round(fps), 1.0


def _end(strip) -> int:
    """ストリップの終わりのフレーム（Blender の版で名前が違う）。"""
    for name in ("right_handle", "frame_final_end"):
        v = getattr(strip, name, None)
        if isinstance(v, (int, float)):
            return int(v)
    return int(strip.frame_start + strip.frame_duration)


# ---------------------------------------------------------------- 動画をつくる
def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _app_dir() -> str | None:
    pr = _prefs()
    if pr and pr.app_dir and find_app_dir([bpy.path.abspath(pr.app_dir)]):
        return bpy.path.abspath(pr.app_dir)
    return find_app_dir(
        app_candidates(
            os.path.expanduser("~"),
            os.environ.get("LOCALAPPDATA", ""),
            os.path.dirname(os.path.abspath(__file__)),
        )
    )


def _start_engine(server: str) -> str:
    """アプリ（API）を画面なしで起動し、応答するまで待つ（別スレッドから呼ぶ）。"""
    app_dir = _app_dir()
    if app_dir is None:
        raise BridgeError(
            "アプリが見つかりません。くわしい設定の「アプリの場所」でフォルダを選んでください"
        )
    cmd = engine_command(app_dir)
    logs = os.path.join(app_dir, "data", "logs")
    os.makedirs(logs, exist_ok=True)
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    with open(os.path.join(logs, "blender_engine.log"), "ab") as log:
        _engine["proc"] = subprocess.Popen(  # noqa: S603 - アプリの Python を固定の引数で起動
            cmd, cwd=app_dir, stdout=log, stderr=log, stdin=subprocess.DEVNULL, creationflags=flags
        )
    c = Client(server)
    for _ in range(120):
        if _engine["proc"].poll() is not None:
            raise BridgeError("アプリが起動直後に止まりました（data/logs/blender_engine.log）")
        try:
            c.health()
            return app_dir
        except BridgeError:
            time.sleep(1.0)
    raise BridgeError("アプリが 2 分以内に応答しませんでした")


def _ensure_engine(server: str) -> None:
    """アプリが応答しなければ（止まっていれば）起動し直す。"""
    try:
        Client(server).health()
    except BridgeError:
        pr = _prefs()
        if pr is not None and not pr.auto_start:
            raise
        _jobs.put(("status", "アプリを起動しています…"))
        _start_engine(server)


def _check_ready(server: str, start: bool) -> None:
    c = Client(server)
    try:
        c.health()
    except BridgeError:
        if not start:
            raise
        _jobs.put(("status", "アプリを起動しています…（初回は 1 分ほど）"))
        _start_engine(server)
    _jobs.put(("status", "必要なものを確かめています…"))
    try:
        c.call("POST", "/providers/discover", {}, timeout=60)  # ローカル LLM を探す
    except BridgeError:
        pass
    ready = c.readiness()
    _jobs.put(("ready", ready))
    missing = [r["label"] for r in ready if r["required"] and not r["ok"]]
    if missing:
        _jobs.put(("status", "足りないもの: " + "、".join(missing)))
    else:
        _jobs.put(("status", "準備ができました"))
        _jobs.put(("screen", "CREATE"))


# ---------------------------------------------------------------- 導入の補助（はじめに）
_SETUP_INFO = {
    "start_voice": ("VOICEVOX を起動", None),
    "install_voice": ("VOICEVOX を入れる", "voice"),
    "install_ffmpeg": ("FFmpeg を入れる", "ffmpeg"),
    "start_ollama": ("Ollama を起動", None),
    "install_ollama": ("Ollama を入れる", "ollama"),
    "pull_model": (f"文章を作る AI（{sa.LLM_MODEL}）を入れる", None),
}


def _localappdata() -> str:
    return os.environ.get("LOCALAPPDATA", "")


def _setup_actions() -> list[str]:
    """はじめにの確認結果から、出すボタン（足りないものを起動する・入れる）を決める。"""
    out: list[str] = []
    bad = {r["key"] for r in _ready if not r["ok"]}
    app = _app_dir()
    if "voice" in bad:
        found = sa.find_voicevox(app, _localappdata(), _voice_dir())
        out.append("start_voice" if found else "install_voice")
    if "ffmpeg" in bad:
        out.append("install_ffmpeg")
    if "llm" in bad:
        if sa.find_ollama(_localappdata()):
            out += ["start_ollama", "pull_model"]
        else:
            out.append("install_ollama")
    return out


def _voice_dir() -> str:
    pr = _prefs()
    return bpy.path.abspath(pr.voicevox_dir) if pr and pr.voicevox_dir else ""


def _restart_engine(server: str) -> None:
    proc = _engine.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()
        proc.wait(timeout=30)
        _start_engine(server)
    else:
        _jobs.put(("status", "アプリを起動し直してください（FFmpeg を読み込むため）"))


def _do_setup(what: str, server: str, app: str | None) -> None:
    def prog(label):
        return lambda f: _jobs.put(("progress", (f, label)))

    local = _localappdata()
    logs = os.path.join(app, "data", "logs") if app else None
    if logs:
        os.makedirs(logs, exist_ok=True)
    if what in ("install_voice", "install_ffmpeg", "install_ollama") and not app:
        raise BridgeError("アプリの場所が分かりません（くわしい設定で選んでください）")
    if what == "install_voice":
        src = sa.SOURCES["voice"]
        dl = os.path.join(sa.tools_dir(app), "_dl", "voicevox_engine.vvpp")
        sa.download(src, dl, prog("VOICEVOX をダウンロード中"))
        sa.extract_zip(dl, os.path.join(sa.tools_dir(app), "voicevox"), prog("VOICEVOX を展開中"))
        os.remove(dl)
        what = "start_voice"
    if what == "start_voice":
        exe = sa.find_voicevox(app, local, _voice_dir())
        if exe is None:
            raise BridgeError("VOICEVOX ENGINE（run.exe）が見つかりません")
        _jobs.put(("status", "VOICEVOX を起動しています…（30 秒ほど）"))
        log = os.path.join(logs, "voicevox.log") if logs else None
        _engine["voice"] = sa.start_process(sa.voicevox_command(exe), os.path.dirname(exe), log)
        for _ in range(90):
            try:
                ready = {r["key"]: r["ok"] for r in Client(server).readiness()}
                if ready.get("voice"):
                    break
            except BridgeError:
                pass
            time.sleep(2.0)
    elif what == "install_ffmpeg":
        src = sa.SOURCES["ffmpeg"]
        dl = os.path.join(sa.tools_dir(app), "_dl", "ffmpeg.zip")
        sa.download(src, dl, prog("FFmpeg をダウンロード中"))
        sa.extract_zip(dl, os.path.join(sa.tools_dir(app), "ffmpeg"), prog("FFmpeg を展開中"))
        os.remove(dl)
        _restart_engine(server)
    elif what == "install_ollama":
        src = sa.SOURCES["ollama"]
        dl = os.path.join(sa.tools_dir(app), "_dl", "OllamaSetup.exe")
        sa.download(src, dl, prog("Ollama をダウンロード中"))
        _jobs.put(
            ("status", "Ollama のインストーラーを開きました。画面の案内に沿って進めてください")
        )
        sa.start_process([dl])
        return
    elif what == "start_ollama":
        exe = sa.find_ollama(local)
        if exe is None:
            raise BridgeError("Ollama が見つかりません")
        sa.start_process([exe, "serve"], None, os.path.join(logs, "ollama.log") if logs else None)
        time.sleep(5.0)
    elif what == "pull_model":
        _jobs.put(("status", f"{sa.LLM_MODEL} を入れています（数分〜数十分）…"))
        _pull_model(prog(f"{sa.LLM_MODEL} をダウンロード中"))
    _check_ready(server, start=True)


def _pull_model(progress) -> None:
    """Ollama（同じ PC）にモデルを取り込ませる（ollama.com の公式ライブラリから）。"""
    import urllib.request

    body = json.dumps({"model": sa.LLM_MODEL, "stream": True}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/pull", data=body, headers={"Content-Type": "application/json"}
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=3600) as r:
        for line in r:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                raise BridgeError(f"モデルを入れられませんでした: {ev['error']}")
            if ev.get("total") and ev.get("completed"):
                progress(ev["completed"] / ev["total"])


class VA_OT_setup(bpy.types.Operator):
    bl_idname = "va_bridge.setup"
    bl_label = "準備"
    bl_description = "足りないものを起動します。入れるときは配布元と大きさを見せて確認します"

    what: StringProperty()  # type: ignore[valid-type]

    def _source(self):
        key = _SETUP_INFO.get(self.what, ("", None))[1]
        return sa.SOURCES.get(key) if key else None

    def invoke(self, context, event):
        if self.what.startswith("install") or self.what == "pull_model":
            if not getattr(bpy.app, "online_access", True):
                _settings(context).status = (
                    "Blender の設定でインターネットへの接続が許可されていません"
                    "（編集 → プリファレンス → システム → ネットワーク）"
                )
                return {"CANCELLED"}
            return context.window_manager.invoke_props_dialog(self, width=560)
        return self.execute(context)

    def draw(self, context):
        lay = self.layout
        src = self._source()
        app = _app_dir() or "（アプリの場所が未設定）"
        if src is not None:
            lay.label(text=f"{src.label} を入れます", icon="IMPORT")
            lay.label(text=f"配布元: {src.origin}")
            lay.label(text=f"大きさ: 約 {sa.human_size(src.size)}")
            lay.label(text=f"入れる場所: {sa.tools_dir(app)}")
            lay.label(text=src.terms)
        else:
            lay.label(text=f"{sa.LLM_MODEL} を Ollama に入れます", icon="IMPORT")
            lay.label(text="配布元: Ollama 公式ライブラリ（ollama.com/library/qwen3）")
            lay.label(text=f"大きさ: 約 {sa.human_size(sa.LLM_MODEL_SIZE)}")
            lay.label(text="ライセンス: Apache 2.0")
        lay.label(text="「OK」を押すとダウンロードを始めます", icon="INFO")

    def execute(self, context):
        st = _settings(context)
        if self.what not in _SETUP_INFO:
            return {"CANCELLED"}
        if not _background(_do_setup, self.what, st.server, _app_dir()):
            st.status = "ほかの処理が実行中です"
            return {"CANCELLED"}
        return {"FINISHED"}


class VA_OT_start(bpy.types.Operator):
    bl_idname = "va_bridge.start"
    bl_label = "はじめる"
    bl_description = "アプリにつなぎ（動いていなければ起動し）、必要なものが揃っているか確かめます"

    def execute(self, context):
        st = _settings(context)
        pr = _prefs()
        start = pr.auto_start if pr else True
        if not _background(_check_ready, st.server, start):
            st.status = "ほかの処理が実行中です"
            return {"CANCELLED"}
        return {"FINISHED"}


class VA_OT_make_plan(bpy.types.Operator):
    bl_idname = "va_bridge.make_plan"
    bl_label = "構成を作る"
    bl_description = "文章から用途・長さ・話し手・手順を決めます（まだ動画は作りません）"

    def execute(self, context):
        st = _settings(context)
        text = st.instruction.strip()
        if not text:
            st.status = "作りたいものを書いてください"
            return {"CANCELLED"}
        ref = st.reference.as_string() if st.reference else ""
        dur = int(st.length) or None
        server, orient = st.server, st.orientation
        rec = bpy.path.abspath(st.recording) if st.recording else ""
        cam = bpy.path.abspath(st.facecam) if st.facecam and st.recording else ""
        song = bpy.path.abspath(st.music) if st.music else ""
        words = st.lyrics.as_string() if st.lyrics else ""
        pic = bpy.path.abspath(st.mv_image) if st.mv_image else ""
        direction = "" if st.lyric_direction == "AUTO" else st.lyric_direction

        def work():
            _ensure_engine(server)
            _jobs.put(("status", "構成を考えています…"))
            cl = Client(server)
            profiles = cl.profiles()
            _jobs.put(
                (
                    "cache",
                    (
                        "purposes",
                        [(p["purpose"], p["label"], p.get("summary", "")) for p in profiles],
                    ),
                )
            )
            plan = cl.build_plan(text, dur, orient, ref, rec, song, words, pic, direction, cam)
            _jobs.put(("plan", plan))
            _jobs.put(("status", "構成を確認してください"))

        if not _background(work):
            st.status = "ほかの処理が実行中です"
            return {"CANCELLED"}
        return {"FINISHED"}


class VA_OT_make_run(bpy.types.Operator):
    bl_idname = "va_bridge.make_run"
    bl_label = "この内容で作る"
    bl_description = (
        "構成の内容を承認し、台本・音声・字幕・演出・書き出しまで続けて作ります。"
        "途中で承認を求めることはありません"
    )

    revise: BoolProperty(default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]
    keep_script: BoolProperty(default=False, options={"SKIP_SAVE"})  # type: ignore[valid-type]

    @classmethod
    def description(cls, _context, props):
        if props.revise and props.keep_script:
            return "台本・声はそのままで、画面の演出（カード・テロップ・題名）だけを作り直します"
        if props.revise:
            return "前の台本と「直したいこと」を AI に渡して、台本から作り直します"
        return cls.bl_description

    def execute(self, context):
        st = _settings(context)
        try:
            plan = json.loads(st.plan_json)
        except ValueError:
            st.status = "先に構成を作ってください"
            return {"CANCELLED"}
        server = st.server
        fix = st.fix_text.strip() if self.revise else ""
        keep = bool(self.revise and self.keep_script)
        if self.revise and not fix:
            st.status = "直したいことを書いてください"
            return {"CANCELLED"}
        back = "DONE" if self.revise else "REVIEW"

        def work():
            _ensure_engine(server)
            c = Client(server)
            job = c.build_run(plan["project_id"], plan["script_id"], fix, keep)
            jid = job["id"]
            _jobs.put(("progress", (0.0, "始めています…")))
            _jobs.put(("screen", "WORKING"))
            _jobs.put(("status", "制作中です（画面は使えます）"))
            _job_ref["id"] = jid
            while True:
                j = c.job(jid)
                _jobs.put(("progress", (float(j.get("progress") or 0), j.get("message") or "")))
                if j["status"] == "SUCCEEDED":
                    _jobs.put(("done", j.get("result") or {}))
                    _jobs.put(("status", "できあがりました"))
                    return
                if j["status"] in ("FAILED", "CANCELLED"):
                    _jobs.put(("screen", back))
                    why = "止めました" if j["status"] == "CANCELLED" else (j.get("error") or "")
                    raise BridgeError(f"作れませんでした: {why}")
                time.sleep(1.0)

        if not _background(work):
            st.status = "ほかの処理が実行中です"
            return {"CANCELLED"}
        return {"FINISHED"}


_job_ref: dict[str, str] = {"id": ""}


class VA_OT_make_change(bpy.types.Operator):
    bl_idname = "va_bridge.make_change"
    bl_label = "変更を反映"
    bl_description = "用途・長さ・立ち絵の変更を構成に反映します（まだ動画は作りません）"

    def execute(self, context):
        st = _settings(context)
        plan = _plan(st)
        if not plan:
            st.status = "先に構成を作ってください"
            return {"CANCELLED"}
        purpose = st.re_purpose if st.re_purpose and st.re_purpose != plan["purpose"] else None
        dur = None if st.re_length == "KEEP" else int(st.re_length)
        tachie = {}
        for i, ch in enumerate((plan.get("characters") or [])[:MAX_CAST]):
            v = getattr(st, f"tachie{i}")
            tachie[ch["name"]] = "" if v == "NONE" else v
        server = st.server

        def work():
            _ensure_engine(server)
            new = Client(server).build_change(
                plan["project_id"], plan["script_id"], purpose, dur, tachie
            )
            _jobs.put(("plan", new))
            _jobs.put(("status", "構成を変えました。確認してください"))

        if not _background(work):
            st.status = "ほかの処理が実行中です"
            return {"CANCELLED"}
        return {"FINISHED"}


class VA_OT_make_cancel(bpy.types.Operator):
    bl_idname = "va_bridge.make_cancel"
    bl_label = "止める"
    bl_description = "制作を途中で止めます（作ったところまでは残ります）"

    def execute(self, context):
        st = _settings(context)
        if _job_ref["id"]:
            try:
                Client(st.server).cancel_job(_job_ref["id"])
            except BridgeError as e:
                st.status = f"エラー: {e}"
                return {"CANCELLED"}
        return {"FINISHED"}


class VA_OT_make_goto(bpy.types.Operator):
    bl_idname = "va_bridge.make_goto"
    bl_label = "移動"
    bl_options = {"INTERNAL"}

    screen: StringProperty()  # type: ignore[valid-type]

    def execute(self, context):
        _settings(context).screen = self.screen
        return {"FINISHED"}


class VA_OT_open_folder(bpy.types.Operator):
    bl_idname = "va_bridge.open_folder"
    bl_label = "フォルダを開く"
    bl_description = "できあがった動画のあるフォルダを開きます"

    def execute(self, context):
        path = bpy.path.abspath(_settings(context).video)
        if path and os.path.exists(path):
            bpy.ops.wm.path_open(filepath=os.path.dirname(path))
        return {"FINISHED"}


def _plan(st) -> dict[str, Any]:
    try:
        return json.loads(st.plan_json) if st.plan_json else {}
    except ValueError:
        return {}


class VA_PT_make(bpy.types.Panel):
    bl_label = "動画をつくる"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "動画自動化"
    bl_order = 0

    def draw(self, context):
        st = _settings(context)
        lay = self.layout
        steps = ["START", "CREATE", "REVIEW", "WORKING", "DONE"]
        names = ["はじめに", "つくる", "構成の確認", "制作中", "できあがり"]
        i = steps.index(st.screen)
        nxt = f"　次: {names[i + 1]}" if i + 1 < len(names) else ""
        lay.label(text=f"{i + 1}/{len(steps)} {names[i]}{nxt}", icon="SEQUENCE")
        getattr(self, "_" + st.screen.lower())(lay, st)
        lay.separator()
        lay.label(text=st.status, icon="SORTTIME" if _busy.is_set() else "INFO")
        for n in _notes[:8]:
            lay.label(text=n)

    def _start(self, lay, st):
        box = lay.box()
        box.label(text="必要なものを確かめます", icon="CHECKMARK")
        for r in _ready:
            icon = "CHECKMARK" if r["ok"] else ("ERROR" if r["required"] else "INFO")
            line = r["label"] + (f"（{r['detail']}）" if r.get("detail") else "")
            box.label(text=line, icon=icon)
        if _busy.is_set() and st.step:
            if hasattr(box, "progress"):
                box.progress(factor=st.progress, type="BAR", text=f"{st.step} {st.progress:.0%}")
            else:
                box.label(text=f"{st.step} {st.progress:.0%}")
        acts = _setup_actions() if _ready else []
        if acts:
            col = box.column(align=True)
            for a in acts:
                col.operator(VA_OT_setup.bl_idname, text=_SETUP_INFO[a][0], icon="IMPORT").what = a
        lay.operator(VA_OT_start.bl_idname, icon="PLAY")
        if _ready and all(r["ok"] for r in _ready if r["required"]):
            lay.operator(VA_OT_make_goto.bl_idname, text="つくる へ進む").screen = "CREATE"

    def _create(self, lay, st):
        box = lay.box()
        box.label(text="作りたいものを文章で書いてください", icon="TEXT")
        box.prop(st, "instruction", text="")
        box.label(text="例: ダイヤモンドより硬い物質の解説動画を作って")
        row = box.row()
        row.prop(st, "length")
        row.prop(st, "orientation")
        box.prop(st, "reference")
        box.prop(st, "recording")
        if st.recording:
            box.label(text="録画の中身は AI に見えません。操作の手順も文章に書いてください")
            box.prop(st, "facecam")
        box.prop(st, "music")
        if st.music:
            box.prop(st, "lyrics")
            box.prop(st, "mv_image")
            box.prop(st, "lyric_direction")
        lay.operator(VA_OT_make_plan.bl_idname, icon="VIEWZOOM")

    def _review(self, lay, st):
        pl = _plan(st)
        if not pl:
            lay.operator(VA_OT_make_goto.bl_idname, text="つくる に戻る").screen = "CREATE"
            return
        box = lay.box()
        box.label(text=pl["title"], icon="FILE_MOVIE")
        warn = "（ほかの用途かもしれません）" if pl.get("purpose_uncertain") else ""
        box.label(text=f"用途: {pl['purpose_label']}{warn}")
        shape = "縦長" if pl["height"] > pl["width"] else "横長"
        size = f"{pl['width']}x{pl['height']}（{shape}）"
        box.label(text=f"大きさ: {size}・長さ: 約 {pl['duration_sec']} 秒")
        for ch in pl["characters"]:
            extra = "・立ち絵あり" if ch.get("tachie_pack") else ""
            box.label(text=f"{ch['name']}: {ch['role']}{extra}", icon="USER")
        chg = lay.box()
        chg.label(text="変えたいところがあれば", icon="PREFERENCES")
        chg.prop(st, "re_purpose")
        chg.prop(st, "re_length")
        if pl.get("packs"):
            for i, ch in enumerate(pl["characters"][:MAX_CAST]):
                chg.prop(st, f"tachie{i}", text=f"{ch['name']} の立ち絵")
        chg.operator(VA_OT_make_change.bl_idname, icon="FILE_REFRESH")
        col = box.column(align=True)
        col.label(text="作る手順:")
        for i, s_ in enumerate(pl["steps"], 1):
            col.label(text=f"  {i}. {s_}")
        for n in pl.get("notes") or []:
            box.label(text=n, icon="INFO")
        for r in pl.get("readiness") or []:
            if not r["ok"] and r["required"]:
                box.label(text=f"足りないもの: {r['label']}", icon="ERROR")
        lay.prop(st, "auto_import")
        row = lay.row()
        row.scale_y = 1.5
        row.enabled = bool(pl.get("can_run"))
        row.operator(VA_OT_make_run.bl_idname, icon="PLAY")
        lay.operator(VA_OT_make_goto.bl_idname, text="書き直す", icon="LOOP_BACK").screen = "CREATE"
        if not pl.get("can_run"):
            lay.operator(
                VA_OT_make_goto.bl_idname, text="はじめに（準備）へ戻る", icon="TOOL_SETTINGS"
            ).screen = "START"

    def _working(self, lay, st):
        box = lay.box()
        box.label(text=st.step or "制作中…", icon="SORTTIME")
        if hasattr(box, "progress"):
            box.progress(factor=st.progress, type="BAR", text=f"{st.progress:.0%}")
        else:
            box.label(text=f"{st.progress:.0%}")
        box.label(text="数分〜十数分かかります。Blender はそのまま使えます")
        lay.operator(VA_OT_make_cancel.bl_idname, icon="CANCEL")

    def _done(self, lay, st):
        box = lay.box()
        box.label(text="できあがりました", icon="CHECKMARK")
        box.prop(st, "video", text="")
        row = box.row()
        row.operator(VA_OT_import_video.bl_idname, icon="FILE_MOVIE")
        row.operator(VA_OT_open_folder.bl_idname, icon="FILE_FOLDER")
        fix = lay.box()
        fix.label(text="直したいところがあれば", icon="GREASEPENCIL")
        fix.prop(st, "fix_text", text="")
        fix.label(text="例: もっと短く／カードを減らして／説明をやさしく")
        row = fix.row(align=True)
        op = row.operator(VA_OT_make_run.bl_idname, text="台本から作り直す", icon="TEXT")
        op.revise, op.keep_script = True, False
        op = row.operator(VA_OT_make_run.bl_idname, text="見た目だけ作り直す", icon="IMAGE_DATA")
        op.revise, op.keep_script = True, True
        lay.operator(VA_OT_make_goto.bl_idname, text="もう一本つくる", icon="ADD").screen = "CREATE"


class VA_PT_advanced(bpy.types.Panel):
    bl_label = "くわしい設定"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "動画自動化"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 1

    def draw(self, context):
        st = _settings(context)
        lay = self.layout
        lay.prop(st, "server")
        pr = _prefs()
        if pr is not None:
            lay.prop(pr, "app_dir")
            lay.prop(pr, "voicevox_dir")
            lay.prop(pr, "auto_start")
        lay.label(text=f"アプリの場所: {_app_dir() or '見つかりません'}")


class VA_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    app_dir: StringProperty(  # type: ignore[valid-type]
        name="アプリの場所",
        subtype="DIR_PATH",
        description="VideoAutomation を入れたフォルダ（空なら自動で探します）",
    )
    voicevox_dir: StringProperty(  # type: ignore[valid-type]
        name="VOICEVOX の場所",
        subtype="DIR_PATH",
        description="VOICEVOX ENGINE（run.exe）のあるフォルダ（空なら自動で探します）",
    )
    auto_start: BoolProperty(  # type: ignore[valid-type]
        name="アプリを自動で起動する",
        default=True,
        description="はじめるときにアプリが動いていなければ、画面なしで起動します",
    )

    def draw(self, context):
        self.layout.prop(self, "app_dir")
        self.layout.prop(self, "voicevox_dir")
        self.layout.prop(self, "auto_start")


# ---------------------------------------------------------------- 画面
class VA_PT_panel(bpy.types.Panel):
    """従来の画面（1 手順ずつ実行・素材ごとの読み込み）。くわしい設定の中に置く。"""

    bl_label = "従来の画面"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "動画自動化"
    bl_parent_id = "VA_PT_advanced"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        st = _settings(context)
        col = self.layout.column()
        box = col.box()
        box.label(text="1. 接続", icon="LINKED")
        row = box.row(align=True)
        row.prop(st, "server", text="")
        row.operator(VA_OT_connect.bl_idname, text="", icon="FILE_REFRESH")
        box.prop(st, "project")
        box.prop(st, "script")

        box = col.box()
        box.label(text="2. 作るもの", icon="TEXT")
        box.prop(st, "instruction", text="")
        box.operator(VA_OT_classify.bl_idname, icon="VIEWZOOM")
        row = box.row(align=True)
        row.prop(st, "purpose", text="")
        row.operator(VA_OT_set_purpose.bl_idname, text="設定", icon="CHECKMARK")

        box = col.box()
        box.label(text="3. 作る手順", icon="SEQUENCE")
        box.prop(st, "use_llm")
        grid = box.grid_flow(columns=2, align=True)
        for s in STEPS:
            grid.operator(VA_OT_run.bl_idname, text=STEP_LABELS[s]).step = s
        op = box.operator(VA_OT_run.bl_idname, text="すべて続けて", icon="PLAY")
        op.step = "all"

        box = col.box()
        box.label(text="4. 読み込み", icon="IMPORT")
        box.prop(st, "video", text="")
        box.operator(VA_OT_import_video.bl_idname, icon="FILE_MOVIE")
        box.separator()
        box.prop(st, "with_subtitles")
        box.operator(VA_OT_import_timeline.bl_idname, icon="SEQ_STRIP_DUPLICATE")

        col.separator()
        col.label(text=st.status, icon="INFO" if not _busy.is_set() else "SORTTIME")
        for n in _notes[:8]:
            col.label(text=n)


classes = (
    VA_Preferences,
    VA_Settings,
    VA_OT_setup,
    VA_OT_start,
    VA_OT_make_plan,
    VA_OT_make_run,
    VA_OT_make_change,
    VA_OT_make_cancel,
    VA_OT_make_goto,
    VA_OT_open_folder,
    VA_PT_make,
    VA_PT_advanced,
    VA_OT_connect,
    VA_OT_classify,
    VA_OT_set_purpose,
    VA_OT_run,
    VA_OT_import_video,
    VA_OT_import_timeline,
    VA_PT_panel,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.va_bridge = PointerProperty(type=VA_Settings)


def unregister():
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
    for key in ("proc", "voice"):  # このアドオンが起動したアプリ・VOICEVOX だけを止める
        proc = _engine.get(key)
        if proc is not None and proc.poll() is None:
            proc.terminate()
    # 古い版（scripts/addons に入れた同名のアドオン）と同時に入っていると、片方が先に
    # 外したものを外そうとして失敗する。外せないものは飛ばす
    if hasattr(bpy.types.Scene, "va_bridge"):
        del bpy.types.Scene.va_bridge
    for c in reversed(classes):
        try:
            bpy.utils.unregister_class(c)
        except RuntimeError:
            pass
