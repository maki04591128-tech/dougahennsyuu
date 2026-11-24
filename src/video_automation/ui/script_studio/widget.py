"""対話型台本作成 UI（Phase 8 §17）。

モード選択（全自動 / ユーザー片側入力 / 手動）・キャラクター設定・セリフ一覧（生成元で色分け）・
固定・手修正・1発話の再生成（候補から採用）・検証・版の差分と復元。LLM を使う操作は
別スレッドで実行して画面を止めない。
"""

import re
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_automation.services.direction.plan import describe_plan
from video_automation.ui.async_call import AsyncCaller, error_text

MODES = [
    ("FULL_AI", "全自動（AIが全員分を作成）"),
    ("USER_ONE_SIDE", "片側入力（相手をAIが作成）"),
    ("MANUAL", "手動"),
]
POLICIES = ["おまかせ", "補足", "質問", "ツッコミ", "解説"]
SOURCE_LABEL = {"USER": "ユーザー", "AI": "AI", "EDITED": "手修正"}
SOURCE_COLOR = {"USER": "#dbe9ff", "AI": "#ffffff", "EDITED": "#fff3c4"}

_COLOR = re.compile(r"^#?[0-9A-Fa-f]{6}$")


def _color(text: str) -> str | None:
    """「#RRGGBB」以外は自動（None）。"""
    t = text.strip()
    return ("#" + t.lstrip("#")).upper() if _COLOR.match(t) else None


CHAR_COLS = [
    "名前",
    "役割",
    "性格",
    "口調",
    "ユーザー担当",
    "話者ID",
    "抑揚の強さ(0-2)",
    "字幕の色(#RRGGBB)",
    "立ち絵",
    "字幕の書体（空欄=自動）",
]
TURN_COLS = ["#", "シーン", "話者", "セリフ", "生成元", "固定", "推定秒"]


def _csv(text: str) -> list[str]:
    return [x.strip() for x in text.replace("、", ",").split(",") if x.strip()]


class ScriptStudioWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self.script_id: str | None = None
        self.detail: dict[str, Any] | None = None
        self.runner = AsyncCaller(self, self._error, self._set_busy)

        # ---- 設定 ----
        self.project_combo = QComboBox()
        self.mode_combo = QComboBox()
        for v, label in MODES:
            self.mode_combo.addItem(label, v)
        self.title_edit = QLineEdit()
        self.topic_edit = QLineEdit()
        self.purpose_edit = QLineEdit()
        self.audience_edit = QLineEdit()
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(0, 7200)
        self.duration_spin.setSpecialValueText("指定なし")
        self.duration_spin.setSuffix(" 秒")
        self.keywords_edit = QLineEdit()
        self.keywords_edit.setPlaceholderText("カンマ区切り")
        self.banned_edit = QLineEdit()
        self.banned_edit.setPlaceholderText("カンマ区切り")
        self.policy_combo = QComboBox()
        self.policy_combo.addItems(POLICIES)
        self.ratio_spin = QDoubleSpinBox()
        self.ratio_spin.setRange(0.2, 3.0)
        self.ratio_spin.setSingleStep(0.1)
        self.ratio_spin.setValue(1.0)
        self.reference_edit = QPlainTextEdit()
        self.reference_edit.setPlaceholderText("参考資料・要点（任意）")
        self.reference_edit.setMaximumHeight(60)

        self.char_table = QTableWidget(0, len(CHAR_COLS))
        self.char_table.setHorizontalHeaderLabels(CHAR_COLS)
        self.char_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.add_char_button = QPushButton("キャラクター追加")
        self.add_char_button.clicked.connect(lambda: self._add_char_row({}))
        self.del_char_button = QPushButton("選択キャラクター削除")
        self.del_char_button.clicked.connect(
            lambda: self.char_table.removeRow(self.char_table.currentRow())
        )
        # 立ち絵（PSD を取り込むと表情・腕の部品に分けたパックになる）
        self.tachie_packs: list[dict[str, Any]] = []
        self.tachie_path_edit = QLineEdit()
        self.tachie_path_edit.setPlaceholderText("立ち絵の PSD または zip の場所（PSDTool 形式）")
        self.tachie_import_button = QPushButton("立ち絵を取り込む")
        self.tachie_import_button.clicked.connect(self.import_tachie)

        self.new_button = QPushButton("新規作成")
        self.new_button.clicked.connect(self.create_script)
        self.save_button = QPushButton("設定を保存")
        self.save_button.clicked.connect(self.save_settings)
        self.instruction_edit = QLineEdit()
        self.instruction_edit.setPlaceholderText("生成への追加指示（任意）")
        self.generate_button = QPushButton("AIで生成")
        self.generate_button.clicked.connect(self.generate)

        form = QFormLayout()
        form.addRow("プロジェクト", self.project_combo)
        self.project_combo.currentIndexChanged.connect(self.open_latest)
        form.addRow("モード", self.mode_combo)
        form.addRow("タイトル", self.title_edit)
        form.addRow("テーマ", self.topic_edit)
        form.addRow("目的", self.purpose_edit)
        form.addRow("視聴者", self.audience_edit)
        form.addRow("目標尺", self.duration_spin)
        form.addRow("必須キーワード", self.keywords_edit)
        form.addRow("禁止事項", self.banned_edit)
        form.addRow("応答方針（片側入力）", self.policy_combo)
        form.addRow("AI側の分量（倍）", self.ratio_spin)
        form.addRow("参考資料", self.reference_edit)
        settings_box = QGroupBox("台本の設定")
        sl = QVBoxLayout(settings_box)
        sl.addLayout(form)
        sl.addWidget(QLabel("キャラクター"))
        sl.addWidget(self.char_table)
        cb = QHBoxLayout()
        cb.addWidget(self.add_char_button)
        cb.addWidget(self.del_char_button)
        sl.addLayout(cb)
        tb = QHBoxLayout()
        tb.addWidget(self.tachie_path_edit, 1)
        tb.addWidget(self.tachie_import_button)
        sl.addLayout(tb)
        bb = QHBoxLayout()
        bb.addWidget(self.new_button)
        bb.addWidget(self.save_button)
        sl.addLayout(bb)

        # ---- ユーザー入力（片側入力・手動） ----
        self.lines_edit = QPlainTextEdit()
        self.lines_edit.setPlaceholderText(
            "ユーザーのセリフ（1行1発話。「話者：セリフ」で話者指定。省略時はユーザー担当）"
        )
        self.lines_edit.setMaximumHeight(90)
        self.add_lines_button = QPushButton("セリフを追加")
        self.add_lines_button.clicked.connect(self.add_lines)

        # ---- セリフ一覧 ----
        self.turn_table = QTableWidget(0, len(TURN_COLS))
        self.turn_table.setHorizontalHeaderLabels(TURN_COLS)
        h = self.turn_table.horizontalHeader()
        h.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.edit_button = QPushButton("編集を保存")
        self.edit_button.clicked.connect(self.save_turn_edit)
        self.lock_button = QPushButton("固定 / 解除")
        self.lock_button.clicked.connect(self.toggle_lock)
        self.delete_button = QPushButton("削除")
        self.delete_button.clicked.connect(self.delete_turn)
        self.regen_button = QPushButton("言い換え候補（3案）")
        self.regen_button.clicked.connect(self.regenerate)
        self.candidate_list = QListWidget()
        self.candidate_list.setMaximumHeight(80)
        self.apply_button = QPushButton("候補を採用")
        self.apply_button.clicked.connect(self.apply_candidate)

        # ---- 検証・版 ----
        self.validate_button = QPushButton("検証")
        self.validate_button.clicked.connect(lambda: self.validate(False))
        self.validate_llm_button = QPushButton("検証（LLMで整合性も）")
        self.validate_llm_button.clicked.connect(lambda: self.validate(True))
        self.reading_button = QPushButton("読みチェック（AI）")
        self.reading_button.clicked.connect(self.check_readings)
        self.direction_button = QPushButton("演出プラン（AI）")
        self.direction_button.clicked.connect(self.plan_direction)
        self.direction_apply_button = QPushButton("演出を適用")
        self.direction_apply_button.setEnabled(False)
        self.direction_apply_button.clicked.connect(self.apply_direction)
        self.direction_plan: dict[str, Any] | None = None
        self.issue_list = QListWidget()
        self.version_combo = QComboBox()
        self.diff_button = QPushButton("最新版との差分")
        self.diff_button.clicked.connect(self.show_diff)
        self.restore_button = QPushButton("この版に戻す")
        self.restore_button.clicked.connect(self.restore)
        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.notes_label = QLabel("")
        self.notes_label.setWordWrap(True)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)

        right = QWidget()
        rl = QVBoxLayout(right)
        gl = QHBoxLayout()
        gl.addWidget(self.instruction_edit)
        gl.addWidget(self.generate_button)
        rl.addLayout(gl)
        il = QHBoxLayout()
        il.addWidget(self.lines_edit)
        il.addWidget(self.add_lines_button)
        rl.addLayout(il)
        rl.addWidget(self.turn_table, 3)
        tb = QHBoxLayout()
        for b in (self.edit_button, self.lock_button, self.delete_button, self.regen_button):
            tb.addWidget(b)
        rl.addLayout(tb)
        cl = QHBoxLayout()
        cl.addWidget(self.candidate_list)
        cl.addWidget(self.apply_button)
        rl.addLayout(cl)
        rl.addWidget(self.notes_label)
        vb = QHBoxLayout()
        vb.addWidget(self.validate_button)
        vb.addWidget(self.validate_llm_button)
        vb.addWidget(self.reading_button)
        vb.addWidget(self.direction_button)
        vb.addWidget(self.direction_apply_button)
        vb.addWidget(QLabel("版"))
        vb.addWidget(self.version_combo)
        vb.addWidget(self.diff_button)
        vb.addWidget(self.restore_button)
        rl.addLayout(vb)
        bottom = QHBoxLayout()
        bottom.addWidget(self.issue_list)
        bottom.addWidget(self.diff_view)
        rl.addLayout(bottom, 1)
        rl.addWidget(self.status_label)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(settings_box)
        split.addWidget(right)
        split.setStretchFactor(1, 2)
        layout = QVBoxLayout(self)
        layout.addWidget(split)
        self._add_char_row({"name": "ずんだもん", "role": "解説役"})
        self._add_char_row({"name": "めたん", "role": "聞き役"})

    # ---------- 非同期実行 ----------
    @property
    def run_async(self) -> bool:
        return self.runner.enabled

    @run_async.setter
    def run_async(self, value: bool) -> None:
        self.runner.enabled = value

    @property
    def _pending(self) -> list[Any]:
        return self.runner.pending

    def _poll(self) -> None:
        self.runner.poll()

    def _call(self, fn: Callable[[], Any], done: Callable[[Any], None], busy: str = "") -> None:
        if self.runner.enabled:
            self.status_label.setText(busy or "処理中…")
        self.runner.call(fn, done)

    def _finish(self, fn: Callable[[], Any], done: Callable[[Any], None]) -> None:
        self.runner.run_now(fn, done)

    def _set_busy(self, busy: bool) -> None:
        for b in (
            self.generate_button,
            self.regen_button,
            self.validate_llm_button,
            self.reading_button,
            self.direction_button,
        ):
            b.setEnabled(not busy)
        self.direction_apply_button.setEnabled(not busy and self.direction_plan is not None)

    def _error(self, exc: BaseException) -> None:
        self.status_label.setText(f"エラー: {error_text(exc)[:300]}")

    # ---------- 読み込み ----------
    def refresh(self) -> None:
        try:
            projects = self.api.list_projects()
        except Exception as exc:  # noqa: BLE001
            self._error(exc)
            return
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for p in projects:
            self.project_combo.addItem(p["name"], p["id"])
        self.project_combo.blockSignals(False)
        self._load_packs()
        self.open_latest()

    def open_latest(self, *_: Any) -> None:
        """選んだプロジェクトの最後に作った台本を開く（なければ空のまま）。"""
        pid = self.project_combo.currentData()
        if not pid or not hasattr(self.api, "list_scripts"):
            return
        try:
            scripts = self.api.list_scripts(pid)
            if not scripts:
                return
            d = self.api.studio_detail(scripts[-1]["id"])
        except Exception as exc:  # noqa: BLE001
            self._error(exc)
            return
        self.show_detail(d, "保存済みの台本を開きました")

    def _add_char_row(self, c: dict[str, Any]) -> None:
        r = self.char_table.rowCount()
        self.char_table.insertRow(r)
        voice = c.get("voice") or {}
        for i, key in enumerate(("name", "role", "personality", "speaking_style")):
            self.char_table.setItem(r, i, QTableWidgetItem(str(c.get(key) or "")))
        chk = QTableWidgetItem()
        chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
        chk.setCheckState(Qt.CheckState.Checked if c.get("is_user") else Qt.CheckState.Unchecked)
        self.char_table.setItem(r, 4, chk)
        sid = voice.get("style_id")
        self.char_table.setItem(r, 5, QTableWidgetItem("" if sid is None else str(sid)))
        ex = voice.get("expressiveness")
        self.char_table.setItem(r, 6, QTableWidgetItem("" if ex is None else f"{ex:g}"))
        self.char_table.setItem(r, 7, QTableWidgetItem(str(c.get("color") or "")))
        combo = QComboBox()
        combo.addItem("（なし）", None)
        for p in self.tachie_packs:
            combo.addItem(f"{p['name']}（{p.get('author') or '作者不明'}）", p["id"])
        want = c.get("tachie_pack") if "tachie_pack" in c else self._guess_pack(c.get("name"))
        i = combo.findData(want) if want else 0
        combo.setCurrentIndex(max(0, i))
        self.char_table.setCellWidget(r, 8, combo)
        self.char_table.setItem(r, 9, QTableWidgetItem(str(c.get("subtitle_font") or "")))

    def _guess_pack(self, name: Any) -> str | None:
        """新しいキャラクターの立ち絵: 名前が合うパックを既定で選ぶ（めたん→四国めたん）。"""
        n = str(name or "").strip()
        if not n:
            return None
        for p in self.tachie_packs:
            if n == p["name"] or n in p["name"] or p["name"] in n:
                return str(p["id"])
        return None

    def _load_packs(self) -> None:
        if not hasattr(self.api, "tachie_packs"):
            return
        try:
            self.tachie_packs = self.api.tachie_packs()
        except Exception:  # noqa: BLE001 - 立ち絵の一覧が取れなくても台本は使える
            self.tachie_packs = []

    def import_tachie(self) -> None:
        path = self.tachie_path_edit.text().strip().strip('"')
        if not path:
            self.status_label.setText("立ち絵の PSD または zip の場所を入れてください")
            return

        def done(p: dict[str, Any]) -> None:
            chars = self.characters()
            self._load_packs()
            self.char_table.setRowCount(0)
            for c in chars:
                self._add_char_row(c)
            self.status_label.setText(f"立ち絵「{p['name']}」を取り込みました")

        self._call(lambda: self.api.tachie_import(path), done, "立ち絵を取り込み中…（1〜2 分）")

    def characters(self) -> list[dict[str, Any]]:
        out = []
        for r in range(self.char_table.rowCount()):
            cell = [self.char_table.item(r, i) for i in range(len(CHAR_COLS))]
            name = (cell[0].text() if cell[0] else "").strip()
            if not name:
                continue
            sid_text = (cell[5].text() if cell[5] else "").strip()
            voice: dict[str, Any] = {"style_id": int(sid_text)} if sid_text.isdigit() else {}
            ex_text = (cell[6].text() if len(cell) > 6 and cell[6] else "").strip()
            try:
                ex_val: float | None = max(0.0, min(2.0, float(ex_text))) if ex_text else None
            except ValueError:
                ex_val = None
            if ex_val is not None:
                voice["expressiveness"] = ex_val
            if self.detail:  # 話速など UI で編集しない項目は保持する
                prev = next((c for c in self.detail["characters"] if c["name"] == name), None)
                if prev:
                    voice = {**prev["voice"], **voice}
                    if not sid_text:
                        voice["style_id"] = None
                    if not ex_text:
                        voice.pop("expressiveness", None)
            out.append(
                {
                    "name": name,
                    "role": cell[1].text() if cell[1] else "",
                    "personality": cell[2].text() if cell[2] else "",
                    "speaking_style": cell[3].text() if cell[3] else "",
                    "is_user": bool(cell[4] and cell[4].checkState() == Qt.CheckState.Checked),
                    "voice": voice,
                    "color": _color(cell[7].text() if len(cell) > 7 and cell[7] else ""),
                    "tachie_pack": self._pack_of(r),
                    "subtitle_font": (cell[9].text().strip() if len(cell) > 9 and cell[9] else "")
                    or None,
                }
            )
        return out

    def _pack_of(self, row: int) -> str | None:
        w = self.char_table.cellWidget(row, 8)
        return w.currentData() if isinstance(w, QComboBox) else None

    def settings(self) -> dict[str, Any]:
        return {
            "topic": self.topic_edit.text(),
            "purpose": self.purpose_edit.text(),
            "audience": self.audience_edit.text(),
            "target_duration_sec": self.duration_spin.value() or None,
            "must_keywords": _csv(self.keywords_edit.text()),
            "banned": _csv(self.banned_edit.text()),
            "response_policy": self.policy_combo.currentText(),
            "ai_ratio": round(self.ratio_spin.value(), 2),
            "reference": self.reference_edit.toPlainText(),
        }

    # ---------- 操作 ----------
    def create_script(self) -> None:
        pid = self.project_combo.currentData()
        if not pid:
            self.status_label.setText("プロジェクトを選んでください")
            return
        self._finish(
            lambda: self.api.studio_create(
                pid,
                title=self.title_edit.text(),
                mode=self.mode_combo.currentData(),
                characters=self.characters(),
                settings=self.settings(),
            ),
            lambda d: self.show_detail(d, "台本を作成しました"),
        )

    def save_settings(self) -> None:
        if not self._require():
            return
        sid = self.script_id
        self._finish(
            lambda: self.api.studio_update(
                sid,
                title=self.title_edit.text(),
                mode=self.mode_combo.currentData(),
                characters=self.characters(),
                settings={**(self.detail or {}).get("settings", {}), **self.settings()},
            ),
            lambda d: self.show_detail(d, "設定を保存しました"),
        )

    def _require(self) -> bool:
        if not self.script_id:
            self.status_label.setText("先に「新規作成」で台本を作ってください")
            return False
        return True

    def generate(self) -> None:
        if not self._require():
            return
        sid, ins = self.script_id, self.instruction_edit.text()
        self._call(
            lambda: self.api.studio_generate(sid, ins),
            lambda d: self.show_detail(d, "生成しました（固定・ユーザー入力・手修正は保持）"),
            "AIで生成中…（ローカルLLMでは数分かかることがあります）",
        )

    def add_lines(self) -> None:
        if not self._require():
            return
        names = {c["name"] for c in self.characters()}
        lines = []
        for raw in self.lines_edit.toPlainText().splitlines():
            t = raw.strip()
            if not t:
                continue
            for sep in ("：", ":"):
                head, _, rest = t.partition(sep)
                if rest and head.strip() in names:
                    lines.append({"speaker": head.strip(), "text": rest.strip()})
                    break
            else:
                lines.append({"text": t})
        if not lines:
            return
        sid = self.script_id
        self._finish(
            lambda: self.api.studio_add_lines(sid, lines),
            lambda d: self._after_lines(d, len(lines)),
        )

    def _after_lines(self, d: dict[str, Any], n: int) -> None:
        self.lines_edit.clear()
        self.show_detail(d, f"{n}件追加しました")

    def _after_apply(self, d: dict[str, Any]) -> None:
        self.candidate_list.clear()
        self.show_detail(d, "候補を採用しました")

    def _cell(self, r: int, c: int) -> str:
        item = self.turn_table.item(r, c)
        return item.text().strip() if item else ""

    def _turn(self) -> dict[str, Any] | None:
        r = self.turn_table.currentRow()
        if not self.detail or r < 0 or r >= len(self.detail["segments"]):
            self.status_label.setText("セリフを選んでください")
            return None
        return self.detail["segments"][r]

    def save_turn_edit(self) -> None:
        t = self._turn()
        if t is None:
            return
        r = self.turn_table.currentRow()
        text = self._cell(r, 3)
        speaker = self._cell(r, 2)
        kw: dict[str, Any] = {}
        if text and text != t["text"]:
            kw["text"] = text
        if speaker and speaker != t["speaker"]:
            kw["speaker"] = speaker
        if not kw:
            self.status_label.setText("変更はありません")
            return
        sid = self.script_id
        self._finish(
            lambda: self.api.studio_update_turn(sid, t["id"], **kw),
            lambda d: self.show_detail(d, "手修正を保存しました（AIは変更しません）"),
        )

    def toggle_lock(self) -> None:
        t = self._turn()
        if t is None:
            return
        sid = self.script_id
        self._finish(
            lambda: self.api.studio_lock(sid, t["id"], not t["locked"]),
            lambda d: self.show_detail(
                d, "固定しました" if not t["locked"] else "固定を解除しました"
            ),
        )

    def delete_turn(self) -> None:
        t = self._turn()
        if t is None:
            return
        sid = self.script_id
        self._finish(
            lambda: self.api.studio_delete_turn(sid, t["id"]),
            lambda d: self.show_detail(d, "削除しました"),
        )

    def regenerate(self) -> None:
        t = self._turn()
        if t is None:
            return
        if t["locked"] or t["generation_source"] != "AI":
            self.status_label.setText("固定・ユーザー入力・手修正のセリフは再生成しません")
            return
        sid, ins = self.script_id, self.instruction_edit.text()
        self._regen_turn = t["id"]

        def done(res: dict[str, Any]) -> None:
            self.candidate_list.clear()
            self.candidate_list.addItems(res.get("candidates", []))
            self.status_label.setText("候補を選んで「候補を採用」を押してください")

        self._call(lambda: self.api.studio_regenerate(sid, t["id"], 3, ins), done, "候補を生成中…")

    def apply_candidate(self) -> None:
        item = self.candidate_list.currentItem()
        tid = getattr(self, "_regen_turn", None)
        if item is None or tid is None or not self.script_id:
            self.status_label.setText("候補を選んでください")
            return
        sid, text = self.script_id, item.text()
        self._finish(
            lambda: self.api.studio_apply(sid, tid, text),
            self._after_apply,
        )

    def validate(self, use_llm: bool) -> None:
        if not self._require():
            return
        sid = self.script_id

        def done(v: dict[str, Any]) -> None:
            self.issue_list.clear()
            order = {t["id"]: i + 1 for i, t in enumerate((self.detail or {}).get("segments", []))}
            mark = {"error": "✖", "warning": "⚠", "info": "ℹ"}
            for i in v["issues"]:
                at = f" [#{order[i['turn_id']]}]" if i.get("turn_id") in order else ""
                self.issue_list.addItem(f"{mark[i['level']]}{at} {i['message']}")
            target = v.get("target_duration_sec")
            self.status_label.setText(
                ("問題なし" if v["valid"] else "エラーがあります")
                + f" / 推定 {v['estimated_total_sec']:.0f}秒"
                + (f"（目標 {target}秒）" if target else "")
            )

        if use_llm:
            self._call(lambda: self.api.studio_validate(sid, True), done, "LLMで検査中…")
        else:
            self._finish(lambda: self.api.studio_validate(sid, False), done)

    def check_readings(self) -> None:
        """エンジンの読みを AI が確認し、読み間違いを読みのルールに登録する（字幕は変えない）。"""
        if not self._require():
            return
        sid = self.script_id

        def done(r: dict[str, Any]) -> None:
            self.issue_list.clear()
            for i in r["issues"]:
                self.issue_list.addItem(
                    f"読み: {i['surface']} → {i['reading']}（{i['text'][:24]}）"
                )
            n = len(r["issues"])
            self.status_label.setText(
                f"{r['checked']} 発話の読みを確認し、{n} 件を読みのルールに登録しました"
                + ("（音声を作り直すと反映されます）" if n else "")
            )

        self._call(lambda: self.api.check_readings(sid), done, "読みを確認中…")

    # ---------- 演出 ----------
    def plan_direction(self) -> None:
        """AI が演出プラン（カード・図表・テロップ）を作る。確認後「演出を適用」で配置する。"""
        if not self._require():
            return
        sid = self.script_id

        def done(r: dict[str, Any]) -> None:
            self.direction_plan = r["plan"]
            self.diff_view.setPlainText(describe_plan(r["plan"]))
            self.direction_apply_button.setEnabled(True)
            n = len(r["plan"]["shots"])
            self.status_label.setText(
                f"演出プラン: {n} 件（内容を確認して「演出を適用」を押してください）"
            )

        self._call(lambda: self.api.direction_plan(sid), done, "演出を考えています…")

    def apply_direction(self) -> None:
        if not self._require() or self.direction_plan is None:
            return
        sid, plan = self.script_id, self.direction_plan

        def done(r: dict[str, Any]) -> None:
            skipped = "\n".join(r["skipped"])
            self.status_label.setText(
                f"演出を配置しました（{r['clips']} クリップ、元に戻すで取り消せます）"
            )
            if skipped:
                self.diff_view.appendPlainText("\n[外した演出]\n" + skipped)

        self._call(lambda: self.api.direction_apply(sid, plan), done, "素材を作って配置中…")

    def show_diff(self) -> None:
        v = self.version_combo.currentData()
        if not self.script_id or v is None:
            return
        sid = self.script_id

        def done(d: dict[str, Any]) -> None:
            lines = [f"版{d['from_version']} → 版{d['to_version']}"]
            if d["settings_changed"]:
                lines.append("・設定を変更")
            if d["characters_changed"]:
                lines.append("・キャラクターを変更")
            sym = {"added": "+", "removed": "-", "changed": "~", "moved": "↕"}
            for ch in d["changes"]:
                body = ch.get("after") or ch.get("before") or ""
                if ch["kind"] == "changed":
                    body = f"{ch['before']} → {ch['after']}"
                elif ch["kind"] == "moved":
                    body = f"位置 {ch['before']} → {ch['after']}"
                lines.append(f"{sym[ch['kind']]} {ch['speaker']}: {body}")
            self.diff_view.setPlainText("\n".join(lines))

        self._finish(lambda: self.api.studio_diff(sid, v), done)

    def restore(self) -> None:
        v = self.version_combo.currentData()
        if not self.script_id or v is None:
            return
        sid = self.script_id
        self._finish(
            lambda: self.api.studio_restore(sid, v),
            lambda d: self.show_detail(d, f"版{v}に戻しました（新しい版として記録）"),
        )

    # ---------- 表示 ----------
    def show_detail(self, d: dict[str, Any], message: str = "") -> None:
        self.detail = d
        self.script_id = d["id"]
        i = self.mode_combo.findData(d["mode"])
        if i >= 0:
            self.mode_combo.setCurrentIndex(i)
        self.title_edit.setText(d.get("title", ""))
        st = d.get("settings", {})
        self.topic_edit.setText(st.get("topic", ""))
        self.purpose_edit.setText(st.get("purpose", ""))
        self.audience_edit.setText(st.get("audience", ""))
        self.duration_spin.setValue(st.get("target_duration_sec") or 0)
        self.keywords_edit.setText(", ".join(st.get("must_keywords", [])))
        self.banned_edit.setText(", ".join(st.get("banned", [])))
        if st.get("response_policy") in POLICIES:
            self.policy_combo.setCurrentText(st["response_policy"])
        self.ratio_spin.setValue(float(st.get("ai_ratio", 1.0)))
        self.reference_edit.setPlainText(st.get("reference", ""))
        self.char_table.setRowCount(0)
        for c in d["characters"]:
            self._add_char_row(c)

        segs = d["segments"]
        self.turn_table.setRowCount(len(segs))
        bold = QFont()
        bold.setBold(True)
        for r, t in enumerate(segs):
            cells = [
                str(r + 1),
                str(t["scene_index"] + 1),
                t["speaker"],
                t["text"],
                SOURCE_LABEL.get(t["generation_source"], t["generation_source"]),
                "🔒" if t["locked"] else "",
                f"{t.get('estimated_sec', 0):.1f}",
            ]
            color = QBrush(QColor(SOURCE_COLOR.get(t["generation_source"], "#ffffff")))
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if col not in (2, 3):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                item.setBackground(color)
                item.setForeground(QBrush(QColor("#000000")))  # 暗いテーマでも読める
                if t["locked"]:
                    item.setFont(bold)
                self.turn_table.setItem(r, col, item)

        notes = d.get("notes") or []
        self.notes_label.setText("AIのメモ: " + " / ".join(notes) if notes else "")
        try:
            versions = self.api.studio_versions(d["id"])
        except Exception:  # noqa: BLE001
            versions = []
        self.version_combo.clear()
        for v in versions:
            self.version_combo.addItem(f"版{v['version']}: {v['reason']}", v["version"])
        if versions:
            self.version_combo.setCurrentIndex(len(versions) - 1)
        self.status_label.setText(
            (message + " / " if message else "")
            + f"版{d['version']}・{len(segs)}発話・推定 {d['estimated_total_sec']:.0f}秒"
        )
