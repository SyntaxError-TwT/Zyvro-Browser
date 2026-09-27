"""Persistent settings, history, bookmarks, and Qt WebEngine downloads."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from qtpy.QtCore import QObject, QSettings, QStandardPaths, QTimer, QUrl, Signal
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import QFileDialog, QMessageBox, QWidget
from qtpy.QtWebEngineCore import QWebEngineDownloadRequest, QWebEngineProfile
from qtpy.QtWebEngineCore import (
    QWebEngineSettings,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from adblock_manager import AdBlockManager, CombinedRequestInterceptor
from site_privacy_manager import CookiePrivacyManager, SitePermissionManager
from privacy_timeline import PrivacyTimelineManager
from vpn_proxy_manager import VpnProxyManager
from security_data import SecurityData
from malware_scanner import MalwareScanner
from session_manager import SessionManager
from navigation_security import NavigationSecurityManager
from web_app_manager import WebAppManager


APP_NAME = "Python Browser"
ORGANIZATION_NAME = "Local Browser"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def app_data_directory() -> Path:
    location = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    path = Path(location) if location else Path(__file__).resolve().parent / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


class SettingsManager(QObject):
    """Central QSettings-backed browser preferences."""

    changed = Signal()

    DEFAULTS = {
        "general/homepage": "https://www.google.com",
        "general/startup": "new_tab",
        "branding/zyvro_startup_migrated": False,
        "session/windows_json": "[]",
        "session/recently_closed_json": "[]",
        "session/recently_closed_windows_json": "[]",
        "session/previous_sessions_json": "[]",
        "session/clean_shutdown": True,
        "appearance/theme": "dark",
        "appearance/show_bookmarks_bar": False,
        "appearance/default_zoom": 100,
        "search/engine": "Google",
        "downloads/location": QStandardPaths.writableLocation(
            QStandardPaths.DownloadLocation
        ),
        "downloads/ask_each_time": True,
        "privacy/webrtc_leak_protection": True,
        "privacy/tracker_protection_enabled": False,
        "privacy/tracker_choice_remembered": False,
        "privacy/tracker_site_exceptions": [],
        "privacy/adblock_enabled": True,
        "privacy/adblock_allowed_sites": [],
        "privacy/vpn_mode": "off",
        "privacy/tor_host": "127.0.0.1",
        "privacy/tor_port": 9050,
        "privacy/proxy_type": "socks5",
        "privacy/proxy_host": "",
        "privacy/proxy_port": 1080,
        "privacy/proxy_username": "",
        "privacy/vpn_kill_switch": False,
        "privacy/block_third_party_cookies": False,
        "privacy/third_party_cookie_prompted": False,
        "privacy/clear_cookies_on_exit": False,
        "privacy/third_party_cookie_exceptions": [],
        "privacy/vpn_country": "United States",
        "privacy/vpn_server_profiles_json": "{}",
        "developer/devtools_behavior": "dock_right",
        "performance/sleeping_tabs_enabled": True,
        "performance/sleeping_tabs_minutes": 15,
        "updates/manifest_url": "",
        "security/virustotal_tier": "public",
        "security/manual_download_decisions": True,
        "security/https_only_enabled": False,
        "security/malicious_site_protection": True,
        "updates/ed25519_public_key": "",
    }

    SEARCH_TEMPLATES = {
        "Google": "https://www.google.com/search?q={query}",
        "Bing": "https://www.bing.com/search?q={query}",
        "DuckDuckGo": "https://duckduckgo.com/?q={query}",
    }

    def __init__(self) -> None:
        super().__init__()
        self._settings = QSettings(ORGANIZATION_NAME, APP_NAME)
        # Existing installations used Google/homepage startup. Apply the new
        # Zyvro new-tab startup once, while leaving the Settings choice usable
        # if the user changes it later.
        migrated = self._settings.value(
            "branding/zyvro_startup_migrated", False
        )
        if str(migrated).lower() not in {"1", "true", "yes", "on"}:
            self._settings.setValue("general/startup", "new_tab")
            self._settings.setValue("branding/zyvro_startup_migrated", True)
            self._settings.sync()

    def value(self, key: str):
        default = self.DEFAULTS.get(key)
        value = self._settings.value(key, default)
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if isinstance(default, int):
            return int(value)
        return value

    def set_value(self, key: str, value) -> None:
        self._settings.setValue(key, value)

    def save_values(self, values: dict[str, object]) -> None:
        for key, value in values.items():
            self._settings.setValue(key, value)
        self._settings.sync()
        self.changed.emit()

    def sync(self) -> None:
        self._settings.sync()

    def clear_all(self) -> None:
        """Remove every Python Browser preference from local QSettings."""
        self._settings.clear()
        self._settings.sync()
        self.changed.emit()

    def homepage(self) -> str:
        return str(self.value("general/homepage")).strip() or self.DEFAULTS[
            "general/homepage"
        ]

    def default_zoom_factor(self) -> float:
        return max(25, min(500, int(self.value("appearance/default_zoom")))) / 100

    def search_url(self, query_text: str) -> QUrl:
        engine = str(self.value("search/engine"))
        template = self.SEARCH_TEMPLATES.get(engine, self.SEARCH_TEMPLATES["Google"])
        encoded = QUrl.toPercentEncoding(query_text).data().decode("ascii")
        return QUrl(template.format(query=encoded))


class BrowserProfileManager(QObject):
    """Own the persistent normal profile and disposable private profiles."""

    privacy_configuration_changed = Signal()
    NORMAL_PROFILE_NAME = "PythonBrowserNormal"

    def __init__(
        self, parent: QObject | None = None, app_profile_id: str = ""
    ) -> None:
        super().__init__(parent)
        app_profile_id = "".join(
            character for character in str(app_profile_id)
            if character.isalnum() or character in {"-", "_"}
        )[:64]
        webengine_root = app_data_directory() / "webengine"
        if app_profile_id:
            webengine_root = webengine_root / "apps" / app_profile_id
        storage_path = webengine_root / "storage"
        cache_path = webengine_root / "cache"
        storage_path.mkdir(parents=True, exist_ok=True)
        cache_path.mkdir(parents=True, exist_ok=True)

        # A named Qt 6 profile is persistent. The Qt 6 default profile is
        # off-the-record, so normal pages must also be created explicitly with
        # this profile if cookies, cache, and local storage are to survive.
        profile_name = (
            f"PythonBrowserApp_{app_profile_id}"
            if app_profile_id else self.NORMAL_PROFILE_NAME
        )
        self.normal_profile = QWebEngineProfile(profile_name, self)
        self.normal_profile.setPersistentStoragePath(str(storage_path))
        self.normal_profile.setCachePath(str(cache_path))
        self.normal_profile.setHttpCacheType(
            QWebEngineProfile.HttpCacheType.DiskHttpCache
        )
        self.normal_profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.AllowPersistentCookies
        )
        permissions = getattr(QWebEngineProfile, "PersistentPermissionsPolicy", None)
        if permissions is not None:
            # Our per-site manager owns persistence so "Allow Once" remains
            # temporary instead of being silently remembered by Chromium.
            self.normal_profile.setPersistentPermissionsPolicy(permissions.AskEveryTime)

        self.privacy_settings = SettingsManager()
        self.session_manager = SessionManager(self.privacy_settings, self)
        self.web_app_manager = WebAppManager(
            app_data_directory(), Path(__file__).resolve().parent, self
        )
        self.security_data = SecurityData(
            app_data_directory() / "browser_data.sqlite3", self
        )
        self.navigation_security = NavigationSecurityManager(
            self.privacy_settings,
            app_data_directory() / "browser_data.sqlite3",
            self,
        )
        self.malware_scanner = MalwareScanner(
            self.security_data,
            self.privacy_settings,
            app_data_directory() / "quarantine",
            self,
        )
        self.privacy_manager = PrivacyManager()
        self.privacy_timeline_manager = PrivacyTimelineManager(
            app_data_directory() / "browser_data.sqlite3", self
        )
        self.site_permission_manager = SitePermissionManager(
            app_data_directory() / "browser_data.sqlite3", self
        )
        self.cookie_privacy_manager = CookiePrivacyManager(
            self.privacy_settings, self
        )
        self.vpn_proxy_manager = VpnProxyManager(self.privacy_settings, self)
        self.adblock_manager = AdBlockManager(
            self.privacy_settings,
            app_data_directory() / "browser_data.sqlite3",
            self,
        )
        self.adblock_manager.blocked.connect(self._record_ad_timeline)
        self.cookie_privacy_manager.third_party_blocked.connect(
            self._record_cookie_timeline
        )
        self._timeline_vpn_state = (
            self.vpn_proxy_manager.mode(), self.vpn_proxy_manager.state()
        )
        self.vpn_proxy_manager.state_changed.connect(
            self._record_vpn_timeline
        )
        self._tracker_session_override: bool | None = None
        self._private_profiles: set[QWebEngineProfile] = set()
        self._download_managers: dict[int, DownloadManager] = {}
        self._tracker_interceptors: dict[int, TrackerBlocker] = {}
        self._request_interceptors: dict[int, CombinedRequestInterceptor] = {}
        self._install_privacy_tools(self.normal_profile)

    def create_private_profile(self) -> QWebEngineProfile:
        # An unnamed profile is Qt WebEngine's real off-the-record profile.
        profile = QWebEngineProfile(self)
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies
        )
        permissions = getattr(QWebEngineProfile, "PersistentPermissionsPolicy", None)
        if permissions is not None:
            profile.setPersistentPermissionsPolicy(permissions.AskEveryTime)
        self._private_profiles.add(profile)
        self._install_privacy_tools(profile, private_session=str(id(profile)))
        return profile

    def _install_privacy_tools(
        self, profile: QWebEngineProfile, private_session: str = ""
    ) -> None:
        interceptor = TrackerBlocker(private_session=private_session, parent=self)
        interceptor.blocked.connect(self.privacy_manager.record_block)
        interceptor.blocked.connect(self._record_tracker_timeline)
        self._tracker_interceptors[id(profile)] = interceptor
        adblock = self.adblock_manager.create_interceptor(
            profile, private_session
        )
        combined = CombinedRequestInterceptor(
            adblock, interceptor, self.vpn_proxy_manager, self
        )
        self._request_interceptors[id(profile)] = combined
        profile.setUrlRequestInterceptor(combined)
        self.site_permission_manager.install_profile(profile)
        self.cookie_privacy_manager.install_profile(profile)
        profile._python_browser_navigation_security = self.navigation_security
        self._apply_webrtc_setting(profile)
        self._configure_interceptor(interceptor)

    def _record_tracker_timeline(
        self, site: str, _tracker: str, private_session: str
    ) -> None:
        if not private_session:
            self.privacy_timeline_manager.record(
                "trackers", "trackers_blocked", "Tracker blocked", site
            )

    def _record_ad_timeline(
        self, site: str, amount: int, private_session: str, _source: str
    ) -> None:
        if not private_session:
            self.privacy_timeline_manager.record(
                "ads", "ads_blocked", "Ad blocked", site, amount
            )

    def _record_cookie_timeline(self, site: str) -> None:
        self.privacy_timeline_manager.record(
            "permissions", "third_party_cookie_blocked",
            "Third-party cookie blocked", site,
        )

    def _record_vpn_timeline(self) -> None:
        previous_mode, previous_state = self._timeline_vpn_state
        mode, state = self.vpn_proxy_manager.mode(), self.vpn_proxy_manager.state()
        if state == "connected" and (mode, state) != self._timeline_vpn_state:
            title = "Tor connected" if mode == "tor" else "Private network connected"
            site = "Local Tor" if mode == "tor" else self.vpn_proxy_manager.selected_country()
            self.privacy_timeline_manager.record(
                "network", f"{mode}_connected", title, site
            )
        elif previous_state == "connected" and state != "connected":
            title = (
                "Tor disconnected" if previous_mode == "tor"
                else "Private network disconnected"
            )
            site = "Local Tor" if previous_mode == "tor" else "Browser network"
            self.privacy_timeline_manager.record(
                "network", f"{previous_mode}_disconnected", title, site
            )
        self._timeline_vpn_state = (mode, state)

    def _apply_webrtc_setting(self, profile: QWebEngineProfile) -> None:
        profile.settings().setAttribute(
            QWebEngineSettings.WebAttribute.WebRTCPublicInterfacesOnly,
            bool(self.privacy_settings.value("privacy/webrtc_leak_protection")),
        )

    def _configure_interceptor(self, interceptor: "TrackerBlocker") -> None:
        raw_exceptions = self.privacy_settings.value(
            "privacy/tracker_site_exceptions"
        )
        if isinstance(raw_exceptions, str):
            exceptions = {raw_exceptions} if raw_exceptions else set()
        else:
            exceptions = {str(item) for item in (raw_exceptions or [])}
        interceptor.configure(
            self.tracker_protection_enabled(),
            exceptions,
        )

    def tracker_protection_enabled(self) -> bool:
        if self._tracker_session_override is not None:
            return self._tracker_session_override
        return bool(
            self.privacy_settings.value("privacy/tracker_protection_enabled")
        )

    def apply_tracker_prompt_choice(self, enabled: bool, remember: bool) -> None:
        if remember:
            self._tracker_session_override = None
            self.privacy_settings.save_values(
                {
                    "privacy/tracker_protection_enabled": enabled,
                    "privacy/tracker_choice_remembered": True,
                }
            )
        else:
            self._tracker_session_override = enabled
            self.privacy_settings.save_values(
                {"privacy/tracker_choice_remembered": False}
            )
        self.refresh_privacy_settings()

    def clear_tracker_session_override(self) -> None:
        self._tracker_session_override = None

    def refresh_privacy_settings(self) -> None:
        self.privacy_settings.sync()
        self._apply_webrtc_setting(self.normal_profile)
        for profile in list(self._private_profiles):
            self._apply_webrtc_setting(profile)
        for interceptor in self._tracker_interceptors.values():
            self._configure_interceptor(interceptor)
        self.adblock_manager.refresh()
        self.vpn_proxy_manager.reload_from_settings()
        self.cookie_privacy_manager.refresh()
        self.privacy_configuration_changed.emit()

    def set_site_protection(self, host: str, enabled: bool) -> None:
        host = host.lower().strip(".")
        raw = self.privacy_settings.value("privacy/tracker_site_exceptions")
        if isinstance(raw, str):
            exceptions = {raw} if raw else set()
        else:
            exceptions = {str(item) for item in (raw or [])}
        if enabled:
            exceptions.discard(host)
        elif host:
            exceptions.add(host)
        self.privacy_settings.save_values(
            {"privacy/tracker_site_exceptions": sorted(exceptions)}
        )
        self.refresh_privacy_settings()

    def site_protection_enabled(self, host: str) -> bool:
        if not self.tracker_protection_enabled():
            return False
        raw = self.privacy_settings.value("privacy/tracker_site_exceptions")
        exceptions = {raw} if isinstance(raw, str) else set(raw or [])
        return host.lower().strip(".") not in exceptions

    def download_manager_for(
        self,
        profile: QWebEngineProfile,
        settings: SettingsManager,
        parent_widget: QWidget,
        *,
        private: bool,
    ) -> "DownloadManager":
        key = id(profile)
        manager = self._download_managers.get(key)
        if manager is None:
            manager = DownloadManager(
                settings, parent_widget, self.malware_scanner,
                persist_history=not private, private_session=private,
            )
            manager.attach_profile(profile)
            self._download_managers[key] = manager
        else:
            manager.parent_widget = parent_widget
        return manager

    def release_private_profile(self, profile: QWebEngineProfile) -> None:
        """Clear and delete a private profile after all of its pages are gone."""
        if profile not in self._private_profiles:
            return
        self._private_profiles.discard(profile)
        self._download_managers.pop(id(profile), None)
        self._tracker_interceptors.pop(id(profile), None)
        self._request_interceptors.pop(id(profile), None)
        self.adblock_manager.release_profile(profile)
        self.site_permission_manager.release_profile(profile)
        self.cookie_privacy_manager.release_profile(profile)
        self.privacy_manager.clear_private_session(str(id(profile)))
        try:
            profile.cookieStore().deleteAllCookies()
            profile.clearHttpCache()
            profile.clearAllVisitedLinks()
        except RuntimeError:
            pass
        # BrowserWindow queues page deletion first. Queueing the profile one
        # event-loop turn later avoids destroying a profile before its pages.
        QTimer.singleShot(0, profile.deleteLater)


class _DatabaseManager(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.database_path = app_data_directory() / "browser_data.sqlite3"

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


class PrivacyManager(_DatabaseManager):
    """Local-only blocked-tracker counters and per-private-session counters."""

    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._private_counts: dict[str, dict[str, int]] = {}
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tracker_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site TEXT NOT NULL,
                    tracker TEXT NOT NULL,
                    blocked_at TEXT NOT NULL
                )
                """
            )

    def record_block(self, site: str, tracker: str, private_session: str) -> None:
        site = site or "Unknown site"
        if private_session:
            sites = self._private_counts.setdefault(private_session, {})
            sites[site] = sites.get(site, 0) + 1
        else:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO tracker_events(site, tracker, blocked_at) VALUES (?, ?, ?)",
                    (site, tracker, _now_iso()),
                )
        self.changed.emit()

    def count_today(self, private_session: str = "") -> int:
        if private_session:
            return sum(self._private_counts.get(private_session, {}).values())
        today = datetime.now().astimezone().date().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM tracker_events WHERE substr(blocked_at, 1, 10) = ?",
                (today,),
            ).fetchone()
        return int(row["count"])

    def count_total(self, private_session: str = "") -> int:
        if private_session:
            return self.count_today(private_session)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM tracker_events"
            ).fetchone()
        return int(row["count"])

    def count_for_site(self, site: str, private_session: str = "") -> int:
        if private_session:
            return self._private_counts.get(private_session, {}).get(site, 0)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM tracker_events WHERE site = ?",
                (site,),
            ).fetchone()
        return int(row["count"])

    def sites(self, private_session: str = "") -> list[dict]:
        if private_session:
            rows = self._private_counts.get(private_session, {})
            return [
                {"site": site, "count": count}
                for site, count in sorted(rows.items(), key=lambda item: -item[1])
            ]
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT site, COUNT(*) AS count FROM tracker_events
                GROUP BY site ORDER BY count DESC, site COLLATE NOCASE
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_private_session(self, session_id: str) -> None:
        self._private_counts.pop(session_id, None)


