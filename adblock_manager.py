"""Local ad-block rules, request interception, cosmetic filtering, and stats."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import re
import secrets
import sqlite3

from qtpy.QtCore import QObject, Signal, Slot
from qtpy.QtWebEngineCore import (
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from rust_adblock_engine import RustAdblockEngine


class AdBlockInterceptor(QWebEngineUrlRequestInterceptor):
    """Blocks conservative built-in ad rules before network loading."""

    blocked = Signal(str, str, str)

    AD_DOMAINS = frozenset(
        {
            "2mdn.net",
            "adform.net",
            "adroll.com",
            "adsafeprotected.com",
            "adzerk.net",
            "adnxs.com",
            "adsrvr.org",
            "adservice.google.com",
            "adservice.google.ca",
            "adservice.google.co.uk",
            "adservice.google.com.au",
            "amazon-adsystem.com",
            "bannersnack.com",
            "casalemedia.com",
            "criteo.com",
            "criteo.net",
            "doubleclick.net",
            "googleadservices.com",
            "googlesyndication.com",
            "moatads.com",
            "openx.net",
            "outbrain.com",
            "pubmatic.com",
            "quantserve.com",
            "rubiconproject.com",
            "smartadserver.com",
            "taboola.com",
            "yieldmo.com",
            "zedo.com",
        }
    )
    AD_HOST_MARKERS = (".adservice.", ".adsystem.")
    # Keep generic path matching deliberately narrow. "banner" is commonly
    # used for legitimate site branding and hero artwork, so it must not be a
    # blocking signal by itself.
    AD_PATH = re.compile(
        r"/(?:ads?|advert(?:s|ising|isement)?|interstitial[-_]?ads?|"
        r"pop[-_]?under[-_]?ads?|prebid|vpaid)(?:/|[-_.?])",
        re.IGNORECASE,
    )

    RESOURCE_TYPES = {
        "ResourceTypeMainFrame": "main_frame",
        "ResourceTypeSubFrame": "sub_frame",
        "ResourceTypeStylesheet": "stylesheet",
        "ResourceTypeScript": "script",
        "ResourceTypeImage": "image",
        "ResourceTypeFontResource": "font",
        "ResourceTypeSubResource": "other",
        "ResourceTypeObject": "object",
        "ResourceTypeMedia": "media",
        "ResourceTypeWorker": "other",
        "ResourceTypeSharedWorker": "other",
        "ResourceTypePrefetch": "other",
        "ResourceTypeFavicon": "image",
        "ResourceTypeXhr": "xmlhttprequest",
        "ResourceTypePing": "ping",
        "ResourceTypeServiceWorker": "other",
        "ResourceTypeCspReport": "csp_report",
        "ResourceTypePluginResource": "object",
    }

    def __init__(
        self,
        engine: RustAdblockEngine | None = None,
        private_session: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine or RustAdblockEngine()
        self.private_session = private_session
        self._enabled = True
        self._allowed_sites: frozenset[str] = frozenset()

    def configure(self, enabled: bool, allowed_sites: set[str]) -> None:
        self._enabled = bool(enabled)
        self._allowed_sites = frozenset(
            host.lower().strip(".") for host in allowed_sites if host
        )

    @staticmethod
    def _domain_matches(host: str, domain: str) -> bool:
        return host == domain or host.endswith(f".{domain}")

    def site_is_allowed(self, site: str) -> bool:
        return any(
            self._domain_matches(site, allowed)
            for allowed in self._allowed_sites
        )

    def should_block(self, info: QWebEngineUrlRequestInfo) -> bool:
        if not self._enabled:
            return False
        request_url = info.requestUrl()
        request_host = request_url.host().lower().strip(".")
        page_url = info.firstPartyUrl()
        if page_url.isEmpty():
            page_url = info.initiator()
        site = page_url.host().lower().strip(".")
        if not request_host or self.site_is_allowed(site):
            return False

        resource_type = getattr(info, "resourceType", lambda: None)()
        main_frame = getattr(
            QWebEngineUrlRequestInfo.ResourceType,
            "ResourceTypeMainFrame",
            None,
        )
        if resource_type == main_frame:
            return False

        if self.engine.available:
            type_name = getattr(resource_type, "name", "")
            method_value = getattr(info, "requestMethod", lambda: b"GET")()
            try:
                method = bytes(method_value).decode("ascii", errors="ignore")
            except TypeError:
                method = str(method_value)
            return self.engine.should_block(
                request_url.toString(),
                request_host,
                site,
                self.RESOURCE_TYPES.get(type_name, "other"),
                method or "GET",
                page_url.toString(),
            )

        domain_rule = any(
            self._domain_matches(request_host, domain)
            for domain in self.AD_DOMAINS
        ) or any(marker in f".{request_host}." for marker in self.AD_HOST_MARKERS)
        path_rule = bool(self.AD_PATH.search(request_url.path().lower() + "/"))
        return domain_rule or path_rule

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> bool:
        if not self.should_block(info):
            return False
        request_url = info.requestUrl()
        page_url = info.firstPartyUrl()
        if page_url.isEmpty():
            page_url = info.initiator()
        site = page_url.host().lower().strip(".") or "Unknown site"
        info.block(True)
        self.blocked.emit(
            site,
            request_url.host().lower().strip(".") or request_url.toString(),
            self.private_session,
        )
        return True


class AdBlockManager(QObject):
    """Own settings, interceptors, cosmetic rules, and local-only counters."""

    changed = Signal()
    # site, amount, private-session id, source (network/cosmetic)
    blocked = Signal(str, int, str, str)
    COSMETIC_SCRIPT_NAME = "Python Browser cosmetic ad filtering"
    PAGE_SCRIPT_NAME = "Zyvro adblock-rust page rules"
    ISOLATED_SCRIPT_NAME = "Zyvro YouTube ad scriptlets"
    GENERIC_STYLE_ID = "zyvro-adblock-rust-generic"

    def __init__(self, settings, database_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.database_path = database_path
        self.rust_engine = RustAdblockEngine()
        self._private_counts: dict[str, dict[str, int]] = {}
        self._interceptors: dict[int, AdBlockInterceptor] = {}
        self._profiles: dict[int, QWebEngineProfile] = {}
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS adblock_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    site TEXT NOT NULL,
                    source TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    blocked_at TEXT NOT NULL
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
    def _setting_list(value) -> set[str]:
        if isinstance(value, str):
            return {value.lower().strip(".")} if value else set()
        return {str(item).lower().strip(".") for item in (value or []) if item}

    def enabled(self) -> bool:
        return bool(self.settings.value("privacy/adblock_enabled"))

    def allowed_sites(self) -> set[str]:
        return self._setting_list(
            self.settings.value("privacy/adblock_allowed_sites")
        )

    def site_enabled(self, host: str) -> bool:
        host = host.lower().strip(".")
        return self.enabled() and not any(
            host == allowed or host.endswith(f".{allowed}")
            for allowed in self.allowed_sites()
        )

    def set_site_enabled(self, host: str, enabled: bool) -> None:
        host = host.lower().strip(".")
        allowed = self.allowed_sites()
        if enabled:
            allowed.discard(host)
        elif host:
            allowed.add(host)
        self.settings.save_values(
            {"privacy/adblock_allowed_sites": sorted(allowed)}
        )
        self.refresh()

    def create_interceptor(
        self, profile: QWebEngineProfile, private_session: str = ""
    ) -> AdBlockInterceptor:
        interceptor = AdBlockInterceptor(
            self.rust_engine, private_session, self
        )
        interceptor.blocked.connect(self._record_network_block)
        self._interceptors[id(profile)] = interceptor
        self._profiles[id(profile)] = profile
        profile._python_browser_adblock_manager = self
        self._configure(interceptor)
        self._install_cosmetic_script(profile)
        return interceptor

    def release_profile(self, profile: QWebEngineProfile) -> None:
        self._interceptors.pop(id(profile), None)
        self._profiles.pop(id(profile), None)
        self._private_counts.pop(str(id(profile)), None)
        if getattr(profile, "_python_browser_adblock_manager", None) is self:
            profile._python_browser_adblock_manager = None

    def refresh(self) -> None:
        for interceptor in self._interceptors.values():
            self._configure(interceptor)
        for profile in self._profiles.values():
            self._install_cosmetic_script(profile)
        self.changed.emit()

    def _configure(self, interceptor: AdBlockInterceptor) -> None:
        interceptor.configure(self.enabled(), self.allowed_sites())

    def _install_cosmetic_script(self, profile: QWebEngineProfile) -> None:
        scripts = profile.scripts()
        # QWebEngineScriptCollection.findScript is not exposed by every QtPy
        # binding, while toList/name/remove works in PySide6 and PyQt6.
        for existing in scripts.toList():
            if existing.name() == self.COSMETIC_SCRIPT_NAME:
                scripts.remove(existing)
        token = getattr(profile, "_python_browser_adblock_token", None)
        if token is None:
            token = secrets.token_hex(16)
            profile._python_browser_adblock_token = token
        if not self.enabled():
            return
        # Keep a small dynamic fallback for ad containers added after load.
        # Host-specific CSS and scriptlets still come from adblock-rust in
        # prepare_page immediately before each navigation commits.
        script = QWebEngineScript()
        script.setName(self.COSMETIC_SCRIPT_NAME)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(
            self._cosmetic_source(token, sorted(self.allowed_sites()))
        )
        scripts.insert(script)
        profile._python_browser_adblock_script = script

    def prepare_page(self, page, url) -> None:
        """Install host rules and scriptlets before the new document runs."""
        scripts = page.scripts()
        for existing in scripts.toList():
            if existing.name() in {
                self.PAGE_SCRIPT_NAME, self.ISOLATED_SCRIPT_NAME
            }:
                scripts.remove(existing)
        page._zyvro_adblock_exceptions = []
        host = url.host().lower().strip(".")
        if (
            not self.rust_engine.available
            or not host
            or not self.site_enabled(host)
        ):
            return
        resources = self.rust_engine.cosmetic_resources(url.toString())
        selectors = sorted(
            str(value)
            for value in resources.get("hide_selectors", [])
            if value
        )
        exceptions = sorted(
            str(value)
            for value in resources.get("exceptions", [])
            if value
        )
        native_scriptlets = str(resources.get("injected_script") or "")
        # YouTube serves video ads from its own infrastructure, so URL-level
        # blocking alone is insufficient. Run the bundled uBlock scriptlets at
        # document creation to prevent ad payloads from reaching the player.
        youtube_main, youtube_isolated = self.rust_engine.supplemental_scriptlets(
            host
        )
        # The isolated-world companion script interferes with YouTube's live
        # player in QtWebEngine. The main-world rules contain the playerAds,
        # adPlacements, and adSlots protections that prevent ads from being
        # selected, so keep those and omit only the incompatible live layer.
        if self._is_youtube_host(host):
            youtube_isolated = ""
        scriptlets = "\n".join(
            value for value in (native_scriptlets, youtube_main) if value
        )
        page._zyvro_adblock_exceptions = exceptions
        if not selectors and not scriptlets:
            return

        token = getattr(page.profile(), "_python_browser_adblock_token", "")
        prefix = f"__PY_BROWSER_ADBLOCK__{token}:"
        source = f"""
