# VideoAutomation のアンインストール。ショートカットとアプリのファイルを消す。
# データ（data フォルダ：プロジェクト・素材・書き出し）は -RemoveData を付けたときだけ消す。
param([switch]$RemoveData)
$ErrorActionPreference = 'Stop'
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($t in @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'VideoAutomation.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'VideoAutomation.lnk'))) {
  if (Test-Path $t) {
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut($t)
    if ($lnk.TargetPath -like "$Dir*") { Remove-Item -Force $t; Write-Host "削除: $t" }
  }
}
foreach ($item in 'src', 'pyproject.toml', 'uv.lock', 'README.md', 'docs', '.env.example', '.venv', 'uninstall.bat') {
  $p = Join-Path $Dir $item
  if (Test-Path $p) { Remove-Item -Recurse -Force $p }
}
if ($RemoveData) {
  foreach ($item in 'data', '.env') { $p = Join-Path $Dir $item; if (Test-Path $p) { Remove-Item -Recurse -Force $p } }
  Write-Host "データも削除しました"
} else {
  Write-Host "データは残しました: $(Join-Path $Dir 'data')"
}
Remove-Item -Force (Join-Path $Dir 'uninstall.ps1')
