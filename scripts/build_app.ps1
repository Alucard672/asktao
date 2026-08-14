# Clean build of the Windows onedir app and distribution archive.
# Run from any directory:  powershell -ExecutionPolicy Bypass -File scripts\build_app.ps1
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "Project virtual environment not found at $Python. Create it first (see README)."
}

$AppName = "问道前台助手"
$DistDir = Join-Path $ProjectRoot "dist"
$ZipPath = Join-Path $DistDir "$AppName-win.zip"

Remove-Item -Force -ErrorAction SilentlyContinue $ZipPath
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $ProjectRoot "build")
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $DistDir $AppName)

& $Python -m PyInstaller --noconfirm --clean `
    --distpath $DistDir `
    --workpath (Join-Path $ProjectRoot "build") `
    (Join-Path $ProjectRoot "packaging\wendao_app_win.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

Compress-Archive -Path (Join-Path $DistDir $AppName) -DestinationPath $ZipPath

Write-Host "Built: $(Join-Path $DistDir $AppName)\$AppName.exe"
Write-Host "Archive: $ZipPath"