(() => {{
  const selectors = {json.dumps(selectors)};
  if (selectors.length) {{
    const installStyle = () => {{
      if (!document.documentElement) return false;
      let style = document.getElementById('zyvro-adblock-rust-site');
      if (!style) {{
        style = document.createElement('style');
        style.id = 'zyvro-adblock-rust-site';
        (document.head || document.documentElement).appendChild(style);
      }}
      style.textContent = selectors.map(value => value + '{{display:none!important}}').join('\\n');
      return true;
    }};
    if (!installStyle()) {{
      // Chromium can run DocumentCreation before it has parsed <html>.
      // Appending a style directly to Document can then replace the incoming
      // page root, so wait for a real documentElement before inserting CSS.
      const rootObserver = new MutationObserver(() => {{
        if (installStyle()) rootObserver.disconnect();
      }});
      rootObserver.observe(document, {{childList:true}});
      document.addEventListener('DOMContentLoaded', () => {{
        installStyle();
        rootObserver.disconnect();
      }}, {{once:true}});
    }}
    const report = () => {{
      let hidden = 0;
      for (const selector of selectors) {{
        try {{ hidden += document.querySelectorAll(selector).length; }} catch (_) {{}}
      }}
      if (hidden) console.info({json.dumps(prefix)} + hidden);
    }};
    if (document.readyState === 'loading')
      document.addEventListener('DOMContentLoaded', report, {{once:true}});
    else report();
  }}
  try {{
{scriptlets}
  }} catch (error) {{
    console.debug('Zyvro adblock scriptlet error', error);
  }}
}})();
"""
        script = QWebEngineScript()
        script.setName(self.PAGE_SCRIPT_NAME)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(source)
        scripts.insert(script)
        page._zyvro_adblock_page_script = script
        if youtube_isolated:
            isolated = QWebEngineScript()
            isolated.setName(self.ISOLATED_SCRIPT_NAME)
            isolated.setInjectionPoint(
                QWebEngineScript.InjectionPoint.DocumentCreation
            )
            isolated.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
            isolated.setRunsOnSubFrames(False)
            isolated.setSourceCode(youtube_isolated)
            scripts.insert(isolated)
            page._zyvro_adblock_isolated_script = isolated

    def apply_generic_cosmetics(self, page, url) -> None:
        """Resolve generic class/id rules against the loaded document."""
        host = url.host().lower().strip(".")
        if (
            not self.rust_engine.available
            or not host
            or not self.site_enabled(host)
            or self._is_youtube_host(host)
        ):
            return
        expected_url = url.toString()
        exceptions = list(getattr(page, "_zyvro_adblock_exceptions", []))
        collector = """
