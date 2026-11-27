"""文字の読み取り（OCR）。Windows の Windows.Media.Ocr を PowerShell から使う。

- 日本語が使えることを OcrEngine.IsLanguageSupported で確かめる（使えなければ OcrUnavailable）
- 画像は OcrEngine.MaxImageDimension 以下に縮めてから読み、語の位置（OcrWord.BoundingRect）は
  元の画像の画素に戻して返す
- 1 回の呼び出しで複数の画像を読む（PowerShell の起動は 1 回）
- テストや Windows 以外では FakeOcr（あらかじめ決めた結果を返す）を使う
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from video_automation.app.logging import get_logger

log = get_logger("ocr")


class OcrUnavailable(RuntimeError):
    """OCR が使えない（Windows 以外・日本語の OCR が入っていない・PowerShell の失敗）。"""


@dataclass
class OcrWord:
    text: str
    x: float  # 元の画像の画素（左上）
    y: float
    w: float
    h: float


@dataclass
class OcrLine:
    words: list[OcrWord] = field(default_factory=list)

    @property
    def text(self) -> str:
        return join_words([w.text for w in self.words])

    @property
    def box(self) -> tuple[float, float, float, float]:
        return union([(w.x, w.y, w.w, w.h) for w in self.words])


@dataclass
class OcrResult:
    width: int
    height: int
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)


def _ascii(c: str) -> bool:
    return c.isascii() and c.isalnum()


def join_words(words: list[str]) -> str:
    """語をつなぐ。日本語の OCR は 1 字ずつの語を返すので、英数字どうしの間だけ空白を入れる。"""
    out = ""
    for w in words:
        if out and w and _ascii(out[-1]) and _ascii(w[0]):
            out += " "
        out += w
    return out


def union(boxes: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    if not boxes:
        return (0.0, 0.0, 0.0, 0.0)
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


class OcrEngine(Protocol):
    def read(self, images: list[Path]) -> list[OcrResult]: ...


def parse(data: list[dict]) -> list[OcrResult]:
    out: list[OcrResult] = []
    for item in data:
        lines = [
            OcrLine(
                [
                    OcrWord(str(w["t"]), float(w["x"]), float(w["y"]), float(w["w"]), float(w["h"]))
                    for w in ln.get("words", [])
                ]
            )
            for ln in item.get("lines", [])
        ]
        out.append(OcrResult(int(item.get("w", 0)), int(item.get("h", 0)), lines))
    return out


# PowerShell 5.1（Windows 10 以降）。入力: 画像のパスの一覧（JSON）。出力: 画像ごとの行と語（JSON）
PS_SCRIPT = r"""
param([string]$In, [string]$Out)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Wait-Op($op, [Type]$t) {
  $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op))
  $null = $task.Wait(-1)
  $task.Result
}
$lang = [Windows.Globalization.Language]::new('ja')
if (-not [Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) {
  [IO.File]::WriteAllText($Out, '{"error":"ja_not_supported"}', [Text.Encoding]::UTF8); exit 0
}
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
$max = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
$paths = [IO.File]::ReadAllText($In, [Text.Encoding]::UTF8) | ConvertFrom-Json
$SF = [Windows.Storage.StorageFile]
$RS = [Windows.Storage.Streams.IRandomAccessStream]
$BD = [Windows.Graphics.Imaging.BitmapDecoder]
$SB = [Windows.Graphics.Imaging.SoftwareBitmap]
$GI = 'Windows.Graphics.Imaging'
$res = @()
foreach ($p in $paths) {
  $file = Wait-Op ($SF::GetFileFromPathAsync($p)) $SF
  $stream = Wait-Op ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) $RS
  $dec = Wait-Op ($BD::CreateAsync($stream)) $BD
  $w = $dec.PixelWidth; $h = $dec.PixelHeight
  $s = [Math]::Min(1.0, $max / [double][Math]::Max($w, $h))
  $tr = [Windows.Graphics.Imaging.BitmapTransform]::new()
  $tr.ScaledWidth = [uint32][Math]::Max(1, [Math]::Floor($w * $s))
  $tr.ScaledHeight = [uint32][Math]::Max(1, [Math]::Floor($h * $s))
  $bmp = Wait-Op ($dec.GetSoftwareBitmapAsync(
    [Enum]::Parse([Type]"$GI.BitmapPixelFormat", 'Bgra8'),
    [Enum]::Parse([Type]"$GI.BitmapAlphaMode", 'Premultiplied'), $tr,
    [Enum]::Parse([Type]"$GI.ExifOrientationMode", 'IgnoreExifOrientation'),
    [Enum]::Parse([Type]"$GI.ColorManagementMode", 'DoNotColorManage'))) $SB
  $r = Wait-Op ($engine.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
  $lines = @()
  foreach ($ln in $r.Lines) {
    $ws = @()
    foreach ($wd in $ln.Words) {
      $b = $wd.BoundingRect
      $ws += @{ t = $wd.Text; x = $b.X / $s; y = $b.Y / $s; w = $b.Width / $s; h = $b.Height / $s }
    }
    $lines += @{ words = $ws }
  }
  $res += @{ w = $w; h = $h; lines = $lines }
  $stream.Dispose()
}
$json = ConvertTo-Json -InputObject @{ items = $res } -Depth 8 -Compress
[IO.File]::WriteAllText($Out, $json, [Text.Encoding]::UTF8)
"""


class WindowsOcr:
    """Windows.Media.Ocr（日本語）。PowerShell 5.1 を 1 回起こして複数の画像を読む。"""

    def __init__(self, powershell: str = "powershell", timeout: float = 600.0) -> None:
        self.powershell, self.timeout = powershell, timeout

    def read(self, images: list[Path]) -> list[OcrResult]:
        if not images:
            return []
        if sys.platform != "win32":
            raise OcrUnavailable("Windows.Media.Ocr is only on Windows")
        with tempfile.TemporaryDirectory(prefix="va_ocr_") as d:
            ps = Path(d) / "ocr.ps1"
            # PowerShell 5.1 は BOM の無い UTF-8 のスクリプトの日本語を壊す
            ps.write_text(PS_SCRIPT, encoding="utf-8-sig")
            src, dst = Path(d) / "in.json", Path(d) / "out.json"
            src.write_text(json.dumps([str(p.resolve()) for p in images]), encoding="utf-8")
            cmd = [
                self.powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ps),
                "-In",
                str(src),
                "-Out",
                str(dst),
            ]
            try:
                p = subprocess.run(cmd, capture_output=True, timeout=self.timeout, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise OcrUnavailable(f"powershell failed: {type(exc).__name__}") from exc
            if not dst.is_file():
                tail = p.stderr.decode("utf-8", "replace")[-300:]
                log.warning("ocr_failed", code=p.returncode, stderr=tail)
                raise OcrUnavailable("ocr script failed")
            data = json.loads(dst.read_text(encoding="utf-8-sig"))
        if "error" in data:
            raise OcrUnavailable(str(data["error"]))
        items = data.get("items") or []
        if isinstance(items, dict):  # 1 件だけのとき ConvertTo-Json は配列にしない
            items = [items]
        return parse(items)


class FakeOcr:
    """テスト用: 画像の名前（stem）ごとに決めた結果を返す。無い画像は空の結果。"""

    def __init__(self, results: dict[str, OcrResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[list[Path]] = []

    def read(self, images: list[Path]) -> list[OcrResult]:
        self.calls.append(list(images))
        return [self.results.get(p.stem, OcrResult(0, 0)) for p in images]


def default_engine() -> OcrEngine | None:
    return WindowsOcr() if sys.platform == "win32" else None
