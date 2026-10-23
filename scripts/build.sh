#!/usr/bin/env bash
# Build script — packages the project for distribution
#
# Usage: ./scripts/build.sh

set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Building package..."
python -m build --sdist --wheel
echo "==> Build complete. Artifacts in dist/"
