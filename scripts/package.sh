#!/usr/bin/env bash
# Builds Remote-Control-v<version>.zip in the Decky layout (single top-level folder).
set -euo pipefail
cd "$(dirname "$0")/.."
pnpm install --frozen-lockfile
pnpm run build
VER=$(node -p "require('./package.json').version")
STAGE=$(mktemp -d)
D="$STAGE/Remote Control"
mkdir -p "$D"
cp -r dist web py_modules main.py plugin.json package.json LICENSE README.md "$D/"
find "$D" -name __pycache__ -prune -exec rm -rf {} +
mkdir -p out
python3 -c "import shutil,sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2], 'Remote Control')" "out/Remote-Control-v$VER" "$STAGE"
echo "out/Remote-Control-v$VER.zip"
