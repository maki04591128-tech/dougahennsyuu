"""利用者の直しの記録と集計（統合版 W2）。

- 利用者が画面から直接送った編集命令（タイムラインの命令の API。origin=user）を、
  用途・部品・項目ごとに data/training/design_edits.jsonl に 1 行ずつ記録する
  （data/ は Git に入れない。PC の中だけに置く。台本の文・素材の名前・個人の情報は入れない）
- 演出を適用した動画は「作った」行（用途と使った部品）も記録する（直さなかった動画を数えるため）
- 同じ用途の直近 5 本以上（最大 10 本。初期値）で、同じ項目を同じ向きに直した本数が 80% 以上なら、
  直した量の中央値で既定値の変更を提案する。反映は利用者が承認したときだけ
  （data/design_defaults.json に保存し、演出・BGM を作るときに足す）
- 提案するのは、作るときに足せる項目だけ（位置 x・y、大きさ、BGM の音量）。長さ・開始の直しは
  記録だけにする（入り・出の動きの時刻と合わなくなるため）
- 安全・コントラスト・点滅の基準値は対象外（不透明度は文字のコントラストに関わるので記録しない。
  反映した後も書き出し前の検査はそのまま通す）
"""

from __future__ import annotations

import json
import statistics
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from video_automation.app.logging import get_logger
from video_automation.schemas.timeline import (
    Batch,
    ClipRead,
    DeleteClip,
    MoveClip,
    SetClipProps,
    TimelineRead,
    TrimClip,
)

log = get_logger(__name__)

MIN_VIDEOS = 5  # 直近の何本から提案するか（最小）
MAX_VIDEOS = 10  # 直近の何本まで数えるか（初期値）
AGREE = 0.8  # 同じ向きに直した本数の割合
# 作るときに足せる項目（部品 → 項目）。大きさと音量は比、位置は画面に対する差
APPLY: dict[str, tuple[str, ...]] = {
    "background": ("x", "y", "scale"),
    "title_card": ("x", "y", "scale"),
    "card": ("x", "y", "scale"),
    "chart": ("x", "y", "scale"),
    "image": ("x", "y", "scale"),
    "keyword": ("x", "y", "scale"),
    "bgm": ("volume",),
}
RATIO_ITEMS = ("scale", "volume")
# 演出の外のトラック（名前で部品を決める）
TRACK_ROLES = {"立ち絵": "character", "BGM": "bgm", "効果音": "sfx", "曲": "music"}
_EPS = {"x": 0.002, "y": 0.002, "scale": 0.01, "volume": 0.01}  # これより小さい直しは数えない


@dataclass(frozen=True)
class EditItem:
    clip_id: str
    component: str
    item: str
    amount: float  # 差（x・y・秒）か比（scale・volume）


class EditProposal(BaseModel):
    purpose: str
    component: str
    item: str
    amount: float  # 中央値（差か比）
    videos: int  # 数えた動画の本数
    agree: int  # 同じ向きに直した本数
    text: str  # 利用者に見せる文


class DesignDefaults(BaseModel):
    """承認された既定値の変更（用途 → 部品 → 項目 → 量）。"""

    accepted: dict[str, dict[str, dict[str, float]]] = {}
    # 断った提案（用途/部品/項目 → 断ったときの「作った」本数。5 本増えるまで出さない）
    rejected: dict[str, int] = {}


def _changed(item: str, amount: float) -> bool:
    base = 1.0 if item in RATIO_ITEMS else 0.0
    return abs(amount - base) >= _EPS.get(item, 1e-6)


