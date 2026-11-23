"""素材・BGM タブ（Phase 6）。素材検索 → ライセンス確認 → 取得、LLM 要求の承認、BGM 生成・配置、
クレジット表示。UI は API 経由でのみ操作し、外部サービスへ直接アクセスしない。"""

import tempfile
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

MEDIA_TYPES = [("画像", "image"), ("動画", "video"), ("効果音・音源", "audio")]
VERDICT_LABEL = {"allowed": "利用可", "needs_ack": "要確認", "forbidden": "利用不可"}


def fmt_duration(sec: float | None) -> str:
    if sec is None:
        return "-"
    m, s = divmod(int(round(sec)), 60)
    return f"{m}:{s:02d}"


class AssetsWidget(QWidget):
    def __init__(self, api: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.api = api
        self._results: list[dict[str, Any]] = []
        self._jobs: dict[str, str] = {}  # job_id -> 種類（download / music）
        self._last_music: str | None = None
        self._player: Any = None

        # --- 検索 ---
        self.project_combo = QComboBox()
        self.query_edit = QLineEdit()
        self.query_edit.setPlaceholderText("検索語（例: 夜景、波の音）")
        self.query_edit.returnPressed.connect(self.search)
        self.type_combo = QComboBox()
        for label, value in MEDIA_TYPES:
            self.type_combo.addItem(label, value)
        self.allowed_only = QCheckBox("そのまま使えるライセンスのみ")
        self.allowed_only.setChecked(True)
        self.search_button = QPushButton("検索")
        self.search_button.clicked.connect(self.search)
        self.key_button = QPushButton("APIキー設定…")
        self.key_button.clicked.connect(self.set_key)
        self.sources_label = QLabel("")
        self.sources_label.setWordWrap(True)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["取得元", "タイトル", "作者", "ライセンス", "判定", "サイズ/長さ"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self.show_selected)

        self.thumb = QLabel()
        self.thumb.setFixedSize(320, 200)
        self.thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.ack_check = QCheckBox("ライセンス条件を確認した上で使う")
        self.download_button = QPushButton("プロジェクトに取り込む")
        self.download_button.clicked.connect(self.download)

        self.pending_list = QListWidget()
        self.approve_button = QPushButton("承認して取得")
        self.approve_button.clicked.connect(lambda: self.decide(True))
        self.reject_button = QPushButton("却下")
        self.reject_button.clicked.connect(lambda: self.decide(False))

        # --- BGM ---
        self.music_status = QLabel("")
        self.music_prompt = QLineEdit()
        self.music_prompt.setPlaceholderText(
            "曲調・楽器・雰囲気（英語推奨: calm lo-fi piano, warm）"
        )
        self.lyrics = QPlainTextEdit()
        self.lyrics.setPlaceholderText("歌詞（[Verse] [Chorus] などのタグ可）")
        self.lyrics.setEnabled(False)
        self.lyrics.setMaximumHeight(80)
        self.instrumental = QCheckBox("インスト（歌なし）")
        self.instrumental.setChecked(True)
        self.instrumental.toggled.connect(lambda on: self.lyrics.setEnabled(not on))
        self.music_duration = QSpinBox()
        self.music_duration.setRange(10, 600)
        self.music_duration.setValue(60)
        self.music_duration.setSuffix(" 秒")
        self.music_bpm = QSpinBox()
        self.music_bpm.setRange(0, 300)
        self.music_bpm.setSpecialValueText("自動")
        self.music_button = QPushButton("BGMを生成")
        self.music_button.clicked.connect(self.generate_music)
        self.play_button = QPushButton("試聴")
        self.play_button.clicked.connect(self.play_music)
        self.bgm_volume = QDoubleSpinBox()
        self.bgm_volume.setRange(0.0, 2.0)
        self.bgm_volume.setSingleStep(0.05)
        self.bgm_volume.setValue(0.25)
        self.bgm_button = QPushButton("BGMとして配置")
        self.bgm_button.clicked.connect(self.place_bgm)
        self.credits_button = QPushButton("クレジットを表示")
        self.credits_button.clicked.connect(self.show_credits)
        self.credits_text = QPlainTextEdit()
        self.credits_text.setReadOnly(True)
        self.credits_text.setMaximumHeight(120)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self._set_music_ready(False)

        # --- レイアウト ---
        top = QHBoxLayout()
        top.addWidget(QLabel("プロジェクト"))
        top.addWidget(self.project_combo, 1)
        top.addWidget(self.key_button)
        bar = QHBoxLayout()
        bar.addWidget(self.query_edit, 2)
        bar.addWidget(self.type_combo)
        bar.addWidget(self.allowed_only)
        bar.addWidget(self.search_button)
        side = QVBoxLayout()
        side.addWidget(self.thumb)
        side.addWidget(self.detail, 1)
        side.addWidget(self.ack_check)
        side.addWidget(self.download_button)
        mid = QHBoxLayout()
        mid.addWidget(self.table, 3)
        mid.addLayout(side, 2)
        pend = QGroupBox("承認待ち（LLMからの素材取得）")
        pl = QVBoxLayout(pend)
        pl.addWidget(self.pending_list)
        pb = QHBoxLayout()
        pb.addWidget(self.approve_button)
        pb.addWidget(self.reject_button)
        pl.addLayout(pb)
        music = QGroupBox("BGM生成（ACE-Step 1.5）")
        mf = QFormLayout(music)
        mf.addRow(self.music_status)
        mf.addRow("プロンプト", self.music_prompt)
        mf.addRow(self.instrumental)
        mf.addRow("歌詞", self.lyrics)
        mf.addRow("長さ", self.music_duration)
        mf.addRow("BPM", self.music_bpm)
        mb = QHBoxLayout()
        mb.addWidget(self.music_button)
        mb.addWidget(self.play_button)
        mb.addWidget(QLabel("音量"))
        mb.addWidget(self.bgm_volume)
        mb.addWidget(self.bgm_button)
        mf.addRow(mb)
        bottom = QHBoxLayout()
        bottom.addWidget(pend, 1)
        bottom.addWidget(music, 1)
        root = QVBoxLayout(self)
        root.addLayout(top)
        root.addWidget(self.sources_label)
        root.addLayout(bar)
        root.addLayout(mid, 3)
        root.addLayout(bottom, 2)
        cr = QHBoxLayout()
        cr.addWidget(self.credits_button)
        cr.addWidget(self.credits_text, 1)
        root.addLayout(cr)
        root.addWidget(self.status_label)

        self._poll = QTimer(self)
        self._poll.setInterval(1000)
        self._poll.timeout.connect(self._poll_jobs)

    # ---------- 共通 ----------
    def _pid(self) -> str | None:
        return self.project_combo.currentData()

    def _error(self, action: str, exc: Exception) -> None:
        detail = ""
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                detail = f": {resp.json().get('message', '')}"
            except ValueError:
                pass
        self.status_label.setText(f"{action}に失敗しました ({type(exc).__name__}{detail})")

    def _set_music_ready(self, ready: bool) -> None:
        self.play_button.setEnabled(ready)
        self.bgm_button.setEnabled(ready)

    def refresh(self) -> None:
        try:
            projects = self.api.list_projects()
            sources = self.api.asset_sources()
            engine = self.api.music_engines()
        except Exception as exc:
            self._error("読み込み", exc)
            return
        self.project_combo.clear()
        for p in projects:
            self.project_combo.addItem(f"{p['name']} ({p['width']}x{p['height']})", p["id"])
        parts = [
            f"{s['label']}: {'利用可' if s['configured'] else 'APIキー未設定'}" for s in sources
        ]
        self.sources_label.setText(" / ".join(parts))
        if engine.get("available"):
            self.music_status.setText(f"接続済み（{engine.get('default_model') or '-'}）")
            self.music_button.setEnabled(True)
        else:
            self.music_status.setText(
                "ACE-Step が起動していません（acestep-api を起動してください）"
            )
            self.music_button.setEnabled(False)
        self.load_pending()

    def set_key(self) -> None:
        name, ok = QInputDialog.getItem(
            self, "APIキー設定", "取得元", ["pexels", "pixabay", "freesound"], 0, False
        )
        if not ok:
            return
        key, ok = QInputDialog.getText(
            self, "APIキー設定", f"{name} の APIキー（暗号化して保存）", QLineEdit.EchoMode.Password
        )
        if not ok or not key.strip():
            return
        try:
            self.api.set_source_key(name, key.strip())
        except Exception as exc:
            self._error("APIキーの保存", exc)
            return
        self.status_label.setText(f"{name} の APIキーを保存しました")
        self.refresh()

    # ---------- 検索 ----------
    def search(self) -> None:
        q = self.query_edit.text().strip()
        if not q:
            return
        try:
            resp = self.api.search_assets(
                q, self.type_combo.currentData(), allowed_only=self.allowed_only.isChecked()
            )
        except Exception as exc:
            self._error("検索", exc)
            return
        self._results = resp["results"]
        t = self.table
        t.setRowCount(len(self._results))
        for r, x in enumerate(self._results):
            size = (
                f"{x['width']}x{x['height']}"
                if x.get("width")
                else fmt_duration(x.get("duration_sec"))
            )
            vals = [
                x["source"],
                x["title"] or "(無題)",
                x.get("creator") or "-",
                x["license"]["name"],
                VERDICT_LABEL.get(x["verdict"], x["verdict"]),
                size,
            ]
            for col, v in enumerate(vals):
                t.setItem(r, col, QTableWidgetItem(str(v)))
        msg = f"{len(self._results)} 件"
        if resp.get("errors"):
            msg += "（エラー: " + ", ".join(f"{k}: {v}" for k, v in resp["errors"].items()) + "）"
        self.status_label.setText(msg)

    def _selected(self) -> dict[str, Any] | None:
        rows = self.table.selectionModel().selectedRows()
        return self._results[rows[0].row()] if rows else None

    def show_selected(self) -> None:
        x = self._selected()
        if x is None:
            return
        lic = x["license"]
        lines = [f"<b>{lic['name']}</b>", x.get("attribution") or ""]
        lines += [f"・{n}" for n in lic.get("notes") or []]
        self.detail.setText("<br>".join(lines))
        needs = x["verdict"] != "allowed"
        self.ack_check.setVisible(needs)
        self.ack_check.setChecked(False)
        self.thumb.clear()
        if x.get("preview_url"):
            try:
                data = self.api.asset_thumbnail(x["source"], x["source_id"], x["media_type"])
                pix = QPixmap()
                if pix.loadFromData(data):
                    self.thumb.setPixmap(
                        pix.scaled(
                            self.thumb.size(),
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
            except Exception as exc:
                self._error("サムネイル取得", exc)

    def download(self) -> None:
        x, pid = self._selected(), self._pid()
        if x is None or not pid:
            return
        if x["verdict"] != "allowed" and not self.ack_check.isChecked():
            self.status_label.setText("ライセンス条件を確認し、チェックを入れてください")
            return
        try:
            r = self.api.download_asset(
                pid, x["source"], x["source_id"], x["media_type"], self.ack_check.isChecked()
            )
        except Exception as exc:
            self._error("取り込み", exc)
            return
        self._watch(r["job_id"], "download")

    # ---------- 承認 ----------
    def load_pending(self) -> None:
        pid = self._pid()
        self.pending_list.clear()
        if not pid:
            return
        try:
            reqs = self.api.asset_requests(pid)
        except Exception as exc:
            self._error("承認待ちの取得", exc)
            return
        for r in reqs:
            mark = "" if r["verdict"] == "allowed" else "【要確認】"
            item = QListWidgetItem(
                f"{mark}{r['source']} {r['title'] or r['source_id']}（{r['license_code']}）"
            )
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.pending_list.addItem(item)

    def decide(self, approve: bool) -> None:
        item = self.pending_list.currentItem()
        if item is None:
            return
        try:
            r = self.api.decide_asset_request(item.data(Qt.ItemDataRole.UserRole), approve)
        except Exception as exc:
            self._error("承認" if approve else "却下", exc)
            return
        if approve and r.get("job_id"):
            self._watch(r["job_id"], "download")
        self.load_pending()

    # ---------- BGM ----------
    def generate_music(self) -> None:
        pid, prompt = self._pid(), self.music_prompt.text().strip()
        if not pid or not prompt:
            self.status_label.setText("プロジェクトとプロンプトを指定してください")
            return
        kw: dict[str, Any] = {
            "instrumental": self.instrumental.isChecked(),
            "duration_sec": self.music_duration.value(),
        }
        if not self.instrumental.isChecked():
            kw["lyrics"] = self.lyrics.toPlainText()
        if self.music_bpm.value():
            kw["bpm"] = self.music_bpm.value()
        try:
            job = self.api.generate_music(pid, prompt, **kw)
        except Exception as exc:
            self._error("BGM生成", exc)
            return
        self.music_button.setEnabled(False)
        self._watch(job["id"], "music")

    def play_music(self) -> None:  # pragma: no cover - 音声デバイス依存
        if not self._last_music:
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError:
            return
        path = Path(tempfile.gettempdir()) / "video_automation_bgm_preview.wav"
        path.write_bytes(self.api.asset_file(self._last_music))
        if self._player is None:
            self._player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._player.setAudioOutput(self._audio_out)
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def place_bgm(self) -> None:
        pid = self._pid()
        if not pid or not self._last_music:
            return
        try:
            self.api.add_bgm(pid, self._last_music, self.bgm_volume.value())
        except Exception as exc:
            self._error("BGMの配置", exc)
            return
        self.status_label.setText("BGM をタイムラインに配置しました（編集・書き出しタブで確認）")

    def show_credits(self) -> None:
        pid = self._pid()
        if not pid:
            return
        try:
            self.credits_text.setPlainText(
                self.api.credits(pid)["text"] or "（クレジット対象なし）"
            )
        except Exception as exc:
            self._error("クレジット取得", exc)

    # ---------- Job ----------
    def _watch(self, job_id: str, kind: str) -> None:
        self._jobs[job_id] = kind
        self.status_label.setText("処理中…")
        self._poll.start()

    def _poll_jobs(self) -> None:
        for job_id, kind in list(self._jobs.items()):
            try:
                job = self.api.get_job(job_id)
            except Exception as exc:
                self._error("状態取得", exc)
                continue
            label = "BGM生成" if kind == "music" else "取り込み"
            if job["status"] in ("PENDING", "RUNNING"):
                self.status_label.setText(f"{label}中… {int(job.get('progress', 0) * 100)}%")
                continue
            del self._jobs[job_id]
            if kind == "music":
                self.music_button.setEnabled(True)
            if job["status"] != "SUCCEEDED":
                self.status_label.setText(f"{label}に失敗しました（{job.get('error')}）")
                continue
            if kind == "music":
                self._last_music = job["result"]["assets"][0]["id"]
                self._set_music_ready(True)
                self.status_label.setText("BGM を生成しました（試聴・配置できます）")
            else:
                self.status_label.setText(f"取り込みました: {job['result']['name']}")
        if not self._jobs:
            self._poll.stop()
