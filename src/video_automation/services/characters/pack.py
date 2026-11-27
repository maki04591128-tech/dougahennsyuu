"""立ち絵パック: PSD を部品（固定の層・差し替える部品の選択肢）の PNG に分けて保存し、読み込む。

保存の形（data_dir/characters/<id>/）:
- pack.json: 名前・作者・向き・層の重なり順（stack）・部品・表情とポーズの対応
- parts/*.png: 部品の画像（PSD のキャンバスでの位置 x, y つき、余白は切り取る）

重なり順は PSD のまま保つ（前髪が目・眉より上に来るなど）。部品の差し替えは
「層の重なり順の中の 1 か所」を入れ替えるだけなので、どの組み合わせでも PSD と同じ見た目になる。
"""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from video_automation.services.characters.presets import EXPRESSIONS, POSES, find_preset

PACK_VERSION = 1
_SLUG = re.compile(r"[^0-9A-Za-z_\-]+")


class PackError(Exception):
    pass


# ---------------------------------------------------------------- PSD から作る
def _resolve(root: Any, path: str) -> Any:
    layer = root
    for step in [p for p in path.split("/") if p]:
        hit = [x for x in layer if x.name == step]
        if not hit:
            raise PackError(f"layer not found: {path}")
        layer = hit[0]
    return layer


def _radio(parent: Any, child: Any) -> bool:
    return parent.name.startswith("!") or child.name.startswith("*")


def _select(group: Any, rel_paths: list[str]) -> None:
    """部品のグループの中で、rel_paths の層だけを表示する（途中の段も選ぶ）。"""
    for c in group:
        c.visible = False
    for path in rel_paths:
        layer = group
        for step in [p for p in path.split("/") if p]:
            target = next((x for x in layer if x.name == step), None)
            if target is None:
                raise PackError(f"layer not found: {group.name}/{path}")
            if layer is not group and _radio(layer, target):
                for sib in layer:
                    if _radio(layer, sib):
                        sib.visible = sib is target
            target.visible = True
            layer = target


def _leaves(layer: Any) -> list[Any]:
    out: list[Any] = []
    for c in layer:
        if c.is_group():
            out.extend(_leaves(c))
        else:
            out.append(c)
    return out


def _composite(psd: Any, keep: set[int]) -> Any:
    """keep（葉の層の id）だけを PSD の重なり方で描く（キャンバス全体の RGBA）。"""

    def flt(layer: Any) -> bool:
        return bool(layer.visible) and (layer.is_group() or id(layer) in keep)

    im = psd.composite(
        viewport=(0, 0, psd.width, psd.height), force=True, layer_filter=flt, ignore_preview=True
    )
    if im is None:
        return None
    return im.convert("RGBA")


def _save_part(im: Any, out_dir: Path, name: str) -> dict[str, Any] | None:
    if im is None:
        return None
    bbox = im.getbbox()
    if bbox is None:
        return None
    crop = im.crop(bbox)
    fn = f"{name}.png"
    crop.save(out_dir / fn, optimize=False, compress_level=3)
    return {"file": fn, "x": bbox[0], "y": bbox[1], "w": crop.width, "h": crop.height}


def _safe(name: str) -> str:
    s = _SLUG.sub("_", name).strip("_")
    return s or "x"


