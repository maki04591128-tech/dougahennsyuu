"""PDF のページを画像にする（統合版 P3: 2 倍の解像度）。

- Windows: Windows.Data.Pdf（PdfPage.RenderToStreamAsync）を PowerShell から使う（追加の部品なし）
- それ以外: Poppler の pdftoppm があれば使う（テスト・開発用）
2 倍 = 画面の標準（96dpi、Windows.Data.Pdf のページの大きさの単位）の 2 倍（192dpi）。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from video_automation.app.logging import get_logger
from video_automation.integrations.ocr.engine import OcrUnavailable

log = get_logger("pdf")
SCALE = 2.0
MAX_PAGES = 200

PS_SCRIPT = r"""
param([string]$Pdf, [string]$Dir, [double]$Scale, [int]$Max)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFolder, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]
$ms = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTask = ($ms | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskA = ($ms | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
function Wait-Op($op, [Type]$t) {
  $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $null = $task.Wait(-1); $task.Result
}
function Wait-Act($op) { $task = $asTaskA.Invoke($null, @($op)); $null = $task.Wait(-1) }
$SF = [Windows.Storage.StorageFile]
$FO = [Windows.Storage.StorageFolder]
$PD = [Windows.Data.Pdf.PdfDocument]
$file = Wait-Op ($SF::GetFileFromPathAsync($Pdf)) $SF
$doc = Wait-Op ($PD::LoadFromFileAsync($file)) $PD
$folder = Wait-Op ($FO::GetFolderFromPathAsync($Dir)) $FO
$n = [Math]::Min([int]$doc.PageCount, $Max)
$coll = [Windows.Storage.CreationCollisionOption]::ReplaceExisting
for ($i = 0; $i -lt $n; $i++) {
  $page = $doc.GetPage([uint32]$i)
  $opt = [Windows.Data.Pdf.PdfPageRenderOptions]::new()
  $opt.DestinationWidth = [uint32][Math]::Round($page.Size.Width * $Scale)
  $opt.DestinationHeight = [uint32][Math]::Round($page.Size.Height * $Scale)
  $name = 'p{0:D3}.png' -f ($i + 1)
  $out = Wait-Op ($folder.CreateFileAsync($name, $coll)) $SF
  $st = Wait-Op ($out.OpenAsync([Windows.Storage.FileAccessMode]::ReadWrite)) `
    ([Windows.Storage.Streams.IRandomAccessStream])
  Wait-Act ($page.RenderToStreamAsync($st, $opt))
  $st.Dispose(); $page.Dispose()
}
"""


def render_pages(pdf: Path, out_dir: Path, scale: float = SCALE) -> list[Path]:
    """PDF の各ページを out_dir/p001.png … にする（最大 MAX_PAGES 枚）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("p*.png"):
        old.unlink()
    if sys.platform == "win32":
        _windows(pdf, out_dir, scale)
    elif shutil.which("pdftoppm"):
        subprocess.run(
            [
                "pdftoppm",
                "-png",
                "-r",
                str(round(72 * scale)),
                "-l",
                str(MAX_PAGES),
                str(pdf),
                str(out_dir / "pp"),
            ],
            check=True,
            capture_output=True,
        )
        # pdftoppm の名前（pp-01.png など、桁はページ数で変わる）を p001.png にそろえる
        for f in sorted(out_dir.glob("pp-*.png"), key=lambda p: int(p.stem.split("-")[-1])):
            f.rename(out_dir / f"p{int(f.stem.split('-')[-1]):03d}.png")
    else:
        raise OcrUnavailable("no PDF renderer (Windows.Data.Pdf or pdftoppm)")
    pages = sorted(out_dir.glob("p[0-9][0-9][0-9].png"))
    if not pages:
        raise OcrUnavailable("PDF has no pages")
    return pages


def _windows(pdf: Path, out_dir: Path, scale: float) -> None:
    with tempfile.TemporaryDirectory(prefix="va_pdf_") as d:
        ps = Path(d) / "pdf.ps1"
        ps.write_text(PS_SCRIPT, encoding="utf-8-sig")
        cmd = [
            "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(ps), "-Pdf", str(pdf.resolve()), "-Dir", str(out_dir.resolve()),
            "-Scale", str(scale), "-Max", str(MAX_PAGES),
        ]  # fmt: skip
        try:
            p = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OcrUnavailable(f"powershell failed: {type(exc).__name__}") from exc
        if p.returncode != 0:
            log.warning("pdf_render_failed", stderr=p.stderr.decode("utf-8", "replace")[-300:])
            raise OcrUnavailable("PDF render failed")
