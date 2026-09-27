# Building Zyvro for Windows

Zyvro is the existing QtPy + PySide6/QtWebEngine browser in this repository. The Windows build packages `scratch_browser.py`; it does not replace the browser with a separate shell.

## Outputs

The complete build creates:

```text
dist/
    Zyvro.exe
    ZyvroSetup.exe
```

`Zyvro.exe` is a one-file, windowed x64 build that can run without Setup. `ZyvroSetup.exe` is a per-user Inno Setup installer for Windows 10 and 11.

## Requirements

- Windows 10 or Windows 11, 64-bit
- The repository's `.venv` with Python 3.14
- Internet access the first time dependencies are installed
- Inno Setup 6 (`winget install --id JRSoftware.InnoSetup -e --scope user`)

Python runtime dependencies are in `requirements.txt`. The build script also installs compatible releases of PyInstaller and Pillow into the project environment.

## Build command

From PowerShell:

```powershell
.\build-windows.ps1
```

Or double-click/run:

```bat
build-windows.bat
```

After dependencies are already installed, a faster repeat build is:

```powershell
.\build-windows.ps1 -SkipDependencyInstall
```

Use `-SkipInstaller` only when testing the standalone application build.

## Version

The product version is read from `APP_VERSION` in `browser_maintenance.py`. Change that value before building. The build generates matching Windows version resources for `Zyvro.exe`; keep `MyAppVersion` in `installer/Zyvro.iss` synchronized for direct manual Inno builds. The normal PowerShell build passes the current application version to the installer compiler.

## Artwork and icon

Source branding remains in:

- `assets/zyvro-logo.png`
- `assets/zyvro-background.png`

`packaging/generate_assets.py` creates the multi-resolution icon and DPI-scaled installer sidebar/header artwork under `installer/assets/`. Replace the two source PNG files and rebuild to regenerate all media. The script center-crops the background without stretching it and keeps the original files unchanged.

## Packaging details

`Zyvro.spec` includes:

- all QtPy/PySide6/QtWebEngine runtime components
- the browser's Python modules
- `assets/`
- the pinned native `adblock-rust` bridge and licenses in `runtime/adblock/`
- EasyList/uAssets filter data and Brave scriptlet resources in `assets/adblock/`
- the bundled local Tor Expert Bundle in `runtime/`

The executable is built with the GUI bootloader, so it does not open a console window. Windows metadata identifies it as Zyvro Browser, versioned from the project.

## Installer behavior

The default installation directory is:

```text
%LOCALAPPDATA%\Programs\Zyvro
```

This is a per-user installation and normally does not require administrator rights. Selected-by-default installer tasks create:

- `Desktop\Zyvro.lnk`
- `Start Menu\Programs\Zyvro\Zyvro.lnk`
- `Start Menu\Programs\Zyvro\Uninstall Zyvro.lnk`

Inno Setup registers Zyvro in Windows Installed Apps and supplies the working uninstaller. It also registers Zyvro as an available handler for HTTP, HTTPS, `.html`, and `.htm`; it never changes the current default browser automatically.

## Uninstall and user data

Uninstall removes program files, installer-created shortcuts, capability registration, and installation metadata. Browser profiles and user data are stored outside the installation folder and are intentionally preserved, including bookmarks, settings, history, cache, and saved site permissions.

Use Zyvro's explicit **Nuke Data** command only when the user wants browser-owned data deleted.

## Manual installer build

After `dist/Zyvro.exe` exists, compile:

```powershell
& "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe" .\installer\Zyvro.iss
```

The result is `dist/ZyvroSetup.exe`.
