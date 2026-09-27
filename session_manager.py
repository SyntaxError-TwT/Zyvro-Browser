"""Local browser-session, crash-recovery, and recently-closed-tab storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from qtpy.QtCore import QObject


class SessionManager(QObject):
    """Stores normal-window state in QSettings; private windows never call it."""

    SESSION_KEY = "session/windows_json"
    RECENT_KEY = "session/recently_closed_json"
    CLOSED_WINDOWS_KEY = "session/recently_closed_windows_json"
    PREVIOUS_SESSIONS_KEY = "session/previous_sessions_json"
    CLEAN_KEY = "session/clean_shutdown"
    MAX_RECENT = 25
    MAX_CLOSED_WINDOWS = 12
    MAX_PREVIOUS_SESSIONS = 10

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings

    @staticmethod
    def _decode(value, fallback):
        try:
            decoded = json.loads(str(value or ""))
            return decoded if isinstance(decoded, type(fallback)) else fallback
        except (TypeError, ValueError, json.JSONDecodeError):
            return fallback

    def begin_run(self) -> tuple[bool, list[dict]]:
        """Mark this run unclean and return the prior shutdown/session state."""
        previous_clean = bool(self.settings.value(self.CLEAN_KEY))
        windows = self._decode(self.settings.value(self.SESSION_KEY), [])
        if windows:
            self._archive_session(windows, crashed=not previous_clean)
        self.settings.save_values({self.CLEAN_KEY: False})
        return previous_clean, windows

    def save_windows(self, windows: list[dict]) -> None:
        clean = [window for window in windows if window.get("tabs")]
        self.settings.save_values({self.SESSION_KEY: json.dumps(clean)})

    def mark_clean_shutdown(self) -> None:
        self.settings.save_values({self.CLEAN_KEY: True})

    def recently_closed(self) -> list[dict]:
        return self._decode(self.settings.value(self.RECENT_KEY), [])

    def remember_closed(self, tab: dict) -> None:
        url = str(tab.get("url", ""))
        if not url or url.startswith("devtools:"):
            return
        recent = self.recently_closed()
        recent.insert(0, dict(tab))
        self.settings.save_values({
            self.RECENT_KEY: json.dumps(recent[:self.MAX_RECENT])
        })

    def take_closed(self, index: int = 0) -> dict | None:
        recent = self.recently_closed()
        if not (0 <= index < len(recent)):
            return None
        tab = recent.pop(index)
        self.settings.save_values({self.RECENT_KEY: json.dumps(recent)})
        return tab

    def recently_closed_windows(self) -> list[dict]:
        return self._decode(self.settings.value(self.CLOSED_WINDOWS_KEY), [])

    def remember_closed_window(self, window: dict) -> None:
        if not window.get("tabs"):
            return
        recent = self.recently_closed_windows()
        entry = dict(window)
        entry["closed_at"] = self._now()
        recent.insert(0, entry)
        self.settings.save_values({
            self.CLOSED_WINDOWS_KEY: json.dumps(recent[:self.MAX_CLOSED_WINDOWS])
        })

    def take_closed_window(self, index: int = 0) -> dict | None:
        recent = self.recently_closed_windows()
        if not (0 <= index < len(recent)):
            return None
        window = recent.pop(index)
        self.settings.save_values({self.CLOSED_WINDOWS_KEY: json.dumps(recent)})
        return window

    def previous_sessions(self) -> list[dict]:
        return self._decode(self.settings.value(self.PREVIOUS_SESSIONS_KEY), [])

    def _archive_session(self, windows: list[dict], *, crashed: bool) -> None:
        clean_windows = [dict(window) for window in windows if window.get("tabs")]
        if not clean_windows:
            return
        sessions = self.previous_sessions()
        encoded_windows = json.dumps(clean_windows, sort_keys=True)
        if sessions:
            newest = json.dumps(sessions[0].get("windows", []), sort_keys=True)
            if newest == encoded_windows and bool(sessions[0].get("crashed")) == crashed:
                return
        sessions.insert(0, {
            "saved_at": self._now(),
            "crashed": bool(crashed),
            "windows": clean_windows,
        })
        self.settings.save_values({
            self.PREVIOUS_SESSIONS_KEY: json.dumps(
                sessions[:self.MAX_PREVIOUS_SESSIONS]
            )
        })

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
