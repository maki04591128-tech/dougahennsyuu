"""動画自動化ブリッジ: Blender の動画編集（Video Sequencer）から動画自動化アプリを操作する。

サイドバー（N キー）の「動画自動化」タブ:
1. 接続 … 同じ PC で動いているアプリ（既定 http://127.0.0.1:8765）につなぐ
2. 作るもの … 作りたいものを文章で書くと用途（字幕動画・解説・MV など）を判定し、
   その用途のデザインの型をプロジェクトに設定する
3. 作る手順 … 音声 → 字幕 → 演出 → 検査 → 書き出しを、1 つずつまたは続けて実行
4. 読み込み … 書き出した動画を今のシーンの空いているチャンネルに置く

通信や待ち時間のかかる処理は別スレッドで行い、Blender の画面は止めない。
bpy の操作はタイマー（メインスレッド）からだけ行う。
"""

import queue
import threading
from typing import Any

import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty

from .client import DEFAULT_SERVER, STEP_LABELS, STEPS, BridgeError, Client, run_step

# 画面の候補（EnumProperty の items はコールバックで返す。参照を保持しないと文字化けする）
_cache: dict[str, list[tuple[str, str, str]]] = {"projects": [], "scripts": [], "purposes": []}
_notes: list[str] = []  # 検査・演出の補足（画面に並べる）
_jobs: queue.Queue[tuple[str, Any]] = queue.Queue()
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
    except queue.Empty:
        pass
    _redraw()
    return 0.3 if _busy.is_set() or not _jobs.empty() else None


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
        return {"FINISHED"}


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


# ---------------------------------------------------------------- 画面
class VA_PT_panel(bpy.types.Panel):
    bl_label = "動画自動化"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "動画自動化"

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

        col.separator()
        col.label(text=st.status, icon="INFO" if not _busy.is_set() else "SORTTIME")
        for n in _notes[:8]:
            col.label(text=n)


classes = (
    VA_Settings,
    VA_OT_connect,
    VA_OT_classify,
    VA_OT_set_purpose,
    VA_OT_run,
    VA_OT_import_video,
    VA_PT_panel,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.va_bridge = PointerProperty(type=VA_Settings)


def unregister():
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
    del bpy.types.Scene.va_bridge
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