def edit_items(before: TimelineRead, command: Any, roles: dict[str, str]) -> list[EditItem]:
    """編集命令 1 つ（Batch は中を順に）から、部品・項目ごとの直しを取り出す。

    roles は演出が作ったクリップの部品（クリップ ID → 部品）。演出の外のクリップは
    トラックの名前で決め、どちらでもないクリップ（ナレーションなど）は数えない。"""
    fps = before.fps_num / before.fps_den if before.fps_den else 30.0
    tracks = {t.id: t.name for t in before.tracks}
    clips: dict[str, ClipRead] = {c.id: c.model_copy(deep=True) for c in before.clips}
    cmds = command.commands if isinstance(command, Batch) else [command]
    out: list[EditItem] = []

    def role(c: ClipRead) -> str | None:
        return roles.get(c.id) or TRACK_ROLES.get(tracks.get(c.track_id, ""))

    for cmd in cmds:
        c = clips.get(getattr(cmd, "clip_id", "") or "")
        if c is None:
            continue
        comp = role(c)
        if comp is None:
            continue

        def put(item: str, amount: float, c: ClipRead = c, comp: str = comp) -> None:
            if _changed(item, amount):
                out.append(EditItem(c.id, comp, item, round(amount, 4)))

        if isinstance(cmd, SetClipProps):
            if cmd.transform is not None:
                old, new = c.transform, cmd.transform
                put("x", new.x - old.x)
                put("y", new.y - old.y)
                if old.scale > 0:
                    put("scale", new.scale / old.scale)
                c.transform = new
            if cmd.volume is not None:
                if c.volume > 0:
                    put("volume", cmd.volume / c.volume)
                c.volume = cmd.volume
            if cmd.fade_in_frames is not None:
                put("fade_in_sec", (cmd.fade_in_frames - c.fade_in_frames) / fps)
                c.fade_in_frames = cmd.fade_in_frames
            if cmd.fade_out_frames is not None:
                put("fade_out_sec", (cmd.fade_out_frames - c.fade_out_frames) / fps)
                c.fade_out_frames = cmd.fade_out_frames
        elif isinstance(cmd, TrimClip):
            start = c.start_frame if cmd.start_frame is None else cmd.start_frame
            end = c.end_frame if cmd.end_frame is None else cmd.end_frame
            put("duration_sec", ((end - start) - (c.end_frame - c.start_frame)) / fps)
            if cmd.start_frame is not None:
                put("start_sec", (start - c.start_frame) / fps)
            c.start_frame, c.end_frame = start, end
        elif isinstance(cmd, MoveClip):
            put("start_sec", (cmd.start_frame - c.start_frame) / fps)
            c.end_frame += cmd.start_frame - c.start_frame
            c.start_frame = cmd.start_frame
        elif isinstance(cmd, DeleteClip):
            out.append(EditItem(c.id, comp, "deleted", 1.0))
            clips.pop(c.id, None)
    return out


def _sign(item: str, amount: float) -> int:
    d = amount - (1.0 if item in RATIO_ITEMS else 0.0)
    return 0 if not _changed(item, amount) else (1 if d > 0 else -1)


def _describe(component: str, item: str, amount: float) -> str:
    names = {
        "background": "背景",
        "title_card": "題名",
        "card": "カード",
        "chart": "図表",
        "image": "画像",
        "keyword": "テロップ",
        "bgm": "BGM",
    }
    what = names.get(component, component)
    if item == "x":
        return f"{what}の位置を{'右' if amount > 0 else '左'}へ画面の幅の {abs(amount) * 100:.1f}%"
    if item == "y":
        return (
            f"{what}の位置を{'下' if amount > 0 else '上'}へ画面の高さの {abs(amount) * 100:.1f}%"
        )
    if item == "scale":
        return f"{what}の大きさを {amount * 100:.0f}% に"
    return f"{what}の音量を {amount * 100:.0f}% に"


def proposals(
    rows: list[dict[str, Any]], defaults: DesignDefaults | None = None
) -> list[EditProposal]:
    """記録から、既定値の変更の提案を作る（承認済み・断った直後のものは出さない）。"""
    defaults = defaults or DesignDefaults()
    made: dict[str, dict[str, tuple[str, set[str]]]] = {}  # 用途 → 動画 → (時刻, 部品)
    edits: dict[tuple[str, str, str, str], list[float]] = {}  # (動画, 部品, 項目, クリップ)
    for r in rows:
        if r.get("kind") == "made" and r.get("purpose"):
            # 演出と BGM は別の行になるので、同じ動画の部品は合わせる（時刻は新しい方）
            vids = made.setdefault(r["purpose"], {})
            ts0, comps0 = vids.get(r["project"], ("", set()))
            vids[r["project"]] = (max(ts0, r["ts"]), comps0 | set(r["components"]))
        elif r.get("kind") == "edit":
            key = (r["project"], r["component"], r["item"], r.get("clip", ""))
            edits.setdefault(key, []).append(float(r["amount"]))
    # 動画・部品・項目ごとに、クリップの直しを合わせた量（比は掛け合わせ、差は足す）の中央値
    per: dict[tuple[str, str, str], list[float]] = {}
    for (proj, comp, item, _clip), vals in edits.items():
        total = 1.0 if item in RATIO_ITEMS else 0.0
        for v in vals:
            total = total * v if item in RATIO_ITEMS else total + v
        per.setdefault((proj, comp, item), []).append(total)
    out: list[EditProposal] = []
    for purpose, vids in made.items():
        for comp, items in APPLY.items():
            using = sorted((ts, proj) for proj, (ts, comps) in vids.items() if comp in comps)[
                -MAX_VIDEOS:
            ]
            if len(using) < MIN_VIDEOS:
                continue
            for item in items:
                rkey = f"{purpose}/{comp}/{item}"
                if item in defaults.accepted.get(purpose, {}).get(comp, {}):
                    continue
                if rkey in defaults.rejected and len(vids) < defaults.rejected[rkey] + MIN_VIDEOS:
                    continue
                nets: list[float | None] = []
                for _ts, proj in using:
                    got = per.get((proj, comp, item))
                    nets.append(statistics.median(got) if got else None)
                for sign in (1, -1):
                    hit = [n for n in nets if n is not None and _sign(item, n) == sign]
                    if len(hit) >= AGREE * len(using):
                        amount = round(statistics.median(hit), 4)
                        out.append(
                            EditProposal(
                                purpose=purpose,
                                component=comp,
                                item=item,
                                amount=amount,
                                videos=len(using),
                                agree=len(hit),
                                text=(
                                    f"直近 {len(using)} 本のうち {len(hit)} 本で直していました。"
                                    f"既定を「{_describe(comp, item, amount)}」にしますか"
                                ),
                            )
                        )
    return out


