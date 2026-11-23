# VideoAutomation セットアップ（Windows）。配布 zip を展開したフォルダで install.bat から実行する。
#  - アプリを InstallDir にコピーし、uv で Python 3.12 と依存パッケージを入れる
#  - データは InstallDir\data に置く（再インストール・更新でも消さない）
#  - デスクトップとスタートメニューにショートカットを作る（-NoShortcut で作らない）
#  - 最後に動作環境の確認（--doctor）を行い、結果を install_log.txt に書く
param(
  [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'VideoAutomation'),
  [switch]$NoShortcut,
  [switch]$InstallUv
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log = Join-Path $Src 'install_log.txt'
$Lines = New-Object System.Collections.Generic.List[string]
function Log([string]$s) { $Lines.Add($s); Write-Host $s; [IO.File]::WriteAllText($Log, ($Lines -join "`r`n"), (New-Object Text.UTF8Encoding($true))) }

try {
  Log "VideoAutomation セットアップ開始 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
  Log "インストール先: $InstallDir"

  # 1) uv（Python とパッケージの管理ツール）
  $Uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
  if (-not $Uv) { $cand = Join-Path $env:USERPROFILE '.local\bin\uv.exe'; if (Test-Path $cand) { $Uv = $cand } }
  if (-not $Uv) {
    if (-not $InstallUv) {
      Log "uv が見つかりません。https://docs.astral.sh/uv/ の手順で入れるか、install.bat に -InstallUv を付けて再実行してください。"
      exit 2
    }
    Log "uv を公式のインストーラーで入れます（https://astral.sh/uv/install.ps1）"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    $Uv = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
  }
  Log "uv: $(& $Uv --version)"

  # 2) アプリのファイルをコピー（data と .env は残す）
  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  foreach ($item in 'src', 'pyproject.toml', 'uv.lock', 'README.md', 'docs', '.env.example', 'uninstall.ps1', 'uninstall.bat') {
    $p = Join-Path $Src $item
    if (-not (Test-Path $p)) { continue }
    $dst = Join-Path $InstallDir $item
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse -Force $p $dst
  }
  New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir 'data') | Out-Null
  $envFile = Join-Path $InstallDir '.env'
  if (-not (Test-Path $envFile)) {
    $dataDir = Join-Path $InstallDir 'data'
    [IO.File]::WriteAllText($envFile, "VA_DATA_DIR=$dataDir`r`n", (New-Object Text.UTF8Encoding($false)))
    Log ".env を作成しました（データ: $dataDir）"
  } else { Log ".env は既存のものを使います" }

  # 3) Python 3.12 と依存パッケージ（uv.lock の固定版）
  Push-Location $InstallDir
  try {
    $env:UV_PYTHON = '3.12'
    $out = & $Uv sync --frozen --no-dev --extra ui --python 3.12 2>&1 | Out-String
    Log $out.Trim()
    if ($LASTEXITCODE -ne 0) { throw "uv sync に失敗しました（exit $LASTEXITCODE）" }
  } finally { Pop-Location }
  $Exe = Join-Path $InstallDir '.venv\Scripts\video-automation.exe'
  if (-not (Test-Path $Exe)) { throw "起動ファイルが作られていません: $Exe" }

  # 4) ショートカット
  if (-not $NoShortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $targets = @(
      (Join-Path ([Environment]::GetFolderPath('Desktop')) 'VideoAutomation.lnk'),
      (Join-Path ([Environment]::GetFolderPath('Programs')) 'VideoAutomation.lnk')
    )
    foreach ($t in $targets) {
      $lnk = $shell.CreateShortcut($t)
      $lnk.TargetPath = $Exe
      $lnk.WorkingDirectory = $InstallDir
      $lnk.WindowStyle = 7  # 最小化（コンソールを前面に出さない）
      $lnk.Description = 'VideoAutomation 動画編集自動化'
      $lnk.Save()
      Log "ショートカット: $t"
    }
  } else { Log "ショートカットは作りません（-NoShortcut）" }

  # 5) 動作環境の確認
  Push-Location $InstallDir
  try {
    $env:PYTHONUTF8 = '1'
    $doc = & $Exe --doctor 2>&1 | Out-String
    $code = $LASTEXITCODE
    Log "---- 動作環境の確認 ----"
    Log $doc.Trim()
  } finally { Pop-Location }
  if ($code -ne 0) { Log "必須の項目が不足しています。docs\USER_MANUAL.md の「必要なソフト」を参照してください。"; exit 1 }
  Log "セットアップ完了 $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
  exit 0
} catch {
  Log "エラー: $($_.Exception.Message)"
  exit 1
}
