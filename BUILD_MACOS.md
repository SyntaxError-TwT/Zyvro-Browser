# Building Zyvro for macOS

Zyvro remains a QtPy + PySide6/QtWebEngine browser on macOS. It does not switch
to WebKit. The Mac package includes the native Rust ad blocker, Qt WebEngine,
the filter lists, browser artwork, and all ordinary Python dependencies.

## Output

Running the Mac build produces an architecture-specific disk image:

```text
dist/Zyvro-0.4.1-macOS-arm64.dmg
```

On an Intel Mac, the suffix is `x86_64`. The disk image contains `Zyvro.app`
and an Applications shortcut for drag-and-drop installation.

The Finder window uses a center-cropped, unstretched copy of
`assets/zyvro-background.png`, the same artwork used on Zyvro's new-tab page.
The app and Applications icons are placed symmetrically over that background.

## Why the final build must run on a Mac

The `.app` contains platform-native Qt frameworks, a QtWebEngineProcess helper,
and `libzyvro_adblock.dylib`. Those binaries cannot be generated or validated
on Windows. Creating a DMG-shaped archive on Windows would not produce a
working Mac application.

## Requirements

- macOS 13 or newer
- Xcode Command Line Tools: `xcode-select --install`
- Python 3 matching the Mac architecture
- Rust through rustup: `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh`
- `create-dmg`: `brew install create-dmg`

Create the project environment once:

```bash
cd "/path/to/Python Web browser"
python3 -m venv .venv
```

Build the app and DMG:

```bash
chmod +x build-macos.sh
./build-macos.sh
```

The default build uses an ad-hoc signature and is suitable for local testing.
Gatekeeper can warn when that DMG is copied to another Mac.

If the source is placed in a GitHub repository, the included manually triggered
`Build Zyvro macOS DMG` workflow can perform the same native Mac build and
return the unsigned test DMG as a workflow artifact. It does not publish a
release or change anyone's default browser.

## Developer ID signing and notarization

For distribution, import an Apple Developer ID Application certificate and
set its exact Keychain identity:

```bash
export APPLE_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
./build-macos.sh
```

To notarize as part of the same build, first save notary credentials:

```bash
xcrun notarytool store-credentials "zyvro-notary" \
  --apple-id "you@example.com" \
  --team-id "TEAMID" \
  --password "app-specific-password"
```

Then build with both values:

```bash
export APPLE_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export APPLE_NOTARY_PROFILE="zyvro-notary"
./build-macos.sh
```

The script signs the complete app—including Chromium helpers and the Rust
library—verifies it, creates the compressed DMG, optionally notarizes it, and
prints the final SHA-256 checksum.

## Tor on macOS

The existing bundled Tor Expert Bundle is a Windows binary and is deliberately
not copied into the Mac application. Zyvro's Tor mode can connect to a real
local Tor SOCKS service at `127.0.0.1:9050` or `127.0.0.1:9150`; it does not
claim a connection if neither endpoint responds.

## Architecture

The build is native to the Mac running it. Build on Apple Silicon for `arm64`
or on an Intel Mac for `x86_64`. A universal app requires universal Python and
Qt binaries plus both Rust targets; this script intentionally avoids labeling
a single-architecture application as universal.
