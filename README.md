# 🌐 Zyvro Browser

**A fast, privacy-focused desktop browser built with Python, Qt, and a little stubbornness.**

Zyvro is designed for people who want a modern browser without unnecessary accounts, telemetry, or clutter. It includes built-in privacy tools, Rust-powered ad blocking, private browsing, malware scanning, standalone web apps, session recovery, themes, and more.

> Zyvro is an independent browser project. It is not affiliated with Google Chrome, Mozilla Firefox, Microsoft Edge, Brave, or Tor Browser.

---

## 📥 Download Zyvro

Download the newest version from the official releases page:

### [Download the latest Zyvro release](https://github.com/SyntaxError-TwT/Zyvro-Browser/releases/latest)

Never download Zyvro from an unofficial mirror or a random download website.

### Windows

Download:

- **`ZyvroSetup.exe`** — recommended installer
- **`Zyvro.exe`** — portable standalone version, when available

### macOS

Download:

- **`Zyvro-*-macOS-arm64.dmg`** — for Apple Silicon Macs with an M1, M2, M3, M4, or newer processor

The current macOS package is intended for Apple Silicon. Intel Macs require a separate `x86_64` or universal build.

---

## 🪟 Installing on Windows

1. Open the [latest release](https://github.com/SyntaxError-TwT/Zyvro-Browser/releases/latest).
2. Download **`ZyvroSetup.exe`**.
3. Close any currently running Zyvro windows.
4. Double-click the installer.
5. Follow the installation instructions.
6. Open Zyvro from the Start menu or desktop shortcut.

Windows may display a SmartScreen message for new or independently distributed applications. Verify that the installer came from this official GitHub repository before continuing.

### Portable Windows version

If a portable `Zyvro.exe` is included in the release:

1. Download `Zyvro.exe`.
2. Place it in a permanent folder.
3. Double-click it to launch Zyvro.

The portable EXE does not need a traditional installation, although browser settings and profiles are still stored in the appropriate Windows application-data directories.

---

## 🍎 Installing on macOS

1. Open the [latest release](https://github.com/SyntaxError-TwT/Zyvro-Browser/releases/latest).
2. Download the Apple Silicon `.dmg` file.
3. Open the downloaded DMG.
4. Drag **Zyvro** into the **Applications** folder.
5. Open the Applications folder and launch Zyvro.

A normally distributed macOS build should use Developer ID signing and Apple notarization. Test or development builds may require additional approval in **System Settings → Privacy & Security**.

---

## ✨ What makes Zyvro different?

Zyvro is not trying to fill every pixel with buttons. It focuses on useful browser features that give you more control over your browsing.

### 🦀 Rust-powered ad blocking

Zyvro uses Brave’s native `adblock-rust` engine with bundled filter lists such as:

- EasyList
- EasyPrivacy
- uAssets filters
- Cosmetic filtering rules
- Supported scriptlet rules

The blocker handles network requests and page elements instead of merely hiding an empty advertisement box.

Click the crossed-out **AD** button beside the address bar to:

- View blocked-request counts
- Allow advertisements on the current website
- Re-enable blocking
- Review locally stored blocking totals

Incognito windows use the same filter rules while keeping their private counters in memory.

---

## 🛡️ Privacy controls

Zyvro includes privacy tools without requiring a browser account.

Features include:

- Tracker protection
- Third-party cookie controls
- WebRTC local-IP leak protection
- Per-site JavaScript controls
- Camera and microphone indicators
- Stored site-permission controls
- Local privacy reports
- Incognito browsing
- HTTPS-focused navigation
- Certificate-error protection

Site permissions can include:

- Camera
- Microphone
- Location
- Notifications
- Clipboard access
- Popups
- Autoplay
- JavaScript

Permanent choices are stored locally on your computer.

---

## 🕶️ Incognito browsing

Open an Incognito window with:

```text
Ctrl + Shift + N
