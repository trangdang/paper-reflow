#!/usr/bin/env bash
# Bundle the pure-Python reflow core into web/dist/app.zip, which index.html
# fetches and unpacks onto Pyodide's sys.path in the browser. Only the modules
# the browser actually imports are included: web_adapter.py (the bytes-in/
# bytes-out entry point) plus the workflow/ and lib/ packages it pulls in. The
# CLI adapter (reflow.py), tests/, and scripts/ are deliberately excluded.
#
# Everything the static site needs to serve lives together under web/dist/ so
# it can be uploaded as a single self-contained deploy directory.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
web="$root/web"
dist="$web/dist"

cd "$root"
rm -rf "$dist"
mkdir -p "$dist"
cp "$web/index.html" "$web/app.js" "$web/style.css" "$dist/"
zip -r "$dist/app.zip" web_adapter.py workflow lib -x '*__pycache__*' >/dev/null
echo "[build-web] wrote $dist"
