#!/usr/bin/env bash
# Bundle the pure-Python reflow core into web/app.zip, which index.html fetches
# and unpacks onto Pyodide's sys.path in the browser. Only the modules the
# browser actually imports are included: web_adapter.py (the bytes-in/bytes-out
# entry point) plus the workflow/ and lib/ packages it pulls in. The CLI adapter
# (reflow.py), tests/, and scripts/ are deliberately excluded.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="$root/web/app.zip"

cd "$root"
rm -f "$out"
zip -r "$out" web_adapter.py workflow lib -x '*__pycache__*' >/dev/null
echo "[build-web] wrote $out"
