#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo 'Build this target on a Mac. Windows cannot produce a working macOS bundle.' >&2
  exit 1
fi
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python scripts/make-icon.py
.venv/bin/python scripts/prepare-ocr.py
.venv/bin/python scripts/collect-licenses.py
.venv/bin/python scripts/package-sources.py
.venv/bin/python -m PyInstaller --noconfirm --clean ADF.spec
mkdir -p release
stage="$(mktemp -d "${TMPDIR:-/tmp}/adf-dmg.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
ditto dist/ADF.app "$stage/ADF.app"
ln -s /Applications "$stage/Applications"
hdiutil create -volname ADF -srcfolder "$stage" -ov -format UDZO release/ADF-0.3.24-macOS.dmg
.venv/bin/python scripts/package-release.py
echo 'Unsigned native architecture macOS DMG built. Signing, notarization, and Mac smoke testing are release steps.'