(() => {
  const classes = new Set(), ids = new Set();
  for (const element of document.querySelectorAll('*')) {
    if (element.id) ids.add(element.id);
    for (const name of element.classList || []) classes.add(name);
    if (classes.size + ids.size > 20000) break;
  }
  return {classes: [...classes], ids: [...ids]};
})()
"""

        def apply(values) -> None:
            if page.url().toString() != expected_url or not isinstance(values, dict):
                return
            selectors = self.rust_engine.generic_selectors(
                [str(value) for value in values.get("classes", [])],
                [str(value) for value in values.get("ids", [])],
                exceptions,
            )
            if not selectors:
                return
            token = getattr(page.profile(), "_python_browser_adblock_token", "")
            prefix = f"__PY_BROWSER_ADBLOCK__{token}:"
            injection = f"""
(() => {{
  const selectors = {json.dumps(selectors)};
  let style = document.getElementById({json.dumps(self.GENERIC_STYLE_ID)});
  if (!style) {{
    style = document.createElement('style');
    style.id = {json.dumps(self.GENERIC_STYLE_ID)};
    (document.head || document.documentElement).appendChild(style);
  }}
  style.textContent = selectors.map(value => value + '{{display:none!important}}').join('\\n');
  let hidden = 0;
  for (const selector of selectors) {{
    try {{ hidden += document.querySelectorAll(selector).length; }} catch (_) {{}}
  }}
  if (hidden) console.info({json.dumps(prefix)} + hidden);
}})();
"""
            page.runJavaScript(injection)

        page.runJavaScript(collector, apply)

    @staticmethod
    def _is_youtube_host(host: str) -> bool:
        host = str(host).lower().strip(".")
        return any(
            host == site or host.endswith(f".{site}")
            for site in (
                "youtube.com",
                "youtube-nocookie.com",
                "youtubekids.com",
            )
        )

    @staticmethod
    def _cosmetic_source(token: str, allowed_sites: list[str]) -> str:
        allowed_json = json.dumps(allowed_sites)
        prefix = f"__PY_BROWSER_ADBLOCK__{token}:"
        return f"""
