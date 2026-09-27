"""Local per-site permissions and third-party cookie policy."""

from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import secrets
import sqlite3

from qtpy.QtCore import QObject, Signal
from qtpy.QtWebEngineCore import QWebEngineProfile, QWebEngineScript


PERMISSION_LABELS = {
    "camera": "Camera",
    "microphone": "Microphone",
    "location": "Location",
    "notifications": "Notifications",
    "clipboard": "Clipboard",
    "popups": "Popups",
    "autoplay": "Autoplay",
    "javascript": "JavaScript",
    "screen": "Screen sharing",
    "filesystem": "File system access",
}


class SitePermissionManager(QObject):
    """Persist site decisions and install small popup/autoplay policy hooks."""

    changed = Signal()

    def __init__(self, database_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.database_path = Path(database_path)
        self._profiles: dict[int, QWebEngineProfile] = {}
        self._token = secrets.token_hex(16)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS site_permissions (
                    site TEXT NOT NULL,
                    permission TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    PRIMARY KEY (site, permission)
                )
                """
            )

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def normalize_site(site: str) -> str:
        return str(site).lower().strip().strip(".")

    def decision(self, site: str, permission: str) -> str:
        site = self.normalize_site(site)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT decision FROM site_permissions WHERE site=? AND permission=?",
                (site, permission),
            ).fetchone()
        return str(row["decision"]) if row else "ask"

    def records(self) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT site, permission, decision FROM site_permissions "
                "ORDER BY site, permission"
            ).fetchall()
        return [dict(row) for row in rows]

    def decisions_for_site(self, site: str) -> dict[str, str]:
        result = {name: "ask" for name in PERMISSION_LABELS}
        site = self.normalize_site(site)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT permission, decision FROM site_permissions WHERE site=?",
                (site,),
            ).fetchall()
        for row in rows:
            if row["permission"] in result:
                result[str(row["permission"])] = str(row["decision"])
        return result

    def set_decision(self, site: str, permission: str, decision: str) -> None:
        site = self.normalize_site(site)
        if not site or permission not in PERMISSION_LABELS:
            return
        with self._connect() as connection:
            if decision == "ask":
                connection.execute(
                    "DELETE FROM site_permissions WHERE site=? AND permission=?",
                    (site, permission),
                )
            elif decision in {"allow", "block"}:
                connection.execute(
                    "INSERT INTO site_permissions(site, permission, decision) "
                    "VALUES(?, ?, ?) ON CONFLICT(site, permission) DO UPDATE "
                    "SET decision=excluded.decision",
                    (site, permission, decision),
                )
            else:
                return
        self.refresh_scripts()
        self.changed.emit()

    def remove_site(self, site: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM site_permissions WHERE site=?",
                (self.normalize_site(site),),
            )
        self.refresh_scripts()
        self.changed.emit()

    def reset(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM site_permissions")
        self.refresh_scripts()
        self.changed.emit()

    def replace_records(self, records: list[dict[str, str]]) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM site_permissions")
            connection.executemany(
                "INSERT INTO site_permissions(site, permission, decision) VALUES(?, ?, ?)",
                [
                    (
                        self.normalize_site(record["site"]),
                        record["permission"],
                        record["decision"],
                    )
                    for record in records
                    if record.get("decision") in {"allow", "block"}
                    and record.get("permission") in PERMISSION_LABELS
                ],
            )
        self.refresh_scripts()
        self.changed.emit()

    def install_profile(self, profile: QWebEngineProfile) -> None:
        self._profiles[id(profile)] = profile
        profile._python_browser_site_permission_manager = self
        profile._python_browser_permission_token = self._token
        self._replace_profile_script(profile)

    def release_profile(self, profile: QWebEngineProfile) -> None:
        self._profiles.pop(id(profile), None)

    def refresh_scripts(self) -> None:
        for profile in list(self._profiles.values()):
            try:
                self._replace_profile_script(profile)
            except RuntimeError:
                self._profiles.pop(id(profile), None)

    def _replace_profile_script(self, profile: QWebEngineProfile) -> None:
        previous = getattr(profile, "_python_browser_permission_script", None)
        if previous is not None:
            profile.scripts().remove(previous)
        script = QWebEngineScript()
        script.setName("Python Browser site popup and autoplay policy")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(self._script_source())
        profile.scripts().insert(script)
        profile._python_browser_permission_script = script

    def _script_source(self) -> str:
        policies: dict[str, dict[str, str]] = {}
        for record in self.records():
            if record["permission"] in {"popups", "autoplay"}:
                policies.setdefault(record["site"], {})[record["permission"]] = record["decision"]
        prefix = f"__PY_BROWSER_PERMISSION__{self._token}:"
        return f"""
(() => {{
  if (window.__pythonBrowserSitePolicyInstalled) return;
  window.__pythonBrowserSitePolicyInstalled = true;
  const policies = {json.dumps(policies)};
  const site = location.hostname.toLowerCase();
  const policy = (name) => (policies[site] || {{}})[name] || 'ask';
  const report = (permission, url='') => console.info(
    {json.dumps(prefix)} + JSON.stringify({{permission, url}}));

  const nativeOpen = window.open.bind(window);
  window.open = (url='', target='', features='') => {{
    const choice = policy('popups');
    if (choice === 'allow') return nativeOpen(url, target, features);
    if (choice === 'ask') report('popups', String(url || 'about:blank'));
    return null;
  }};

  const nativePlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function(...args) {{
    if (navigator.userActivation && navigator.userActivation.isActive)
      return nativePlay.apply(this, args);
    const choice = policy('autoplay');
    if (choice === 'allow') return nativePlay.apply(this, args);
    if (choice === 'ask' && !this.__pythonBrowserAutoplayAsked) {{
      this.__pythonBrowserAutoplayAsked = true;
      report('autoplay');
    }}
    return Promise.reject(new DOMException('Autoplay is blocked', 'NotAllowedError'));
  }};
}})();
"""


class CookiePrivacyManager(QObject):
    """Apply optional third-party cookie filtering to every browser profile."""

    changed = Signal()
    third_party_blocked = Signal(str)

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._profiles: dict[int, QWebEngineProfile] = {}
        self._enabled = False
        self._exceptions: set[str] = set()
        self.refresh()

    def install_profile(self, profile: QWebEngineProfile) -> None:
        self._profiles[id(profile)] = profile
        # Qt invokes this synchronously on the cookie-store thread; keep the
        # callback small and retain it so Python does not garbage-collect it.
        record_timeline = not profile.isOffTheRecord()
        callback = lambda request: self._allow_cookie(request, record_timeline)
        profile._python_browser_cookie_filter = callback
        profile.cookieStore().setCookieFilter(callback)

    def release_profile(self, profile: QWebEngineProfile) -> None:
        self._profiles.pop(id(profile), None)

    def enabled(self) -> bool:
        return self._enabled

    def exceptions(self) -> set[str]:
        return set(self._exceptions)

    def refresh(self) -> None:
        self._enabled = bool(
            self.settings.value("privacy/block_third_party_cookies")
        )
        raw = self.settings.value("privacy/third_party_cookie_exceptions") or []
        if isinstance(raw, str):
            raw = [raw]
        self._exceptions = {
            str(site).lower().strip(".") for site in raw if str(site).strip()
        }

    def site_exception(self, site: str) -> bool:
        return site.lower().strip(".") in self.exceptions()

    def set_site_exception(self, site: str, allowed: bool) -> None:
        site = site.lower().strip(".")
        values = self.exceptions()
        if allowed and site:
            values.add(site)
        else:
            values.discard(site)
        self.settings.save_values(
            {"privacy/third_party_cookie_exceptions": sorted(values)}
        )
        self.refresh()
        self.changed.emit()

    def _allow_cookie(self, request, record_timeline: bool = True) -> bool:
        if not self.enabled() or not bool(request.thirdParty):
            return True
        first_party = request.firstPartyUrl.host().lower().strip(".")
        origin = request.origin.host().lower().strip(".")
        # Chromium can occasionally mark a cookie as third-party while the
        # first-party URL is still unavailable during an off-the-record page
        # load. Do not break ordinary first-party cookies in that ambiguous
        # state, and explicitly allow matching origin/first-party hosts.
        # Some Qt/Chromium builds omit ``origin`` for document.cookie writes,
        # especially on off-the-record profiles.  With no origin there is no
        # reliable evidence that this is a cross-site cookie, so fail open for
        # this one ambiguous request instead of breaking first-party sessions.
        if not first_party or not origin or (
            origin == first_party or origin.endswith(f".{first_party}")
        ):
            return True
        allowed = first_party in self._exceptions
        if not allowed and record_timeline:
            self.third_party_blocked.emit(first_party or "Unknown site")
        return allowed
