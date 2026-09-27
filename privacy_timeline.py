"""Local-only privacy/security event timeline."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3

from qtpy.QtCore import QObject, Signal


class PrivacyTimelineManager(QObject):
    """Store restrained privacy events without retaining page content or URLs."""

    changed = Signal()

    def __init__(self, database_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.database_path = Path(database_path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS privacy_timeline_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    category TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    site TEXT NOT NULL,
                    amount INTEGER NOT NULL DEFAULT 1
                )
                """
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def record(
        self, category: str, event_type: str, title: str, site: str,
        amount: int = 1,
    ) -> None:
        """Record or briefly aggregate an event using only a domain/label."""
        site = (site or "Browser").strip().strip(".")
        if " " not in site:
            site = site.lower()
        now = datetime.now().astimezone()
        with self._connect() as connection:
            latest = connection.execute(
                "SELECT id, occurred_at FROM privacy_timeline_events "
                "WHERE category=? AND event_type=? AND site=? "
                "ORDER BY id DESC LIMIT 1",
                (category, event_type, site),
            ).fetchone()
            aggregate = False
            if latest:
                try:
                    aggregate = (
                        now - datetime.fromisoformat(str(latest["occurred_at"]))
                        <= timedelta(seconds=60)
                    )
                except ValueError:
                    aggregate = False
            if aggregate:
                connection.execute(
                    "UPDATE privacy_timeline_events SET amount=amount+?, occurred_at=? "
                    "WHERE id=?",
                    (max(1, int(amount)), now.isoformat(timespec="seconds"), latest["id"]),
                )
            else:
                connection.execute(
                    "INSERT INTO privacy_timeline_events"
                    "(occurred_at, category, event_type, title, site, amount) "
                    "VALUES(?, ?, ?, ?, ?, ?)",
                    (
                        now.isoformat(timespec="seconds"), category, event_type,
                        title, site, max(1, int(amount)),
                    ),
                )
        self.changed.emit()

    def events(self, category: str = "all", limit: int = 500) -> list[dict]:
        query = (
            "SELECT id, occurred_at, category, event_type, title, site, amount "
            "FROM privacy_timeline_events"
        )
        parameters: list[object] = []
        if category != "all":
            query += " WHERE category=?"
            parameters.append(category)
        query += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
        parameters.append(max(1, int(limit)))
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            amount = int(event["amount"])
            if amount > 1 and event["event_type"] == "trackers_blocked":
                event["display_title"] = f"{amount} trackers blocked"
            elif amount > 1 and event["event_type"] == "ads_blocked":
                event["display_title"] = f"{amount} ads blocked"
            else:
                event["display_title"] = event["title"]
            events.append(event)
        return events

    def clear(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM privacy_timeline_events")
        self.changed.emit()
