"""サムネイルを 3 案作る（統合版 S4）。背景は字幕の無いプレビューの 1 フレーム。

背景のフレームは、背景だけの区間 → 画像の場面 → 題名の後、の順に選ぶ（カードの文字と重ならない所）。
画像を読める LLM があれば、2 案ずつ順番を入れ替えて比べ（統合版 Q1）、勝った数でおすすめ順に並べる。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from video_automation.app.logging import get_logger
from video_automation.schemas.provider import TaskType
from video_automation.schemas.timeline import PreviewRequest
from video_automation.services.rendering import thumbnail as T

log = get_logger(__name__)
QUESTION = "文字が読みやすく、動画の中身がひと目で分かるサムネイル"
_BOLD_FONTS = ("Noto Sans CJK JP Black", "Noto Sans CJK JP Bold", "M PLUS Rounded 1c Bold")


class ThumbnailService:
    def __init__(
        self,
        settings: Any,
        timeline: Any,
        render: Any,
        design: Any,
        titles: Any,
        llm: Any | None = None,
    ) -> None:
        self.settings = settings
        self.timeline = timeline
        self.render = render
        self.design = design
        self.titles = titles
        self.llm = llm

    async def _frames(self, project_id: str, n: int = 3) -> list[int]:
        tl = await self.timeline.get_timeline(project_id)
        fps = tl.fps_num / tl.fps_den
        rec = await self.design.direction_record(project_id)
        roles: dict[str, str] = rec.get("clips") or {}
        picks: list[int] = []
        for a, b in rec.get("empty_spans") or []:
            if b - a >= 1.0:
                picks.append(round((a + b) / 2 * fps))
        for c in tl.clips:
            if roles.get(c.id) == "image":
                picks.append((c.start_frame + c.end_frame) // 2)
        title_end = max(
            (c.end_frame for c in tl.clips if roles.get(c.id) == "title_card"), default=0
        )
        es = (rec.get("outline") or {}).get("end_screen")
        end = round(es * fps) if es else tl.duration_frames
        if not picks:  # 題名の後の、図やカードの出ていない所が無ければ、題名の後から等分
            span = max(1, end - title_end)
            picks = [title_end + span * (i + 1) // (n + 1) for i in range(n)]
        picks = [min(max(0, f), max(0, end - 1)) for f in picks]
        return [picks[i % len(picks)] for i in range(n)]

    def _font(self, heading: str | None) -> str | None:
        for name in (*_BOLD_FONTS, heading):
            if name and (p := self.titles.font_path(name)):
                return str(p)
        return None

    async def make(self, project_id: str, notes: list[str] | None = None) -> list[str]:
        """3 案を作り、おすすめ順のファイルの一覧を返す。"""
        notes = notes if notes is not None else []
        d = await self.design.get(project_id)
        prof = await self.design.profile_for(project_id) if d.purpose else None
        series = await self.design.series_for(project_id)
        layouts = list(T.LAYOUTS)
        if series and series.thumbnail_layout:  # 統合版 Q4: シリーズのサムネイルの型を 1 案目に
            layouts.sort(key=lambda x: x.name != series.thumbnail_layout)
        tl = await self.timeline.get_timeline(project_id)
        vertical = tl.height > tl.width
        outline = await self.design.outline(project_id)
        headings = [h for _, h in outline.get("chapters") or []]
        texts = T.thumb_texts(outline.get("title") or "", outline.get("catch") or "", headings)
        font = self._font(prof.typography.heading_font if prof else None)
        if font is None:
            raise RuntimeError("no Japanese font found")
        pal = prof.palette if prof else None
        out_dir = Path(self.settings.data_dir) / "projects" / project_id / "thumbnails"
        results = []
        for i, (frame, text, layout) in enumerate(
            zip(await self._frames(project_id), texts, layouts, strict=True)
        ):
            bg = await self.render.preview_frame(
                project_id, PreviewRequest(frame=frame, width=1920)
            )
            r = await asyncio.to_thread(
                T.draw,
                bg,
                out_dir / f"thumbnail_{i + 1}.jpg",
                text,
                layout,
                font,
                pal.text if pal else "#FFFFFF",
                pal.accent if pal else "#FFD23F",
                pal.base if pal else "#111111",
                vertical,
            )
            bad = T.checks(r, vertical)
            if bad:
                notes.append(f"サムネイル {i + 1} 案目: " + "、".join(bad))
            results.append(r)
        order = await self._rank([r.path for r in results])
        paths = [str(results[i].path) for i in order]
        how = "画像を読める AI の比べた順" if order != [0, 1, 2] else "作った順"
        notes.append(f"サムネイルを 3 案作りました（{how}）")
        log.info("thumbnails", project_id=project_id, order=order)
        return paths

    async def _rank(self, paths: list[Path]) -> list[int]:
        """2 案ずつ順番を入れ替えて比べ、勝った数の多い順（統合版 Q1）。
        比べられなければ作った順。"""
        if self.llm is None or len(paths) < 2:
            return list(range(len(paths)))
        try:
            await self.llm.resolve(TaskType.IMAGE_UNDERSTANDING)
        except Exception:  # noqa: BLE001 - 画像を読めるモデルが無い
            return list(range(len(paths)))
        from video_automation.services.qa.vision import compare

        imgs = [await asyncio.to_thread(_small, p) for p in paths]
        wins = [0] * len(paths)
        for i in range(len(paths)):
            for j in range(i + 1, len(paths)):
                try:
                    got = await compare(self.llm, imgs[i], imgs[j], QUESTION)
                except Exception:  # noqa: BLE001 - 答えを読めなければ数えない
                    continue
                if got == "a":
                    wins[i] += 1
                elif got == "b":
                    wins[j] += 1
        return sorted(range(len(paths)), key=lambda k: (-wins[k], k))


def _small(path: Path) -> bytes:
    """LLM に見せる縮小版（長辺 1024px の PNG）。"""
    import io

    from PIL import Image

    with Image.open(path) as im:
        im.thumbnail((1024, 1024))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()