class TrackerBlocker(QWebEngineUrlRequestInterceptor):
    """Small domain-based blocker using Qt WebEngine's request API."""

    blocked = Signal(str, str, str)

    TRACKER_DOMAINS = frozenset(
        {
            "2mdn.net",
            "ads-twitter.com",
            "analytics.tiktok.com",
            "amplitude.com",
            "bat.bing.com",
            "clarity.ms",
            "connect.facebook.net",
            "doubleclick.net",
            "google-analytics.com",
            "googleadservices.com",
            "googlesyndication.com",
            "googletagmanager.com",
            "hotjar.com",
            "mixpanel.com",
            "newrelic.com",
            "nr-data.net",
            "px.ads.linkedin.com",
            "scorecardresearch.com",
            "segment.com",
            "segment.io",
            "snap.licdn.com",
        }
    )

    def __init__(self, private_session: str = "", parent=None) -> None:
        super().__init__(parent)
        self.private_session = private_session
        self._enabled = False
        self._exceptions: frozenset[str] = frozenset()

    def configure(self, enabled: bool, exceptions: set[str]) -> None:
        self._enabled = enabled
        self._exceptions = frozenset(host.lower().strip(".") for host in exceptions)

    @staticmethod
    def _matches(host: str, domain: str) -> bool:
        return host == domain or host.endswith(f".{domain}")

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> bool:
        if not self._enabled:
            return False
        request_host = info.requestUrl().host().lower().strip(".")
        site = (
            info.firstPartyUrl().host()
            or info.initiator().host()
            or "Unknown site"
        ).lower().strip(".")
        if not request_host or site in self._exceptions or request_host == site:
            return False
        tracker = next(
            (
                domain
                for domain in self.TRACKER_DOMAINS
                if self._matches(request_host, domain)
            ),
            None,
        )
        if tracker is None:
            return False
        info.block(True)
        self.blocked.emit(site, request_host, self.private_session)
        return True


