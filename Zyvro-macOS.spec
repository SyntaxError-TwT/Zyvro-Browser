# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import re


root = Path(SPECPATH)
version_source = (root / "browser_maintenance.py").read_text(encoding="utf-8")
version_match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', version_source, re.MULTILINE)
if not version_match:
    raise RuntimeError("Could not read APP_VERSION from browser_maintenance.py")
app_version = version_match.group(1)

runtime_macos = root / "runtime-macos"
native_library = runtime_macos / "adblock" / "libzyvro_adblock.dylib"
if not native_library.is_file():
    raise RuntimeError(
        "The macOS Rust ad-block library is missing. Run ./build-macos.sh."
    )

a = Analysis(
    [str(root / "scratch_browser.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / "assets"), "assets"),
        (str(runtime_macos), "runtime"),
    ],
    hiddenimports=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtPrintSupport",
        "keyring.backends.macOS",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "unittest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Zyvro",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=str(root / "packaging" / "macos.entitlements"),
    icon=str(root / "packaging" / "Zyvro.icns"),
)

collection = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Zyvro",
)

app = BUNDLE(
    collection,
    name="Zyvro.app",
    icon=str(root / "packaging" / "Zyvro.icns"),
    bundle_identifier="com.zyvro.browser",
    info_plist={
        "CFBundleDisplayName": "Zyvro",
        "CFBundleName": "Zyvro",
        "CFBundleShortVersionString": app_version,
        "CFBundleVersion": app_version,
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSSupportsAutomaticGraphicsSwitching": True,
        "NSCameraUsageDescription": "Zyvro allows camera access only for websites you approve.",
        "NSMicrophoneUsageDescription": "Zyvro allows microphone access only for websites you approve.",
        "NSLocationWhenInUseUsageDescription": "Zyvro shares location only with websites you approve.",
        "NSScreenCaptureUsageDescription": "Zyvro shares your screen only with websites you approve.",
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "Web addresses",
                "CFBundleURLSchemes": ["http", "https"],
                "CFBundleTypeRole": "Viewer",
            }
        ],
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "HTML document",
                "CFBundleTypeExtensions": ["html", "htm"],
                "CFBundleTypeRole": "Viewer",
            }
        ],
    },
)
