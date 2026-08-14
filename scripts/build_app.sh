#!/bin/zsh
# Clean build of the macOS app bundle and distribution archive.
set -euo pipefail

PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"

rm -f "$PROJECT_ROOT/dist/问道前台助手.zip"
rm -rf "$PROJECT_ROOT/build" "$PROJECT_ROOT/dist/问道前台助手" "$PROJECT_ROOT/dist/问道前台助手.app"

"$PROJECT_ROOT/.venv/bin/python" -m PyInstaller --noconfirm --clean \
  --distpath "$PROJECT_ROOT/dist" \
  --workpath "$PROJECT_ROOT/build" \
  "$PROJECT_ROOT/packaging/wendao_app.spec"

ditto -c -k --sequesterRsrc --keepParent \
  "$PROJECT_ROOT/dist/问道前台助手.app" \
  "$PROJECT_ROOT/dist/问道前台助手.zip"
