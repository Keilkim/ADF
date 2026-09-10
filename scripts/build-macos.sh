#!/bin/bash
# Build dist/ADF.app and release/ADF-<version>-macOS.dmg for Apple Silicon Macs.
#   ADF_CODESIGN_IDENTITY  "Developer ID Application: ..." certificate in the keychain.
#                          Without it the app is only ad hoc signed and Gatekeeper
#                          blocks it on other Macs.
#   ADF_NOTARY_PROFILE     `xcrun notarytool store-credentials` profile. The app and
#                          DMG are notarized and stapled, so first launch works offline.
#   PYTHON                 Python 3.12 used to create .venv (default: python3.12).
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo 'Build this target on a Mac. Windows cannot produce a working macOS bundle.' >&2
  exit 1
fi
identity="${ADF_CODESIGN_IDENTITY:-}"
profile="${ADF_NOTARY_PROFILE:-}"
if [[ -n "$profile" && -z "$identity" ]]; then
  echo 'Notarization requires ADF_CODESIGN_IDENTITY.' >&2
  exit 1
fi
if [[ ! -x .venv/bin/python ]]; then
  "${PYTHON:-python3.12}" -m venv .venv
fi
python=.venv/bin/python
"$python" -m pip install -r requirements-build.txt
# The opencv-python macOS wheel links FFmpeg and GPL video codecs; use a build without video I/O.
"$python" scripts/build-opencv-macos.py
"$python" scripts/make-icon.py
"$python" scripts/prepare-ocr.py
"$python" scripts/collect-licenses.py
"$python" scripts/package-sources.py
ADF_CODESIGN_IDENTITY="$identity" "$python" -m PyInstaller --noconfirm --clean ADF.spec
version="$("$python" -c 'import sys; sys.path.insert(0, "scripts"); from pathlib import Path; from release_common import version; print(version(Path.cwd()))')"
dmg="release/ADF-$version-macOS.dmg"
mkdir -p release .tools/verification

notarize() {
  local log=".tools/verification/notarization-$2-$version.json"
  xcrun notarytool submit "$1" --keychain-profile "$profile" --wait --output-format json > "$log"
  if ! grep -q '"status" *: *"Accepted"' "$log"; then
    echo "Notarization was not accepted. See $log and 'xcrun notarytool log'." >&2
    exit 1
  fi
}

if [[ -n "$identity" ]]; then
  codesign --verify --deep --strict dist/ADF.app
  if [[ -n "$profile" ]]; then
    ditto -c -k --keepParent dist/ADF.app build/ADF-notarization.zip
    notarize build/ADF-notarization.zip app
    xcrun stapler staple dist/ADF.app
  fi
fi
stage="$(mktemp -d "${TMPDIR:-/tmp}/adf-dmg.XXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
ditto dist/ADF.app "$stage/ADF.app"
ln -s /Applications "$stage/Applications"
hdiutil create -volname ADF -srcfolder "$stage" -ov -format UDZO "$dmg"
if [[ -n "$identity" ]]; then
  codesign --sign "$identity" --timestamp "$dmg"
  if [[ -n "$profile" ]]; then
    notarize "$dmg" dmg
    xcrun stapler staple "$dmg"
    spctl --assess --type execute --verbose dist/ADF.app
    spctl --assess --type open --context context:primary-signature --verbose "$dmg"
  fi
fi
"$python" scripts/package-release.py
"$python" scripts/verify-release.py --report ".tools/verification/release-macos-$version.json"
if [[ -n "$profile" ]]; then
  echo "Signed and notarized: $dmg"
elif [[ -n "$identity" ]]; then
  echo "Signed but not notarized: Gatekeeper still blocks $dmg on other Macs."
else
  echo "Ad hoc signed test build: $dmg. Gatekeeper blocks it on other Macs."
fi