def build_pack(
    psd_path: Path, out_dir: Path, recipe: dict[str, Any] | None = None
) -> dict[str, Any]:
    """PSD から立ち絵パックを作る（psd-tools が必要）。レシピが無ければ既知のものから探す。"""
    try:
        from psd_tools import PSDImage
    except ImportError as exc:  # pragma: no cover - 依存の欠け
        raise PackError("psd-tools is not installed") from exc
    psd = PSDImage.open(psd_path)
    names = {x.name for x in psd.descendants()}
    recipe = recipe or find_preset(names)
    if recipe is None:
        raise PackError(
            "この PSD の層の名前に合うレシピがありません（pack.json のレシピを指定してください）"
        )
    # 部品以外の表示
    for gpath, visible in recipe.get("base", {}).items():
        g = psd if gpath == "" else _resolve(psd, gpath)
        for c in g:
            c.visible = c.name in visible
    slots = {k: _resolve(psd, v["group"]) for k, v in recipe["slots"].items()}
    slot_of: dict[int, str] = {}
    for k, g in slots.items():
        g.visible = True
        for leaf in _leaves(g):
            slot_of[id(leaf)] = k
    # 重なり順（下から上）: 固定の層のまとまりと、部品の位置
    order: list[tuple[str, Any]] = []
    for leaf in [x for x in psd.descendants() if not x.is_group()]:
        k = slot_of.get(id(leaf))
        if k is None:
            if order and order[-1][0] == "fixed":
                order[-1][1].add(id(leaf))
            else:
                order.append(("fixed", {id(leaf)}))
        elif not any(o == ("slot", k) for o in order):
            order.append(("slot", k))
    parts_dir = out_dir / "parts"
    if parts_dir.exists():
        shutil.rmtree(parts_dir)
    parts_dir.mkdir(parents=True)
    # 部品のグループを既定の選択肢にしてから固定の層を描く（部品のグループは描かない）
    stack: list[dict[str, Any]] = []
    for i, (kind, val) in enumerate(order):
        if kind == "fixed":
            part = _save_part(_composite(psd, val), parts_dir, f"fixed_{i}")
            if part:
                stack.append({"fixed": part})
        else:
            stack.append({"slot": val})
    parts: dict[str, dict[str, Any]] = {}
    for k, spec in recipe["slots"].items():
        g = slots[k]
        keep = {id(x) for x in _leaves(g)}
        parts[k] = {}
        for opt, rel in spec["options"].items():
            _select(g, rel)
            parts[k][opt] = (
                _save_part(_composite(psd, keep), parts_dir, f"{k}__{_safe(opt)}_{len(parts[k])}")
                if rel
                else None
            )
    # 全体の大きさ（既定の表情・ポーズで描いた範囲）
    exp = recipe["expressions"]["neutral"]
    pose = recipe["poses"]["neutral"]
    default = {**pose, "eyes": exp["eyes"], "brows": exp["brows"], "cheek": exp["cheek"]}
    default["mouth"] = exp["mouth"][0]
    boxes = []
    for item in stack:
        slot = str(item.get("slot", ""))
        p = item.get("fixed") or parts.get(slot, {}).get(default.get(slot, ""))
        if p:
            boxes.append((p["x"], p["y"], p["x"] + p["w"], p["y"] + p["h"]))
    if not boxes:
        raise PackError("立ち絵の絵が見つかりません")
    bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes)]
    bbox += [max(b[2] for b in boxes), max(b[3] for b in boxes)]
    meta = {
        "version": PACK_VERSION,
        "name": recipe.get("name") or psd_path.stem,
        "author": recipe.get("author", ""),
        "credit": f"立ち絵：{recipe['author']}" if recipe.get("author") else "",
        "faces": recipe.get("faces", "front"),
        "source": psd_path.name,
        "canvas": [psd.width, psd.height],
        "bbox": bbox,
        "stack": stack,
        "parts": parts,
        "blink": recipe.get("blink", "closed"),
        "blink_half": recipe.get("blink_half", "half"),  # 統合版 F1（目の部品に無ければ使わない）
        "expressions": recipe["expressions"],
        "poses": recipe["poses"],
        "gesture_arm": recipe.get("gesture_arm", {}),
    }
    (out_dir / "pack.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
    return meta


def psd_from(path: Path, work: Path) -> Path:
    """PSD または PSD を含む zip から PSD のパスを返す（zip は work に展開）。"""
    if path.suffix.lower() == ".psd":
        return path
    if path.suffix.lower() != ".zip":
        raise PackError("PSD または zip を指定してください")
    with zipfile.ZipFile(path) as z:
        psds = [n for n in z.namelist() if n.lower().endswith(".psd")]
        if not psds:
            raise PackError("zip の中に PSD がありません")
        name = psds[0]
        work.mkdir(parents=True, exist_ok=True)
        dst = work / Path(name).name
        with z.open(name) as fi, dst.open("wb") as fo:
            shutil.copyfileobj(fi, fo)
        for n in z.namelist():  # 作者の説明（規約）も残す
            if n.lower().endswith("readme.txt"):
                with z.open(n) as fi, (work / "readme.txt").open("wb") as fo:
                    shutil.copyfileobj(fi, fo)
                break
    return dst


# ---------------------------------------------------------------- 読み込み（描画用）
@dataclass
class Part:
    image: Any  # PIL RGBA（縮小・反転済み）
    x: float
    y: float

    @property
    def w(self) -> int:
        return int(self.image.width)

    @property
    def h(self) -> int:
        return int(self.image.height)


@dataclass
class Pack:
    id: str
    name: str
    credit: str
    faces: str
    size: tuple[int, int]  # 描画用キャンバス（bbox を縮小したもの）
    stack: list[Part | str]
    parts: dict[str, dict[str, Part | None]]
    expressions: dict[str, dict[str, Any]]
    poses: dict[str, dict[str, str]]
    blink: str
    gesture_arm: dict[str, str] = field(default_factory=dict)
    blink_half: str = ""  # 統合版 F1: 半目の目の部品の名前（無ければ空）

    def expression(self, name: str) -> dict[str, Any]:
        return self.expressions.get(name) or self.expressions["neutral"]

    def pose(self, name: str) -> dict[str, str]:
        return self.poses.get(name) or self.poses["neutral"]


def read_meta(pack_dir: Path) -> dict[str, Any]:
    return dict(json.loads((pack_dir / "pack.json").read_text("utf-8")))


def load_pack(pack_dir: Path, height: int, mirror: bool = False) -> Pack:
    """パックを高さ height（全体の範囲の高さ）に縮小して読み込む。mirror で左右反転。"""
    from PIL import Image

    meta = read_meta(pack_dir)
    x0, y0, x1, y1 = meta["bbox"]
    s = height / max(1, y1 - y0)
    cw, ch = round((x1 - x0) * s), height

    def part(p: dict[str, Any] | None) -> Part | None:
        if p is None:
            return None
        im = Image.open(pack_dir / "parts" / p["file"]).convert("RGBA")
        w, h = max(1, round(im.width * s)), max(1, round(im.height * s))
        im = im.resize((w, h), Image.Resampling.LANCZOS)
        x, y = (p["x"] - x0) * s, (p["y"] - y0) * s
        if mirror:
            im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            x = cw - (x + w)
        return Part(im, x, y)

    stack: list[Part | str] = []
    for item in meta["stack"]:
        if "fixed" in item:
            p = part(item["fixed"])
            if p is not None:
                stack.append(p)
        else:
            stack.append(item["slot"])
    parts = {k: {o: part(v) for o, v in opts.items()} for k, opts in meta["parts"].items()}
    for e in EXPRESSIONS:  # 欠けている表情・ポーズは neutral で補う
        meta["expressions"].setdefault(e, meta["expressions"]["neutral"])
    for p_ in POSES:
        meta["poses"].setdefault(p_, meta["poses"]["neutral"])
    return Pack(
        id=pack_dir.name,
        name=meta["name"],
        credit=meta.get("credit", ""),
        faces=meta.get("faces", "front"),
        size=(cw, ch),
        stack=stack,
        parts=parts,
        expressions=meta["expressions"],
        poses=meta["poses"],
        blink=meta.get("blink", "closed"),
        gesture_arm=meta.get("gesture_arm", {}),
        blink_half=meta.get("blink_half", "half"),
    )
