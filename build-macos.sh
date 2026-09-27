#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
RUST_MANIFEST="$ROOT/rust/zyvro-adblock/Cargo.toml"
RUST_LIBRARY="$ROOT/rust/zyvro-adblock/target/release/libzyvro_adblock.dylib"
MAC_RUNTIME="$ROOT/runtime-macos/adblock"
APP="$ROOT/dist/Zyvro.app"
STAGE="$ROOT/build/dmg-stage"
ARCH="$(uname -m)"
VERSION="$(sed -nE 's/^APP_VERSION[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/p' browser_maintenance.py | head -n 1)"
DMG="$ROOT/dist/Zyvro-${VERSION}-macOS-${ARCH}.dmg"
SIGN_IDENTITY="${APPLE_SIGN_IDENTITY:--}"
NOTARY_PROFILE="${APPLE_NOTARY_PROFILE:-}"

if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "This build must run on macOS. Qt WebEngine and the Rust library are native Mac binaries." >&2
    exit 2
fi
if [[ ! -x "$PYTHON" ]]; then
    echo "Missing project environment: $PYTHON" >&2
    echo "Create it with: python3 -m venv .venv" >&2
    exit 2
fi
if [[ -z "$VERSION" ]]; then
    echo "Could not read APP_VERSION from browser_maintenance.py" >&2
    exit 2
fi
command -v cargo >/dev/null || { echo "Rust Cargo is required." >&2; exit 2; }
command -v codesign >/dev/null || { echo "codesign is required." >&2; exit 2; }
if ! command -v create-dmg >/dev/null; then
    if command -v brew >/dev/null; then
        brew install create-dmg
    else
        echo "create-dmg is required. Install it from https://github.com/create-dmg/create-dmg" >&2
        exit 2
    fi
fi

"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt 'pyinstaller>=6.15,<7' 'pillow>=11,<12'
"$PYTHON" packaging/generate_macos_icon.py
"$PYTHON" packaging/generate_dmg_background.py

# Cargo automatically builds for the Mac architecture running this script.
# An arm64 Mac creates an arm64 dylib; an Intel Mac creates x86_64.
cargo build --release --locked --manifest-path "$RUST_MANIFEST"
if [[ ! -f "$RUST_LIBRARY" ]]; then
    echo "Cargo did not produce $RUST_LIBRARY" >&2
    exit 3
fi
rm -rf "$ROOT/runtime-macos"
mkdir -p "$MAC_RUNTIME"
cp "$RUST_LIBRARY" "$MAC_RUNTIME/libzyvro_adblock.dylib"
cp "$ROOT/rust/zyvro-adblock/LICENSE.txt" "$MAC_RUNTIME/LICENSE.txt"

rm -rf "$ROOT/build/Zyvro-macOS" "$ROOT/dist/Zyvro" "$APP" "$STAGE" "$DMG"
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    --workpath "$ROOT/build/Zyvro-macOS" \
    "$ROOT/Zyvro-macOS.spec"

if [[ ! -d "$APP" ]]; then
    echo "PyInstaller did not produce $APP" >&2
    exit 3
fi

# Sign the complete app, including QtWebEngineProcess helpers and the Rust
# library. '-' is a local ad-hoc signature; provide APPLE_SIGN_IDENTITY for a
# distributable Developer ID signature.
codesign --force --deep --options runtime \
    --entitlements "$ROOT/packaging/macos.entitlements" \
    --sign "$SIGN_IDENTITY" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/Zyvro.app"
create-dmg \
    --volname "Zyvro" \
    --volicon "$ROOT/packaging/Zyvro.icns" \
    --background "$ROOT/packaging/Zyvro-dmg-background.png" \
    --window-pos 160 100 \
    --window-size 720 450 \
    --text-size 13 \
    --icon-size 112 \
    --icon "Zyvro.app" 180 225 \
    --hide-extension "Zyvro.app" \
    --app-drop-link 540 225 \
    --filesystem APFS \
    --format UDZO \
    --overwrite \
    "$DMG" \
    "$STAGE"

if [[ "$SIGN_IDENTITY" != "-" ]]; then
    codesign --force --sign "$SIGN_IDENTITY" "$DMG"
fi

if [[ -n "$NOTARY_PROFILE" ]]; then
    if [[ "$SIGN_IDENTITY" == "-" ]]; then
        echo "APPLE_NOTARY_PROFILE requires a Developer ID APPLE_SIGN_IDENTITY." >&2
        exit 4
    fi
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
    xcrun stapler validate "$DMG"
fi

echo
echo "Build complete:"
echo "$DMG"
shasum -a 256 "$DMG"