class HistoryManager(_DatabaseManager):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    visited_at TEXT NOT NULL
                )
                """
            )

    def add_visit(self, title: str, url: str, private: bool = False) -> None:
        if private or not url.startswith(("http://", "https://")):
            return
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO history(title, url, visited_at) VALUES (?, ?, ?)",
                (title or url, url, _now_iso()),
            )
        self.changed.emit()

    def entries(self, search: str = "", limit: int = 500) -> list[dict]:
        with self._connect() as connection:
            if search:
                pattern = f"%{search}%"
                rows = connection.execute(
                    """
                    SELECT id, title, url, visited_at FROM history
                    WHERE title LIKE ? OR url LIKE ?
                    ORDER BY id DESC LIMIT ?
                    """,
                    (pattern, pattern, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, title, url, visited_at FROM history
                    ORDER BY id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def remove(self, entry_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM history WHERE id = ?", (entry_id,))
        self.changed.emit()

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM history")
        self.changed.emit()


class BookmarkManager(_DatabaseManager):
    changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bookmarks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                )
                """
            )

    def add(self, title: str, url: str) -> None:
        if not url:
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bookmarks(title, url, created_at) VALUES (?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET title = excluded.title
                """,
                (title or url, url, _now_iso()),
            )
        self.changed.emit()

    def remove_url(self, url: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM bookmarks WHERE url = ?", (url,))
        self.changed.emit()

    def remove(self, bookmark_id: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,))
        self.changed.emit()

    def update(self, bookmark_id: int, title: str, url: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE bookmarks SET title = ?, url = ? WHERE id = ?",
                (title or url, url, bookmark_id),
            )
        self.changed.emit()

    def contains(self, url: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM bookmarks WHERE url = ?", (url,)
            ).fetchone()
        return row is not None

    def entries(self, search: str = "") -> list[dict]:
        with self._connect() as connection:
            if search:
                pattern = f"%{search}%"
                rows = connection.execute(
                    """
                    SELECT id, title, url, created_at FROM bookmarks
                    WHERE title LIKE ? OR url LIKE ? ORDER BY title COLLATE NOCASE
                    """,
                    (pattern, pattern),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT id, title, url, created_at FROM bookmarks
                    ORDER BY title COLLATE NOCASE
                    """
                ).fetchall()
        return [dict(row) for row in rows]