(() => {{
  const host = location.hostname.toLowerCase();
  const allowed = {allowed_json};
  if (allowed.some(site => host === site || host.endsWith('.' + site))) return;
  const youtubeHosts = ['youtube.com', 'youtube-nocookie.com', 'youtubekids.com'];
  if (youtubeHosts.some(site => host === site || host.endsWith('.' + site))) return;
  const selectors = [
    '.adsbygoogle', '[data-ad-client]', '[data-ad-slot]',
    '[id^="google_ads_"]', '[id^="div-gpt-ad-"]',
    '[aria-label="Advertisement"]', '[aria-label="advertisement"]',
    '.advertisement', '[id="advertisement"]',
    '.ad-slot', '.ad-unit', '[id="interads"]',
    '[id^="interstitial-ad"]', '[class~="interstitial-ad"]',
    '[id^="ad-overlay"]', '[class~="ad-overlay"]',
    '[id^="ad-modal"]', '[class~="ad-modal"]',
    '[class~="ad-popup"]', '[class~="popup-ad"]',
    '[data-ad-format="interstitial"]', '[data-ad-format="overlay"]',
    '[class~="sponsored-ad"]', '[data-testid="ad-container"]',
    '[id^="taboola-"]', '.taboola', '[class~="outbrain"]'
  ];
  const selectorText = selectors.join(',');
  const warningText = /(?:adblocker? (?:has been )?detected|disable (?:your )?ad ?blocker|turn off (?:your )?ad ?blocker|whitelist (?:this|our) site)/i;
  const warningContainers = '[role="dialog"], [aria-modal="true"], [class*="modal"], [id*="modal"], [class*="popup"], [id*="popup"]';
  const residueText = /^(?:advertisement:?|sponsored|banner ads|do you see advertisements around this box\\?)$/i;
  const residueElements = 'p, span, h1, h2, h3, h4, h5, h6, div';
  const hideElement = (element) => {{
    if (!element || element.dataset.pythonBrowserAdHidden) return 0;
    element.dataset.pythonBrowserAdHidden = 'true';
    element.style.setProperty('display', 'none', 'important');
    return 1;
  }};
  const collapseAdResidue = (adElement) => {{
    let container = adElement.parentElement;
    for (let depth = 0; container && depth < 16; depth++, container = container.parentElement) {{
      for (const label of container.querySelectorAll(residueElements)) {{
        // Leaf text labels are safe to remove; avoiding nested DIVs prevents
        // broad wrappers from being classified by all descendant text.
        if (label.tagName === 'DIV' && label.childElementCount) continue;
        const text = (label.textContent || '').replace(/\\s+/g, ' ').trim();
        if (residueText.test(text)) hideElement(label);
      }}
      if (container.matches('.code-block, [data-ad-container], [data-ad-slot-container]')) {{
        hideElement(container);
      }}
      const stillHasAdSignal = container.querySelector(selectorText);
      const visibleText = (container.innerText || '').replace(/\\s+/g, ' ').trim();
      if (stillHasAdSignal && !visibleText && !['BODY', 'HTML'].includes(container.tagName)) {{
        hideElement(container);
      }}
    }}
  }};
  const hideAds = (root) => {{
    let hidden = 0;
    const candidates = [];
    if (root.nodeType === 1 && root.matches && root.matches(selectorText)) candidates.push(root);
    if (root.querySelectorAll) candidates.push(...root.querySelectorAll(selectorText));
    for (const element of candidates) {{
      hidden += hideElement(element);
    }}
    for (const element of candidates) collapseAdResidue(element);
    const warnings = [];
    if (root.nodeType === 1 && root.matches && root.matches(warningContainers)) warnings.push(root);
    if (root.querySelectorAll) warnings.push(...root.querySelectorAll(warningContainers));
    for (const element of warnings) {{
      const text = (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim();
      if (text.length >= 12 && text.length <= 2500 && warningText.test(text)) {{
        hidden += hideElement(element);
      }}
    }}
    if (hidden) console.info('{prefix}' + hidden);
  }};
  const start = () => {{
    hideAds(document);
    new MutationObserver(records => {{
      for (const record of records) for (const node of record.addedNodes) hideAds(node);
    }}).observe(document.documentElement, {{childList:true, subtree:true}});
  }};
  if (document.documentElement) start();
  else document.addEventListener('DOMContentLoaded', start, {{once:true}});
}})();
"""

    @Slot(str, str, str)
    def _record_network_block(
        self, site: str, source: str, private_session: str
    ) -> None:
        self.record(site, source, 1, private_session)

    def record_cosmetic(self, site: str, amount: int, private_session: str) -> None:
        if amount > 0:
            self.record(site, "cosmetic", amount, private_session)

    def record(
        self, site: str, source: str, amount: int, private_session: str = ""
    ) -> None:
        site = site or "Unknown site"
        amount = max(0, int(amount))
        if not amount:
            return
        if private_session:
            sites = self._private_counts.setdefault(private_session, {})
            sites[site] = sites.get(site, 0) + amount
        else:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO adblock_events(site, source, amount, blocked_at) VALUES (?, ?, ?, ?)",
                    (site, source, amount, datetime.now().astimezone().isoformat(timespec="seconds")),
                )
                connection.commit()
        self.blocked.emit(site, amount, private_session, source)
        self.changed.emit()

    def count_today(self, private_session: str = "") -> int:
        if private_session:
            return sum(self._private_counts.get(private_session, {}).values())
        today = datetime.now().astimezone().date().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(amount), 0) AS count FROM adblock_events WHERE substr(blocked_at, 1, 10) = ?",
                (today,),
            ).fetchone()
        return int(row["count"])

    def count_total(self, private_session: str = "") -> int:
        if private_session:
            return self.count_today(private_session)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(amount), 0) AS count FROM adblock_events"
            ).fetchone()
        return int(row["count"])


class CombinedRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Runs the kill switch, tracker protection, then ad blocking."""

    def __init__(
        self, adblock: AdBlockInterceptor, tracker, vpn_manager=None, parent=None
    ) -> None:
        super().__init__(parent)
        self.adblock = adblock
        self.tracker = tracker
        self.vpn_manager = vpn_manager

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        if (
            self.vpn_manager is not None
            and self.vpn_manager.should_block_network()
            and info.requestUrl().scheme().lower() in {"http", "https", "ws", "wss"}
        ):
            info.block(True)
            return
        # Let the dedicated tracker module classify known analytics first so
        # its privacy report remains accurate even when the Rust lists also
        # contain the same domain.
        if self.tracker.interceptRequest(info):
            return
        self.adblock.interceptRequest(info)
