"""Focused checks for standalone web apps and advanced session recovery."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from session_manager import SessionManager
from web_app_manager import WebAppManager


class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key):
        return self.values.get(key)

    def save_values(self, values):
        self.values.update(values)


class WebAppAndRecoveryTests(unittest.TestCase):
    def test_web_app_manifest_install_update_and_remove(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = WebAppManager(root, root)
            with patch.object(manager, "create_windows_shortcuts", return_value=[]):
                app = manager.install("  Example   App  ", "https://example.com/")
            self.assertEqual(app["name"], "Example App")
            self.assertEqual(manager.get(app["id"])["url"], "https://example.com/")
            with patch.object(manager, "_remove_windows_shortcuts"):
                self.assertTrue(manager.uninstall(app["id"]))
            self.assertIsNone(manager.get(app["id"]))

    def test_closed_windows_and_crashed_previous_sessions(self) -> None:
        window = {
            "geometry": [10, 20, 900, 700],
            "current": 0,
            "groups": {},
            "tabs": [{"url": "https://example.com", "title": "Example"}],
        }
        settings = _Settings({
            SessionManager.SESSION_KEY: json.dumps([window]),
            SessionManager.CLEAN_KEY: False,
            SessionManager.RECENT_KEY: "[]",
            SessionManager.CLOSED_WINDOWS_KEY: "[]",
            SessionManager.PREVIOUS_SESSIONS_KEY: "[]",
        })
        manager = SessionManager(settings)
        clean, windows = manager.begin_run()
        self.assertFalse(clean)
        self.assertEqual(windows[0]["tabs"][0]["title"], "Example")
        self.assertTrue(manager.previous_sessions()[0]["crashed"])

        manager.remember_closed_window(window)
        self.assertEqual(len(manager.recently_closed_windows()), 1)
        restored = manager.take_closed_window()
        self.assertEqual(restored["tabs"][0]["url"], "https://example.com")
        self.assertEqual(manager.recently_closed_windows(), [])


if __name__ == "__main__":
    unittest.main()
