"""編集差分の人が読める表示（P8-07）。

タイムライン命令の before / after スナップショット（{"tracks": {id: row|None}, "clips": {...}}）
から、追加・削除・移動・長さ・音量・フェード・アニメーション等の変化を日本語で列挙する。
"""

from typing import Any


def fmt_time(frame: int, fps: float) -> str:
    sec = frame / fps
    m, s = divmod(sec, 60)
    return f"{int(m):02d}:{s:05.2f}"


def _clip_label(c: dict[str, Any], names: dict[str, str]) -> str:
    asset = names.get(c.get("asset_id", ""), str(c.get("asset_id", ""))[:8])
    return f"「{asset}」"


def describe_snapshots(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    fps: float,
    asset_names: dict[str, str] | None = None,
    track_names: dict[str, str] | None = None,
) -> list[str]:
    before = before or {"tracks": {}, "clips": {}}
    after = after or {"tracks": {}, "clips": {}}
    names = asset_names or {}
    tnames = dict(track_names or {})
    for snap in (before, after):
        for tid, t in snap.get("tracks", {}).items():
            if t:
                tnames.setdefault(tid, t.get("name") or tid[:8])
    out: list[str] = []
    for tid in sorted(set(before.get("tracks", {})) | set(after.get("tracks", {}))):
        a, b = before["tracks"].get(tid), after["tracks"].get(tid)
        if a is None and b is not None:
            out.append(f"トラック追加: {b.get('name') or tid[:8]}（{b.get('kind')}）")
        elif a is not None and b is None:
            out.append(f"トラック削除: {a.get('name') or tid[:8]}")
        elif a and b:
            if a.get("muted") != b.get("muted"):
                out.append(
                    f"トラック {b.get('name')}: {'ミュート' if b.get('muted') else 'ミュート解除'}"
                )
            if a.get("name") != b.get("name"):
                out.append(f"トラック名: {a.get('name')} → {b.get('name')}")
    for cid in sorted(set(before.get("clips", {})) | set(after.get("clips", {}))):
        a, b = before["clips"].get(cid), after["clips"].get(cid)
        if a is None and b is None:
            continue
        if a is None:
            assert b is not None
            extra = []
            if b.get("volume", 1.0) != 1.0:
                extra.append(f"音量 {b['volume']:.2f}")
            if b.get("fade_in_frames"):
                extra.append(f"フェードイン {b['fade_in_frames']}f")
            if b.get("fade_out_frames"):
                extra.append(f"フェードアウト {b['fade_out_frames']}f")
            if b.get("animation"):
                extra.append("アニメーションあり")
            out.append(
                f"クリップ追加: {_clip_label(b, names)} を {tnames.get(b['track_id'], '?')} の "
                f"{fmt_time(b['start_frame'], fps)}〜{fmt_time(b['end_frame'], fps)} に配置"
                + (f"（{'、'.join(extra)}）" if extra else "")
            )
            continue
        if b is None:
            out.append(
                f"クリップ削除: {_clip_label(a, names)}（{fmt_time(a['start_frame'], fps)}〜）"
            )
            continue
        label = _clip_label(b, names)
        parts: list[str] = []
        if a["track_id"] != b["track_id"]:
            parts.append(
                f"トラック {tnames.get(a['track_id'], '?')} → {tnames.get(b['track_id'], '?')}"
            )
        la, lb = a["end_frame"] - a["start_frame"], b["end_frame"] - b["start_frame"]
        if a["start_frame"] != b["start_frame"] and la == lb:
            parts.append(
                f"移動 {fmt_time(a['start_frame'], fps)} → {fmt_time(b['start_frame'], fps)}"
            )
        elif (a["start_frame"], a["end_frame"]) != (b["start_frame"], b["end_frame"]):
            parts.append(
                f"区間 {fmt_time(a['start_frame'], fps)}〜{fmt_time(a['end_frame'], fps)} → "
                f"{fmt_time(b['start_frame'], fps)}〜{fmt_time(b['end_frame'], fps)}"
            )
        if (a.get("source_in_frame"), a.get("source_out_frame")) != (
            b.get("source_in_frame"),
            b.get("source_out_frame"),
        ) and la == lb:
            parts.append("素材の使用範囲を変更")
        if a.get("volume") != b.get("volume"):
            parts.append(f"音量 {a.get('volume'):.2f} → {b.get('volume'):.2f}")
        for key, name in (
            ("fade_in_frames", "フェードイン"),
            ("fade_out_frames", "フェードアウト"),
        ):
            if a.get(key) != b.get(key):
                parts.append(f"{name} {a.get(key)}f → {b.get(key)}f")
        if a.get("transform") != b.get("transform"):
            parts.append("位置・大きさを変更")
        if a.get("animation") != b.get("animation"):
            anim = b.get("animation") or {}
            what = []
            if anim.get("keyframes"):
                what.append(f"キーフレーム{len(anim['keyframes'])}個")
            if anim.get("transition_in"):
                what.append(f"切替 {anim['transition_in'].get('type')}")
            parts.append("アニメーション" + (f"（{'・'.join(what)}）" if what else "を解除"))
        if parts:
            out.append(f"クリップ {label}: " + "、".join(parts))
    return out
