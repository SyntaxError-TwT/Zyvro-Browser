"""Offscreen integration checks for normal/private profile isolation."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import threading
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from qtpy.QtCore import QEventLoop, QStandardPaths, Qt, QTimer, QUrl
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QApplication
from qtpy.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from qtpy.QtNetwork import QNetworkProxy

from adblock_manager import AdBlockInterceptor
from browser_data import BrowserProfileManager, SettingsManager, TrackerBlocker
from browser_dialogs import SettingsDialog
from scratch_browser import BrowserPage, BrowserWindow, HOME_URL
from nuke_browser_data import allowed_target


class _PageHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/cosmetic-root"):
            body = (
                b"<!doctype html><html><head><title>Search</title></head>"
                b"<body><main id='results'>Search results survive</main>"
                b"<div class='advertisement'>Ad</div></body></html>"
            )
        else:
            body = b"<!doctype html><title>Isolation Test</title><p>local</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def log_message(self, _format: str, *_args) -> None:
        pass


class IncognitoIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        QStandardPaths.setTestModeEnabled(True)
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _PageHandler)
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever, daemon=True
        )
        cls.server_thread.start()
        cls.origin = QUrl(f"http://127.0.0.1:{cls.server.server_port}/")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        SettingsManager().save_values(
            {
                "privacy/vpn_mode": "off",
                "privacy/vpn_kill_switch": False,
            }
        )
        self.manager = BrowserProfileManager(self.app)
        self.windows: list[BrowserWindow] = []

    def tearDown(self) -> None:
        for window in reversed(self.windows):
            window.close()
        self._wait(100)
        self.manager.deleteLater()
        self._wait(50)

    @staticmethod
    def _wait(milliseconds: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def _load(self, browser, url: QUrl) -> None:
        loop = QEventLoop()
        result: list[bool] = []

        def finished(success: bool) -> None:
            if browser.url() != url:
                return
            result.append(success)
            loop.quit()

        browser.loadFinished.connect(finished)
        browser.setUrl(url)
        QTimer.singleShot(8000, loop.quit)
        loop.exec()
        browser.loadFinished.disconnect(finished)
        self.assertTrue(result and result[-1])

    def _javascript(self, browser, script: str):
        loop = QEventLoop()
        result: list[object] = []
        browser.page().runJavaScript(
            script, lambda value: (result.append(value), loop.quit())
        )
        QTimer.singleShot(5000, loop.quit)
        loop.exec()
        self.assertTrue(result, "JavaScript callback timed out")
        return result[0]

    def _window(self, private: bool) -> BrowserWindow:
        window = BrowserWindow(private, self.manager)
        self.windows.append(window)
        return window

    def test_webengine_fullscreen_requests_are_enabled_and_restored(self) -> None:
        window = self._window(False)
        browser = window.current_browser()
        self.assertTrue(
            browser.settings().testAttribute(
                QWebEngineSettings.WebAttribute.FullScreenSupportEnabled
            )
        )

        class Request:
            def __init__(self, enabled: bool) -> None:
                self.enabled = enabled
                self.accepted = False
                self.rejected = False

            def toggleOn(self) -> bool:
                return self.enabled

            def accept(self) -> None:
                self.accepted = True

            def reject(self) -> None:
                self.rejected = True

        enter = Request(True)
        window._full_screen_requested(browser, enter)
        self.assertTrue(enter.accepted)
        self.assertFalse(enter.rejected)
        self.assertIs(window._fullscreen_browser, browser)
        self.assertFalse(window.tabs.tabBar().isVisible())

        leave = Request(False)
        window._full_screen_requested(browser, leave)
        self.assertTrue(leave.accepted)
        self.assertIsNone(window._fullscreen_browser)

    def test_automatic_update_check_obeys_two_day_interval(self) -> None:
        window = self._window(False)
        previous = {
            key: window.settings_manager.value(key)
            for key in (
                "updates/automatic_check_enabled",
                "updates/manifest_url",
                "updates/ed25519_public_key",
                "updates/last_check_epoch",
            )
        }
        window.settings_manager.save_values(
            {
                "updates/automatic_check_enabled": True,
                "updates/manifest_url": "https://updates.example.test/manifest.json",
                "updates/ed25519_public_key": "test-key",
                "updates/last_check_epoch": int(__import__("time").time()),
            }
        )
        calls: list[tuple[str, str]] = []
        original = __import__("scratch_browser").UpdateChecker.check

        def checked(_checker, manifest: str, key: str) -> dict:
            calls.append((manifest, key))
            return {"available": False, "version": "0.4.0"}

        __import__("scratch_browser").UpdateChecker.check = checked
        try:
            window._maybe_check_for_updates()
            self._wait(50)
            self.assertEqual(calls, [])
            window.settings_manager.set_value("updates/last_check_epoch", 0)
            window._maybe_check_for_updates()
            self._wait(250)
            self.assertEqual(len(calls), 1)
        finally:
            __import__("scratch_browser").UpdateChecker.check = original
            window.settings_manager.save_values(previous)

    @staticmethod
    def _proxy_server(protocol: str):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        server.settimeout(0.2)
        stopped = threading.Event()

        def serve() -> None:
            while not stopped.is_set():
                try:
                    connection, _address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with connection:
                    connection.settimeout(1)
                    try:
                        request = connection.recv(512)
                        if protocol == "socks5" and request.startswith(b"\x05"):
                            connection.sendall(b"\x05\x00")
                        elif protocol == "http" and request.startswith(b"CONNECT "):
                            connection.sendall(
                                b"HTTP/1.1 200 Connection Established\r\n\r\n"
                            )
                    except OSError:
                        pass

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()

        def stop() -> None:
            stopped.set()
            server.close()
            thread.join(timeout=1)

        return server.getsockname()[1], stop

    def test_profiles_tabs_popups_history_and_devtools(self) -> None:
        normal = self._window(False)
        private = self._window(True)

        self.assertFalse(normal.web_profile.isOffTheRecord())
        self.assertTrue(private.web_profile.isOffTheRecord())
        self.assertEqual(
            private.web_profile.httpCacheType(),
            QWebEngineProfile.HttpCacheType.MemoryHttpCache,
        )
        self.assertEqual(
            private.web_profile.persistentCookiesPolicy(),
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies,
        )
        self.assertEqual(normal.home_url().toString(), HOME_URL)

        second = private.add_new_tab()
        self.assertIs(second.web_profile, private.web_profile)
        before_popup = private.tabs.count()
        popup = second.createWindow(
            second.page().WebWindowType.WebBrowserTab
        )
        self.assertEqual(private.tabs.count(), before_popup + 1)
        self.assertIs(popup.web_profile, private.web_profile)

        private.toggle_devtools()
        tab = private.tabs.currentWidget()
        self.assertIsInstance(tab, BrowserPage)
        self.assertIsNotNone(tab.devtools_dock)
        self.assertTrue(tab.devtools_dock.devtools_view.page().profile().isOffTheRecord())
        tab.close_devtools()

        before = private.history_manager.entries()
        private.history_manager.add_visit(
            "Private Test", "https://private.invalid/", private=True
        )
        self.assertEqual(private.history_manager.entries(), before)
        self.assertFalse(private.download_manager.persist_history)
        self.assertTrue(normal.download_manager.persist_history)

    def test_address_click_selects_all_and_security_state_tracks_url(self) -> None:
        normal = self._window(False)
        self.assertTrue(normal.download_action.isVisible())
        self.assertIn("Downloads", normal.download_button.toolTip())
        menu_names = list(normal.browser_menu.action_rows)
        self.assertIn("nuke_data", menu_names)
        self.assertLess(menu_names.index("nuke_data"), menu_names.index("exit"))
        self.assertFalse(normal._menu_icons()["nuke_data"].isNull())
        self.assertTrue(
            allowed_target(Path(os.environ["APPDATA"]) / "webengine")
        )
        self.assertFalse(allowed_target(Path.home()))
        normal.address_bar.setText("https://www.google.com/search?q=browser")
        normal.address_bar.show()
        QTest.mouseClick(normal.address_bar, Qt.MouseButton.LeftButton)
        self._wait(20)
        self.assertEqual(normal.address_bar.selectedText(), normal.address_bar.text())

        normal._update_security_button(QUrl("https://www.google.com"))
        self.assertIn("certificate", normal.security_action.toolTip().lower())
        normal._update_security_button(QUrl("http://example.com"))
        self.assertIn("not secure", normal.security_action.toolTip().lower())

    def test_zyvro_new_tab_branding_search_and_private_title(self) -> None:
        normal = self._window(False)
        browser = normal.add_new_tab()
        self._wait(800)
        self.assertEqual(normal.home_url().toString(), HOME_URL)
        self.assertEqual(normal.windowTitle(), "New Tab - Zyvro")
        self.assertEqual(normal.address_bar.text(), "")
        self.assertEqual(
            self._javascript(
                browser,
                "[document.querySelector('h1').textContent,"
                "[...document.querySelectorAll('h2')].map(x=>x.textContent).join('|'),"
                "document.querySelector('form').action].join('::')",
            ),
            "Zyvro Browser::Recent|Suggested::https://www.google.com/search",
        )
        page = normal._page_for_browser(browser)
        self.assertEqual(normal._tab_state(page)["url"], "zyvro:newtab")

        private = self._window(True)
        self._wait(500)
        self.assertEqual(private.windowTitle(), "New Tab - Zyvro Private")
        self.assertEqual(
            self._javascript(
                private.current_browser(),
                "[...document.querySelectorAll('h2')].map(x=>x.textContent).join('|')",
            ),
            "Recent|Suggested",
        )

    def test_cookie_isolation_and_private_session_cleanup(self) -> None:
        normal = self._window(False)
        private = self._window(True)
        normal_browser = normal.current_browser()
        private_browser = private.current_browser()
        self._load(normal_browser, self.origin)
        self._load(private_browser, self.origin)
        self.assertTrue(
            self._javascript(
                normal_browser,
                "window.__pythonBrowserCaptureInstalled === true",
            )
        )

        normal_cookie = f"normal_{uuid.uuid4().hex}"
        private_cookie = f"private_{uuid.uuid4().hex}"
        self._javascript(
            normal_browser,
            f"document.cookie='{normal_cookie}=visible; path=/'; document.cookie",
        )
        private_values = self._javascript(private_browser, "document.cookie")
        self.assertNotIn(normal_cookie, private_values)

        private_values = self._javascript(
            private_browser,
            f"document.cookie='{private_cookie}=temporary; path=/'; document.cookie",
        )
        # Cookie-store commits can complete one event-loop turn after the
        # JavaScript setter on busy/offscreen Chromium test runs.
        for _attempt in range(10):
            if private_cookie in private_values:
                break
            self._wait(50)
            private_values = self._javascript(private_browser, "document.cookie")
        self.assertIn(private_cookie, private_values)

        private.close()
        self.windows.remove(private)
        self._wait(200)
        replacement = self._window(True)
        self._load(replacement.current_browser(), self.origin)
        replacement_values = self._javascript(
            replacement.current_browser(), "document.cookie"
        )
        self.assertNotIn(private_cookie, replacement_values)

        normal_values = self._javascript(normal_browser, "document.cookie")
        self.assertIn(normal_cookie, normal_values)

    def test_webrtc_setting_tracker_interception_and_local_report(self) -> None:
        attribute = QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly
        self.assertTrue(
            self.manager.normal_profile.settings().testAttribute(attribute)
        )
        self.manager.privacy_settings.save_values(
            {"privacy/webrtc_leak_protection": False}
        )
        self.manager.refresh_privacy_settings()
        self.assertFalse(
            self.manager.normal_profile.settings().testAttribute(attribute)
        )
        self.manager.privacy_settings.save_values(
            {"privacy/webrtc_leak_protection": True}
        )
        self.manager.refresh_privacy_settings()

        self.manager.privacy_settings.save_values(
            {
                "privacy/tracker_protection_enabled": False,
                "privacy/tracker_choice_remembered": False,
            }
        )
        self.manager.apply_tracker_prompt_choice(True, remember=False)
        self.assertTrue(self.manager.tracker_protection_enabled())
        self.assertFalse(
            bool(
                self.manager.privacy_settings.value(
                    "privacy/tracker_protection_enabled"
                )
            )
        )
        self.manager.clear_tracker_session_override()
        self.manager.refresh_privacy_settings()
        self.assertFalse(self.manager.tracker_protection_enabled())

        class Request:
            def __init__(self, request: str, site: str) -> None:
                self._request = QUrl(request)
                self._site = QUrl(site)
                self.blocked = False

            def requestUrl(self):
                return self._request

            def firstPartyUrl(self):
                return self._site

            def initiator(self):
                return self._site

            def block(self, blocked: bool) -> None:
                self.blocked = blocked

        blocker = TrackerBlocker(parent=self.manager)
        blocker.blocked.connect(self.manager.privacy_manager.record_block)
        blocker.configure(True, set())
        request = Request(
            "https://www.google-analytics.com/collect",
            "https://example.com/",
        )
        before = self.manager.privacy_manager.count_total()
        blocker.interceptRequest(request)
        self.assertTrue(request.blocked)
        self.assertEqual(self.manager.privacy_manager.count_total(), before + 1)

        blocker.configure(True, {"example.com"})
        excepted = Request(
            "https://www.google-analytics.com/collect",
            "https://example.com/",
        )
        blocker.interceptRequest(excepted)
        self.assertFalse(excepted.blocked)

    def test_media_capture_state_drives_real_toolbar_indicator(self) -> None:
        normal = self._window(False)
        browser = normal.current_browser()
        page = browser.page()
        message = (
            f"__PY_BROWSER_MEDIA__{page._media_token}:"
            '{"audio":true,"video":true}'
        )
        page.javaScriptConsoleMessage(
            QWebEnginePage.JavaScriptConsoleMessageLevel.InfoMessageLevel,
            message,
            1,
            "test",
        )
        self.assertTrue(browser.media_audio_active)
        self.assertTrue(browser.media_video_active)
        self.assertFalse(normal.media_indicator.isHidden())
        self.assertIn("Camera and microphone", normal.media_indicator.toolTip())
        browser.stop_and_block_media()
        self.assertFalse(browser.media_audio_active)
        self.assertFalse(browser.media_video_active)
        self.assertTrue(normal.media_indicator.isHidden())

    def test_profile_interceptor_blocks_an_integrated_webengine_request(self) -> None:
        self.manager.privacy_settings.save_values(
            {
                "privacy/tracker_protection_enabled": True,
                "privacy/tracker_choice_remembered": True,
                "privacy/tracker_site_exceptions": [],
            }
        )
        self.manager.refresh_privacy_settings()
        normal = self._window(False)
        before = self.manager.privacy_manager.count_for_site("example.com")
        normal.current_browser().setHtml(
            '<img src="https://www.google-analytics.com/collect?integration=1">',
            QUrl("https://example.com/"),
        )
        self._wait(500)
        after = self.manager.privacy_manager.count_for_site("example.com")
        self.assertGreater(after, before)

    def test_adblock_rules_exceptions_stats_and_incognito_memory(self) -> None:
        class Request:
            def __init__(self, request: str, site: str) -> None:
                self._request = QUrl(request)
                self._site = QUrl(site)
                self.blocked = False

            def requestUrl(self):
                return self._request

            def firstPartyUrl(self):
                return self._site

            def initiator(self):
                return self._site

            def resourceType(self):
                return None

            def block(self, blocked: bool) -> None:
                self.blocked = blocked

        blocker = AdBlockInterceptor(parent=self.manager)
        blocker.configure(True, set())
        obvious_ad = Request(
            "https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",
            "https://example.com/article",
        )
        blocker.interceptRequest(obvious_ad)
        self.assertTrue(obvious_ad.blocked)

        ordinary = Request(
            "https://example.com/downloads/header-image.png",
            "https://example.com/article",
        )
        blocker.interceptRequest(ordinary)
        self.assertFalse(ordinary.blocked)

        legitimate_banner = Request(
            "https://cdn.example-assets.com/banner/google-logo.png",
            "https://www.google.com/",
        )
        blocker.interceptRequest(legitimate_banner)
        self.assertFalse(legitimate_banner.blocked)

        blocker.configure(True, {"example.com"})
        allowed = Request(
            "https://doubleclick.net/ads/banner.js",
            "https://example.com/article",
        )
        blocker.interceptRequest(allowed)
        self.assertFalse(allowed.blocked)

        manager = self.manager.adblock_manager
        before = manager.count_total()
        manager.record("example.com", "unit-test", 2)
        self.assertEqual(manager.count_total(), before + 2)
        private_session = "private-unit-test"
        manager.record("private.example", "unit-test", 3, private_session)
        self.assertEqual(manager.count_total(private_session), 3)
        self.assertEqual(manager.count_total(), before + 2)

    def test_cosmetic_ad_filtering_toolbar_count_and_site_exception(self) -> None:
        self.manager.privacy_settings.save_values(
            {
                "privacy/adblock_enabled": True,
                "privacy/adblock_allowed_sites": [],
            }
        )
        self.manager.refresh_privacy_settings()
        normal = self._window(False)
        browser = normal.current_browser()
        browser.setHtml(
            '<main>Article</main><div id="test-ad" class="advertisement">Ad</div>',
            QUrl("https://example.com/"),
        )
        self._wait(550)
        self.assertEqual(
            self._javascript(
                browser,
                "getComputedStyle(document.getElementById('test-ad')).display",
            ),
            "none",
        )
        self.assertGreaterEqual(browser.ads_blocked_current, 1)
        self.assertIn("blocked on this page", normal.adblock_button.toolTip())
        self.assertEqual(normal.navigation_bar.height(), 42)
        self.assertEqual(normal.adblock_button.iconSize().width(), 20)

        self._javascript(
            browser,
            "const n=document.createElement('div'); n.id='dynamic-ad'; "
            "n.className='sponsored-ad'; document.body.appendChild(n);",
        )
        self._wait(150)
        self.assertEqual(
            self._javascript(
                browser,
                "getComputedStyle(document.getElementById('dynamic-ad')).display",
            ),
            "none",
        )

        self._javascript(
            browser,
            "const interstitial=document.createElement('div'); "
            "interstitial.id='interads'; document.body.appendChild(interstitial); "
            "const warning=document.createElement('div'); warning.id='notice-modal'; "
            "warning.setAttribute('role','dialog'); "
            "warning.textContent='Adblocker detected! Please disable your ad blocker.'; "
            "document.body.appendChild(warning);",
        )
        self._wait(150)
        self.assertEqual(
            self._javascript(
                browser,
                "getComputedStyle(document.getElementById('interads')).display",
            ),
            "none",
        )
        self.assertEqual(
            self._javascript(
                browser,
                "getComputedStyle(document.getElementById('notice-modal')).display",
            ),
            "none",
        )

        self._javascript(
            browser,
            "const region=document.createElement('section'); region.id='ad-region'; "
            "region.innerHTML=`<div><div class='code-block'><ins class='adsbygoogle' "
            "data-ad-client='test'></ins></div><p>Advertisement</p></div>"
            "<div><h5>Banner Ads</h5><div>Do you see advertisements around this box?</div>"
            "<div class='code-block'><ins class='adsbygoogle' data-ad-slot='2'></ins></div></div>`; "
            "document.body.appendChild(region);",
        )
        self._wait(150)
        self.assertEqual(
            self._javascript(
                browser,
                "getComputedStyle(document.getElementById('ad-region')).display",
            ),
            "none",
        )

        normal.set_current_site_adblocking(False)
        self._wait(100)
        self.assertIn(
            "example.com",
            self.manager.adblock_manager.allowed_sites(),
        )
        self.assertFalse(self.manager.adblock_manager.site_enabled("example.com"))

        dialog = SettingsDialog(
            normal.settings_manager,
            normal.history_manager,
            normal.web_profile,
            normal,
            normal.theme_manager,
        )
        self.assertTrue(dialog.adblock_enabled.isChecked())
        self.assertEqual(dialog.adblock_allowed_sites.count(), 1)
        dialog.adblock_allowed_sites.setCurrentRow(0)
        dialog._remove_allowed_adblock_site()
        self.assertEqual(dialog.adblock_allowed_sites.count(), 0)

    def test_document_start_cosmetic_css_preserves_the_page_root(self) -> None:
        """A document-start stylesheet must never become the HTML root node."""
        self.manager.privacy_settings.save_values(
            {
                "privacy/adblock_enabled": True,
                "privacy/adblock_allowed_sites": [],
            }
        )
        self.manager.refresh_privacy_settings()
        normal = self._window(False)
        browser = normal.current_browser()
        manager = self.manager.adblock_manager
        original_resources = manager.rust_engine.cosmetic_resources
        manager.rust_engine.cosmetic_resources = lambda _url: {
            "hide_selectors": [".advertisement"],
            "exceptions": [],
            "injected_script": "",
        }
        try:
            url = QUrl(self.origin)
            url.setPath("/cosmetic-root")
            self._load(browser, url)
            self.assertEqual(
                self._javascript(browser, "document.documentElement.tagName"),
                "HTML",
            )
            self.assertEqual(
                self._javascript(
                    browser, "document.getElementById('results').textContent"
                ),
                "Search results survive",
            )
            self.assertTrue(
                self._javascript(
                    browser,
                    "Boolean(document.getElementById('zyvro-adblock-rust-site'))",
                )
            )
        finally:
            manager.rust_engine.cosmetic_resources = original_resources

    def test_real_tor_custom_proxy_kill_switch_and_vpn_ui(self) -> None:
        vpn = self.manager.vpn_proxy_manager
        tor_port, stop_tor = self._proxy_server("socks5")
        self.manager.privacy_settings.save_values(
            {
                "privacy/tor_host": "127.0.0.1",
                "privacy/tor_port": tor_port,
            }
        )
        vpn.set_kill_switch(True)
        self.assertTrue(vpn.connect_mode("tor"))
        self.assertEqual(vpn.state(), "connected")
        self.assertEqual(vpn.detail(), "Connected through Tor")
        self.assertEqual(
            QNetworkProxy.applicationProxy().type(),
            QNetworkProxy.ProxyType.Socks5Proxy,
        )
        self.assertEqual(QNetworkProxy.applicationProxy().port(), tor_port)

        normal = self._window(False)
        private = self._window(True)
        self.assertIs(normal.vpn_manager, private.vpn_manager)
        self.assertEqual(normal.vpn_button.iconSize().width(), 18)
        normal.show_vpn_popup()
        self.assertEqual(normal._vpn_popup.mode_combo.count(), 4)
        normal._vpn_popup.close()

        stop_tor()
        vpn._poll_connection()
        self._wait(1000)
        self.assertEqual(vpn.state(), "error")
        self.assertEqual(vpn.detail(), "Private connection lost")
        self.assertTrue(vpn.should_block_network())

        class NetworkRequest:
            def __init__(self) -> None:
                self.blocked = False

            def requestUrl(self):
                return QUrl("https://example.com/")

            def block(self, blocked: bool) -> None:
                self.blocked = blocked

        request = NetworkRequest()
        self.manager._request_interceptors[id(self.manager.normal_profile)].interceptRequest(request)
        self.assertTrue(request.blocked)

        http_port, stop_http = self._proxy_server("http")
        self.manager.privacy_settings.save_values(
            {
                "privacy/proxy_type": "http",
                "privacy/proxy_host": "127.0.0.1",
                "privacy/proxy_port": http_port,
                "privacy/proxy_username": "",
            }
        )
        vpn.save_server_profile(
            vpn.selected_country(), "http", "127.0.0.1", http_port
        )
        # The compact popup must support first-class custom configuration and
        # connection; it must not be only a link to the Settings dialog.
        normal.show_vpn_popup()
        popup = normal._vpn_popup
        popup._select_mode("custom")
        popup._load_custom_profile(popup.location.currentText())
        self.assertEqual(popup.proxy_host.text(), "127.0.0.1")
        self.assertEqual(popup.proxy_port.value(), http_port)
        self.assertEqual(popup.connection_button.text(), "Turn On Private Network")
        popup._toggle_connection()
        self.assertEqual(vpn.state(), "connected")
        self.assertEqual(
            QNetworkProxy.applicationProxy().type(),
            QNetworkProxy.ProxyType.HttpProxy,
        )
        popup.close()
        stop_http()

        dialog = SettingsDialog(
            normal.settings_manager,
            normal.history_manager,
            normal.web_profile,
            normal,
            normal.theme_manager,
            vpn,
        )
        dialog.vpn_mode.setCurrentIndex(dialog.vpn_mode.findData("off"))
        dialog.vpn_kill_switch.setChecked(False)
        dialog.proxy_password.setText("session-only-secret")
        dialog.save()
        self.assertFalse(
            self.manager.privacy_settings._settings.contains(
                "privacy/proxy_password"
            )
        )
        self.assertEqual(vpn.mode(), "off")
        self.assertFalse(vpn.should_block_network())

    def test_all_themes_preview_persist_and_keep_incognito_dark(self) -> None:
        normal = self._window(False)
        private = self._window(True)
        theme_manager = normal.theme_manager
        original = str(normal.settings_manager.value("appearance/theme"))

        theme_manager.commit("dark")
        dark_style = normal.styleSheet()
        self.assertIn("#171a21", dark_style)

        theme_manager.preview("light")
        self.assertIn("#f3f5f8", normal.styleSheet())
        self.assertIn("#14121a", private.styleSheet())

        theme_manager.preview("neon")
        self.assertIn("#55d6d0", normal.styleSheet())
        self.assertIn("#14121a", private.styleSheet())

        theme_manager.preview("rainbow")
        self.assertIn("qlineargradient", normal.styleSheet())
        self.assertEqual(
            normal.browser_menu.gradient_separator.colors,
            ("#9567e8", "#5298ef", "#e06fb5"),
        )

        dialog = SettingsDialog(
            normal.settings_manager,
            normal.history_manager,
            normal.web_profile,
            normal,
            theme_manager,
        )
        self.assertEqual(
            [dialog.theme.itemText(i) for i in range(dialog.theme.count())],
            ["System", "Light", "Dark", "Neon", "Rainbow"],
        )
        dialog.theme.setCurrentText("Neon")
        self.assertEqual(theme_manager.active_theme(), "neon")
        dialog.save()
        self.assertEqual(
            str(normal.settings_manager.value("appearance/theme")), "neon"
        )

        normal.settings_manager.save_values({"appearance/theme": original})
        theme_manager.commit(original)

    def test_session_restore_recently_closed_pinned_tabs_and_groups(self) -> None:
        normal = self._window(False)
        normal.settings_manager.save_values({"session/recently_closed_json": "[]"})
        browser = normal.add_tab(QUrl("https://example.com/session-test"), "Session Test")
        page_index = normal._index_of_browser(browser)
        normal._tab_groups["Work"] = "#5f8fe8"
        normal.set_tab_group(page_index, "Work")
        normal.set_tab_pinned(normal._index_of_browser(browser), True)

        state = normal.session_state()
        saved = next(tab for tab in state["tabs"] if "session-test" in tab["url"])
        self.assertTrue(saved["pinned"])
        self.assertEqual(saved["group"], "Work")

        restored = BrowserWindow(False, self.manager, restore_state=state)
        self.windows.append(restored)
        restored_page = next(
            restored.tabs.widget(index)
            for index in range(restored.tabs.count())
            if "session-test" in restored.tabs.widget(index).browser.url().toString()
        )
        self.assertTrue(restored_page.pinned)
        self.assertEqual(restored_page.tab_group, "Work")

        normal.close_tab(normal._index_of_browser(browser))
        self.assertTrue(normal.session_manager.recently_closed())
        before = normal.tabs.count()
        normal.restore_recently_closed_tab()
        self.assertEqual(normal.tabs.count(), before + 1)

        dialog = SettingsDialog(
            normal.settings_manager, normal.history_manager,
            normal.web_profile, normal, normal.theme_manager,
        )
        self.assertGreaterEqual(dialog.startup.findData("continue"), 0)
        dialog.reject()


if __name__ == "__main__":
    unittest.main(verbosity=2)
