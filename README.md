# Python Web Browser

Scratch prototype of a tabbed desktop browser built with Python, PySide6, and
QtWebEngine.

Native distribution instructions are available in `BUILD_WINDOWS.md` and
`BUILD_MACOS.md`. The macOS build keeps Qt WebEngine and packages Zyvro as a
native `.app` inside a drag-to-Applications DMG.

## Run the scratch build

Double-click `run_browser.cmd`, or run it from PowerShell:

```powershell
.\run_browser.cmd
```

To run the Python file directly:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python scratch_browser.py
```

The scratch build includes tabs, popup-to-tab handling, an address/search bar,
back/forward/reload/stop/home controls, favicons, and common keyboard shortcuts.

## Sessions and tabs

Choose **Settings → General → Continue where you left off** to reopen normal
windows and tabs after restart. Session snapshots are updated locally while
browsing, so an unclean shutdown also restores the last snapshot. Incognito
windows are never included. Press `Ctrl+Shift+T` or use **Recently Closed** in
the main menu to restore closed tabs. The same menu also lists recently closed
windows and up to ten timestamped previous sessions. Sessions captured after an
unclean shutdown are labeled as crash-recovery snapshots.

Right-click a tab to pin it or add it to a named color group. Pinned and grouped
state is included in normal session restoration.

## Standalone web apps

Open the site, then choose **Main menu → Browser Tools → Install This Site as
App**. The browser creates Desktop and Start Menu shortcuts. Launching either
shortcut opens the site in a standalone process with compact back, forward,
reload, and app-menu controls, its own persistent site profile, and a distinct
Windows taskbar identity. The app menu can open the current address in a full
browser window or uninstall the app and remove its shortcuts.

Installed apps continue to use the browser's navigation, permission, download,
malware, and certificate protections. They are excluded from ordinary browser
session recovery and Incognito history.

## Private browsing

Press `Ctrl+Shift+N`, or choose **New Incognito Window** near the top of the
main menu. Each Incognito window uses a separate off-the-record Qt WebEngine
profile with memory-only cookies and cache. Private tabs, popups, DevTools, and
download records stay in that session; intentionally downloaded files and
bookmarks remain on disk.

Run the offscreen privacy regression checks with:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\.venv\Scripts\python -m unittest -v test_incognito.py
```

## Developer Tools

Press `F12` or `Ctrl+Shift+I` to open the real Chromium Developer Tools docked
on the browser's right side. Each tab owns its own DevTools panel, which is
cleaned up when its tab closes. Press either shortcut again to close the panel.

The application imports Qt through QtPy. The included requirements use PySide6.
To use PyQt6 instead, install `QtPy`, `PyQt6`, and `PyQt6-WebEngine`, then select
that binding with `QT_API=pyqt6` before starting the browser.

## Privacy controls

The Privacy settings include WebRTC local-IP protection and global tracker
protection. WebRTC protection defaults to on. Tracker protection remains off
until the first-run prompt is accepted or it is enabled manually in Settings.

The address-bar shield shows blocked requests for the current site, provides a
per-site switch, and opens the local Privacy Report. Camera and microphone
capture displays a live toolbar indicator with a **Stop & Block Access** action.
All preferences and tracker totals stay in local `QSettings`/SQLite storage;
the browser contains no account sync, telemetry, or analytics integration.

## Ad blocking

Zyvro Ad Blocker is enabled by default. It uses Brave's native `adblock-rust`
engine with bundled uAssets, EasyList, and EasyPrivacy rules for network
blocking, cosmetic filtering, and supported scriptlets. The Rust engine is
pinned separately from the filter data, so rule updates do not require a new
browser engine build. Click the
crossed-out **AD** icon beside the address bar to view the current-page count
and local totals or to allow ads on that site.

The global switch and allowed-sites editor are under **Settings → Privacy &
Security**. Normal totals are stored locally in SQLite. Incognito uses the same
blocking rules but keeps its counters in memory and discards them with the
private profile.

Third-party licenses and a SHA-256 manifest for bundled rule data are under
`assets/adblock/`; the native bridge source is under `rust/zyvro-adblock/`.

## VPN / Private Network

Click the small network-lock icon beside the AD control to use the operating
system route, a local Tor SOCKS5 service, or a configured SOCKS5/HTTP proxy.
Tor first checks `127.0.0.1:9050` and `127.0.0.1:9150` with a real SOCKS5
handshake. If no external service answers, Tor mode starts the locally bundled
Tor Expert Bundle from `runtime/tor-expert-bundle`, waits for SOCKS5 readiness,
and stops that browser-owned process on disconnect or browser exit. Custom
proxies are tested before the browser reports them as connected. Qt's
application proxy is forwarded to Chromium and is shared by normal and
Incognito profiles.

The optional kill switch blocks WebEngine network requests when a selected Tor
or custom proxy is unavailable. Proxy passwords are session-only and are never
written to `QSettings`. System VPN mode follows the operating system route and
only reports connected when a running VPN-style adapter is detected. This is
network-route support, not a replacement for Tor Browser or a VPN provider.

## Themes

Open **Settings → Appearance → Theme** to choose System, Light, Dark, Neon, or
Rainbow. Changes preview immediately; Save persists the selection through
`QSettings`, while Cancel restores the previously saved theme. System follows
Qt's current operating-system color scheme. Incognito windows retain a dark
private background while inheriting the selected theme's accent.

## Malware Scanner and Security Center

Every completed browser download up to 500 MB is hashed with SHA-256 and
checked locally without executing the file. Local analysis checks extensions,
double extensions, file signatures, PE masquerading, suspicious names, HTTPS
source information, and known local indicators. Files over 500 MB are recorded
as **NOT SCANNED** and are never described as safe.

Open **Settings → Malware Scanner** for the full Security Center dashboard,
scan history, VirusTotal engine totals, activity charts, and quarantine. The
browser will not open a downloaded file from its Downloads page until the scan
has completed with no known threats detected.

The scanner first checks Windows Security Center for active antivirus software,
including Microsoft Defender and registered third-party products. When active
antivirus protection is detected, the browser uses the local route and does not
read or call the VirusTotal API key. Defender supports a direct on-demand file
scan; other registered products provide their active real-time protection.
Name normalization includes 360 Total Security/Qihoo, Huorong, Tencent PC
Manager, Kingsoft, Rising, Jiangmin, Baidu Antivirus, Micropoint, Antiy,
Sangfor, Topsec, and Venustech in both common English and Chinese registrations.

When no active antivirus is detected, VirusTotal uses the official v3 API.
Existing SHA-256 reports are checked first; unknown files are submitted
automatically when the configured account tier permits it. Files over 32 MB use
VirusTotal's one-time large-upload URL, and uploads are streamed from disk.
Public-tier calls are queued and throttled to respect the documented quota.
**Downloaded files up to 500 MB may be automatically submitted to VirusTotal
for malware analysis when no active local antivirus is detected.** Browser
cookies, profiles, credentials, settings databases, and internal browser files
are never submitted.

Configure the API key in Security Center. It is stored through the operating
system credential vault, not `QSettings`; alternatively set `VT_API_KEY` in the
environment. Without a validated key, the dashboard reports **PROTECTION
UNAVAILABLE** instead of claiming the download is safe.

## Main menu and browser data

The top-right menu provides persistent history, real Qt WebEngine downloads,
editable bookmarks, webpage printing and saving, native find-in-page, per-tab
zoom, settings, and clean exit handling. Browser preferences use `QSettings`;
history, bookmarks, and download records use a local SQLite database in the
platform application-data folder.
