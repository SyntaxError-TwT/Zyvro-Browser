"""Scratch prototype for a tabbed QtWebEngine browser."""

from __future__ import annotations

import importlib.util
import base64
import hashlib
import html
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

def _ensure_project_python() -> None:
    """Relaunch with the project venv when system Python runs this file."""
    has_qtpy = importlib.util.find_spec("qtpy") is not None
    has_qt6_binding = any(
        importlib.util.find_spec(binding) is not None
        for binding in ("PySide6", "PyQt6")
    )
    if has_qtpy and has_qt6_binding:
        return

    venv = Path(__file__).resolve().parent / ".venv"
    project_python = (
        venv / "Scripts" / "python.exe"
        if os.name == "nt"
        else venv / "bin" / "python"
    )
    if not project_python.exists():
        raise SystemExit(
            "QtPy and a Qt6 binding are not installed. Create the project "
            "environment and run "
            "'python -m pip install -r requirements.txt'."
        )

    result = subprocess.run(
        [str(project_python), str(Path(__file__).resolve()), *sys.argv[1:]],
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    _ensure_project_python()
    if "--nuke-helper" in sys.argv:
        # A frozen build cannot launch nuke_browser_data.py through
        # sys.executable because sys.executable is Zyvro.exe. Re-enter the
        # packaged executable in a small helper mode instead.
        from nuke_browser_data import main as nuke_helper_main

        helper_arguments = [
            argument for argument in sys.argv[1:] if argument != "--nuke-helper"
        ]
        raise SystemExit(nuke_helper_main(helper_arguments))


# Import the selected binding once before QtPy. This gives frozen builds a
# deterministic binding choice and lets the bootloader establish PySide's DLL
# search paths before QtPy probes alternatives.
import PySide6.QtCore  # noqa: F401

from qtpy.QtCore import QCryptographicHash, QPoint, QSize, QStandardPaths, Qt, QTimer, QUrl, QUrlQuery, Signal
from qtpy.QtGui import (
    QAction,
    QColor,
    QIcon,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QRegion,
    QShortcut,
)
from qtpy.QtNetwork import QSslCertificate, QSslSocket
from qtpy.QtPrintSupport import QPrintDialog, QPrinter
from qtpy.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QMenu,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QToolBar,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qtpy.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEnginePermission,
    QWebEngineScript,
    QWebEngineSettings,
)
from qtpy.QtWebEngineWidgets import QWebEngineView

from browser_data import (
    BookmarkManager,
    BrowserProfileManager,
    HistoryManager,
    SettingsManager,
    app_data_directory,
)
from browser_dialogs import (
    BookmarksDialog,
    DownloadsDialog,
    FindBar,
    HistoryDialog,
    SettingsDialog,
)
from browser_menu import BrowserMenu
from browser_maintenance import APP_VERSION, UpdateChecker
from browser_tools_dialogs import (
    BackupDialog,
    TabOverviewDialog,
    TaskManagerDialog,
    UpdateDialog,
)
from theme_manager import ThemeManager
from site_privacy_manager import PERMISSION_LABELS


HOME_URL = "https://www.google.com"

