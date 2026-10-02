"""Local browser maintenance, portable backups, diagnostics, and update checks."""

from __future__ import annotations

from datetime import datetime, timedelta
import base64
import hashlib
import json
import platform
from pathlib import Path
import sqlite3
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen

from qtpy import API_NAME, QT_VERSION
from qtpy.QtCore import QObject, QSettings, Signal

from antivirus_detector import AntivirusDetector
from browser_data import APP_NAME, ORGANIZATION_NAME, app_data_directory


APP_VERSION = "1.0.0"
BACKUP_FORMAT = "python-browser-backup-v1"


def _cutoff_iso(time_range: str) -> str | None:
    delta = {
        "hour": timedelta(hours=1),
        "day": timedelta(days=1),
        "week": timedelta(days=7),
    }.get(time_range)
    if delta is None:
        return None
    return (datetime.now().astimezone() - delta).isoformat(timespec="seconds")


class ClearBrowsingDataManager(QObject):
    """Deletes browser records but never removes downloaded files."""

    cleared = Signal(dict)

    def __init__(self, profile_manager, parent=None) -> None:
        super().__init__(parent)
        self.profile_manager = profile_manager
        self.database_path = app_data_directory() / "browser_data.sqlite3"

    def clear(self, time_range: str, categories: set[str]) -> dict[str, int | str]:
        cutoff = _cutoff_iso(time_range)
        result: dict[str, int | str] = {}
        timestamp_tables = {
            "history": (("history", "visited_at"),),
            "download_list": (("downloads", "created_at"),),
            "privacy_records": (
                ("tracker_events", "blocked_at"),
                ("adblock_events", "blocked_at"),
                ("privacy_timeline_events", "occurred_at"),
            ),
        }
        if self.database_path.exists():
            with sqlite3.connect(self.database_path, timeout=10) as db:
                existing = {
                    row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for category, tables in timestamp_tables.items():
                    if category not in categories:
                        continue
                    amount = 0
                    for table, column in tables:
                        if table not in existing:
                            continue
                        query = f"DELETE FROM {table}"
                        values: tuple[str, ...] = ()
                        if cutoff:
                            query += f" WHERE {column} >= ?"
                            values = (cutoff,)
                        cursor = db.execute(query, values)
                        amount += max(0, int(cursor.rowcount))
                    result[category] = amount

        if "permissions" in categories:
            # Saved permissions do not have timestamps, so the requested time
            # range cannot be applied safely; clearing this category resets all.
            amount = len(self.profile_manager.site_permission_manager.records())
            self.profile_manager.site_permission_manager.reset()
            result["permissions"] = amount

        profile = self.profile_manager.normal_profile
        if "cookies" in categories:
            # Qt WebEngine does not expose cookie creation timestamps. This is
            # intentionally an all-cookie operation even for a short range.
            profile.cookieStore().deleteAllCookies()
            result["cookies"] = "all"
        if "cache" in categories:
            # Chromium's Qt API similarly provides whole-cache clearing only.
            profile.clearHttpCache()
            result["cache"] = "all"
        if "history" in categories:
            profile.clearAllVisitedLinks()

        self.cleared.emit(result)
        return result


class BackupManager(QObject):
    """Exports/imports ordinary local configuration without secrets."""

    imported = Signal()
    SENSITIVE_PARTS = ("password", "secret", "token", "api_key", "apikey")
    OMITTED_KEYS = {
        "session/windows_json", "session/recently_closed_json",
        "session/recently_closed_windows_json", "session/previous_sessions_json",
        "session/clean_shutdown",
    }

    def __init__(self, settings_manager, bookmark_manager, permission_manager, parent=None):
        super().__init__(parent)
        self.settings = settings_manager
        self.bookmarks = bookmark_manager
        self.permissions = permission_manager

    @classmethod
    def safe_setting(cls, key: str) -> bool:
        lowered = key.lower()
        return key not in cls.OMITTED_KEYS and not any(
            part in lowered for part in cls.SENSITIVE_PARTS
        )

    def payload(self) -> dict:
        settings = {
            key: self.settings.value(key)
            for key in self.settings.DEFAULTS
            if self.safe_setting(key)
        }
        return {
            "format": BACKUP_FORMAT,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "browser_version": APP_VERSION,
            "bookmarks": self.bookmarks.entries(),
            "settings": settings,
            "site_permissions": self.permissions.records(),
            "excluded": ["passwords", "API keys", "history", "cookies", "cache"],
        }

    def export_file(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.payload(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def import_file(self, path: str | Path) -> dict[str, int]:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("format") != BACKUP_FORMAT:
            raise ValueError("This is not a supported Python Browser backup.")
        settings = {
            str(key): value for key, value in dict(data.get("settings") or {}).items()
            if key in self.settings.DEFAULTS and self.safe_setting(str(key))
        }
        self.settings.save_values(settings)
        bookmark_count = 0
        for item in data.get("bookmarks") or []:
            url = str(item.get("url") or "").strip()
            if url:
                self.bookmarks.add(str(item.get("title") or url), url)
                bookmark_count += 1
        permissions = [
            item for item in (data.get("site_permissions") or [])
            if isinstance(item, dict)
        ]
        self.permissions.replace_records(permissions)
        self.imported.emit()
        return {
            "settings": len(settings), "bookmarks": bookmark_count,
            "permissions": len(permissions),
        }


class DiagnosticsManager:
    """Creates a local support report containing no browsing/private data."""

    def __init__(self, profile_manager) -> None:
        self.profile_manager = profile_manager

    def report(self) -> str:
        binding_version = "Unknown"
        try:
            module = __import__(API_NAME)
            binding_version = getattr(module, "__version__", "Unknown")
        except (ImportError, AttributeError):
            pass
        products = AntivirusDetector().detect()
        active = [item["name"] for item in products if item.get("active")]
        vpn = self.profile_manager.vpn_proxy_manager
        host, port = vpn.active_endpoint()
        endpoint = f"{host}:{port}" if host and port else "System/default route"
        activities = self.profile_manager.security_data.activities(20)
        errors = [
            f"{item['created_at']} — {item['title']}: {item['detail']}"
            for item in activities
            if any(word in str(item.get("event_type", "")).lower()
                   for word in ("error", "failed", "unavailable"))
        ][:8]
        settings_path = QSettings(ORGANIZATION_NAME, APP_NAME).fileName()
        lines = [
            "Python Browser Diagnostic Report",
            f"Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"Browser version: {APP_VERSION}",
            f"Python: {platform.python_version()} ({platform.architecture()[0]})",
            f"Operating system: {platform.platform()}",
            f"Qt API: {API_NAME} {binding_version}",
            f"Qt version: {QT_VERSION}",
            f"Active antivirus: {', '.join(active) if active else 'None reported active'}",
            f"Private network: {vpn.mode()} / {vpn.state()} — {vpn.detail()}",
            f"Network endpoint: {endpoint}",
            f"App data: {app_data_directory()}",
            f"Profile storage: {self.profile_manager.normal_profile.persistentStoragePath()}",
            f"Cache: {self.profile_manager.normal_profile.cachePath()}",
            f"Settings: {settings_path}",
            "Recent local errors:",
            *(errors or ["None recorded"]),
            "",
            "Excluded: browsing history, URLs, searches, cookies, passwords, form data, API keys, and incognito data.",
        ]
        return "\n".join(lines)


class UpdateChecker:
    """Checks an explicitly configured HTTPS JSON manifest; never installs."""

    @staticmethod
    def _version_tuple(value: str) -> tuple[int, ...]:
        values = []
        for part in str(value).split("."):
            digits = "".join(character for character in part if character.isdigit())
            values.append(int(digits or 0))
        return tuple(values)

    def check(self, manifest_url: str, public_key: str = "") -> dict:
        url = manifest_url.strip()
        if not url:
            raise ValueError("No update manifest is configured.")
        if not url.lower().startswith("https://"):
            raise ValueError("The update manifest must use HTTPS.")
        request = Request(url, headers={"User-Agent": f"PythonBrowser/{APP_VERSION}"})
        try:
            with urlopen(request, timeout=10) as response:
                data = json.loads(response.read(1_000_000).decode("utf-8"))
        except (OSError, URLError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Update check failed: {error}") from error
        version = str(data.get("version") or "").strip()
        if not version:
            raise ValueError("The update manifest has no version.")
        download_url = str(data.get("download_url") or "").strip()
        if download_url and not download_url.lower().startswith("https://"):
            raise ValueError("The update download link must use HTTPS.")
        signature = str(data.get("signature") or "").strip()
        key = str(public_key or "").strip()
        if not key or not signature:
            raise ValueError(
                "Signed updates require an Ed25519 public key and manifest signature."
            )
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            payload = dict(data)
            payload.pop("signature", None)
            canonical = json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            Ed25519PublicKey.from_public_bytes(
                base64.b64decode(key, validate=True)
            ).verify(base64.b64decode(signature, validate=True), canonical)
        except ImportError as error:
            raise RuntimeError(
                "The cryptography package is required to verify signed updates."
            ) from error
        except Exception as error:
            raise ValueError("The update manifest signature is invalid.") from error
        sha256 = str(data.get("sha256") or "").lower().strip()
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ValueError("The signed update manifest has no valid SHA-256 digest.")
        return {
            "version": version,
            "available": self._version_tuple(version) > self._version_tuple(APP_VERSION),
            "download_url": download_url,
            "notes": str(data.get("notes") or ""),
            "sha256": sha256,
            "signature_verified": True,
        }

    @staticmethod
    def download_and_verify(result: dict, destination: str | Path) -> Path:
        url = str(result.get("download_url") or "")
        expected = str(result.get("sha256") or "").lower()
        if not url.lower().startswith("https://") or len(expected) != 64:
            raise ValueError("The signed update metadata is incomplete.")
        destination = Path(destination)
        partial = destination.with_name(destination.name + ".part")
        digest = hashlib.sha256()
        try:
            request = Request(url, headers={"User-Agent": f"PythonBrowser/{APP_VERSION}"})
            with urlopen(request, timeout=30) as response, partial.open("wb") as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest().lower() != expected:
                partial.unlink(missing_ok=True)
                raise ValueError("The downloaded update failed SHA-256 verification.")
            partial.replace(destination)
            return destination
        except Exception:
            partial.unlink(missing_ok=True)
            raise
