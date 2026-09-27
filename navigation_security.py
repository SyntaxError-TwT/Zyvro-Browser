"""Local navigation protections: HTTPS upgrades and malicious-host blocking."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit

from qtpy.QtCore import QObject, Signal


HOST_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}$", re.I)


class NavigationSecurityManager(QObject):
    """Stores a local host blocklist and session-only user overrides."""

    changed = Signal()

    def __init__(self, settings, database_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.database_path = Path(database_path)
        self._http_session_exceptions: set[str] = set()
        self._malicious_session_overrides: set[str] = set()
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS malicious_hosts (
                    host TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    added_at TEXT NOT NULL
                )
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        try:
            yield db
            db.commit()
        finally:
            db.close()

    @staticmethod
    def normalize_host(value: str) -> str:
        raw = str(value or "").strip().lower()
        if "://" in raw:
            raw = urlsplit(raw).hostname or ""
        raw = raw.split("/", 1)[0].split(":", 1)[0].strip(".")
        return raw

    def https_only_enabled(self) -> bool:
        return bool(self.settings.value("security/https_only_enabled"))

    def phishing_protection_enabled(self) -> bool:
        return bool(self.settings.value("security/malicious_site_protection"))

    @staticmethod
    def _is_local(host: str) -> bool:
        return (
            host in {"localhost", "127.0.0.1", "::1"}
            or host.endswith(".localhost")
        )

    def should_upgrade(self, url) -> bool:
        host = self.normalize_host(url.host())
        return bool(
            self.https_only_enabled()
            and url.scheme().lower() == "http"
            and host
            and not self._is_local(host)
            and host not in self._http_session_exceptions
        )

    def allow_http_for_session(self, host: str) -> None:
        host = self.normalize_host(host)
        if host:
            self._http_session_exceptions.add(host)

    def allow_malicious_for_session(self, host: str) -> None:
        host = self.normalize_host(host)
        if host:
            self._malicious_session_overrides.add(host)

    def is_malicious(self, host: str) -> bool:
        host = self.normalize_host(host)
        if (
            not host or not self.phishing_protection_enabled()
            or host in self._malicious_session_overrides
        ):
            return False
        labels = host.split(".")
        candidates = [".".join(labels[index:]) for index in range(max(1, len(labels) - 1))]
        with self._connect() as db:
            placeholders = ",".join("?" for _ in candidates)
            row = db.execute(
                f"SELECT 1 FROM malicious_hosts WHERE host IN ({placeholders}) LIMIT 1",
                candidates,
            ).fetchone()
        return row is not None

    def import_hosts(self, text: str, source: str = "local import") -> int:
        hosts: set[str] = set()
        for line in str(text).splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "!")):
                continue
            # Accept plain domains and common hosts-file lines.
            parts = line.split()
            candidate = parts[-1] if parts else ""
            host = self.normalize_host(candidate)
            if HOST_RE.fullmatch(host) and host not in {"localhost"}:
                hosts.add(host)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with self._connect() as db:
            db.executemany(
                "INSERT INTO malicious_hosts(host,source,added_at) VALUES(?,?,?) "
                "ON CONFLICT(host) DO UPDATE SET source=excluded.source,added_at=excluded.added_at",
                [(host, source, timestamp) for host in sorted(hosts)],
            )
        if hosts:
            self.changed.emit()
        return len(hosts)

    def host_count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM malicious_hosts").fetchone()[0])

    def clear_hosts(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM malicious_hosts")
        self.changed.emit()