PRIVATE_NEW_TAB_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Private Browsing</title>
<style>
  :root { color-scheme: dark; font-family: system-ui, "Segoe UI", sans-serif; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: radial-gradient(circle at top, #30264a, #16141d 55%); color: #f4efff; }
  main { width: min(620px, calc(100% - 56px)); padding: 48px;
         background: rgba(31, 27, 42, .86); border: 1px solid #514567;
         border-radius: 20px; box-shadow: 0 20px 70px rgba(0,0,0,.35); }
  .mark { width: 58px; height: 58px; display: grid; place-items: center;
          border-radius: 16px; background: #47385f; color: #d9c8ff; font-size: 29px; }
  h1 { margin: 22px 0 10px; font-size: 30px; font-weight: 650; }
  p { color: #c5bdd2; line-height: 1.55; }
  ul { padding-left: 22px; color: #e4ddec; line-height: 1.8; }
  .note { margin-top: 24px; padding-top: 18px; border-top: 1px solid #4b4258; font-size: 14px; }
</style></head><body><main>
  <div class="mark" aria-hidden="true">◌</div>
  <h1>You're browsing privately</h1>
  <p>After this private window closes, Python Browser will not keep:</p>
  <ul><li>Browsing history</li><li>Cookies from this private session</li>
      <li>Session restoration data</li></ul>
  <p><strong>Downloads and bookmarks you intentionally create may remain on this computer.</strong></p>
  <p class="note">Private browsing limits data saved locally. It does not make you anonymous to websites, your internet provider, employer, school, or network administrator.</p>
</main></body></html>"""

class BrowserTabBar(QTabBar):
    """Tab bar with a Chrome-style new-tab button after the final tab."""

    new_tab_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.new_tab_button = QToolButton(self)
        self.new_tab_button.setObjectName("newTabButton")
        self.new_tab_button.setIcon(
            QIcon(str(Path(__file__).resolve().parent / "assets" / "plus.svg"))
        )
        self.new_tab_button.setIconSize(QSize(14, 14))
        self.new_tab_button.setToolTip("New tab (Ctrl+T)")
        self.new_tab_button.setFixedSize(30, 29)
        self.new_tab_button.clicked.connect(self.new_tab_requested.emit)
        self.setExpanding(False)

    def sizeHint(self):
        size = super().sizeHint()
        size.setWidth(size.width() + self.new_tab_button.width() + 10)
        return size

    def minimumSizeHint(self):
        return self.sizeHint()

    def tabSizeHint(self, index: int) -> QSize:
        size = super().tabSizeHint(index)
        data = self.tabData(index)
        if isinstance(data, dict) and data.get("pinned"):
            size.setWidth(48)
        return size

    def tabInserted(self, index: int) -> None:
        super().tabInserted(index)
        self._position_new_tab_button()
        self.updateGeometry()

    def tabRemoved(self, index: int) -> None:
        super().tabRemoved(index)
        self._position_new_tab_button()
        self.updateGeometry()

    def tabLayoutChange(self) -> None:
        super().tabLayoutChange()
        self._position_new_tab_button()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._position_new_tab_button()

    def _position_new_tab_button(self) -> None:
        if self.count():
            last_tab = self.tabRect(self.count() - 1)
            x = last_tab.right() + 7
        else:
            x = 7

        x = min(x, max(0, self.width() - self.new_tab_button.width()))
        y = max(0, (self.height() - self.new_tab_button.height()) // 2 + 2)
        self.new_tab_button.move(x, y)
        self.new_tab_button.raise_()


class AddressBar(QLineEdit):
    """Firefox-like address field that selects the whole location on click."""

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        # Queue selectAll so Qt's normal mouse handling cannot immediately move
        # the caret afterward. This is the same result as pressing Ctrl+A.
        QTimer.singleShot(0, self.selectAll)


class BrowserWebPage(QWebEnginePage):
    """Page with capture tracking injected before site scripts run."""

    media_capture_changed = Signal(bool, bool, bool)
    cosmetic_ads_hidden = Signal(int)
    scripted_permission_requested = Signal(str, str)
    malicious_navigation_requested = Signal(QUrl)
    https_upgrade_requested = Signal(QUrl, QUrl)

    def __init__(self, profile: QWebEngineProfile, parent=None) -> None:
        super().__init__(profile, parent)
        token = getattr(profile, "_python_browser_media_token", None)
        if token is None:
            token = secrets.token_hex(16)
            profile._python_browser_media_token = token
            self._media_token = token
            script = QWebEngineScript()
            script.setName("Python Browser media capture indicator")
            script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
            script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
            script.setRunsOnSubFrames(False)
            script.setSourceCode(self._media_tracking_script())
            # Profile-level scripts are installed before any page navigation,
            # including popup pages created later on the same profile.
            profile.scripts().insert(script)
            profile._python_browser_media_script = script
        self._media_token = token

    def _media_tracking_script(self) -> str:
        prefix = f"__PY_BROWSER_MEDIA__{self._media_token}:"
        return f"""
(() => {{
  if (window.__pythonBrowserCaptureInstalled) return;
  window.__pythonBrowserCaptureInstalled = true;
  const activeTracks = new Set();
  const screenTracks = new Set();
  const originalLog = console.info.bind(console);
  const report = () => {{
    let audio = false, video = false, screen = false;
    for (const track of activeTracks) {{
      if (track.readyState === 'ended') continue;
      if (track.kind === 'audio') audio = true;
      if (track.kind === 'video') video = true;
      if (screenTracks.has(track)) screen = true;
    }}
    originalLog('{prefix}' + JSON.stringify({{audio, video, screen}}));
  }};
  const watch = (stream, isScreen=false) => {{
    for (const track of stream.getTracks()) {{
      activeTracks.add(track);
      if (isScreen) screenTracks.add(track);
      track.addEventListener('ended', () => {{ activeTracks.delete(track); screenTracks.delete(track); report(); }}, {{once:true}});
    }}
    report();
    return stream;
  }};
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {{
    const nativeGetUserMedia = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = (...args) => nativeGetUserMedia(...args).then(watch);
  }}
  if (navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {{
    const nativeGetDisplayMedia = navigator.mediaDevices.getDisplayMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getDisplayMedia = (...args) => nativeGetDisplayMedia(...args).then(stream => watch(stream, true));
  }}
  window.__pythonBrowserStopCapture = () => {{
    for (const track of activeTracks) track.stop();
    activeTracks.clear();
    screenTracks.clear();
    report();
  }};
}})();
"""

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame) -> bool:
        if is_main_frame:
            adblock = getattr(
                self.profile(), "_python_browser_adblock_manager", None
            )
            if adblock is not None:
                adblock.prepare_page(self, url)
            security = getattr(
                self.profile(), "_python_browser_navigation_security", None
            )
            host = url.host().lower().strip(".")
            if security is not None and security.is_malicious(host):
                self.malicious_navigation_requested.emit(QUrl(url))
                return False
            if security is not None and security.should_upgrade(url):
                secure = QUrl(url)
                secure.setScheme("https")
                if secure.port() == 80:
                    secure.setPort(-1)
                self.https_upgrade_requested.emit(QUrl(url), secure)
                return False
            manager = getattr(
                self.profile(), "_python_browser_site_permission_manager", None
            )
            site = url.host().lower().strip(".")
            blocked = bool(
                manager is not None
                and site
                and manager.decision(site, "javascript") == "block"
            )
            # This per-page WebEngine attribute is set before Chromium commits
            # the main-frame navigation, so blocked domains never start their
            # page JavaScript during the new load.
            self.settings().setAttribute(
                QWebEngineSettings.WebAttribute.JavascriptEnabled, not blocked
            )
        return super().acceptNavigationRequest(
            url, navigation_type, is_main_frame
        )

    def javaScriptConsoleMessage(self, level, message, line_number, source_id) -> None:
        prefix = f"__PY_BROWSER_MEDIA__{self._media_token}:"
        if message.startswith(prefix):
            try:
                state = json.loads(message[len(prefix):])
                self.media_capture_changed.emit(
                    bool(state.get("audio")), bool(state.get("video")),
                    bool(state.get("screen")),
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            return
        adblock_token = getattr(self.profile(), "_python_browser_adblock_token", "")
        adblock_prefix = f"__PY_BROWSER_ADBLOCK__{adblock_token}:"
        if adblock_token and message.startswith(adblock_prefix):
            try:
                amount = int(message[len(adblock_prefix):])
                if amount > 0:
                    self.cosmetic_ads_hidden.emit(amount)
            except ValueError:
                pass
            return
        permission_token = getattr(
            self.profile(), "_python_browser_permission_token", ""
        )
        permission_prefix = f"__PY_BROWSER_PERMISSION__{permission_token}:"
        if permission_token and message.startswith(permission_prefix):
            try:
                request = json.loads(message[len(permission_prefix):])
                permission_name = str(request.get("permission", ""))
                if permission_name in {"popups", "autoplay"}:
                    self.scripted_permission_requested.emit(
                        permission_name, str(request.get("url", ""))
                    )
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
            return
        super().javaScriptConsoleMessage(level, message, line_number, source_id)


class BrowserView(QWebEngineView):
    """Web view that sends popup requests to the main window as new tabs."""

    new_tab_requested = Signal(QWebEngineView)
    media_state_changed = Signal()
    adblock_state_changed = Signal()
    cosmetic_ads_hidden = Signal(int)
    permission_requested = Signal(object)
    scripted_permission_requested = Signal(str, str)
    certificate_error = Signal(object)
    desktop_media_requested = Signal(object)
    filesystem_access_requested = Signal(object)
    malicious_navigation_requested = Signal(QUrl)
    https_upgrade_requested = Signal(QUrl, QUrl)
    renderer_terminated = Signal(object, int)
    full_screen_requested = Signal(object)

    def __init__(
        self,
        profile: QWebEngineProfile,
        *,
        private: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.web_profile = profile
        self.is_private = private
        self.media_audio_active = False
        self.media_video_active = False
        self.screen_capture_active = False
        self.media_permissions: list[QWebEnginePermission] = []
        self.ads_blocked_current = 0
        # Every tab gets an explicit page. This prevents Qt from silently
        # falling back to its default profile when a private tab or popup opens.
        page = BrowserWebPage(profile, self)
        self.setPage(page)
        # Chromium only exposes the HTML Fullscreen API when this attribute is
        # enabled and the application accepts each fullScreenRequested event.
        # Without both pieces YouTube reports that fullscreen is unavailable.
        self.settings().setAttribute(
            QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True
        )
        page.media_capture_changed.connect(self._media_capture_changed)
        page.cosmetic_ads_hidden.connect(self.cosmetic_ads_hidden.emit)
        page.permissionRequested.connect(self.permission_requested.emit)
        page.scripted_permission_requested.connect(
            self.scripted_permission_requested.emit
        )
        page.certificateError.connect(self.certificate_error.emit)
        if hasattr(page, "desktopMediaRequested"):
            page.desktopMediaRequested.connect(self.desktop_media_requested.emit)
        if hasattr(page, "fileSystemAccessRequested"):
            page.fileSystemAccessRequested.connect(
                self.filesystem_access_requested.emit
            )
        page.malicious_navigation_requested.connect(
            self.malicious_navigation_requested.emit
        )
        page.https_upgrade_requested.connect(self.https_upgrade_requested.emit)
        page.renderProcessTerminated.connect(self.renderer_terminated.emit)
        page.fullScreenRequested.connect(self.full_screen_requested.emit)
        self.loadStarted.connect(self._reset_media_state)
        self.loadStarted.connect(self._reset_adblock_count)
        self.loadFinished.connect(self._apply_adblock_cosmetics)

    def _apply_adblock_cosmetics(self, success: bool) -> None:
        if not success:
            return
        manager = getattr(
            self.web_profile, "_python_browser_adblock_manager", None
        )
        if manager is not None:
            manager.apply_generic_cosmetics(self.page(), self.url())

    def createWindow(self, window_type: QWebEnginePage.WebWindowType) -> QWebEngineView:
        # The browser's existing popup behavior is to open a tab. Reusing this
        # view's profile guarantees target=_blank/window.open stays private.
        view = BrowserView(self.web_profile, private=self.is_private)
        self.new_tab_requested.emit(view)
        return view

    def _media_capture_changed(
        self, audio: bool, video: bool, screen: bool = False
    ) -> None:
        self.media_audio_active = audio
        self.media_video_active = video
        self.screen_capture_active = screen
        self.media_state_changed.emit()

    def _reset_media_state(self) -> None:
        if (
            self.media_audio_active or self.media_video_active
            or self.screen_capture_active
        ):
            self.media_audio_active = False
            self.media_video_active = False
            self.screen_capture_active = False
            self.media_state_changed.emit()

    def _reset_adblock_count(self) -> None:
        if self.ads_blocked_current:
            self.ads_blocked_current = 0
            self.adblock_state_changed.emit()

    def add_blocked_ads(self, amount: int) -> None:
        if amount > 0:
            self.ads_blocked_current += int(amount)
            self.adblock_state_changed.emit()

    def stop_and_block_media(self) -> None:
        self.page().runJavaScript(
            "if (window.__pythonBrowserStopCapture) window.__pythonBrowserStopCapture();"
        )
        for permission in self.media_permissions:
            try:
                permission.deny()
            except RuntimeError:
                pass
        self.media_permissions.clear()
        self._reset_media_state()


class DeveloperToolsDock(QDockWidget):
    """Docked Chromium DevTools panel for one inspected web page."""

    closed = Signal()

    def __init__(self, inspected_page: QWebEnginePage, parent: QMainWindow) -> None:
        super().__init__("Developer Tools", parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
        self.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)

        self._inspected_page = inspected_page
        self.devtools_view = QWebEngineView(self)
        self.devtools_view.setPage(
            QWebEnginePage(inspected_page.profile(), self.devtools_view)
        )
        self.setWidget(self.devtools_view)

        # This is the real Qt WebEngine/Chromium DevTools relationship. The
        # DevTools page automatically loads Qt WebEngine's internal inspector
        # and inspects only the QWebEnginePage belonging to this browser tab.
        inspected_page.setDevToolsPage(self.devtools_view.page())

    def focus_panel(self) -> None:
        self.show()
        self.raise_()
        self.devtools_view.setFocus()

    def closeEvent(self, event) -> None:
        # Explicitly sever the inspected-page relationship before Qt deletes
        # the DevTools view. This prevents stale page pointers during tab close.
        try:
            self._inspected_page.setDevToolsPage(None)
        except RuntimeError:
            # The inspected page may already be undergoing Qt-side deletion.
            pass
        self.closed.emit()
        super().closeEvent(event)


class ReaderBar(QWidget):
    """Small set of controls shown only while Chromium displays reader HTML."""

    exit_requested = Signal()

    def __init__(self, browser: BrowserView, parent=None) -> None:
        super().__init__(parent)
        self.browser = browser
        self.setObjectName("readerBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 5, 10, 5)
        row.setSpacing(7)
        label = QLabel("Reader View")
        label.setStyleSheet("font-weight: 650;")
        smaller = QPushButton("A−")
        larger = QPushButton("A+")
        narrow = QPushButton("Narrow")
        wide = QPushButton("Wide")
        tighter = QPushButton("Spacing −")
        looser = QPushButton("Spacing +")
        background = QComboBox()
        background.addItems(["Paper", "Sepia", "Dark"])
        done = QPushButton("Exit Reader View")
        smaller.clicked.connect(lambda: self._adjust("font", -1))
        larger.clicked.connect(lambda: self._adjust("font", 1))
        narrow.clicked.connect(lambda: self._adjust("width", -1))
        wide.clicked.connect(lambda: self._adjust("width", 1))
        tighter.clicked.connect(lambda: self._adjust("spacing", -1))
        looser.clicked.connect(lambda: self._adjust("spacing", 1))
        background.currentTextChanged.connect(self._set_background)
        done.clicked.connect(self.exit_requested)
        row.addWidget(label)
        row.addStretch()
        row.addWidget(smaller)
        row.addWidget(larger)
        row.addWidget(narrow)
        row.addWidget(wide)
        row.addWidget(tighter)
        row.addWidget(looser)
        row.addWidget(background)
        row.addWidget(done)
        self.hide()

    def _adjust(self, kind: str, direction: int) -> None:
        if kind == "font":
            script = (
                "const r=document.documentElement;const n=Math.max(14,Math.min(30,"
                f"parseInt(getComputedStyle(r).getPropertyValue('--reader-font'))||19)+({direction});"
                "r.style.setProperty('--reader-font',n+'px');"
            )
        elif kind == "width":
            script = (
                "const r=document.documentElement;const n=Math.max(520,Math.min(1100,"
                f"parseInt(getComputedStyle(r).getPropertyValue('--reader-width'))||760)+({direction}*60);"
                "r.style.setProperty('--reader-width',n+'px');"
            )
        else:
            script = (
                "const r=document.documentElement;const n=Math.max(1.35,Math.min(2.1,"
                f"(parseFloat(getComputedStyle(r).getPropertyValue('--reader-spacing'))||1.72)+({direction}*.08)));"
                "r.style.setProperty('--reader-spacing',n);"
            )
        self.browser.page().runJavaScript(script)

    def _set_background(self, name: str) -> None:
        values = {
            "Paper": ("#f7f5ef", "#262521"),
            "Sepia": ("#efe3c6", "#382f25"),
            "Dark": ("#202329", "#e8e9ec"),
        }
        background, text = values.get(name, values["Paper"])
        self.browser.page().runJavaScript(
            "document.documentElement.style.setProperty('--reader-bg',"
            f"{json.dumps(background)});"
            "document.documentElement.style.setProperty('--reader-text',"
            f"{json.dumps(text)});"
        )


class SecurityInterstitial(QFrame):
    """Full-page browser warning with an explicit session-only override."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("securityInterstitial")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(70, 55, 70, 55)
        outer.addStretch()
        card = QFrame()
        card.setObjectName("securityInterstitialCard")
        card.setMaximumWidth(720)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(14)
        self.heading = QLabel()
        self.heading.setObjectName("securityInterstitialHeading")
        self.heading.setWordWrap(True)
        self.message = QLabel()
        self.message.setObjectName("securityInterstitialMessage")
        self.message.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setObjectName("securityInterstitialDetail")
        self.detail.setWordWrap(True)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.back = QPushButton("Go Back")
        self.proceed = QPushButton("Still Proceed")
        self.proceed.setObjectName("securityProceedButton")
        # Seed both signals so the first reconfiguration can disconnect them
        # without PySide emitting a noisy "no connections" runtime warning.
        self.back.clicked.connect(lambda: None)
        self.proceed.clicked.connect(lambda: None)
        buttons.addWidget(self.back)
        buttons.addWidget(self.proceed)
        layout.addWidget(self.heading)
        layout.addWidget(self.message)
        layout.addWidget(self.detail)
        layout.addLayout(buttons)
        centered = QHBoxLayout()
        centered.addStretch()
        centered.addWidget(card, 1)
        centered.addStretch()
        outer.addLayout(centered)
        outer.addStretch()
        self.hide()

    def configure(
        self, title: str, message: str, detail: str, proceed_text: str,
        proceed_callback, back_callback, *, allow_proceed: bool = True,
    ) -> None:
        for button in (self.proceed, self.back):
            try:
                button.clicked.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.heading.setText(title)
        self.message.setText(message)
        self.detail.setText(detail)
        self.proceed.setText(proceed_text)
        self.proceed.setEnabled(allow_proceed)
        self.proceed.setVisible(allow_proceed)
        self.proceed.clicked.connect(proceed_callback)
        self.back.clicked.connect(back_callback)


class BrowserPage(QWidget):
    """One tab page, with the shared navigation bar above its web view."""

    def __init__(self, browser: BrowserView, devtools_host: QMainWindow) -> None:
        super().__init__()
        self.browser = browser
        self._devtools_host = devtools_host
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.layout.setSpacing(0)
        self.reader_bar = ReaderBar(browser, self)
        self.layout.addWidget(self.reader_bar)
        self.find_bar = FindBar(
            browser,
            self,
            theme_manager=devtools_host.theme_manager,
            incognito=devtools_host.incognito,
        )
        self.layout.addWidget(self.find_bar)
        self.security_interstitial = SecurityInterstitial(self)
        self.layout.addWidget(self.security_interstitial, 1)
        self.layout.addWidget(browser, 1)
        self.devtools_dock: DeveloperToolsDock | None = None
        self._devtools_requested_visible = False
        self.last_active_at = time.monotonic()
        self.sleeping = False

    def show_security_interstitial(self, **configuration) -> None:
        self.browser.hide()
        self.reader_bar.hide()
        self.find_bar.hide()
        self.security_interstitial.configure(**configuration)
        self.security_interstitial.show()

    def hide_security_interstitial(self) -> None:
        self.security_interstitial.hide()
        self.browser.show()

    def attach_browser_bars(
        self, navigation_bar: QToolBar, bookmarks_bar: QToolBar
    ) -> None:
        self.layout.insertWidget(0, navigation_bar)
        self.layout.insertWidget(1, bookmarks_bar)

    def show_find_bar(self) -> None:
        self.find_bar.show_and_focus()

    def toggle_devtools(self) -> None:
        dock = self.devtools_dock
        if dock is not None:
            if dock.isVisible():
                dock.close()
            else:
                # Reuse and focus an existing dock instead of creating a duplicate.
                self._devtools_requested_visible = True
                dock.focus_panel()
            return

        dock = DeveloperToolsDock(self.browser.page(), self._devtools_host)
        dock.closed.connect(
            lambda tracked_dock=dock: self._devtools_closed(tracked_dock)
        )
        self.devtools_dock = dock
        self._devtools_requested_visible = True
        self._devtools_host.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea, dock
        )
        self._devtools_host.resizeDocks(
            [dock], [500], Qt.Orientation.Horizontal
        )
        dock.focus_panel()

    def close_devtools(self) -> None:
        dock = self.devtools_dock
        if dock is not None:
            self._devtools_requested_visible = False
            self.devtools_dock = None
            dock.close()

    def set_devtools_active(self, active: bool) -> None:
        dock = self.devtools_dock
        if dock is None:
            return
        if active and self._devtools_requested_visible:
            dock.show()
            dock.raise_()
        else:
            dock.hide()

    def _devtools_closed(self, dock: DeveloperToolsDock) -> None:
        if self.devtools_dock is dock:
            self._devtools_requested_visible = False
            self.devtools_dock = None


class PrivacyReportDialog(QDialog):
    """Local tracker-blocking totals and protected-site list."""

    def __init__(self, privacy_manager, private_session: str = "", parent=None) -> None:
        super().__init__(parent)
        self.theme_manager = getattr(parent, "theme_manager", None)
        self.theme_incognito = bool(getattr(parent, "incognito", False))
        self.setWindowTitle("Privacy Report")
        self.resize(560, 430)
        if self.theme_manager is not None:
            self.theme_manager.theme_changed.connect(self.apply_theme)
            self.apply_theme()
        layout = QVBoxLayout(self)
        title = QLabel("Privacy Report")
        title.setStyleSheet("font-size:22px;font-weight:650;")
        subtitle = QLabel(
            "Private-session activity only" if private_session
            else "Tracker protection activity stored locally on this device"
        )
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        today = privacy_manager.count_today(private_session)
        total = privacy_manager.count_total(private_session)
        totals = QLabel(
            f"Trackers blocked today:  {today}\nTrackers blocked total:  {total}"
        )
        totals.setStyleSheet("font-size:15px;margin:12px 0;")
        layout.addWidget(totals)
        layout.addWidget(QLabel("Sites where tracking protection was used"))

        sites = privacy_manager.sites(private_session)
        table = QTableWidget(len(sites), 2)
        table.setHorizontalHeaderLabels(["Site", "Blocked"])
        table.horizontalHeader().setStretchLastSection(False)
        table.horizontalHeader().setSectionResizeMode(0, table.horizontalHeader().ResizeMode.Stretch)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for row, record in enumerate(sites):
            table.setItem(row, 0, QTableWidgetItem(record["site"]))
            table.setItem(row, 1, QTableWidgetItem(str(record["count"])))
        layout.addWidget(table, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def apply_theme(self, *_args) -> None:
        if self.theme_manager is not None:
            self.setStyleSheet(
                self.theme_manager.dialog_stylesheet(self.theme_incognito)
            )


class RoundedPopup(QDialog):
    """Top-level popup whose native rectangular backing is fully clipped."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:
        # Paint the popup panel ourselves: the top-level window stays
        # transparent only outside this rounded shape, while the content area
        # is always the requested solid dark color with a visible outline.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor("#15181e"), 1.2))
        painter.setBrush(QColor("#22242a"))
        path = QPainterPath()
        path.addRoundedRect(
            self.rect().adjusted(1, 1, -1, -1), 13.0, 13.0
        )
        painter.drawPath(path)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(
            self.rect().adjusted(0, 0, -1, -1), 13.0, 13.0
        )
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))


class DownloadsPopup(RoundedPopup):
    """Chrome-style recent-download panel anchored to the toolbar button."""

    def __init__(self, browser_window: "BrowserWindow") -> None:
        super().__init__(browser_window)
        self.browser_window = browser_window
        self.manager = browser_window.download_manager
        self.setFixedWidth(440)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(18, 15, 18, 15)
        self.root.setSpacing(9)

        header = QHBoxLayout()
        title = QLabel("Downloads")
        title.setObjectName("privacyPopupTitle")
        close = QPushButton("×")
        close.setObjectName("downloadPopupClose")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.close)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(close)
        self.root.addLayout(header)

        self.items = QWidget()
        self.items_layout = QVBoxLayout(self.items)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(7)
        self.root.addWidget(self.items)

        full = QPushButton("See Full Downloads")
        full.setObjectName("downloadPopupFull")
        full.clicked.connect(self._open_full)
        self.root.addWidget(full)
        self.manager.changed.connect(self.refresh)
        browser_window.theme_manager.theme_changed.connect(self.apply_theme)
        self.apply_theme()
        self.refresh()

    def apply_theme(self, *_args) -> None:
        self.setStyleSheet(
            self.browser_window.theme_manager.popup_stylesheet(
                self.browser_window.incognito
            )
            + """
QFrame#downloadPopupItem { background: #191c22; border: 1px solid #303540;
    border-radius: 9px; }
QLabel#downloadPopupFilename { font-weight: 650; }
QLabel#downloadPopupStatus { color: #adb4c1; font-size: 11px; }
QProgressBar { background: #111419; border: none; border-radius: 3px;
    min-height: 6px; max-height: 6px; text-align: center; }
QProgressBar::chunk { background: #5b8def; border-radius: 3px; }
QPushButton#downloadPopupClose { background: transparent; padding: 0; font-size: 19px; }
QPushButton#downloadPopupClose:hover { background: #343841; }
QPushButton#downloadItemAction, QPushButton#downloadItemFolder {
    padding: 5px 9px; border-radius: 6px; }
"""
        )

    def refresh(self, *_args) -> None:
        while self.items_layout.count():
            item = self.items_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        records = self.manager.records()[:4]
        if not records:
            empty = QLabel("No downloads yet")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("padding: 24px 4px; color: #adb4c1;")
            self.items_layout.addWidget(empty)
        for record in records:
            self.items_layout.addWidget(self._download_row(record))
        self.adjustSize()

    def _download_row(self, record: dict) -> QWidget:
        frame = QFrame()
        frame.setObjectName("downloadPopupItem")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(11, 9, 11, 9)
        layout.setSpacing(5)
        filename = QLabel(str(record["filename"]))
        filename.setObjectName("downloadPopupFilename")
        filename.setToolTip(str(record["destination"]))
        layout.addWidget(filename)

        total = int(record.get("total", -1))
        received = int(record.get("received", 0))
        progress = QProgressBar()
        progress.setTextVisible(False)
        if total > 0:
            progress.setRange(0, total)
            progress.setValue(min(received, total))
        else:
            progress.setRange(0, 0)
        layout.addWidget(progress)

        status_row = QHBoxLayout()
        status = QLabel(str(record.get("status") or "Downloading"))
        status.setObjectName("downloadPopupStatus")
        status.setWordWrap(True)
        status_row.addWidget(status, 1)
        active = str(record.get("status")) in {
            "Requested", "InProgress", "Downloading"
        }
        scan = (
            self.manager.malware_scanner.data.latest_for_path(
                str(record["destination"])
            )
            if self.manager.malware_scanner is not None else None
        )
        blocked = bool(
            scan and scan.get("verdict") in {"malware", "suspicious"}
            and not scan.get("quarantined") and not scan.get("override_kept")
        )
        action = QPushButton("Cancel" if active else "Open")
        action.setObjectName("downloadItemAction")
        if active:
            action.clicked.connect(
                lambda _checked=False, item_id=int(record["id"]):
                self.manager.cancel(item_id)
            )
        else:
            action.clicked.connect(
                lambda _checked=False, path=str(record["destination"]):
                self.manager.open_file(path)
            )
        folder = QPushButton("Folder")
        folder.setObjectName("downloadItemFolder")
        folder.clicked.connect(
            lambda _checked=False, path=str(record["destination"]):
            self.manager.open_folder(path)
        )
        if not blocked:
            status_row.addWidget(action)
            status_row.addWidget(folder)
        layout.addLayout(status_row)
        if blocked:
            decision_row = QHBoxLayout()
            warning = QLabel("Download Blocked")
            warning.setStyleSheet("color:#ff7d86;font-weight:700;")
            quarantine = QPushButton("Quarantine")
            allow = QPushButton("Allow")
            quarantine.clicked.connect(
                lambda _checked=False, current=dict(scan):
                self._quarantine_scan(current)
            )
            allow.clicked.connect(
                lambda _checked=False, scan_id=int(scan["id"]):
                self._allow_scan(scan_id)
            )
            decision_row.addWidget(warning, 1)
            decision_row.addWidget(quarantine)
            decision_row.addWidget(allow)
            layout.addLayout(decision_row)
        return frame

    def _quarantine_scan(self, scan: dict) -> None:
        detections = scan.get("vt_detections") or []
        detection = (
            str(detections[0].get("name")) if detections
            else "Suspicious or malicious download"
        )
        try:
            self.manager.malware_scanner.quarantine.quarantine(scan, detection)
            self.manager.malware_scanner.data.add_activity(
                int(scan["id"]), "user_quarantine", "Blocked download quarantined",
                str(scan["filename"]),
            )
        except OSError as error:
            QMessageBox.critical(self, "Quarantine failed", str(error))
        self.manager.changed.emit()
        self.refresh()

    def _allow_scan(self, scan_id: int) -> None:
        answer = QMessageBox.warning(
            self, "Allow blocked download?",
            "This file was blocked by security checks. Allow it only if you trust "
            "the source and understand that it may harm this computer.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.manager.malware_scanner.data.update_scan(
            int(scan_id), override_kept=1
        )
        self.manager.malware_scanner.data.add_activity(
            int(scan_id), "override", "Blocked download allowed by user",
            "User accepted the security warning",
        )
        self.manager.changed.emit()
        self.refresh()

    def _open_full(self) -> None:
        self.close()
        QTimer.singleShot(0, self.browser_window.open_full_downloads)


class SitePermissionPopup(RoundedPopup):
    """Address-bar permission prompt with temporary and saved choices."""

    def __init__(
        self, browser_window: "BrowserWindow", site: str,
        permission_label: str, callback,
    ) -> None:
        super().__init__(browser_window)
        self.setFixedWidth(390)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(11)
        title = QLabel(f"{site} wants to use your {permission_label.lower()}")
        title.setObjectName("privacyPopupTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        for text, choice in (
            ("Allow Once", "once"),
            ("Always Allow", "allow"),
            ("Block", "block"),
        ):
            button = QPushButton(text)
            button.clicked.connect(
                lambda _checked=False, selected=choice: self._finish(
                    callback, selected
                )
            )
            row.addWidget(button)
        layout.addWidget(actions)
        self.setStyleSheet(
            browser_window.theme_manager.popup_stylesheet(browser_window.incognito)
        )

    def _finish(self, callback, choice: str) -> None:
        callback(choice)
        self.close()


class SiteInfoPopup(RoundedPopup):
    """Current-site connection, permission, and cookie controls."""

    def __init__(self, browser_window: "BrowserWindow") -> None:
        super().__init__(browser_window)
        self.browser_window = browser_window
        self.setFixedWidth(390)
        browser = browser_window.current_browser()
        url = browser.url()
        self.site = url.host().lower().strip(".")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        title = QLabel(self.site or "Page Information")
        title.setObjectName("privacyPopupTitle")
        layout.addWidget(title)
        secure = url.scheme().lower() == "https"
        connection = QLabel(
            "Secure connection" if secure else
            "Connection is not secure" if url.scheme().lower() == "http" else
            "Internal browser page"
        )
        layout.addWidget(connection)

        if self.site:
            layout.addWidget(QLabel("Permissions"))
            for permission, label in PERMISSION_LABELS.items():
                if permission == "javascript":
                    continue
                line = QWidget()
                row = QHBoxLayout(line)
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(QLabel(label), 1)
                choice = QComboBox()
                choice.addItem("Ask", "ask")
                choice.addItem("Allow", "allow")
                choice.addItem("Block", "block")
                current = browser_window.site_permission_manager.decision(
                    self.site, permission
                )
                choice.setCurrentIndex(max(0, choice.findData(current)))
                choice.currentIndexChanged.connect(
                    lambda _index, p=permission, widget=choice:
                    browser_window.site_permission_manager.set_decision(
                        self.site, p, str(widget.currentData())
                    )
                )
                row.addWidget(choice)
                layout.addWidget(line)

            layout.addWidget(QLabel("Site Settings"))
            javascript_line = QWidget()
            javascript_row = QHBoxLayout(javascript_line)
            javascript_row.setContentsMargins(0, 0, 0, 0)
            javascript_row.addWidget(QLabel("JavaScript"), 1)
            self.javascript_choice = QComboBox()
            self.javascript_choice.addItem("Allow", "allow")
            self.javascript_choice.addItem("Block", "block")
            javascript_blocked = (
                browser_window.site_permission_manager.decision(
                    self.site, "javascript"
                ) == "block"
            )
            self.javascript_choice.setCurrentIndex(1 if javascript_blocked else 0)
            self.javascript_choice.currentIndexChanged.connect(
                self._javascript_changed
            )
            javascript_row.addWidget(self.javascript_choice)
            layout.addWidget(javascript_line)
            self.javascript_reload_note = QLabel(
                "Reload this page to apply the change"
            )
            self.javascript_reload_note.setWordWrap(True)
            self.javascript_reload_note.hide()
            layout.addWidget(self.javascript_reload_note)

            exception = browser_window.cookie_privacy_manager.site_exception(self.site)
            self.cookie_exception = QPushButton(
                "Block Third-Party Cookies for This Site"
                if exception else "Allow Third-Party Cookies for This Site"
            )
            self.cookie_exception.clicked.connect(self._toggle_cookie_exception)
            layout.addWidget(self.cookie_exception)

        certificate = QPushButton("Certificate Details")
        certificate.setEnabled(secure)
        certificate.clicked.connect(self._certificate_details)
        layout.addWidget(certificate)
        self.setStyleSheet(
            browser_window.theme_manager.popup_stylesheet(browser_window.incognito)
        )

    def _certificate_details(self) -> None:
        self.close()
        self.browser_window.show_certificate_details()

    def _toggle_cookie_exception(self) -> None:
        manager = self.browser_window.cookie_privacy_manager
        manager.set_site_exception(
            self.site, not manager.site_exception(self.site)
        )
        self.cookie_exception.setText(
            "Block Third-Party Cookies for This Site"
            if manager.site_exception(self.site)
            else "Allow Third-Party Cookies for This Site"
        )

    def _javascript_changed(self) -> None:
        blocked = self.javascript_choice.currentData() == "block"
        self.browser_window.site_permission_manager.set_decision(
            self.site, "javascript", "block" if blocked else "ask"
        )
        if blocked and not self.browser_window.incognito:
            self.browser_window.privacy_timeline_manager.record(
                "permissions", "javascript_blocked",
                "JavaScript blocked for this site", self.site,
            )
        self.javascript_reload_note.show()
        self.adjustSize()


class PrivacyPopup(RoundedPopup):
    """Compact toolbar popup for current-page tracking protection."""

    def __init__(self, browser_window: "BrowserWindow") -> None:
        super().__init__(browser_window)
        self.browser_window = browser_window
        self.setFixedWidth(330)
        self.setObjectName("privacyPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        title = QLabel("Tracking Protection")
        title.setObjectName("privacyPopupTitle")
        layout.addWidget(title)
        host = browser_window.current_browser().url().host() or "This page"
        count = browser_window.profile_manager.privacy_manager.count_for_site(
            host, browser_window.privacy_session
        )
        self.count_label = QLabel(f"{count} trackers blocked on this page")
        layout.addWidget(self.count_label)

        self.host = host
        self.global_button = QPushButton()
        self.global_button.clicked.connect(self._toggle_global)
        self.site_button = QPushButton()
        self.site_button.clicked.connect(self._toggle_site)

        # Primary actions sit below the module status and immediately above
        # the secondary Privacy Report button.
        layout.addWidget(self.global_button)
        layout.addWidget(self.site_button)
        report = QPushButton("View Privacy Report")
        report.clicked.connect(self._open_report)
        layout.addWidget(report)
        self.theme_manager = browser_window.theme_manager
        self.theme_manager.theme_changed.connect(self.apply_theme)
        self.refresh()
        self.apply_theme()

    def refresh(self) -> None:
        enabled = self.browser_window.profile_manager.tracker_protection_enabled()
        self.global_button.setText(
            "Turn Off Tracking Protection" if enabled
            else "Turn On Tracking Protection"
        )
        site_enabled = self.browser_window.profile_manager.site_protection_enabled(
            self.host
        )
        self.site_button.setText(
            "Turn Off Tracking Protection for This Site" if site_enabled
            else "Turn On Tracking Protection for This Site"
        )
        self.site_button.setEnabled(enabled and bool(self.host))

    def _toggle_global(self) -> None:
        enabled = self.browser_window.profile_manager.tracker_protection_enabled()
        self.browser_window.set_tracker_protection_enabled(not enabled)
        self.refresh()

    def _toggle_site(self) -> None:
        enabled = self.browser_window.profile_manager.site_protection_enabled(
            self.host
        )
        self.browser_window.set_current_site_protection(not enabled)
        self.refresh()

    def _open_report(self) -> None:
        self.close()
        self.browser_window.open_privacy_report()

    def apply_theme(self, *_args) -> None:
        self.setStyleSheet(
            self.theme_manager.popup_stylesheet(self.browser_window.incognito)
        )


class AdBlockPopup(RoundedPopup):
    """Compact ad-block controls and local statistics for the active page."""

    def __init__(self, browser_window: "BrowserWindow") -> None:
        super().__init__(browser_window)
        self.browser_window = browser_window
        self.setFixedWidth(340)
        self.setObjectName("adBlockPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)
        engine = browser_window.adblock_manager.rust_engine
        title = QLabel(
            "Zyvro Ad Blocker" if engine.available else "Ad Blocking"
        )
        title.setObjectName("privacyPopupTitle")
        layout.addWidget(title)
        if engine.available:
            version = QLabel(f"Protection rules {engine.version}")
            version.setObjectName("popupSecondaryText")
            layout.addWidget(version)

        browser = browser_window.current_browser()
        host = browser.url().host() or "This page"
        layout.addWidget(
            QLabel(f"{browser.ads_blocked_current} ads blocked on this page")
        )
        self.host = host
        self.global_button = QPushButton()
        self.global_button.clicked.connect(self._toggle_global)
        self.site_button = QPushButton()
        self.site_button.clicked.connect(self._toggle_site)

        today = browser_window.adblock_manager.count_today(
            browser_window.privacy_session
        )
        total = browser_window.adblock_manager.count_total(
            browser_window.privacy_session
        )
        totals = QLabel(
            f"Ads blocked today:  {today:,}\nAds blocked total:  {total:,}"
        )
        totals.setStyleSheet("margin-top:5px;")
        layout.addWidget(totals)
        # Primary on/off actions stay at the bottom of the module content,
        # directly above the secondary Settings button.
        layout.addWidget(self.global_button)
        layout.addWidget(self.site_button)
        settings = QPushButton("Ad Blocking Settings")
        settings.clicked.connect(self._open_settings)
        layout.addWidget(settings)

        self.theme_manager = browser_window.theme_manager
        self.theme_manager.theme_changed.connect(self.apply_theme)
        self.refresh()
        self.apply_theme()

    def refresh(self) -> None:
        enabled = self.browser_window.adblock_manager.enabled()
        self.global_button.setText(
            "Turn Off Ad Blocking" if enabled else "Turn On Ad Blocking"
        )
        site_enabled = self.browser_window.adblock_manager.site_enabled(self.host)
        self.site_button.setText(
            "Allow Ads on This Site" if site_enabled else "Block Ads on This Site"
        )
        self.site_button.setEnabled(enabled and bool(self.host))

    def _toggle_global(self) -> None:
        self.browser_window.set_adblocking_enabled(
            not self.browser_window.adblock_manager.enabled()
        )
        self.refresh()

    def _toggle_site(self) -> None:
        enabled = self.browser_window.adblock_manager.site_enabled(self.host)
        self.browser_window.set_current_site_adblocking(not enabled)
        self.refresh()

    def _open_settings(self) -> None:
        self.close()
        self.browser_window.open_settings()

    def apply_theme(self, *_args) -> None:
        self.setStyleSheet(
            self.theme_manager.popup_stylesheet(self.browser_window.incognito)
        )


class CookieProtectionPopup(RoundedPopup):
    """Compact global and current-site third-party cookie controls."""

    def __init__(self, browser_window: "BrowserWindow") -> None:
        super().__init__(browser_window)
        self.browser_window = browser_window
        self.manager = browser_window.cookie_privacy_manager
        self.setFixedWidth(350)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        title = QLabel("Third-Party Cookies")
        title.setObjectName("privacyPopupTitle")
        layout.addWidget(title)
        self.status = QLabel()
        layout.addWidget(self.status)

        self.global_button = QPushButton()
        self.global_button.clicked.connect(self._toggle_global)

        note = QLabel(
            "First-party cookies, including normal website login cookies, "
            "remain available."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.site = browser_window.current_browser().url().host().lower().strip(".")
        self.site_button = QPushButton()
        self.site_button.clicked.connect(self._toggle_site_exception)
        # Keep both state-changing actions below the explanatory copy and
        # immediately above the secondary Settings button.
        layout.addWidget(self.global_button)
        layout.addWidget(self.site_button)

        settings = QPushButton("Cookie Settings")
        settings.clicked.connect(self._open_settings)
        layout.addWidget(settings)

        self.theme_manager = browser_window.theme_manager
        self.theme_manager.theme_changed.connect(self.apply_theme)
        self.refresh()
        self.apply_theme()

    def refresh(self) -> None:
        blocked = self.manager.enabled()
        self.status.setText(
            "Protection is ON" if blocked else "Protection is OFF"
        )
        self.global_button.setText(
            "Turn Off Third-Party Cookie Blocking" if blocked
            else "Turn On Third-Party Cookie Blocking"
        )
        exception = self.manager.site_exception(self.site)
        self.site_button.setText(
            "Block Third-Party Cookies for This Site" if exception
            else "Allow Third-Party Cookies for This Site"
        )
        self.site_button.setEnabled(bool(self.site) and blocked)

    def _toggle_global(self) -> None:
        enabled = not self.manager.enabled()
        self.browser_window.settings_manager.save_values({
            "privacy/block_third_party_cookies": enabled,
            "privacy/third_party_cookie_prompted": True,
        })
        self.manager.refresh()
        self.manager.changed.emit()
        self.refresh()

    def _toggle_site_exception(self) -> None:
        if self.site:
            self.manager.set_site_exception(
                self.site, not self.manager.site_exception(self.site)
            )
            self.refresh()

    def _open_settings(self) -> None:
        self.close()
        self.browser_window.open_settings()

    def apply_theme(self, *_args) -> None:
        self.setStyleSheet(
            self.theme_manager.popup_stylesheet(self.browser_window.incognito)
        )


class VpnPopup(RoundedPopup):
    """Compact real proxy/Tor controls backed by the shared route manager."""

    def __init__(self, browser_window: "BrowserWindow") -> None:
        super().__init__(browser_window)
        self.browser_window = browser_window
        self.manager = browser_window.vpn_manager
        self.setFixedWidth(340)
        self.setObjectName("vpnPopup")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(9)

        title = QLabel("Private Network")
        title.setObjectName("privacyPopupTitle")
        layout.addWidget(title)

        mode_widget = QWidget()
        mode_layout = QHBoxLayout(mode_widget)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(6)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        # Kept as a hidden compatibility model for callers that previously
        # inspected the old four-mode combo; the visible UI is Custom/Tor.
        self.mode_combo = QComboBox(self)
        for text, data in (
            ("Off", "off"), ("System VPN", "system"),
            ("Tor", "tor"), ("Custom Proxy", "custom"),
        ):
            self.mode_combo.addItem(text, data)
        self.mode_combo.hide()
        self.custom_button = QPushButton("Custom")
        self.custom_button.setObjectName("vpnCustomMode")
        self.tor_button = QPushButton("Tor")
        self.tor_button.setObjectName("vpnTorMode")
        for button, name in (
            (self.custom_button, "custom"), (self.tor_button, "tor")
        ):
            button.setCheckable(True)
            button.setProperty("vpnMode", True)
            self.mode_group.addButton(button)
            button.clicked.connect(lambda _checked=False, mode=name: self._select_mode(mode))
            mode_layout.addWidget(button)
        layout.addWidget(mode_widget)
        self.selected_mode_label = QLabel()
        self.selected_mode_label.setObjectName("vpnSelectedMode")
        layout.addWidget(self.selected_mode_label)

        self.status_label = QLabel()
        self.status_label.setObjectName("vpnStatus")
        layout.addWidget(self.status_label)
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

        self.location_label = QLabel("Location")
        layout.addWidget(self.location_label)
        self.location = QComboBox()
        self.location.addItems(self.manager.COUNTRIES)
        self.location.setCurrentText(self.manager.selected_country())
        self.location.currentTextChanged.connect(self._load_custom_profile)
        layout.addWidget(self.location)

        # Custom proxy details live in the popup so a first-time user can
        # configure and make a real connection without a settings detour.
        self.custom_fields = QWidget()
        custom_form = QFormLayout(self.custom_fields)
        custom_form.setContentsMargins(0, 0, 0, 0)
        custom_form.setHorizontalSpacing(8)
        custom_form.setVerticalSpacing(6)
        self.proxy_type = QComboBox()
        self.proxy_type.addItem("SOCKS5", "socks5")
        self.proxy_type.addItem("HTTP", "http")
        self.proxy_host = QLineEdit()
        self.proxy_host.setPlaceholderText("Proxy server host")
        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(1080)
        self.proxy_username = QLineEdit()
        self.proxy_username.setPlaceholderText("Optional")
        self.proxy_password = QLineEdit()
        self.proxy_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.proxy_password.setPlaceholderText("Session only; never saved")
        custom_form.addRow("Type", self.proxy_type)
        custom_form.addRow("Host", self.proxy_host)
        custom_form.addRow("Port", self.proxy_port)
        custom_form.addRow("Username", self.proxy_username)
        custom_form.addRow("Password", self.proxy_password)
        layout.addWidget(self.custom_fields)

        protection = QWidget()
        protection_layout = QHBoxLayout(protection)
        protection_layout.setContentsMargins(0, 0, 0, 0)
        protection_layout.addWidget(QLabel("IP Protection"))
        self.protection_state = QLabel("Inactive")
        protection_layout.addStretch()
        protection_layout.addWidget(self.protection_state)
        layout.addWidget(protection)

        self.connection_button = QPushButton()
        self.connection_button.clicked.connect(self._toggle_connection)
        layout.addWidget(self.connection_button)

        self.tor_setup_button = QPushButton("Set Up Tor")
        self.tor_setup_button.clicked.connect(self._open_tor_setup)
        layout.addWidget(self.tor_setup_button)

        secondary = QWidget()
        secondary_layout = QHBoxLayout(secondary)
        secondary_layout.setContentsMargins(0, 0, 0, 0)
        secondary_layout.setSpacing(8)
        ip_check = QPushButton("Check My IP")
        ip_check.clicked.connect(self._check_ip)
        settings = QPushButton("Network Settings")
        settings.clicked.connect(self._open_settings)
        secondary_layout.addWidget(ip_check)
        secondary_layout.addWidget(settings)
        layout.addWidget(secondary)

        self.manager.state_changed.connect(self.refresh)
        self.theme_manager = browser_window.theme_manager
        self.theme_manager.theme_changed.connect(self.apply_theme)
        self._load_custom_profile(self.location.currentText())
        self.apply_theme()

    def refresh(self, *_args) -> None:
        manager_mode = self.manager.mode()
        selected = "tor" if self.tor_button.isChecked() else "custom"
        if not self.custom_button.isChecked() and not self.tor_button.isChecked():
            selected = "tor" if manager_mode == "tor" else "custom"
            (self.tor_button if selected == "tor" else self.custom_button).setChecked(True)
        self.custom_button.setText(
            "✓  Custom" if selected == "custom" else "Custom"
        )
        self.tor_button.setText("✓  Tor" if selected == "tor" else "Tor")
        selected_name = "Tor" if selected == "tor" else "Custom"
        self.selected_mode_label.setText(f"Selected mode: {selected_name}")
        state = self.manager.state()
        connected_selected = state == "connected" and selected == manager_mode
        error_selected = state == "error" and selected == manager_mode
        state_text = "Connected" if connected_selected else (
            "Connection Failed" if error_selected else "Disconnected"
        )
        self.status_label.setText(f"●  {state_text}")
        color = (
            "#a77bf3" if connected_selected and selected == "tor"
            else "#62a5ff" if connected_selected
            else "#e59a59" if error_selected
            else "#c9cdd5"
        )
        self.status_label.setStyleSheet(
            f"font-weight:650;color:{color};"
        )
        if selected == "custom":
            profile = self.manager.profile_for_country(self.location.currentText())
            detail = self.manager.detail() if (
                manager_mode == "custom" and state in {"connected", "error"}
            ) else ("Ready to connect" if profile else "Server not configured")
        else:
            detail = self.manager.detail() if manager_mode == "tor" else (
                "Uses local Tor at 127.0.0.1:9050 or 9150"
            )
        self.detail_label.setText(detail)
        self.location_label.setVisible(selected == "custom")
        self.location.setVisible(selected == "custom")
        self.custom_fields.setVisible(selected == "custom")
        self.protection_state.setText("Active" if connected_selected else "Inactive")
        if connected_selected:
            button_text = "Turn Off Private Network"
        elif selected == manager_mode and state == "connecting":
            button_text = "Starting Tor…"
        else:
            button_text = "Turn On Private Network"
        self.connection_button.setText(button_text)
        self.connection_button.setEnabled(
            not (selected == manager_mode and state == "connecting")
        )
        self.tor_setup_button.setVisible(
            selected == "tor" and not connected_selected
            and not self.manager.has_local_tor()
        )

    def _select_mode(self, mode: str) -> None:
        (self.tor_button if mode == "tor" else self.custom_button).setChecked(True)
        self.refresh()

    def _load_custom_profile(self, country: str) -> None:
        profile = self.manager.profile_for_country(country) or {}
        proxy_type = str(profile.get("type", "socks5"))
        type_index = self.proxy_type.findData(proxy_type)
        self.proxy_type.setCurrentIndex(max(0, type_index))
        self.proxy_host.setText(str(profile.get("host", "")))
        self.proxy_port.setValue(int(profile.get("port", 1080)))
        self.proxy_username.setText(str(profile.get("username", "")))
        self.proxy_password.clear()
        self.refresh()

    def _toggle_connection(self) -> None:
        selected = "tor" if self.tor_button.isChecked() else "custom"
        if (
            self.manager.state() == "connected"
            and selected == self.manager.mode()
        ):
            self.manager.disconnect()
        elif selected == "custom":
            host = self.proxy_host.text().strip()
            if not host:
                self.detail_label.setText(
                    "Enter a real proxy host and port before connecting."
                )
                self.proxy_host.setFocus()
                return
            self.manager.save_server_profile(
                self.location.currentText(),
                str(self.proxy_type.currentData()),
                host,
                self.proxy_port.value(),
                self.proxy_username.text(),
            )
            self.manager.connect_country(
                self.location.currentText(), self.proxy_password.text()
            )
        else:
            self.manager.connect_mode("tor")
        self.refresh()

    def _check_ip(self) -> None:
        self.close()
        self.browser_window.current_browser().setUrl(
            QUrl("https://ipinfo.io/what-is-my-ip")
        )

    def _open_settings(self) -> None:
        window = self.browser_window
        self.close()
        # Queue the modal dialog until the native Qt.Popup mouse event has
        # finished; this prevents Windows from immediately swallowing it.
        QTimer.singleShot(
            0, lambda window=window: window.open_settings("Network Settings")
        )

    def _open_tor_setup(self) -> None:
        self.close()
        self.browser_window.current_browser().setUrl(
            QUrl("https://www.torproject.org/download/tor/")
        )

    def apply_theme(self, *_args) -> None:
        palette = self.theme_manager.palette(self.browser_window.incognito)
        self.setStyleSheet(
            self.theme_manager.popup_stylesheet(self.browser_window.incognito)
            + f"""
QPushButton#vpnCustomMode, QPushButton#vpnTorMode {{
    background: #15181e; color: #f1f3f7; border: 1px solid #3c4350;
    font-weight: 600; padding: 8px 12px;
}}
QPushButton#vpnCustomMode:hover, QPushButton#vpnTorMode:hover {{
    background: #2b3039; border-color: {palette.accent};
}}
QPushButton#vpnCustomMode:checked {{
    background: {palette.accent}; color: white;
    border: 2px solid #b9d4ff;
}}
QPushButton#vpnTorMode:checked {{
    background: #7251b5; color: white;
    border: 2px solid #cbb7f4;
}}
QLabel#vpnSelectedMode {{ font-weight: 650; color: #d8dce5; }}
"""
        )


class BrowserWindow(QMainWindow):
    _shared_profile_manager: BrowserProfileManager | None = None
    _open_windows: set["BrowserWindow"] = set()
    _closing_all = False
    _nuking_data = False
    _automatic_update_started = False
    automatic_update_finished = Signal(object, str)

    def __init__(
        self,
        incognito: bool = False,
        profile_manager: BrowserProfileManager | None = None,
        restore_state: dict | None = None,
        web_app: dict | None = None,
        session_managed: bool = True,
    ) -> None:
        super().__init__()
        icon_name = "zyvro.ico" if os.name == "nt" else "zyvro-logo.png"
        self.setWindowIcon(
            QIcon(str(Path(__file__).resolve().parent / "assets" / icon_name))
        )
        self.web_app = dict(web_app or {})
        self.web_app_mode = bool(self.web_app)
        self.incognito = bool(incognito and not self.web_app_mode)
        self.session_managed = bool(
            session_managed and not self.web_app_mode and not self.incognito
        )
        if profile_manager is None:
            if BrowserWindow._shared_profile_manager is None:
                BrowserWindow._shared_profile_manager = BrowserProfileManager(
                    QApplication.instance()
                )
            profile_manager = BrowserWindow._shared_profile_manager
        self.profile_manager = profile_manager
        self.settings_manager = SettingsManager()
        if not hasattr(self.profile_manager, "theme_manager"):
            self.profile_manager.theme_manager = ThemeManager(
                str(self.settings_manager.value("appearance/theme")),
                self.profile_manager,
            )
        self.theme_manager: ThemeManager = self.profile_manager.theme_manager
        self.theme_manager.theme_changed.connect(self.apply_theme)
        self.history_manager = HistoryManager()
        self.bookmark_manager = BookmarkManager()
        self.web_profile = (
            self.profile_manager.create_private_profile()
            if self.incognito
            else self.profile_manager.normal_profile
        )
        self.download_manager = self.profile_manager.download_manager_for(
            self.web_profile,
            self.settings_manager,
            self,
            private=self.incognito,
        )
        self.privacy_session = str(id(self.web_profile)) if self.incognito else ""
        self.adblock_manager = self.profile_manager.adblock_manager
        self.vpn_manager = self.profile_manager.vpn_proxy_manager
        self.site_permission_manager = self.profile_manager.site_permission_manager
        self.cookie_privacy_manager = self.profile_manager.cookie_privacy_manager
        self.privacy_timeline_manager = self.profile_manager.privacy_timeline_manager
        self.malware_scanner = self.profile_manager.malware_scanner
        self.security_data = self.profile_manager.security_data
        self.session_manager = self.profile_manager.session_manager
        self.web_app_manager = self.profile_manager.web_app_manager
        self.favicon_directory = app_data_directory() / "favicons"
        self.favicon_directory.mkdir(parents=True, exist_ok=True)
        self._tab_groups: dict[str, str] = {}
        self._session_save_pending = False
        self._privacy_popup: PrivacyPopup | None = None
        self._adblock_popup: AdBlockPopup | None = None
        self._cookie_popup: CookieProtectionPopup | None = None
        self._vpn_popup: VpnPopup | None = None
        self._downloads_popup: DownloadsPopup | None = None
        self._site_info_popup: SiteInfoPopup | None = None
        self._permission_popup: SitePermissionPopup | None = None
        self._active_printers: list[QPrinter] = []
        self._tool_dialogs: list[QDialog] = []
        self._fullscreen_browser: BrowserView | None = None
        self._fullscreen_geometry = None
        self._fullscreen_was_maximized = False
        self._fullscreen_navigation_visible = True
        self._fullscreen_bookmarks_visible = False
        BrowserWindow._open_windows.add(self)

        self.setWindowTitle(self._window_title(str(self.web_app.get("name") or "New Tab")))
        self.resize(1200, 800)
        self.apply_theme()

        self.tabs = QTabWidget()
        tab_bar = BrowserTabBar()
        tab_bar.new_tab_requested.connect(self.add_new_tab)
        tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tab_bar.customContextMenuRequested.connect(self._show_tab_context_menu)
        tab_bar.tabMoved.connect(lambda *_args: self._schedule_session_save())
        self.tabs.setTabBar(tab_bar)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.setTabsClosable(False)
        self.tabs.currentChanged.connect(self._current_tab_changed)
        self.setCentralWidget(self.tabs)

        self._create_toolbar()
        self._create_bookmarks_bar()
        self._create_browser_menu()
        self._create_shortcuts()
        self.bookmark_manager.changed.connect(self._bookmarks_changed)
        self.profile_manager.privacy_manager.changed.connect(
            self._privacy_stats_changed
        )
        self.profile_manager.privacy_configuration_changed.connect(
            self._update_privacy_shield
        )
        self.adblock_manager.blocked.connect(self._ad_blocked)
        self.adblock_manager.changed.connect(self._update_adblock_button)
        self.cookie_privacy_manager.changed.connect(self._update_cookie_button)
        self.vpn_manager.state_changed.connect(self._update_vpn_button)
        self.malware_scanner.threat_detected.connect(self._security_threat_detected)
        self.download_manager.changed.connect(self._update_download_button)
        self._sleep_timer = QTimer(self)
        self._sleep_timer.setInterval(60_000)
        self._sleep_timer.timeout.connect(self._sleep_inactive_tabs)
        self._sleep_timer.start()
        if self.web_app_mode:
            self.add_tab(
                QUrl.fromUserInput(str(self.web_app.get("url") or HOME_URL)),
                str(self.web_app.get("name") or "Web App"),
            )
        elif self.incognito:
            self.add_new_tab()
        elif restore_state and restore_state.get("tabs"):
            self._tab_groups = dict(restore_state.get("groups") or {})
            for tab_state in restore_state["tabs"]:
                restored_url = str(tab_state.get("url") or HOME_URL)
                if restored_url == "zyvro:newtab":
                    browser = self.add_tab(
                        QUrl("about:blank"), "New Tab", tab_state=tab_state
                    )
                    self._load_zyvro_new_tab(browser)
                else:
                    self.add_tab(
                        QUrl.fromUserInput(restored_url),
                        str(tab_state.get("title") or "Restored Tab"),
                        tab_state=tab_state,
                    )
            self.tabs.setCurrentIndex(
                max(0, min(int(restore_state.get("current", 0)), self.tabs.count() - 1))
            )
            geometry = restore_state.get("geometry") or []
            if len(geometry) == 4:
                self.setGeometry(*(int(value) for value in geometry))
        else:
            if self.settings_manager.value("general/startup") == "new_tab":
                self.add_new_tab()
            else:
                self.add_tab(self.home_url(), "Home")
        if self.web_app_mode:
            self._apply_web_app_mode()
        self.apply_theme()
        self._schedule_session_save()
        self.automatic_update_finished.connect(
            self._automatic_update_check_finished
        )
        if (
            not self.incognito
            and not self.web_app_mode
            and not BrowserWindow._automatic_update_started
        ):
            BrowserWindow._automatic_update_started = True
            self._automatic_update_timer = QTimer(self)
            self._automatic_update_timer.setInterval(60 * 60 * 1000)
            self._automatic_update_timer.timeout.connect(
                self._maybe_check_for_updates
            )
            self._automatic_update_timer.start()
            QTimer.singleShot(3_000, self._maybe_check_for_updates)

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Navigation")
        toolbar.setObjectName("navigationBar")
        toolbar.setMovable(False)
        toolbar.setFixedHeight(42)
        toolbar.setIconSize(QSize(21, 21))
        self.navigation_bar = toolbar
        self._toolbar_actions: dict[str, QAction] = {}

        back = QAction(self._themed_icon("arrow-left.svg"), "Back", self)
        self._toolbar_actions["arrow-left.svg"] = back
        back.triggered.connect(lambda: self.current_browser().back())
        toolbar.addAction(back)

        forward = QAction(self._themed_icon("arrow-right.svg"), "Forward", self)
        self._toolbar_actions["arrow-right.svg"] = forward
        forward.triggered.connect(lambda: self.current_browser().forward())
        toolbar.addAction(forward)

        reload_page = QAction(self._themed_icon("reload.svg"), "Reload", self)
        self._toolbar_actions["reload.svg"] = reload_page
        reload_page.triggered.connect(lambda: self.current_browser().reload())
        toolbar.addAction(reload_page)

        stop = QAction(self._themed_icon("close.svg"), "Stop", self)
        self._toolbar_actions["close.svg"] = stop
        stop.triggered.connect(lambda: self.current_browser().stop())
        toolbar.addAction(stop)

        home = QAction(self._themed_icon("home.svg"), "Home", self)
        self._toolbar_actions["home.svg"] = home
        home.triggered.connect(lambda: self.current_browser().setUrl(self.home_url()))
        toolbar.addAction(home)

        self.address_bar = AddressBar()
        self.address_bar.setClearButtonEnabled(True)
        self.address_bar.setPlaceholderText("Search or enter an address")
        self.address_bar.returnPressed.connect(self.navigate_to_address)
        self.security_action = QAction(self)
        self.security_action.setToolTip("Page information")
        self.security_action.triggered.connect(self.show_site_info)
        self.address_bar.addAction(
            self.security_action, QLineEdit.ActionPosition.LeadingPosition
        )
        self.privacy_action = QAction(self)
        self.privacy_action.setToolTip("Tracking protection")
        self.privacy_action.triggered.connect(self.show_privacy_popup)
        # Leading actions are laid out in insertion order, placing the shield
        # immediately to the right of the lock/site-information icon.
        self.address_bar.addAction(
            self.privacy_action, QLineEdit.ActionPosition.LeadingPosition
        )
        toolbar.addWidget(self.address_bar)

        self.adblock_button = QToolButton()
        self.adblock_button.setObjectName("adBlockButton")
        self.adblock_button.setIconSize(QSize(20, 20))
        self.adblock_button.clicked.connect(self.show_adblock_popup)
        toolbar.addWidget(self.adblock_button)

        self.cookie_button = QToolButton()
        self.cookie_button.setObjectName("cookieButton")
        self.cookie_button.setIconSize(QSize(18, 18))
        self.cookie_button.clicked.connect(self.show_cookie_popup)
        toolbar.addWidget(self.cookie_button)

        self.vpn_button = QToolButton()
        self.vpn_button.setObjectName("vpnButton")
        self.vpn_button.setIconSize(QSize(18, 18))
        self.vpn_button.clicked.connect(self.show_vpn_popup)
        toolbar.addWidget(self.vpn_button)

        self.media_indicator = QToolButton()
        self.media_indicator.setObjectName("mediaIndicator")
        self.media_indicator.setIconSize(QSize(21, 21))
        self.media_indicator.setToolTip("Camera and microphone activity")
        self.media_indicator.clicked.connect(self.show_media_activity)
        self.media_indicator.hide()
        toolbar.addWidget(self.media_indicator)

        self.download_button = QToolButton()
        self.download_button.setObjectName("downloadButton")
        self.download_button.setIcon(self._themed_icon("download.svg"))
        self.download_button.setIconSize(QSize(20, 20))
        self.download_button.setToolTip("Downloads (Ctrl+J)")
        self.download_button.clicked.connect(self.open_downloads)
        self.download_action = toolbar.addWidget(self.download_button)
        self.download_action.setVisible(True)
        self._update_download_button()

        if self.incognito:
            indicator = QWidget()
            indicator.setObjectName("privateIndicator")
            indicator_layout = QHBoxLayout(indicator)
            indicator_layout.setContentsMargins(8, 3, 9, 3)
            indicator_layout.setSpacing(5)
            indicator_icon = QLabel()
            indicator_icon.setPixmap(self._icon("private.svg").pixmap(18, 18))
            indicator_text = QLabel("Incognito")
            indicator_text.setObjectName("privateIndicatorText")
            indicator_layout.addWidget(indicator_icon)
            indicator_layout.addWidget(indicator_text)
            toolbar.addWidget(indicator)

        self.bookmark_button = QToolButton()
        self.bookmark_button.setToolTip("Bookmark this page (Ctrl+D)")
        self.bookmark_button.setIconSize(QSize(21, 21))
        self.bookmark_button.clicked.connect(self.toggle_current_bookmark)
        toolbar.addWidget(self.bookmark_button)

        self.menu_button = QToolButton()
        self.menu_button.setObjectName("mainMenuButton")
        self.menu_button.setToolTip("Main menu")
        self.menu_button.setIcon(self._icon("more.svg"))
        self.menu_button.setIconSize(QSize(21, 21))
        self.menu_button.clicked.connect(self.show_browser_menu)
        toolbar.addWidget(self.menu_button)

    def _create_bookmarks_bar(self) -> None:
        self.bookmarks_bar = QToolBar("Bookmarks")
        self.bookmarks_bar.setObjectName("bookmarksBar")
        self.bookmarks_bar.setMovable(False)
        self.bookmarks_bar.setFixedHeight(30)
        self.bookmarks_bar.setIconSize(QSize(14, 14))
        self._refresh_bookmarks_bar()
        self.bookmarks_bar.setVisible(
            bool(self.settings_manager.value("appearance/show_bookmarks_bar"))
        )

    def _update_download_button(self, *_args) -> None:
        if not hasattr(self, "download_button"):
            return
        active = len(self.download_manager.active_requests())
        self.download_button.setProperty("active", active > 0)
        self.download_button.setToolTip(
            f"Downloads — {active} active (Ctrl+J)"
            if active else "Downloads (Ctrl+J)"
        )
        self.download_button.style().unpolish(self.download_button)
        self.download_button.style().polish(self.download_button)

    def _create_browser_menu(self) -> None:
        callbacks = {
            "new_tab": self.add_new_tab,
            "new_window": self.open_new_window,
            "new_incognito": self.open_incognito_window,
            "history": self.open_history,
            "recently_closed": self.show_recently_closed_menu,
            "downloads": self.open_downloads,
            "bookmarks": self.open_bookmarks,
            "print": self.print_current_page,
            "save": self.save_current_page,
            "find": self.show_find_bar,
            "tools": self.show_browser_tools_menu,
            "zoom_in": self.zoom_in,
            "zoom_out": self.zoom_out,
            "settings": self.open_settings,
            "nuke_data": self.nuke_data,
            "exit": self.close_all_windows,
        }
        icons = self._menu_icons()
        self.browser_menu = BrowserMenu(callbacks, icons, self)
        self.browser_menu.set_theme(
            self.theme_manager.active_theme(), self.incognito
        )

    @staticmethod
    def _icon(filename: str) -> QIcon:
        return QIcon(str(Path(__file__).resolve().parent / "assets" / filename))

    def _themed_icon(self, filename: str, color: str | None = None) -> QIcon:
        path = str(Path(__file__).resolve().parent / "assets" / filename)
        return self.theme_manager.icon(path, self.incognito, color)

    def _menu_icons(self) -> dict[str, QIcon]:
        return {
            "profile": self._themed_icon("profile.svg"),
            "new_tab": self._themed_icon("new-tab.svg"),
            "new_window": self._themed_icon("new-window.svg"),
            "private": self._themed_icon("private.svg", self.theme_manager.palette(True).accent),
            "history": self._themed_icon("history.svg"),
            "recently_closed": self._themed_icon("history.svg"),
            "download": self._themed_icon("download.svg"),
            "bookmark": self._themed_icon("star.svg"),
            "print": self._themed_icon("print.svg"),
            "save": self._themed_icon("save.svg"),
            "find": self._themed_icon("find.svg"),
            "tools": self._themed_icon("settings.svg"),
            "settings": self._themed_icon("settings.svg"),
            "nuke_data": self._themed_icon("database-off.svg", "#c83f49"),
            "exit": self._themed_icon("exit.svg"),
            "minus": self._themed_icon("minus.svg"),
            "plus": self._themed_icon("plus.svg"),
        }

    def _create_shortcuts(self) -> None:
        self._shortcuts: list[QShortcut] = []
        if self.web_app_mode:
            self._add_shortcut("F12", self.toggle_devtools)
            self._add_shortcut("Ctrl+Shift+I", self.toggle_devtools)
            self._add_shortcut("Ctrl+F", self.show_find_bar)
            self._add_shortcut("Ctrl++", self.zoom_in)
            self._add_shortcut("Ctrl+=", self.zoom_in)
            self._add_shortcut("Ctrl+-", self.zoom_out)
            self._add_shortcut("Ctrl+0", self.zoom_reset)
            return
        self._add_shortcut("Ctrl+L", self.focus_address_bar)
        self._add_shortcut("Ctrl+T", self.add_new_tab)
        self._add_shortcut("Ctrl+Shift+T", self.restore_recently_closed_tab)
        self._add_shortcut("Ctrl+N", self.open_new_window)
        self._add_shortcut("Ctrl+Shift+N", self.open_incognito_window)
        self._add_shortcut(
            "Ctrl+W", lambda: self.close_tab(self.tabs.currentIndex())
        )
        self._add_shortcut("F12", self.toggle_devtools)
        self._add_shortcut("Ctrl+Shift+I", self.toggle_devtools)
        self._add_shortcut("Ctrl+H", self.open_history)
        self._add_shortcut("Ctrl+J", self.open_downloads)
        self._add_shortcut("Ctrl+D", self.toggle_current_bookmark)
        self._add_shortcut("Ctrl+P", self.print_current_page)
        self._add_shortcut("Ctrl+S", self.save_current_page)
        self._add_shortcut("Ctrl+F", self.show_find_bar)
        self._add_shortcut("Ctrl+Shift+A", self.open_tab_overview)
        self._add_shortcut("Ctrl+Shift+Delete", self.open_clear_browsing_data)
        self._add_shortcut("Shift+Esc", self.open_task_manager)
        self._add_shortcut("F9", self.toggle_reader_mode)
        self._add_shortcut("Ctrl++", self.zoom_in)
        self._add_shortcut("Ctrl+=", self.zoom_in)
        self._add_shortcut("Ctrl+-", self.zoom_out)
        self._add_shortcut("Ctrl+0", self.zoom_reset)

    def _add_shortcut(self, sequence: str, callback) -> None:
        """Register shortcuts in a way shared by PySide6 and PyQt6."""
        shortcut = QShortcut(QKeySequence(sequence), self)
        shortcut.activated.connect(callback)
        self._shortcuts.append(shortcut)

    def toggle_devtools(self) -> None:
        page = self.tabs.currentWidget()
        if isinstance(page, BrowserPage):
            page.toggle_devtools()

    def add_new_tab(self) -> BrowserView:
        if self.web_app_mode:
            return self.current_browser()
        browser = self.add_tab(QUrl("about:blank"), "New Tab")
        self._load_zyvro_new_tab(browser)
        return browser

    def _load_zyvro_new_tab(self, browser: BrowserView) -> None:
        template = Path(__file__).resolve().parent / "assets" / "zyvro-new-tab.html"
        recent: list[dict[str, str]] = []
        if not self.incognito:
            seen: set[str] = set()
            for entry in self.history_manager.entries(limit=40):
                url = QUrl.fromUserInput(str(entry.get("url") or ""))
                normalized = url.toString().split("#", 1)[0]
                site_key = url.host().lower().removeprefix("www.")
                if not site_key or site_key in seen:
                    continue
                seen.add(site_key)
                recent.append({
                    "title": str(entry.get("title") or url.host())[:70],
                    "url": normalized,
                    "host": url.host().removeprefix("www."),
                    "icon": self._favicon_for_host(url.host()),
                })
                if len(recent) == 4:
                    break

        suggested: list[dict[str, str]] = []
        used: set[str] = set()
        for bookmark in self.bookmark_manager.entries():
            url = QUrl.fromUserInput(str(bookmark.get("url") or ""))
            if not url.host() or url.host() in used:
                continue
            used.add(url.host())
            suggested.append({
                "title": str(bookmark.get("title") or url.host())[:70],
                "url": url.toString(),
                "host": url.host().removeprefix("www."),
                "icon": self._favicon_for_host(url.host()),
            })
            if len(suggested) == 4:
                break
        defaults = (
            ("Google", "https://www.google.com/", "site-google.ico"),
            ("YouTube", "https://www.youtube.com/", "site-youtube.ico"),
            ("Wikipedia", "https://www.wikipedia.org/", "site-wikipedia.ico"),
            ("GitHub", "https://github.com/", "site-github.ico"),
        )
        for title, address, icon_name in defaults:
            url = QUrl(address)
            if url.host() in used:
                continue
            suggested.append({
                "title": title, "url": address,
                "host": url.host().removeprefix("www."),
                "icon": icon_name,
            })
            used.add(url.host())
            if len(suggested) == 4:
                break

        encode = lambda value: base64.b64encode(
            json.dumps(value, ensure_ascii=True).encode("utf-8")
        ).decode("ascii")
        url = QUrl.fromLocalFile(str(template))
        query = QUrlQuery()
        query.addQueryItem("recent", encode(recent))
        query.addQueryItem("suggested", encode(suggested))
        query.addQueryItem("private", "1" if self.incognito else "0")
        url.setQuery(query)
        browser._zyvro_new_tab_path = str(template.resolve())
        browser._is_zyvro_new_tab = True
        browser.setUrl(url)

    def _favicon_path(self, host: str) -> Path:
        digest = hashlib.sha256(host.lower().encode("utf-8")).hexdigest()
        return self.favicon_directory / f"{digest}.png"

    def _favicon_for_host(self, host: str) -> str:
        path = self._favicon_path(host)
        if path.is_file():
            return path.as_uri()
        normalized = host.lower().removeprefix("www.")
        bundled_icons = {
            "google.com": "site-google.ico",
            "youtube.com": "site-youtube.ico",
            "wikipedia.org": "site-wikipedia.ico",
            "github.com": "site-github.ico",
        }
        for domain, icon_name in bundled_icons.items():
            if normalized == domain or normalized.endswith(f".{domain}"):
                return icon_name
        return ""

    def open_new_window(self) -> None:
        window = BrowserWindow(False, self.profile_manager)
        window.show()

    def open_incognito_window(self) -> None:
        window = BrowserWindow(True, self.profile_manager)
        window.show()

    def _apply_web_app_mode(self) -> None:
        """Reduce browser chrome and give an installed site its own identity."""
        self.tabs.tabBar().hide()
        self.bookmarks_bar.hide()
        for filename in ("close.svg", "home.svg"):
            action = self._toolbar_actions.get(filename)
            if action is not None:
                action.setVisible(False)
        self.address_bar.hide()
        for widget in (
            self.adblock_button, self.cookie_button, self.vpn_button,
            self.media_indicator, self.download_button, self.bookmark_button,
        ):
            widget.hide()
        try:
            self.menu_button.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.menu_button.setToolTip("Web app menu")
        self.menu_button.clicked.connect(self.show_web_app_menu)
        self.web_app_title = QLabel(str(self.web_app.get("name") or "Web App"))
        self.web_app_title.setObjectName("webAppTitle")
        self.navigation_bar.insertWidget(
            self.navigation_bar.actions()[-1], self.web_app_title
        )
        icon_path = Path(str(self.web_app.get("icon") or ""))
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setWindowTitle(str(self.web_app.get("name") or "Web App"))

    def install_current_site_as_app(self) -> None:
        browser = self.current_browser()
        url = browser.url()
        if url.scheme().lower() not in {"http", "https"}:
            QMessageBox.warning(
                self, "Cannot Install Web App",
                "Only HTTP and HTTPS websites can be installed as web apps.",
            )
            return
        suggested = browser.title().strip() or url.host() or "Web App"
        name, accepted = QInputDialog.getText(
            self, "Install Web App", "App name", text=suggested
        )
        if not accepted or not name.strip():
            return
        try:
            record = self.web_app_manager.install(
                name.strip(), url.toString(), browser.icon()
            )
        except (OSError, RuntimeError) as error:
            QMessageBox.critical(self, "Web App Installation Failed", str(error))
            return
        message = QMessageBox(self)
        message.setWindowTitle("Web App Installed")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText(f"{record['name']} was installed.")
        message.setInformativeText(
            "Desktop and Start Menu shortcuts were created. The standalone app "
            "uses its own persistent site profile and taskbar identity."
        )
        launch = message.addButton("Launch App", QMessageBox.ButtonRole.AcceptRole)
        message.addButton(QMessageBox.StandardButton.Close)
        message.exec()
        if message.clickedButton() is launch:
            self.web_app_manager.launch(str(record["id"]))

    def show_web_app_menu(self) -> None:
        menu = QMenu(self)
        open_browser = menu.addAction("Open in Python Browser")
        open_browser.triggered.connect(self._open_web_app_url_in_browser)
        menu.addSeparator()
        app_info = menu.addAction("App Info")
        app_info.triggered.connect(
            lambda: QMessageBox.information(
                self, "Web App Info",
                f"{self.web_app.get('name', 'Web App')}\n"
                f"{self.current_browser().url().toDisplayString()}\n\n"
                "This app has separate persistent website storage and uses the "
                "browser's security and download protections."
            )
        )
        uninstall = menu.addAction("Uninstall Web App…")
        uninstall.triggered.connect(self._uninstall_current_web_app)
        menu.popup(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height())))

    def _open_web_app_url_in_browser(self) -> None:
        window = BrowserWindow(
            False, self.profile_manager, session_managed=False
        )
        window.current_browser().setUrl(self.current_browser().url())
        window.show()

    def _uninstall_current_web_app(self) -> None:
        name = str(self.web_app.get("name") or "this web app")
        answer = QMessageBox.question(
            self, "Uninstall Web App?",
            f"Remove {name} and its launch shortcuts?\n\n"
            "The app's website storage is kept so reinstalling does not silently "
            "delete site data.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.web_app_manager.uninstall(str(self.web_app.get("id") or ""))
        self.close()

    @classmethod
    def close_all_windows(cls) -> None:
        cls._closing_all = True
        normal = [
            window for window in cls._open_windows
            if window.session_managed
        ]
        if normal:
            normal[0].session_manager.save_windows(
                [window.session_state() for window in normal]
            )
        for window in list(cls._open_windows):
            window.close()

    def nuke_data(self) -> None:
        warning_box = QMessageBox(self)
        warning_box.setIcon(QMessageBox.Icon.Warning)
        warning_box.setWindowTitle("Nuke All Browser Data?")
        warning_box.setText(
            "This permanently removes:\n\n"
            "• browsing history, bookmarks, recently closed tabs, and sessions\n"
            "• cookies, site storage, cache, saved permissions, and settings\n"
            "• privacy, ad-block, download-list, and security history\n"
            "• quarantined files and the secured VirusTotal API key\n\n"
            "Files you downloaded into your Downloads folder will NOT be deleted.\n\n"
            "The browser will close when deletion begins."
        )
        backup_button = warning_box.addButton(
            "Export Backup First…", QMessageBox.ButtonRole.ActionRole
        )
        continue_button = warning_box.addButton(
            "Continue", QMessageBox.ButtonRole.DestructiveRole
        )
        warning_box.addButton(QMessageBox.StandardButton.Cancel)
        warning_box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        warning_box.exec()
        clicked = warning_box.clickedButton()
        if clicked is backup_button:
            BackupDialog(self).exec()
            return
        if clicked is not continue_button:
            return
        confirmation, accepted = QInputDialog.getText(
            self,
            "Final Confirmation",
            "Type NUKE to permanently erase all Python Browser data:",
        )
        if not accepted:
            return
        if confirmation.strip() != "NUKE":
            QMessageBox.warning(
                self, "Nuke Cancelled", "The confirmation text did not match NUKE."
            )
            return

        # Credential-vault deletion is done before settings are cleared. The
        # private key is never passed to the deletion helper.
        if (
            self.malware_scanner.vt.configured()
            and not self.malware_scanner.vt.set_api_key("")
        ):
            QMessageBox.critical(
                self,
                "Nuke Data Stopped",
                "The secured VirusTotal API key could not be removed. No other browser data was deleted.",
            )
            return

        # Remove launch shortcuts while their local app manifests still exist.
        for installed_app in self.web_app_manager.installed():
            self.web_app_manager.uninstall(str(installed_app.get("id") or ""))

        data_root = app_data_directory()
        local_root = Path(QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        ))
        targets = [
            data_root / "browser_data.sqlite3",
            data_root / "browser_data.sqlite3-shm",
            data_root / "browser_data.sqlite3-wal",
            data_root / "webengine",
            data_root / "web_apps",
            data_root / "favicons",
            data_root / "quarantine",
            local_root / "tor-data",
        ]
        if getattr(sys, "frozen", False):
            arguments = [sys.executable, "--nuke-helper", "--pid", str(os.getpid())]
        else:
            helper = Path(__file__).resolve().parent / "nuke_browser_data.py"
            arguments = [
                sys.executable, str(helper), "--pid", str(os.getpid()),
            ]
        for target in targets:
            arguments.extend(("--target", str(target)))
        creation_flags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        try:
            subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creation_flags,
            )
        except OSError as error:
            QMessageBox.critical(
                self,
                "Nuke Data Stopped",
                f"The secure deletion helper could not start:\n{error}",
            )
            return

        app = QApplication.instance()
        BrowserWindow._nuking_data = True
        for window in list(BrowserWindow._open_windows):
            window.download_manager.cancel_active()
        self.profile_manager.vpn_proxy_manager.shutdown()
        try:
            self.profile_manager.normal_profile.cookieStore().deleteAllCookies()
            self.profile_manager.normal_profile.clearHttpCache()
            self.profile_manager.normal_profile.clearAllVisitedLinks()
        except RuntimeError:
            pass
        self.settings_manager.clear_all()
        if app is not None:
            try:
                app.aboutToQuit.disconnect(
                    self.profile_manager.session_manager.mark_clean_shutdown
                )
            except (RuntimeError, TypeError):
                pass
        QMessageBox.information(
            self,
            "Data Deletion Scheduled",
            "Zyvro will now close. Its browser-owned data will be deleted after shutdown.",
        )
        for window in list(BrowserWindow._open_windows):
            window.close()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    def add_tab(
        self,
        url: QUrl | None = None,
        label: str = "New Tab",
        browser: BrowserView | None = None,
        tab_state: dict | None = None,
    ) -> BrowserView:
        created_browser = browser is None
        browser = browser or BrowserView(
            self.web_profile, private=self.incognito
        )
        if browser.web_profile is not self.web_profile:
            raise ValueError("A tab cannot be moved between normal and private profiles")
        if created_browser:
            browser.setZoomFactor(self.settings_manager.default_zoom_factor())
        if url is not None:
            browser.setUrl(url)
        elif created_browser:
            browser.setUrl(QUrl("about:blank") if self.incognito else self.home_url())
        browser.urlChanged.connect(lambda changed, view=browser: self._url_changed(view, changed))
        browser.titleChanged.connect(lambda title, view=browser: self._title_changed(view, title))
        browser.iconChanged.connect(lambda icon, view=browser: self._icon_changed(view, icon))
        browser.loadFinished.connect(
            lambda success, view=browser: self._record_history(view, success)
        )
        browser.printRequested.connect(self.print_current_page)
        browser.new_tab_requested.connect(self._add_popup_tab)
        browser.media_state_changed.connect(self._update_media_indicator)
        browser.adblock_state_changed.connect(self._update_adblock_button)
        browser.cosmetic_ads_hidden.connect(
            lambda amount, view=browser: self._cosmetic_ads_hidden(view, amount)
        )
        browser.permission_requested.connect(
            lambda permission, view=browser: self._permission_requested(
                view, permission
            )
        )
        browser.scripted_permission_requested.connect(
            lambda permission_name, target, view=browser:
            self._scripted_permission_requested(
                view, permission_name, target
            )
        )
        browser.certificate_error.connect(
            lambda error, view=browser: self._certificate_error(view, error)
        )
        browser.desktop_media_requested.connect(
            lambda request, view=browser: self._desktop_media_requested(view, request)
        )
        browser.filesystem_access_requested.connect(
            lambda request, view=browser: self._filesystem_access_requested(view, request)
        )
        browser.malicious_navigation_requested.connect(
            lambda blocked_url, view=browser:
            self._malicious_navigation_requested(view, blocked_url)
        )
        browser.https_upgrade_requested.connect(
            lambda original, secure, view=browser:
            self._https_upgrade_requested(view, original, secure)
        )
        browser.renderer_terminated.connect(
            lambda status, code, view=browser:
            self._renderer_terminated(view, status, code)
        )
        browser.full_screen_requested.connect(
            lambda request, view=browser:
            self._full_screen_requested(view, request)
        )
        browser.loadFinished.connect(
            lambda success, view=browser: self._https_upgrade_finished(view, success)
        )
        browser.urlChanged.connect(
            lambda _url, view=browser: self._apply_site_runtime_policy(view)
        )

        page = BrowserPage(browser, self)
        page.reader_bar.exit_requested.connect(self.toggle_reader_mode)
        page.tab_title = str((tab_state or {}).get("title") or label)
        page.pinned = bool((tab_state or {}).get("pinned", False))
        page.tab_group = str((tab_state or {}).get("group") or "")
        index = self.tabs.addTab(page, label)
        self._apply_tab_presentation(index)
        self.tabs.setCurrentIndex(index)
        self._schedule_session_save()
        return browser

    def _close_button(self, page: BrowserPage) -> QToolButton:
        button = QToolButton()
        button.setObjectName("tabCloseButton")
        button.setAutoRaise(True)
        button.setFixedSize(20, 20)
        button.setIconSize(QSize(14, 14))
        button.setIcon(self._themed_icon("close.svg"))
        button.setToolTip("Close tab (Ctrl+W)")
        button.clicked.connect(lambda: self.close_tab(self.tabs.indexOf(page)))
        return button

    def _apply_tab_presentation(self, index: int) -> None:
        page = self.tabs.widget(index)
        if not isinstance(page, BrowserPage):
            return
        group = page.tab_group
        color = self._tab_groups.get(group, "")
        self.tabs.tabBar().setTabData(index, {
            "pinned": page.pinned, "group": group, "color": color,
        })
        marker = ("☾ " if page.sleeping else "") + ("● " if group else "")
        self.tabs.setTabText(
            index,
            ("☾" if page.sleeping else ("●" if group else ""))
            if page.pinned else f"{marker}{page.tab_title}",
        )
        self.tabs.setTabToolTip(index, page.tab_title)
        self.tabs.tabBar().setTabTextColor(
            index, QColor(color) if color else QColor()
        )
        self.tabs.tabBar().setTabButton(
            index, QTabBar.ButtonPosition.RightSide,
            None if page.pinned else self._close_button(page),
        )
        self.tabs.tabBar().updateGeometry()

    def _show_tab_context_menu(self, position: QPoint) -> None:
        index = self.tabs.tabBar().tabAt(position)
        page = self.tabs.widget(index)
        if index < 0 or not isinstance(page, BrowserPage):
            return
        menu = QMenu(self)
        pin = menu.addAction("Unpin Tab" if page.pinned else "Pin Tab")
        pin.triggered.connect(lambda: self.set_tab_pinned(index, not page.pinned))
        groups = menu.addMenu("Add to Group")
        new_group = groups.addAction("New Group…")
        new_group.triggered.connect(lambda: self._create_tab_group(index))
        if self._tab_groups:
            groups.addSeparator()
            for name in self._tab_groups:
                action = groups.addAction(name)
                action.triggered.connect(
                    lambda _checked=False, value=name: self.set_tab_group(index, value)
                )
        if page.tab_group:
            remove_group = menu.addAction("Remove from Group")
            remove_group.triggered.connect(lambda: self.set_tab_group(index, ""))
        menu.addSeparator()
        close = menu.addAction("Close Tab")
        close.triggered.connect(lambda: self.close_tab(index))
        menu.exec(self.tabs.tabBar().mapToGlobal(position))

    def set_tab_pinned(self, index: int, pinned: bool) -> None:
        page = self.tabs.widget(index)
        if not isinstance(page, BrowserPage):
            return
        page.pinned = bool(pinned)
        pinned_count = sum(
            isinstance(self.tabs.widget(i), BrowserPage)
            and self.tabs.widget(i).pinned
            for i in range(self.tabs.count())
        )
        target = max(0, pinned_count - 1) if pinned else pinned_count
        self.tabs.tabBar().moveTab(index, min(target, self.tabs.count() - 1))
        self._apply_tab_presentation(self.tabs.indexOf(page))
        self._schedule_session_save()

    def _create_tab_group(self, index: int) -> None:
        name, accepted = QInputDialog.getText(self, "New Tab Group", "Group name")
        name = name.strip()
        if not accepted or not name:
            return
        colors = ("#5f8fe8", "#a875e8", "#47b8a5", "#df8b55", "#d6607b")
        self._tab_groups.setdefault(name, colors[len(self._tab_groups) % len(colors)])
        self.set_tab_group(index, name)

    def set_tab_group(self, index: int, group: str) -> None:
        page = self.tabs.widget(index)
        if not isinstance(page, BrowserPage):
            return
        page.tab_group = group
        self._apply_tab_presentation(index)
        self._schedule_session_save()

    def _add_popup_tab(self, browser: QWebEngineView) -> None:
        self.add_tab(label="New Tab", browser=browser)

    def close_tab(self, index: int) -> None:
        if index < 0 or index >= self.tabs.count():
            return
        widget = self.tabs.widget(index)
        if isinstance(widget, BrowserPage):
            if widget.browser is self._fullscreen_browser:
                self._leave_full_screen()
            if self.session_managed:
                self.session_manager.remember_closed(self._tab_state(widget))
            widget.close_devtools()
        if self.tabs.count() == 1:
            self.close()
            return
        self.tabs.removeTab(index)
        if widget is not None:
            widget.deleteLater()
        self._schedule_session_save()

    def restore_recently_closed_tab(self, recent_index: int = 0) -> None:
        if self.incognito:
            return
        state = self.session_manager.take_closed(recent_index)
        if state:
            if str(state.get("url")) == "zyvro:newtab":
                browser = self.add_tab(
                    QUrl("about:blank"), "New Tab", tab_state=state
                )
                self._load_zyvro_new_tab(browser)
            else:
                self.add_tab(
                    QUrl.fromUserInput(str(state.get("url") or HOME_URL)),
                    str(state.get("title") or "Restored Tab"),
                    tab_state=state,
                )

    def restore_recently_closed_window(self, recent_index: int = 0) -> None:
        if self.incognito or self.web_app_mode:
            return
        state = self.session_manager.take_closed_window(recent_index)
        if state:
            window = BrowserWindow(False, self.profile_manager, restore_state=state)
            window.show()

    def restore_previous_session(self, session_index: int = 0) -> None:
        if self.incognito or self.web_app_mode:
            return
        sessions = self.session_manager.previous_sessions()
        if not (0 <= session_index < len(sessions)):
            return
        for state in sessions[session_index].get("windows", []):
            if state.get("tabs"):
                window = BrowserWindow(False, self.profile_manager, restore_state=state)
                window.show()

    def show_recently_closed_menu(self) -> None:
        menu = QMenu(self)
        recent = self.session_manager.recently_closed() if not self.incognito else []
        closed_windows = (
            self.session_manager.recently_closed_windows()
            if not self.incognito else []
        )
        previous = (
            self.session_manager.previous_sessions()
            if not self.incognito else []
        )
        tabs_heading = menu.addAction("Recently Closed Tabs")
        tabs_heading.setEnabled(False)
        if not recent:
            empty = menu.addAction("  No recently closed tabs")
            empty.setEnabled(False)
        for index, tab in enumerate(recent[:12]):
            title = str(tab.get("title") or tab.get("url") or "Closed Tab")
            action = menu.addAction(f"  {title[:52]}")
            action.setToolTip(str(tab.get("url") or ""))
            action.triggered.connect(
                lambda _checked=False, value=index: self.restore_recently_closed_tab(value)
            )
        menu.addSeparator()
        windows_heading = menu.addAction("Recently Closed Windows")
        windows_heading.setEnabled(False)
        if not closed_windows:
            empty = menu.addAction("  No recently closed windows")
            empty.setEnabled(False)
        for index, state in enumerate(closed_windows[:8]):
            tabs = state.get("tabs", [])
            first = str((tabs[0] if tabs else {}).get("title") or "Browser Window")
            action = menu.addAction(f"  {first[:38]} ({len(tabs)} tabs)")
            action.triggered.connect(
                lambda _checked=False, value=index:
                self.restore_recently_closed_window(value)
            )
        menu.addSeparator()
        sessions_heading = menu.addAction("Previous Sessions")
        sessions_heading.setEnabled(False)
        if not previous:
            empty = menu.addAction("  No previous sessions")
            empty.setEnabled(False)
        for index, session in enumerate(previous[:8]):
            states = session.get("windows", [])
            tab_count = sum(len(state.get("tabs", [])) for state in states)
            saved = str(session.get("saved_at") or "Previous session")
            saved = saved.replace("T", " ")[:16]
            crash = "Crash recovery · " if session.get("crashed") else ""
            action = menu.addAction(
                f"  {crash}{saved} ({tab_count} tabs)"
            )
            action.triggered.connect(
                lambda _checked=False, value=index:
                self.restore_previous_session(value)
            )
        menu.popup(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height())))

    def current_browser(self) -> BrowserView:
        page = self.tabs.currentWidget()
        if not isinstance(page, BrowserPage):
            raise RuntimeError("The active tab is not a browser page")
        return page.browser

    def _set_page_lifecycle(self, page: BrowserPage, state) -> bool:
        """Change Chromium lifecycle state and keep the tab label in sync."""
        try:
            page.browser.page().setLifecycleState(state)
        except (AttributeError, RuntimeError):
            return False
        page.sleeping = state != QWebEnginePage.LifecycleState.Active
        index = self.tabs.indexOf(page)
        if index >= 0:
            self._apply_tab_presentation(index)
        return True

    def _tab_can_sleep(self, page: BrowserPage) -> bool:
        browser = page.browser
        if page is self.tabs.currentWidget():
            return False
        if self.download_manager.active_requests():
            # Qt cannot map a download back to one tab. While a download is
            # active, keeping every page awake is the safe choice.
            return False
        if browser.media_audio_active or browser.media_video_active:
            return False
        try:
            if browser.page().recentlyAudible():
                return False
        except (AttributeError, RuntimeError):
            pass
        if page.devtools_dock is not None:
            return False
        return not browser.page().isLoading()

    def _sleep_inactive_tabs(self) -> None:
        if not bool(self.settings_manager.value("performance/sleeping_tabs_enabled")):
            return
        minutes = max(
            1, int(self.settings_manager.value("performance/sleeping_tabs_minutes"))
        )
        cutoff = time.monotonic() - minutes * 60
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if (
                isinstance(page, BrowserPage)
                and not page.sleeping
                and page.last_active_at <= cutoff
                and self._tab_can_sleep(page)
            ):
                self._set_page_lifecycle(
                    page, QWebEnginePage.LifecycleState.Frozen
                )

    def navigate_to_address(self) -> None:
        text = self.address_bar.text().strip()
        if not text:
            return
        self.current_browser().setUrl(self._address_to_url(text))

    def focus_address_bar(self) -> None:
        self.address_bar.setFocus()
        self.address_bar.selectAll()

    def _address_to_url(self, text: str) -> QUrl:
        """Open probable URLs directly and use the configured search engine."""
        text = text.strip()
        parsed = QUrl(text)
        host_candidate = text.split("/", 1)[0].lower()
        probable_url = (
            bool(parsed.scheme())
            or "." in host_candidate
            or host_candidate.startswith("localhost:")
            or host_candidate == "localhost"
        )
        if probable_url and " " not in text:
            return QUrl.fromUserInput(text)
        return self.settings_manager.search_url(text)

    def _current_tab_changed(self, index: int) -> None:
        if index < 0:
            return
        page = self.tabs.widget(index)
        if isinstance(page, BrowserPage):
            page.attach_browser_bars(self.navigation_bar, self.bookmarks_bar)
            page.last_active_at = time.monotonic()
            self._set_page_lifecycle(page, QWebEnginePage.LifecycleState.Active)
        for tab_index in range(self.tabs.count()):
            tab_page = self.tabs.widget(tab_index)
            if isinstance(tab_page, BrowserPage):
                tab_page.set_devtools_active(tab_index == index)
        browser = self.current_browser()
        self.address_bar.setText(
            "" if getattr(browser, "_is_zyvro_new_tab", False)
            else browser.url().toDisplayString()
        )
        self.address_bar.setCursorPosition(0)
        self._update_security_button(browser.url())
        self._update_privacy_shield()
        self._update_adblock_button()
        self._update_adblock_button()
        self._update_cookie_button()
        self._update_media_indicator()
        self.setWindowTitle(self._window_title(browser.title() or "New Tab"))
        self._update_bookmark_button()
        self.browser_menu.update_zoom(browser.zoomFactor())
        self._schedule_session_save()

    def _url_changed(self, browser: BrowserView, url: QUrl) -> None:
        new_tab_path = str(getattr(browser, "_zyvro_new_tab_path", ""))
        browser._is_zyvro_new_tab = bool(
            new_tab_path and url.isLocalFile()
            and str(Path(url.toLocalFile()).resolve()) == new_tab_path
        )
        if browser is self.current_browser():
            self.address_bar.setText(
                "" if browser._is_zyvro_new_tab else url.toDisplayString()
            )
            self.address_bar.setCursorPosition(0)
            self._update_security_button(url)
            self._update_adblock_button()
            self._update_cookie_button()
            self._update_privacy_shield()
            self._update_bookmark_button()
        self._schedule_session_save()

    def _apply_site_runtime_policy(self, browser: BrowserView) -> None:
        site = browser.url().host().lower().strip(".")
        browser.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled,
            self.site_permission_manager.decision(site, "javascript") != "block",
        )

    def _audit_security(self, event_type: str, title: str, detail: str) -> None:
        if not self.incognito:
            self.security_data.add_activity(None, event_type, title, detail)

    def _page_for_browser(self, browser: BrowserView) -> BrowserPage | None:
        index = self._index_of_browser(browser)
        page = self.tabs.widget(index) if index >= 0 else None
        return page if isinstance(page, BrowserPage) else None

    def _certificate_error(self, browser: BrowserView, error) -> None:
        host = error.url().host() or error.url().toDisplayString()
        detail = f"{error.description()} ({error.type().name})"
        if not error.isMainFrame():
            error.rejectCertificate()
            self._audit_security(
                "certificate_blocked", "Subresource certificate rejected",
                f"{host} · {detail}",
            )
            return
        error.defer()
        page = self._page_for_browser(browser)
        if page is None:
            error.rejectCertificate()
            return
        self._audit_security(
            "certificate_blocked", "Invalid certificate blocked",
            f"{host} · {detail}",
        )

        def back() -> None:
            error.rejectCertificate()
            page.hide_security_interstitial()
            if browser.history().canGoBack():
                browser.back()
            else:
                browser.setUrl(self.home_url())

        def proceed() -> None:
            error.acceptCertificate()
            page.hide_security_interstitial()
            self._audit_security(
                "certificate_override", "Certificate warning overridden",
                host,
            )

        page.show_security_interstitial(
            title="Your connection is not private",
            message=(
                f"Python Browser blocked {host} because its HTTPS certificate "
                "is invalid, expired, mismatched, or untrusted."
            ),
            detail=detail + "\nProceeding can expose passwords and private data.",
            proceed_text="Still Proceed",
            proceed_callback=proceed,
            back_callback=back,
            allow_proceed=bool(error.isOverridable()),
        )

    def _malicious_navigation_requested(
        self, browser: BrowserView, blocked_url: QUrl
    ) -> None:
        page = self._page_for_browser(browser)
        if page is None:
            return
        host = blocked_url.host().lower().strip(".")
        self._audit_security(
            "phishing_blocked", "Potentially malicious website blocked", host
        )

        def back() -> None:
            page.hide_security_interstitial()
            if browser.history().canGoBack():
                browser.back()
            else:
                browser.setUrl(self.home_url())

        def proceed() -> None:
            self.profile_manager.navigation_security.allow_malicious_for_session(host)
            page.hide_security_interstitial()
            self._audit_security(
                "phishing_override", "Malicious-site warning overridden", host
            )
            browser.setUrl(blocked_url)

        page.show_security_interstitial(
            title="Deceptive or dangerous site blocked",
            message=(
                f"The local malicious-site list identifies {host} as potentially "
                "dangerous or deceptive."
            ),
            detail=(
                "The address was checked locally and your browsing history was not uploaded. "
                "If you continue, watch the website carefully and do not enter passwords, "
                "payment information, or personal data."
            ),
            proceed_text="Still Proceed — Watch Carefully",
            proceed_callback=proceed,
            back_callback=back,
        )

    def _https_upgrade_requested(
        self, browser: BrowserView, original: QUrl, secure: QUrl
    ) -> None:
        browser._https_only_original = QUrl(original)
        browser._https_only_secure = QUrl(secure)
        browser.setUrl(secure)

    def _https_upgrade_finished(self, browser: BrowserView, success: bool) -> None:
        original = getattr(browser, "_https_only_original", None)
        secure = getattr(browser, "_https_only_secure", None)
        if not isinstance(original, QUrl) or not isinstance(secure, QUrl):
            return
        if success:
            browser._https_only_original = None
            browser._https_only_secure = None
            return
        page = self._page_for_browser(browser)
        if page is None:
            return
        host = original.host().lower().strip(".")
        self._audit_security(
            "https_upgrade_failed", "HTTPS-only upgrade failed", host
        )

        def back() -> None:
            browser._https_only_original = None
            browser._https_only_secure = None
            page.hide_security_interstitial()
            browser.setUrl(self.home_url())

        def proceed() -> None:
            self.profile_manager.navigation_security.allow_http_for_session(host)
            browser._https_only_original = None
            browser._https_only_secure = None
            page.hide_security_interstitial()
            self._audit_security(
                "http_override", "Unencrypted HTTP allowed for session", host
            )
            browser.setUrl(original)

        page.show_security_interstitial(
            title="Secure connection unavailable",
            message=f"HTTPS-Only Mode could not open a secure connection to {host}.",
            detail=(
                "Continuing uses an unencrypted HTTP connection. Other people on the "
                "network may be able to read or modify the page."
            ),
            proceed_text="Continue to HTTP Once",
            proceed_callback=proceed,
            back_callback=back,
        )

    def _renderer_terminated(self, browser: BrowserView, status, exit_code: int) -> None:
        page = self._page_for_browser(browser)
        if page is None:
            return
        status_name = getattr(status, "name", str(status))
        self._audit_security(
            "renderer_terminated", "Tab renderer terminated",
            f"{status_name} · exit code {exit_code}",
        )

        def close_tab() -> None:
            self.close_tab(self.tabs.indexOf(page))

        def reload_tab() -> None:
            page.hide_security_interstitial()
            browser.reload()

        page.show_security_interstitial(
            title="This tab crashed",
            message="The Chromium renderer for this tab stopped unexpectedly.",
            detail=f"Status: {status_name} · Exit code: {exit_code}",
            proceed_text="Reload Tab",
            proceed_callback=reload_tab,
            back_callback=close_tab,
        )
        page.security_interstitial.back.setText("Close Tab")

    def _full_screen_requested(self, browser: BrowserView, request) -> None:
        """Honor Chromium's HTML fullscreen request for the requesting tab."""
        try:
            toggle_on = bool(request.toggleOn())
        except (AttributeError, RuntimeError):
            request.reject()
            return

        if toggle_on:
            index = self._index_of_browser(browser)
            if index < 0:
                request.reject()
                return
            if self._fullscreen_browser is not None:
                self._leave_full_screen()
            self.tabs.setCurrentIndex(index)
            self._fullscreen_browser = browser
            self._fullscreen_geometry = self.saveGeometry()
            self._fullscreen_was_maximized = self.isMaximized()
            self._fullscreen_navigation_visible = self.navigation_bar.isVisible()
            self._fullscreen_bookmarks_visible = self.bookmarks_bar.isVisible()
            self.tabs.tabBar().hide()
            self.navigation_bar.hide()
            self.bookmarks_bar.hide()
            request.accept()
            self.showFullScreen()
            browser.setFocus()
            return

        request.accept()
        self._leave_full_screen()

    def _leave_full_screen(self) -> None:
        if self._fullscreen_browser is None and not self.isFullScreen():
            return
        self._fullscreen_browser = None
        self.tabs.tabBar().show()
        self.navigation_bar.setVisible(self._fullscreen_navigation_visible)
        self.bookmarks_bar.setVisible(self._fullscreen_bookmarks_visible)
        if self._fullscreen_was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
            if self._fullscreen_geometry is not None:
                self.restoreGeometry(self._fullscreen_geometry)
        self._fullscreen_geometry = None

    def _desktop_media_requested(self, browser: BrowserView, request) -> None:
        site = browser.url().host().lower().strip(".")
        decision = self.site_permission_manager.decision(site, "screen")
        if decision == "block":
            request.cancel()
            self._record_permission_timeline(site, ["screen"], False)
            return

        def choose_source() -> None:
            choices: list[tuple[str, object, object]] = []
            for kind, model in (
                ("Screen", request.screensModel()),
                ("Window", request.windowsModel()),
            ):
                for row in range(model.rowCount()):
                    index = model.index(row, 0)
                    label = str(model.data(index, Qt.ItemDataRole.DisplayRole) or f"{kind} {row + 1}")
                    choices.append((f"{kind}: {label}", kind, index))
            labels = [choice[0] for choice in choices]
            if not labels:
                request.cancel()
                return
            selected, accepted = QInputDialog.getItem(
                self, "Choose what to share", "Screen or window", labels, 0, False
            )
            if not accepted:
                request.cancel()
                return
            _label, kind, index = choices[labels.index(selected)]
            if kind == "Screen":
                request.selectScreen(index)
            else:
                request.selectWindow(index)
            self._record_permission_timeline(site, ["screen"], True)

        if decision == "allow":
            choose_source()
            return

        def finish(choice: str) -> None:
            if choice == "block":
                self.site_permission_manager.set_decision(site, "screen", "block")
                request.cancel()
                self._record_permission_timeline(site, ["screen"], False)
                return
            if choice == "allow":
                self.site_permission_manager.set_decision(site, "screen", "allow")
            choose_source()

        popup = SitePermissionPopup(
            self, site or "This site", "Screen sharing", finish
        )
        self._permission_popup = popup
        popup.adjustSize()
        popup.move(self.address_bar.mapToGlobal(QPoint(0, self.address_bar.height() + 5)))
        popup.show()

    def _filesystem_access_requested(self, browser: BrowserView, request) -> None:
        site = request.origin().host().lower().strip(".")
        decision = self.site_permission_manager.decision(site, "filesystem")
        if decision == "allow":
            request.accept()
            self._record_permission_timeline(site, ["filesystem"], True)
            return
        if decision == "block":
            request.reject()
            self._record_permission_timeline(site, ["filesystem"], False)
            return

        def finish(choice: str) -> None:
            if choice in {"once", "allow"}:
                if choice == "allow":
                    self.site_permission_manager.set_decision(
                        site, "filesystem", "allow"
                    )
                request.accept()
                self._record_permission_timeline(site, ["filesystem"], True)
            else:
                self.site_permission_manager.set_decision(
                    site, "filesystem", "block"
                )
                request.reject()
                self._record_permission_timeline(site, ["filesystem"], False)

        popup = SitePermissionPopup(
            self, site or "This site", "File system access", finish
        )
        self._permission_popup = popup
        popup.adjustSize()
        popup.move(self.address_bar.mapToGlobal(QPoint(0, self.address_bar.height() + 5)))
        popup.show()
        allow_autoplay = (
            self.site_permission_manager.decision(site, "autoplay") == "allow"
        )
        browser.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,
            not allow_autoplay,
        )

    def _update_security_button(self, url: QUrl) -> None:
        scheme = url.scheme().lower()
        if scheme == "https":
            self.security_action.setIcon(self._themed_icon("lock.svg"))
            self.security_action.setToolTip(
                "Secure connection — click for site permissions and certificate"
            )
        elif scheme == "http":
            self.security_action.setIcon(self._icon("not-secure.svg"))
            self.security_action.setToolTip(
                "Not secure — this page does not use an HTTPS certificate"
            )
        else:
            self.security_action.setIcon(self._themed_icon("site-info.svg"))
            self.security_action.setToolTip("Page information")

    @staticmethod
    def _certificate_name(
        certificate: QSslCertificate,
        field: QSslCertificate.SubjectInfo,
    ) -> str:
        values = certificate.subjectInfo(field)
        return ", ".join(values) if values else "Not provided"

    def show_site_info(self) -> None:
        popup = SiteInfoPopup(self)
        self._site_info_popup = popup
        popup.adjustSize()
        popup.move(self.address_bar.mapToGlobal(QPoint(0, self.address_bar.height() + 5)))
        popup.show()
        popup.raise_()

    def show_certificate_info(self) -> None:
        """Backward-compatible entry point used by existing tests/actions."""
        self.show_certificate_details()

    def show_certificate_details(self) -> None:
        """Retrieve and display the current HTTPS host's real TLS certificate."""
        url = self.current_browser().url()
        if url.scheme().lower() != "https" or not url.host():
            message = (
                "This connection is not secure and does not provide a TLS certificate."
                if url.scheme().lower() == "http"
                else "This internal page does not use a website TLS certificate."
            )
            QMessageBox.information(self, "Connection Security", message)
            return

        host = url.host()
        port = url.port(443)
        socket = QSslSocket(self)
        socket.setPeerVerifyName(host)
        socket.connectToHostEncrypted(host, port)
        if not socket.waitForEncrypted(5000):
            error = socket.errorString() or "The TLS connection could not be verified."
            socket.abort()
            socket.deleteLater()
            QMessageBox.warning(
                self,
                "Certificate unavailable",
                f"Could not retrieve a verified certificate for {host}.\n\n{error}",
            )
            return

        certificate = socket.peerCertificate()
        chain_length = len(socket.peerCertificateChain())
        cipher = socket.sessionCipher().name() or "Unknown"
        protocol = socket.sessionProtocol().name
        fingerprint = bytes(
            certificate.digest(QCryptographicHash.Algorithm.Sha256).toHex()
        ).decode("ascii", errors="replace")
        serial = bytes(certificate.serialNumber()).decode("ascii", errors="replace")
        common_name = self._certificate_name(
            certificate, QSslCertificate.SubjectInfo.CommonName
        )
        issuer_values = certificate.issuerInfo(
            QSslCertificate.SubjectInfo.CommonName
        )
        issuer = ", ".join(issuer_values) if issuer_values else "Not provided"
        details = (
            f"Connection to {host}:{port} is encrypted and the certificate passed "
            "Qt's hostname and trust verification.\n\n"
            f"Issued to: {common_name}\n"
            f"Issued by: {issuer}\n"
            f"Valid from: {certificate.effectiveDate().toString('MMM d, yyyy HH:mm:ss t')}\n"
            f"Valid until: {certificate.expiryDate().toString('MMM d, yyyy HH:mm:ss t')}\n"
            f"Serial number: {serial}\n"
            f"SHA-256 fingerprint: {fingerprint}\n\n"
            f"TLS protocol: {protocol}\n"
            f"Cipher: {cipher}\n"
            f"Certificate chain: {chain_length} certificate(s)"
        )
        socket.disconnectFromHost()
        socket.deleteLater()
        QMessageBox.information(self, "Connection Security & Certificate", details)

    def _permission_requested(
        self, browser: BrowserView, permission: QWebEnginePermission
    ) -> None:
        permission_type = permission.permissionType()
        types = QWebEnginePermission.PermissionType
        permission_names: list[str] = []
        if permission_type in (types.MediaAudioCapture, types.MediaAudioVideoCapture):
            permission_names.append("microphone")
        if permission_type in (types.MediaVideoCapture, types.MediaAudioVideoCapture):
            permission_names.append("camera")
        native_mapping = {
            types.Geolocation: "location",
            types.Notifications: "notifications",
            types.ClipboardReadWrite: "clipboard",
        }
        if permission_type in native_mapping:
            permission_names.append(native_mapping[permission_type])
        if not permission_names:
            permission.deny()
            return

        site = permission.origin().host().lower().strip(".")
        decisions = [
            self.site_permission_manager.decision(site, name)
            for name in permission_names
        ]
        if "block" in decisions:
            permission.deny()
            self._record_permission_timeline(site, permission_names, False)
            return
        if decisions and all(choice == "allow" for choice in decisions):
            permission.grant()
            self._record_permission_timeline(site, permission_names, True)
            if any(name in {"camera", "microphone"} for name in permission_names):
                browser.media_permissions.append(permission)
            return

        label = "Camera and microphone" if set(permission_names) == {
            "camera", "microphone"
        } else PERMISSION_LABELS[permission_names[0]]

        def finish(choice: str) -> None:
            if choice in {"once", "allow"}:
                if choice == "allow":
                    for name in permission_names:
                        self.site_permission_manager.set_decision(site, name, "allow")
                permission.grant()
                self._record_permission_timeline(site, permission_names, True)
                if any(name in {"camera", "microphone"} for name in permission_names):
                    browser.media_permissions.append(permission)
            else:
                for name in permission_names:
                    self.site_permission_manager.set_decision(site, name, "block")
                permission.deny()
                self._record_permission_timeline(site, permission_names, False)

        popup = SitePermissionPopup(self, site or "This site", label, finish)
        self._permission_popup = popup
        popup.adjustSize()
        popup.move(self.address_bar.mapToGlobal(QPoint(0, self.address_bar.height() + 5)))
        popup.show()
        popup.raise_()

    def _record_permission_timeline(
        self, site: str, permission_names: list[str], allowed: bool
    ) -> None:
        if self.incognito:
            return
        action = "allowed" if allowed else "blocked"
        for permission_name in permission_names:
            label = PERMISSION_LABELS.get(permission_name, permission_name.title())
            self.privacy_timeline_manager.record(
                "permissions", f"{permission_name}_{action}",
                f"{label} permission {action}", site,
            )

    def _scripted_permission_requested(
        self, browser: BrowserView, permission_name: str, target: str
    ) -> None:
        if browser is not self.current_browser():
            return
        site = browser.url().host().lower().strip(".")
        label = PERMISSION_LABELS[permission_name]

        def finish(choice: str) -> None:
            if choice in {"allow", "block"}:
                self.site_permission_manager.set_decision(
                    site, permission_name, choice
                )
            if choice not in {"once", "allow"}:
                return
            if permission_name == "popups":
                popup_url = QUrl(target)
                if popup_url.isRelative():
                    popup_url = browser.url().resolved(popup_url)
                self.add_tab(popup_url, "New Tab")
            else:
                browser.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,
                    False,
                )
                browser.page().runJavaScript(
                    "document.querySelectorAll('audio,video').forEach(e => e.play().catch(()=>{}));"
                )

        popup = SitePermissionPopup(self, site or "This site", label, finish)
        self._permission_popup = popup
        popup.adjustSize()
        popup.move(self.address_bar.mapToGlobal(QPoint(0, self.address_bar.height() + 5)))
        popup.show()
        popup.raise_()

    def _active_media_views(self) -> list[BrowserView]:
        views: list[BrowserView] = []
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, BrowserPage) and (
                page.browser.media_audio_active
                or page.browser.media_video_active
                or page.browser.screen_capture_active
            ):
                views.append(page.browser)
        return views

    def _update_media_indicator(self) -> None:
        if not hasattr(self, "media_indicator"):
            return
        active = self._active_media_views()
        if not active:
            self.media_indicator.hide()
            return
        audio = any(view.media_audio_active for view in active)
        video = any(view.media_video_active for view in active)
        screen = any(view.screen_capture_active for view in active)
        icon = "screen-share.svg" if screen else (
            "camera-microphone.svg" if audio and video else
            "microphone.svg" if audio else "camera.svg"
        )
        parts = []
        if screen:
            parts.append("Screen sharing")
        if video and audio:
            parts.append("Camera and microphone")
        elif video:
            parts.append("Camera")
        elif audio:
            parts.append("Microphone")
        devices = ", ".join(parts)
        sites = sorted(
            {
                view.url().host() or view.url().toDisplayString()
                for view in active
            }
        )
        self.media_indicator.setIcon(
            self._themed_icon(
                icon, self.theme_manager.palette(self.incognito).accent
            )
        )
        self.media_indicator.setToolTip(
            f"{devices} active — {', '.join(sites)}"
        )
        self.media_indicator.show()

    def show_media_activity(self) -> None:
        active = self._active_media_views()
        if not active:
            self._update_media_indicator()
            return
        lines = []
        for view in active:
            devices = []
            if view.media_video_active:
                devices.append("camera")
            if view.media_audio_active:
                devices.append("microphone")
            if view.screen_capture_active:
                devices.append("screen sharing")
            lines.append(
                f"{view.url().host() or view.url().toDisplayString()}: "
                f"{', '.join(devices)}"
            )
        box = QMessageBox(self)
        box.setWindowTitle("Camera and Microphone Activity")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Active media capture")
        box.setInformativeText("\n".join(lines))
        stop = box.addButton(
            "Stop & Block Access", QMessageBox.ButtonRole.DestructiveRole
        )
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is stop:
            for view in active:
                view.stop_and_block_media()
            self._update_media_indicator()

    def _update_privacy_shield(self) -> None:
        if not hasattr(self, "privacy_action") or self.tabs.count() == 0:
            return
        host = self.current_browser().url().host()
        enabled = self.profile_manager.site_protection_enabled(host)
        count = self.profile_manager.privacy_manager.count_for_site(
            host, self.privacy_session
        )
        self.privacy_action.setIcon(
            self._themed_icon(
                "shield.svg" if enabled else "shield-off.svg",
                self.theme_manager.palette(self.incognito).accent
                if enabled else self.theme_manager.palette(self.incognito).muted,
            )
        )
        status = "on" if enabled else "off"
        self.privacy_action.setToolTip(
            f"Tracking protection {status} — {count} blocked on this page"
        )

    def _privacy_stats_changed(self) -> None:
        self._update_privacy_shield()

    def _ad_blocked(
        self, site: str, amount: int, private_session: str, source: str
    ) -> None:
        if private_session != self.privacy_session or source == "cosmetic":
            return
        if self.tabs.count() == 0:
            return
        browser = self.current_browser()
        if browser.url().host().lower().strip(".") == site.lower().strip("."):
            browser.add_blocked_ads(amount)

    def _cosmetic_ads_hidden(self, browser: BrowserView, amount: int) -> None:
        browser.add_blocked_ads(amount)
        self.adblock_manager.record_cosmetic(
            browser.url().host().lower().strip(".") or "Unknown site",
            amount,
            self.privacy_session,
        )

    def _update_adblock_button(self) -> None:
        if not hasattr(self, "adblock_button") or self.tabs.count() == 0:
            return
        browser = self.current_browser()
        host = browser.url().host()
        enabled = self.adblock_manager.site_enabled(host)
        count = browser.ads_blocked_current
        if not enabled:
            icon_name = "adblock-off.svg"
        elif count:
            icon_name = "adblock-active.svg"
        else:
            icon_name = "adblock.svg"
        self.adblock_button.setIcon(self._icon(icon_name))
        self.adblock_button.setText(str(count) if count else "")
        self.adblock_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
            if count else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        state = "on" if enabled else "off"
        engine_name = (
            self.adblock_manager.rust_engine.display_name
            if self.adblock_manager.rust_engine.available
            else "Ad blocking"
        )
        self.adblock_button.setToolTip(
            f"{engine_name} {state} — {count} blocked on this page"
        )

    def show_adblock_popup(self) -> None:
        popup = AdBlockPopup(self)
        self._adblock_popup = popup
        popup.adjustSize()
        position = self.adblock_button.mapToGlobal(
            QPoint(self.adblock_button.width() - popup.width(),
                   self.adblock_button.height() + 5)
        )
        popup.move(position)
        popup.show()
        popup.raise_()

    def _update_cookie_button(self) -> None:
        if not hasattr(self, "cookie_button") or self.tabs.count() == 0:
            return
        enabled = self.cookie_privacy_manager.enabled()
        host = self.current_browser().url().host().lower().strip(".")
        exception = self.cookie_privacy_manager.site_exception(host)
        icon_name = "cookie-block.svg" if enabled and not exception else "cookie.svg"
        color = (
            self.theme_manager.palette(self.incognito).accent
            if enabled and not exception else
            self.theme_manager.palette(self.incognito).muted
        )
        self.cookie_button.setIcon(self._themed_icon(icon_name, color))
        if enabled and exception:
            status = "allowed for this site"
        else:
            status = "blocked" if enabled else "allowed"
        self.cookie_button.setToolTip(f"Third-party cookies — {status}")

    def show_cookie_popup(self) -> None:
        popup = CookieProtectionPopup(self)
        self._cookie_popup = popup
        popup.adjustSize()
        popup.move(
            self.cookie_button.mapToGlobal(
                QPoint(
                    self.cookie_button.width() - popup.width(),
                    self.cookie_button.height() + 5,
                )
            )
        )
        popup.show()
        popup.raise_()

    def _update_vpn_button(self) -> None:
        if not hasattr(self, "vpn_button"):
            return
        state = self.vpn_manager.state()
        mode = self.vpn_manager.mode()
        if state == "error":
            icon_name = "vpn-error.svg"
        elif state == "connected" and mode == "tor":
            icon_name = "vpn-tor.svg"
        elif state == "connected":
            icon_name = "vpn-active.svg"
        else:
            icon_name = "vpn.svg"
        self.vpn_button.setIcon(self._icon(icon_name))
        self.vpn_button.setToolTip(
            f"VPN / Private Network — {self.vpn_manager.detail()}"
        )

    def show_vpn_popup(self) -> None:
        popup = VpnPopup(self)
        self._vpn_popup = popup
        popup.adjustSize()
        position = self.vpn_button.mapToGlobal(
            QPoint(
                self.vpn_button.width() - popup.width(),
                self.vpn_button.height() + 5,
            )
        )
        popup.move(position)
        popup.show()
        popup.raise_()

    def set_current_site_adblocking(self, enabled: bool) -> None:
        host = self.current_browser().url().host()
        if host:
            self.adblock_manager.set_site_enabled(host, enabled)
            # Reload so cosmetic rules already injected into this document are
            # removed or applied consistently with the new site exception.
            self.current_browser().reload()
        self._update_adblock_button()

    def set_adblocking_enabled(self, enabled: bool) -> None:
        self.settings_manager.save_values(
            {"privacy/adblock_enabled": bool(enabled)}
        )
        self.adblock_manager.refresh()
        self._update_adblock_button()
        # Reload the active document so cosmetic rules match the new global
        # state immediately instead of waiting for the next navigation.
        self.current_browser().reload()

    def show_privacy_popup(self) -> None:
        popup = PrivacyPopup(self)
        self._privacy_popup = popup
        popup.adjustSize()
        position = self.address_bar.mapToGlobal(
            QPoint(0, self.address_bar.height() + 5)
        )
        popup.move(position)
        popup.show()
        popup.raise_()

    def set_current_site_protection(self, enabled: bool) -> None:
        host = self.current_browser().url().host()
        if host:
            self.profile_manager.set_site_protection(host, enabled)
        self._update_privacy_shield()
        self._update_adblock_button()

    def set_tracker_protection_enabled(self, enabled: bool) -> None:
        self.settings_manager.save_values({
            "privacy/tracker_protection_enabled": bool(enabled),
            "privacy/tracker_choice_remembered": True,
        })
        self.profile_manager.clear_tracker_session_override()
        self.profile_manager.refresh_privacy_settings()
        self._update_privacy_shield()

    def open_privacy_report(self) -> None:
        PrivacyReportDialog(
            self.profile_manager.privacy_manager,
            self.privacy_session,
            self,
        ).exec()

    def _title_changed(self, browser: BrowserView, title: str) -> None:
        index = self._index_of_browser(browser)
        if index >= 0:
            short_title = title if len(title) <= 24 else f"{title[:21]}..."
            page = self.tabs.widget(index)
            if isinstance(page, BrowserPage):
                page.tab_title = short_title or "New Tab"
                self._apply_tab_presentation(index)
        if browser is self.current_browser():
            self.setWindowTitle(self._window_title(title or "New Tab"))
        self._schedule_session_save()

    def _icon_changed(self, browser: BrowserView, icon) -> None:
        index = self._index_of_browser(browser)
        if index >= 0:
            self.tabs.setTabIcon(index, icon)
        host = browser.url().host().lower().strip(".")
        if self.incognito or not host or icon.isNull():
            return
        path = self._favicon_path(host)
        temporary = path.with_suffix(".tmp.png")
        if icon.pixmap(64, 64).save(str(temporary), "PNG"):
            try:
                temporary.replace(path)
            except OSError:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def home_url(self) -> QUrl:
        return QUrl.fromUserInput(self.settings_manager.homepage() or HOME_URL)

    def _window_title(self, page_title: str) -> str:
        if self.web_app_mode:
            return str(self.web_app.get("name") or page_title or "Web App")
        is_new_tab = page_title == "New Tab"
        if hasattr(self, "tabs") and self.tabs.count():
            try:
                is_new_tab = bool(
                    getattr(self.current_browser(), "_is_zyvro_new_tab", False)
                )
            except RuntimeError:
                pass
        if is_new_tab:
            return "New Tab - Zyvro Private" if self.incognito else "New Tab - Zyvro"
        suffix = " - Zyvro Private" if self.incognito else " - Zyvro"
        return f"{page_title}{suffix}"

    def _effective_theme(self) -> str:
        return self.theme_manager.active_theme()

    def apply_theme(self, *_args) -> None:
        self.setStyleSheet(
            self.theme_manager.browser_stylesheet(self.incognito)
        )
        if hasattr(self, "_toolbar_actions"):
            for filename, action in self._toolbar_actions.items():
                action.setIcon(self._themed_icon(filename))
        if hasattr(self, "tabs"):
            tab_bar = self.tabs.tabBar()
            if isinstance(tab_bar, BrowserTabBar):
                tab_bar.new_tab_button.setIcon(self._themed_icon("plus.svg"))
            for button in self.findChildren(QToolButton, "tabCloseButton"):
                button.setIcon(self._themed_icon("close.svg"))
        if hasattr(self, "browser_menu"):
            self.browser_menu.set_theme(
                self.theme_manager.active_theme(), self.incognito
            )
            self.browser_menu.set_icons(self._menu_icons())
        if hasattr(self, "tabs") and self.tabs.count():
            self._update_security_button(self.current_browser().url())
            self._update_privacy_shield()
            self._update_bookmark_button()
        if hasattr(self, "download_button"):
            self.download_button.setIcon(self._themed_icon("download.svg"))
            self._update_download_button()
            self._update_media_indicator()
        if hasattr(self, "vpn_button"):
            self._update_vpn_button()
        if hasattr(self, "cookie_button"):
            self._update_cookie_button()

    def show_browser_menu(self) -> None:
        self.browser_menu.update_zoom(self.current_browser().zoomFactor())
        self.browser_menu.set_theme(
            self.theme_manager.active_theme(), self.incognito
        )
        self.browser_menu.adjustSize()
        position = self.menu_button.mapToGlobal(
            QPoint(
                self.menu_button.width() - self.browser_menu.width(),
                self.menu_button.height() + 4,
            )
        )
        screen = QApplication.screenAt(position)
        if screen is not None:
            available = screen.availableGeometry()
            position.setX(
                max(available.left(), min(position.x(), available.right() - self.browser_menu.width()))
            )
            position.setY(
                max(available.top(), min(position.y(), available.bottom() - self.browser_menu.height()))
            )
        self.browser_menu.move(position)
        self.browser_menu.show()
        self.browser_menu.raise_()

    def _record_history(self, browser: BrowserView, success: bool) -> None:
        if success:
            self.history_manager.add_visit(
                browser.title() or browser.url().toDisplayString(),
                browser.url().toString(),
                private=bool(getattr(browser, "is_private", False)),
            )

    def open_history(self) -> None:
        dialog = HistoryDialog(self.history_manager, self, self.theme_manager)
        dialog.open_url.connect(
            lambda url: self.current_browser().setUrl(QUrl.fromUserInput(url))
        )
        dialog.exec()

    def open_downloads(self) -> None:
        popup = self._downloads_popup
        if popup is not None and popup.isVisible():
            popup.close()
            return
        popup = DownloadsPopup(self)
        self._downloads_popup = popup
        popup.destroyed.connect(lambda *_args: setattr(self, "_downloads_popup", None))
        popup.adjustSize()
        position = self.download_button.mapToGlobal(
            QPoint(self.download_button.width() - popup.width(), self.download_button.height() + 5)
        )
        screen = QApplication.screenAt(position)
        if screen is not None:
            available = screen.availableGeometry()
            position.setX(max(available.left(), min(
                position.x(), available.right() - popup.width()
            )))
            position.setY(max(available.top(), min(
                position.y(), available.bottom() - popup.height()
            )))
        popup.move(position)
        popup.show()
        popup.raise_()

    def open_full_downloads(self) -> None:
        DownloadsDialog(self.download_manager, self, self.theme_manager).exec()

    def show_browser_tools_menu(self) -> None:
        menu = QMenu(self)
        actions = (
            ("Search Tabs…", "Ctrl+Shift+A", self.open_tab_overview),
            ("Reader Mode", "F9", self.toggle_reader_mode),
            ("Browser Task Manager", "Shift+Esc", self.open_task_manager),
            ("Clear Browsing Data…", "Ctrl+Shift+Delete", self.open_clear_browsing_data),
            ("Export / Import…", "", self.open_backup_tools),
            ("Local Diagnostics…", "", self.open_diagnostics),
            ("Check for Updates…", "", self.open_update_checker),
        )
        for label, shortcut, callback in actions:
            action = menu.addAction(label)
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
        menu.addSeparator()
        install_app = menu.addAction("Install This Site as App…")
        install_app.triggered.connect(self.install_current_site_as_app)
        installed_menu = menu.addMenu("Installed Web Apps")
        installed = self.web_app_manager.installed()
        if not installed:
            empty = installed_menu.addAction("No installed web apps")
            empty.setEnabled(False)
        for app in installed:
            action = installed_menu.addAction(str(app.get("name") or "Web App"))
            action.setToolTip(str(app.get("url") or ""))
            action.triggered.connect(
                lambda _checked=False, app_id=str(app.get("id") or ""):
                self.web_app_manager.launch(app_id)
            )
        menu.popup(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height())))

    def open_tab_overview(self) -> None:
        TabOverviewDialog(self).exec()

    def open_task_manager(self) -> None:
        TaskManagerDialog(self).exec()

    def open_clear_browsing_data(self) -> None:
        self.open_settings("Clear Browsing Data")

    def open_backup_tools(self) -> None:
        BackupDialog(self).exec()

    def open_diagnostics(self) -> None:
        self.open_settings("Local Diagnostics")

    def open_update_checker(self) -> None:
        UpdateDialog(self).exec()

    def _maybe_check_for_updates(self) -> None:
        """Check the signed update channel at most once every two days."""
        if not bool(self.settings_manager.value("updates/automatic_check_enabled")):
            return
        manifest = str(
            self.settings_manager.value("updates/manifest_url") or ""
        ).strip()
        public_key = str(
            self.settings_manager.value("updates/ed25519_public_key") or ""
        ).strip()
        if not manifest or not public_key:
            return
        now = time.time()
        try:
            last_check = float(
                self.settings_manager.value("updates/last_check_epoch") or 0
            )
        except (TypeError, ValueError):
            last_check = 0
        interval = 2 * 24 * 60 * 60
        if now - last_check < interval:
            return
        # Record the attempt before starting so an unavailable network does not
        # cause every new window to retry and stall the update host.
        self.settings_manager.set_value("updates/last_check_epoch", int(now))
        self.settings_manager.sync()

        def worker() -> None:
            result = None
            error = ""
            try:
                result = UpdateChecker().check(manifest, public_key)
            except (ValueError, RuntimeError) as caught:
                error = str(caught)
            try:
                self.automatic_update_finished.emit(result, error)
            except RuntimeError:
                pass

        threading.Thread(
            target=worker, name="ZyvroUpdateCheck", daemon=True
        ).start()

    def _automatic_update_check_finished(
        self, result: object, error: str
    ) -> None:
        if error:
            self.settings_manager.set_value("updates/last_error", error)
            self.settings_manager.sync()
            return
        if not isinstance(result, dict) or not result.get("available"):
            return
        version = str(result.get("version") or "")
        if version == str(
            self.settings_manager.value("updates/last_notified_version") or ""
        ):
            return
        self.settings_manager.set_value("updates/last_notified_version", version)
        self.settings_manager.sync()
        message = QMessageBox(self)
        message.setWindowTitle("Zyvro Update Available")
        message.setIcon(QMessageBox.Icon.Information)
        message.setText(f"Zyvro {version} is available.")
        notes = str(result.get("notes") or "").strip()
        message.setInformativeText(
            notes or "Open the update checker to download and verify it."
        )
        view = message.addButton(
            "View Update", QMessageBox.ButtonRole.AcceptRole
        )
        message.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        message.exec()
        if message.clickedButton() is view:
            self.open_update_checker()

    def toggle_reader_mode(self) -> None:
        page = self.tabs.currentWidget()
        if not isinstance(page, BrowserPage):
            return
        browser = page.browser
        if bool(getattr(browser, "reader_mode", False)):
            original = getattr(browser, "reader_original_url", QUrl())
            browser.reader_mode = False
            page.reader_bar.hide()
            if isinstance(original, QUrl) and original.isValid():
                browser.setUrl(original)
            else:
                browser.reload()
            return
        url = browser.url()
        if url.scheme().lower() not in {"http", "https"}:
            QMessageBox.information(
                self, "Reader Mode", "Reader Mode is available on web articles."
            )
            return
        extraction = r"""
(() => {
  const source = document.querySelector('article') || document.querySelector('main') || document.body;
  if (!source) return null;
  const clone = source.cloneNode(true);
  clone.querySelectorAll('script,style,noscript,nav,footer,aside,form,button,iframe,canvas,video,audio').forEach(n => n.remove());
  const text = (clone.innerText || '').trim();
  if (text.length < 180) return null;
  return JSON.stringify({title: document.title || 'Reader View', html: clone.innerHTML});
})()
"""
        def show_reader(result) -> None:
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (TypeError, ValueError, json.JSONDecodeError):
                    result = None
            if not isinstance(result, dict) or not result.get("html"):
                QMessageBox.information(
                    self, "Reader Mode", "A readable article could not be detected on this page."
                )
                return
            browser.reader_original_url = QUrl(url)
            browser.reader_mode = True
            title = html.escape(str(result.get("title") or "Reader View"))
            article = str(result["html"])
            document = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>{title}</title><style>