class DownloadManager(_DatabaseManager):
    """Accepts and tracks real QWebEngineProfile download requests."""

    changed = Signal()

    def __init__(
        self,
        settings: SettingsManager,
        parent_widget: QWidget,
        malware_scanner=None,
        *,
        persist_history: bool = True,
        private_session: bool = False,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.parent_widget = parent_widget
        self.persist_history = persist_history
        self.malware_scanner = malware_scanner
        self.private_session = private_session
        self._profile: QWebEngineProfile | None = None
        self._requests: dict[int, QWebEngineDownloadRequest] = {}
        self._record_for_request: dict[int, int] = {}
        self._private_records: list[dict] = []
        self._next_private_id = 1
        self._scanner_notified: set[int] = set()
        self._record_for_scan: dict[int, int] = {}
        if self.malware_scanner is not None:
            self.malware_scanner.scan_updated.connect(self._security_updated)
        if self.persist_history:
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS downloads (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id INTEGER,
                        filename TEXT NOT NULL,
                        url TEXT NOT NULL,
                        destination TEXT NOT NULL,
                        status TEXT NOT NULL,
                        received INTEGER NOT NULL DEFAULT 0,
                        total INTEGER NOT NULL DEFAULT -1,
                        created_at TEXT NOT NULL
                    )
                    """
                )

    def attach_profile(self, profile: QWebEngineProfile) -> None:
        if self._profile is profile:
            return
        self._profile = profile
        profile.setDownloadPath(str(self.settings.value("downloads/location")))
        profile.downloadRequested.connect(self._download_requested)

    def _download_requested(self, request: QWebEngineDownloadRequest) -> None:
        directory = Path(str(self.settings.value("downloads/location")))
        directory.mkdir(parents=True, exist_ok=True)
        suggested = request.suggestedFileName() or request.downloadFileName() or "download"

        if bool(self.settings.value("downloads/ask_each_time")) and not request.isSavePageDownload():
            selected, _ = QFileDialog.getSaveFileName(
                self.parent_widget,
                "Save Download",
                str(directory / suggested),
            )
            if not selected:
                request.cancel()
                return
            selected_path = Path(selected)
            request.setDownloadDirectory(str(selected_path.parent))
            request.setDownloadFileName(selected_path.name)
        elif not request.isSavePageDownload():
            request.setDownloadDirectory(str(directory))
            request.setDownloadFileName(suggested)

        destination = str(
            Path(request.downloadDirectory()) / request.downloadFileName()
        )
        record = {
            "request_id": int(request.id()),
            "filename": request.downloadFileName() or suggested,
            "url": request.url().toString(),
            "destination": destination,
            "status": self._status_text(request),
            "received": int(request.receivedBytes()),
            "total": int(request.totalBytes()),
            "created_at": _now_iso(),
        }
        if self.persist_history:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO downloads(
                        request_id, filename, url, destination, status,
                        received, total, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(record[key] for key in (
                        "request_id", "filename", "url", "destination", "status",
                        "received", "total", "created_at"
                    )),
                )
                record_id = int(cursor.lastrowid)
        else:
            record_id = self._next_private_id
            self._next_private_id += 1
            record["id"] = record_id
            self._private_records.insert(0, record)

        request_id = int(request.id())
        self._requests[request_id] = request
        self._record_for_request[request_id] = record_id
        request.receivedBytesChanged.connect(lambda req=request: self._update(req))
        request.totalBytesChanged.connect(lambda req=request: self._update(req))
        request.stateChanged.connect(lambda _state, req=request: self._update(req))
        request.isFinishedChanged.connect(lambda req=request: self._update(req))

        if request.state() == QWebEngineDownloadRequest.DownloadState.DownloadRequested:
            request.accept()
        self._update(request)

    @staticmethod
    def _status_text(request: QWebEngineDownloadRequest) -> str:
        return request.state().name.removeprefix("Download")

    def _update(self, request: QWebEngineDownloadRequest) -> None:
        request_id = int(request.id())
        record_id = self._record_for_request.get(request_id)
        if record_id is None:
            return
        values = {
            "status": self._status_text(request),
            "received": int(request.receivedBytes()),
            "total": int(request.totalBytes()),
            "destination": str(
                Path(request.downloadDirectory()) / request.downloadFileName()
            ),
            "filename": request.downloadFileName(),
        }
        if self.persist_history:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE downloads SET status = ?, received = ?, total = ?,
                        destination = ?, filename = ? WHERE id = ?
                    """,
                    (*values.values(), record_id),
                )
        else:
            record = next(
                (row for row in self._private_records if row["id"] == record_id),
                None,
            )
            if record is not None:
                record.update(values)
        self.changed.emit()
        if (
            self.malware_scanner is not None
            and request.isFinished()
            and values["status"] == "Completed"
            and request_id not in self._scanner_notified
        ):
            self._scanner_notified.add(request_id)
            scan_record = {
                "filename": values["filename"],
                "destination": values["destination"],
                # Incognito downloads are still scanned, but their source URL
                # is not written into the persistent Security Center database.
                "url": "" if self.private_session else request.url().toString(),
                "received": values["received"],
            }
            scan_id = self.malware_scanner.scan_download(scan_record)
            self._record_for_scan[scan_id] = record_id
            self._security_updated(scan_id)

    def _security_updated(self, scan_id: int) -> None:
        record_id = self._record_for_scan.get(int(scan_id))
        if record_id is None or self.malware_scanner is None:
            return
        scan = self.malware_scanner.data.scan(int(scan_id))
        if not scan:
            return
        if scan["status"] == "waiting":
            status = "Queued for security scan"
        elif scan["status"] == "hashing":
            status = "Local scan — calculating SHA-256"
        elif scan["status"] == "local_scanning":
            status = "Local scan — checking file"
        elif scan["status"] == "local_complete":
            status = "Local scan complete"
        elif scan["status"] == "antivirus_scanning":
            status = "Antivirus scan — checking file"
        elif scan["status"] == "virustotal_lookup":
            status = "VirusTotal check — looking up hash"
        elif scan["status"] == "virustotal_upload":
            status = "VirusTotal check — uploading file"
        elif scan["status"] == "virustotal_waiting":
            status = "VirusTotal analysis — waiting"
        elif scan["verdict"] == "clean":
            status = (
                "Clean — local antivirus reported no threat"
                if scan["vt_status"] == "skipped_antivirus"
                else "Clean — VirusTotal reported no known threats"
            )
        elif scan["verdict"] in {"malware", "suspicious"}:
            status = "Download Blocked — Review required"
        elif scan["status"] == "not_scanned":
            status = "NOT SCANNED — Over 500 MB"
        else:
            status = "UNSAFE — Not verified"
        if self.persist_history:
            with self._connect() as connection:
                connection.execute("UPDATE downloads SET status=? WHERE id=?", (status, record_id))
        else:
            record = next((row for row in self._private_records if row["id"] == record_id), None)
            if record is not None:
                record["status"] = status
        self.changed.emit()

    def records(self) -> list[dict]:
        if not self.persist_history:
            return [dict(record) for record in self._private_records]
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, request_id, filename, url, destination, status,
                       received, total, created_at
                FROM downloads ORDER BY id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def cancel(self, record_id: int) -> None:
        record = next((row for row in self.records() if row["id"] == record_id), None)
        if record is None:
            return
        request = self._requests.get(int(record["request_id"]))
        if request is not None and not request.isFinished():
            request.cancel()

    def active_requests(self) -> list[QWebEngineDownloadRequest]:
        return [request for request in self._requests.values() if not request.isFinished()]

    def cancel_active(self) -> None:
        for request in self.active_requests():
            request.cancel()

    def open_file(self, destination: str) -> None:
        path = Path(destination)
        if not path.exists():
            return
        if self.malware_scanner is not None:
            scan = self.malware_scanner.data.latest_for_path(str(path))
            if not scan or (
                not bool(scan.get("override_kept"))
                and (scan["status"] != "completed" or scan["verdict"] != "clean")
            ):
                QMessageBox.warning(
                    self.parent_widget,
                    "File Not Verified",
                    "This file cannot be opened from the browser until malware "
                    "scanning completes with no known threats detected.",
                )
                return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_folder(self, destination: str) -> None:
        folder = Path(destination).parent
        if folder.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