class DesignEditLog:
    """直しの記録（JSONL）と、承認した既定値の変更（JSON）。path が None なら何もしない。"""

    def __init__(self, path: Path | None, defaults_path: Path | None) -> None:
        self.path = path
        self.defaults_path = defaults_path
        self._lock = threading.Lock()

    # ---------- 記録 ----------
    def _append(self, rows: list[dict[str, Any]]) -> None:
        if self.path is None or not rows:
            return
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    for r in rows:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
        except OSError as exc:  # 記録できなくても編集は止めない
            log.info("design_edit_log_failed", error=type(exc).__name__)

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    def made(self, project_id: str, purpose: str | None, components: list[str]) -> None:
        """演出を適用した（この動画で使った部品）。"""
        if purpose and components:
            self._append(
                [
                    {
                        "ts": self._now(),
                        "kind": "made",
                        "project": project_id,
                        "purpose": purpose,
                        "components": sorted(set(components)),
                    }
                ]
            )

    def record(
        self,
        project_id: str,
        purpose: str | None,
        before: TimelineRead,
        command: Any,
        roles: dict[str, str],
    ) -> int:
        items = edit_items(before, command, roles)
        ts = self._now()
        self._append(
            [
                {
                    "ts": ts,
                    "kind": "edit",
                    "project": project_id,
                    "purpose": purpose or "",
                    "component": x.component,
                    "item": x.item,
                    "amount": x.amount,
                    "clip": x.clip_id,
                }
                for x in items
            ]
        )
        return len(items)

    def rows(self) -> list[dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return []
        out = []
        with self._lock, self.path.open(encoding="utf-8") as f:
            for ln in f:
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    continue  # 途中で切れた行は飛ばす
        return out

    # ---------- 提案と既定値 ----------
    def defaults(self) -> DesignDefaults:
        if self.defaults_path is None or not self.defaults_path.exists():
            return DesignDefaults()
        try:
            return DesignDefaults.model_validate_json(self.defaults_path.read_text("utf-8"))
        except ValueError:
            log.info("design_defaults_invalid")
            return DesignDefaults()

    def _save(self, d: DesignDefaults) -> None:
        if self.defaults_path is None:
            return
        self.defaults_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.defaults_path.with_suffix(".tmp")
        tmp.write_text(d.model_dump_json(indent=2), "utf-8")
        tmp.replace(self.defaults_path)

    def proposals(self) -> list[EditProposal]:
        return proposals(self.rows(), self.defaults())

    def _find(self, purpose: str, component: str, item: str) -> EditProposal:
        for p in self.proposals():
            if (p.purpose, p.component, p.item) == (purpose, component, item):
                return p
        raise KeyError(f"{purpose}/{component}/{item}")

    def accept(self, purpose: str, component: str, item: str) -> DesignDefaults:
        """利用者が承認した提案を既定値に反映する。"""
        p = self._find(purpose, component, item)
        with self._lock:
            d = self.defaults()
            d.accepted.setdefault(purpose, {}).setdefault(component, {})[item] = p.amount
            d.rejected.pop(f"{purpose}/{component}/{item}", None)
            self._save(d)
        return d

    def reject(self, purpose: str, component: str, item: str) -> DesignDefaults:
        self._find(purpose, component, item)
        made = {
            r["project"]
            for r in self.rows()
            if r.get("kind") == "made" and r.get("purpose") == purpose
        }
        with self._lock:
            d = self.defaults()
            d.rejected[f"{purpose}/{component}/{item}"] = len(made)
            self._save(d)
        return d

    def reset(self, purpose: str | None = None) -> DesignDefaults:
        """反映した既定値を元に戻す（purpose を省くと全部）。"""
        with self._lock:
            d = self.defaults()
            if purpose is None:
                d.accepted = {}
            else:
                d.accepted.pop(purpose, None)
            self._save(d)
        return d

    def overrides(self, purpose: str | None) -> dict[str, dict[str, float]]:
        if not purpose:
            return {}
        return self.defaults().accepted.get(purpose, {})


def adjust_transform(tf: Any, o: dict[str, float]) -> Any:
    """承認された位置・大きさの変更を ClipTransform に足す（画面の外へは出さない）。"""
    if not o:
        return tf
    return tf.model_copy(
        update={
            "x": min(1.0, max(-1.0, tf.x + o.get("x", 0.0))),
            "y": min(1.0, max(-1.0, tf.y + o.get("y", 0.0))),
            "scale": min(10.0, max(0.05, tf.scale * o.get("scale", 1.0))),
        }
    )