:root{{--reader-font:19px;--reader-width:760px;--reader-spacing:1.72;--reader-bg:#f7f5ef;--reader-text:#262521}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--reader-bg);color:var(--reader-text);
font-family:Georgia,serif;font-size:var(--reader-font);line-height:var(--reader-spacing)}} article{{max-width:var(--reader-width);margin:0 auto;padding:54px 32px 90px}}
h1,h2,h3{{line-height:1.25}} img{{max-width:100%;height:auto}} a{{color:#356bb3}}
</style></head><body><article><h1>{title}</h1>{article}</article></body></html>"""
            browser.setHtml(document, url)
            page.reader_bar.show()
        browser.page().runJavaScript(extraction, show_reader)

    def open_bookmarks(self) -> None:
        dialog = BookmarksDialog(self.bookmark_manager, self, self.theme_manager)
        dialog.open_url.connect(
            lambda url: self.current_browser().setUrl(QUrl.fromUserInput(url))
        )
        dialog.exec()

    def toggle_current_bookmark(self) -> None:
        browser = self.current_browser()
        url = browser.url().toString()
        if not url or url == "about:blank":
            return
        if self.bookmark_manager.contains(url):
            self.bookmark_manager.remove_url(url)
        else:
            self.bookmark_manager.add(browser.title() or url, url)

    def _update_bookmark_button(self) -> None:
        if not hasattr(self, "bookmark_button") or self.tabs.count() == 0:
            return
        url = self.current_browser().url().toString()
        bookmarked = bool(url) and self.bookmark_manager.contains(url)
        self.bookmark_button.setIcon(
            self._icon("star-filled.svg")
            if bookmarked else self._themed_icon("star.svg")
        )
        self.bookmark_button.setToolTip(
            "Remove bookmark (Ctrl+D)" if bookmarked else "Bookmark this page (Ctrl+D)"
        )

    def _bookmarks_changed(self) -> None:
        self._refresh_bookmarks_bar()
        self._update_bookmark_button()

    def _refresh_bookmarks_bar(self) -> None:
        if not hasattr(self, "bookmarks_bar"):
            return
        self.bookmarks_bar.clear()
        for bookmark in self.bookmark_manager.entries()[:20]:
            action = QAction(bookmark["title"], self.bookmarks_bar)
            action.setToolTip(bookmark["url"])
            action.triggered.connect(
                lambda _checked=False, url=bookmark["url"]: self.current_browser().setUrl(
                    QUrl.fromUserInput(url)
                )
            )
            self.bookmarks_bar.addAction(action)

    def show_find_bar(self) -> None:
        page = self.tabs.currentWidget()
        if isinstance(page, BrowserPage):
            page.show_find_bar()

    ZOOM_LEVELS = [
        0.25,
        0.33,
        0.5,
        0.67,
        0.75,
        0.8,
        0.9,
        1.0,
        1.1,
        1.25,
        1.33,
        1.5,
        1.75,
        2.0,
        2.5,
        3.0,
        4.0,
        5.0,
    ]

    def _step_zoom(self, direction: int) -> None:
        browser = self.current_browser()
        current = browser.zoomFactor()
        if direction > 0:
            target = next((level for level in self.ZOOM_LEVELS if level > current + 0.001), 5.0)
        else:
            target = next(
                (level for level in reversed(self.ZOOM_LEVELS) if level < current - 0.001),
                0.25,
            )
        browser.setZoomFactor(target)
        self.browser_menu.update_zoom(target)

    def zoom_in(self) -> None:
        self._step_zoom(1)

    def zoom_out(self) -> None:
        self._step_zoom(-1)

    def zoom_reset(self) -> None:
        self.current_browser().setZoomFactor(1.0)
        self.browser_menu.update_zoom(1.0)

    def print_current_page(self) -> None:
        browser = self.current_browser()
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._active_printers.append(printer)

        def finished(_success: bool) -> None:
            try:
                browser.printFinished.disconnect(finished)
            except (RuntimeError, TypeError):
                pass
            if printer in self._active_printers:
                self._active_printers.remove(printer)

        browser.printFinished.connect(finished)
        browser.print(printer)

    def save_current_page(self) -> None:
        browser = self.current_browser()
        safe_title = "".join(
            character for character in (browser.title() or "webpage")
            if character not in '<>:"/\\|?*'
        ).strip() or "webpage"
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save Page As",
            f"{safe_title}.mhtml",
            "Web Archive (*.mhtml);;Complete Web Page (*.html);;Single HTML (*.html)",
        )
        if not file_path:
            return
        save_format = QWebEngineDownloadRequest.SavePageFormat.MimeHtmlSaveFormat
        if selected_filter.startswith("Complete"):
            save_format = QWebEngineDownloadRequest.SavePageFormat.CompleteHtmlSaveFormat
        elif selected_filter.startswith("Single"):
            save_format = QWebEngineDownloadRequest.SavePageFormat.SingleHtmlSaveFormat
        browser.page().save(file_path, save_format)

    def open_settings(self, initial_tab: str | None = None) -> None:
        dialog = SettingsDialog(
            self.settings_manager,
            self.history_manager,
            self.web_profile,
            self,
            self.theme_manager,
            self.vpn_manager,
            self.site_permission_manager,
            self.cookie_privacy_manager,
            self.privacy_timeline_manager,
            self.malware_scanner,
            self.security_data,
            initial_tab,
        )
        dialog.settings_applied.connect(self._apply_settings)
        dialog.exec()

    def _security_threat_detected(self, scan: dict) -> None:
        visible = [window for window in BrowserWindow._open_windows if window.isVisible()]
        active = QApplication.activeWindow()
        if visible and active in visible and active is not self:
            return
        if not self.malware_scanner.claim_alert(int(scan["id"])):
            return
        # Decisions now live in the downloads panel so the file remains blocked
        # until the user explicitly chooses Quarantine or Allow.
        self._update_download_button()
        popup = self._downloads_popup
        if popup is not None and popup.isVisible():
            popup.refresh()
        else:
            self.open_downloads()

    def _apply_settings(self) -> None:
        self.profile_manager.clear_tracker_session_override()
        self.profile_manager.refresh_privacy_settings()
        self.apply_theme()
        self.browser_menu.set_theme(
            self.theme_manager.active_theme(), self.incognito
        )
        self.bookmarks_bar.setVisible(
            bool(self.settings_manager.value("appearance/show_bookmarks_bar"))
        )
        self.web_profile.setDownloadPath(
            str(self.settings_manager.value("downloads/location"))
        )
        self._update_privacy_shield()
        self._update_adblock_button()
        self._update_cookie_button()

    def _index_of_browser(self, browser: BrowserView) -> int:
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, BrowserPage) and page.browser is browser:
                return index
        return -1

    @staticmethod
    def _tab_state(page: BrowserPage) -> dict:
        return {
            "url": (
                "zyvro:newtab"
                if getattr(page.browser, "_is_zyvro_new_tab", False)
                else page.browser.url().toString()
            ),
            "title": getattr(page, "tab_title", page.browser.title() or "New Tab"),
            "pinned": bool(getattr(page, "pinned", False)),
            "group": str(getattr(page, "tab_group", "")),
        }

    def session_state(self) -> dict:
        geometry = self.geometry()
        return {
            "geometry": [geometry.x(), geometry.y(), geometry.width(), geometry.height()],
            "current": self.tabs.currentIndex(),
            "groups": dict(self._tab_groups),
            "tabs": [
                self._tab_state(page)
                for index in range(self.tabs.count())
                if isinstance((page := self.tabs.widget(index)), BrowserPage)
            ],
        }

    def _schedule_session_save(self) -> None:
        if not self.session_managed or self._session_save_pending:
            return
        self._session_save_pending = True
        QTimer.singleShot(250, self._save_session_now)

    def _save_session_now(self) -> None:
        self._session_save_pending = False
        if BrowserWindow._nuking_data:
            return
        normal = [
            window for window in BrowserWindow._open_windows
            if window.session_managed
        ]
        if normal:
            self.session_manager.save_windows(
                [window.session_state() for window in normal]
            )

    def closeEvent(self, event) -> None:
        if self._fullscreen_browser is not None:
            self._leave_full_screen()
        active_downloads = self.download_manager.active_requests()
        if active_downloads:
            answer = QMessageBox.question(
                self,
                "Downloads in progress",
                "Cancel active downloads and exit the browser?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.download_manager.cancel_active()
        if (
            self.session_managed
            and not BrowserWindow._closing_all
            and not BrowserWindow._nuking_data
        ):
            other_normal = [
                window for window in BrowserWindow._open_windows
                if (
                    window is not self and window.session_managed
                )
            ]
            if other_normal:
                self.session_manager.remember_closed_window(self.session_state())
            self.session_manager.save_windows(
                [window.session_state() for window in other_normal]
                if other_normal else [self.session_state()]
            )
        # Close each per-tab DevTools dock before its inspected page is deleted.
        for index in range(self.tabs.count()):
            page = self.tabs.widget(index)
            if isinstance(page, BrowserPage):
                page.close_devtools()
                page.browser.stop()
        # Queue all page/view destruction before releasing the private profile.
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            if widget is not None:
                widget.deleteLater()
        if not BrowserWindow._nuking_data:
            self.settings_manager.sync()
        BrowserWindow._open_windows.discard(self)
        if (
            self.session_managed
            and bool(self.settings_manager.value("privacy/clear_cookies_on_exit"))
            and not any(
                window.session_managed
                for window in BrowserWindow._open_windows
            )
        ):
            self.profile_manager.normal_profile.cookieStore().deleteAllCookies()
        if self.incognito:
            profile = self.web_profile
            QTimer.singleShot(
                0, lambda: self.profile_manager.release_private_profile(profile)
            )
        super().closeEvent(event)


def main() -> int:
    smoke_test = "--smoke-test" in sys.argv
    app_id = ""
    launch_targets: list[str] = []
    if "--app-id" in sys.argv:
        index = sys.argv.index("--app-id")
        if index + 1 < len(sys.argv):
            app_id = sys.argv[index + 1]
    qt_arguments = [sys.argv[0]]
    skip_next = False
    for argument in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if argument == "--app-id":
            skip_next = True
            continue
        if argument == "--smoke-test":
            continue
        candidate = QUrl.fromUserInput(argument)
        if (
            candidate.scheme().lower() in {"http", "https", "file"}
            and ("://" in argument or Path(argument).suffix.lower() in {".htm", ".html"})
        ):
            launch_targets.append(argument)
            continue
        qt_arguments.append(argument)
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                f"Zyvro.WebApp.{app_id}" if app_id else "Zyvro.Browser"
            )
        except (AttributeError, OSError):
            pass
    app = QApplication(qt_arguments)
    # Keep the legacy QSettings/data identifiers for existing users, while
    # presenting the released product as Zyvro to Windows and Qt UI surfaces.
    app.setApplicationName("Python Browser")
    app.setApplicationDisplayName("Zyvro")
    app.setOrganizationName("Zyvro")
    app.setApplicationVersion(APP_VERSION)
    icon_name = "zyvro.ico" if os.name == "nt" else "zyvro-logo.png"
    app.setWindowIcon(
        QIcon(str(Path(__file__).resolve().parent / "assets" / icon_name))
    )
    profile_manager = BrowserProfileManager(app, app_profile_id=app_id)
    BrowserWindow._shared_profile_manager = profile_manager
    if smoke_test:
        engine = profile_manager.adblock_manager.rust_engine
        if not engine.available or not engine.should_block(
            "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",
            "pagead2.googlesyndication.com",
            "example.com",
            "script",
            "GET",
            "https://example.com/",
        ):
            return 3
    if app_id:
        definition = profile_manager.web_app_manager.get(app_id)
        if definition is None:
            QMessageBox.critical(
                None, "Web App Not Found",
                "This web app is no longer installed. You can remove its old shortcut.",
            )
            return 2
        app.setApplicationDisplayName(str(definition.get("name") or "Web App"))
        window = BrowserWindow(profile_manager=profile_manager, web_app=definition)
        window.show()
        if smoke_test:
            QTimer.singleShot(750, app.quit)
        return app.exec()
    previous_clean, saved_windows = profile_manager.session_manager.begin_run()
    startup = str(profile_manager.privacy_settings.value("general/startup"))
    restore = bool(saved_windows) and (startup == "continue" or not previous_clean)
    windows = []
    for state in (saved_windows if restore else [None]):
        window = BrowserWindow(profile_manager=profile_manager, restore_state=state)
        window.show()
        windows.append(window)
    if launch_targets:
        first = windows[0]
        first.current_browser().setUrl(QUrl.fromUserInput(launch_targets[0]))
        for target in launch_targets[1:]:
            first.add_tab(QUrl.fromUserInput(target), "Loading...")
    if restore and not previous_clean and not smoke_test:
        QTimer.singleShot(
            250,
            lambda: QMessageBox.information(
                windows[0], "Session Restored",
                "Zyvro recovered your tabs and windows after the previous "
                "session ended unexpectedly.",
            ),
        )
    app.aboutToQuit.connect(profile_manager.session_manager.mark_clean_shutdown)
    if smoke_test:
        QTimer.singleShot(750, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
