"""Real system-route, Tor SOCKS5, custom proxy, and kill-switch handling."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import socket
import subprocess
import threading
import time

from qtpy.QtCore import QCoreApplication, QObject, QStandardPaths, QTimer, Signal, Slot
from qtpy.QtNetwork import QNetworkInterface, QNetworkProxy


class VpnProxyManager(QObject):
    """Own the application-wide route used by Qt WebEngine/Chromium.

    Qt WebEngine forwards QNetworkProxy.applicationProxy() into Chromium's
    network stack. One shared manager therefore covers persistent and
    off-the-record profiles without mixing their browser data.
    """

    state_changed = Signal()
    _poll_finished = Signal(str, bool)
    _tor_bootstrap_finished = Signal(bool, int, str)

    MODES = {"off", "system", "tor", "custom"}
    PRIVATE_MODES = {"tor", "custom"}
    _baseline_proxy: QNetworkProxy | None = None
    COUNTRIES = (
        "United States", "Canada", "United Kingdom", "Germany",
        "Netherlands", "France", "Switzerland", "Japan", "Singapore",
        "Australia",
    )

    def __init__(self, settings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        if VpnProxyManager._baseline_proxy is None:
            VpnProxyManager._baseline_proxy = QNetworkProxy(
                QNetworkProxy.applicationProxy()
            )
        self._session_password = ""
        self._mode = "off"
        self._kill_switch = False
        self._state = "disconnected"
        self._detail = "Disconnected"
        self._active_host = ""
        self._active_port = 0
        self._poll_in_progress = False
        self._local_tor_process: subprocess.Popen | None = None
        self._tor_bootstrap_in_progress = False
        self._poll_finished.connect(self._handle_poll_result)
        self._tor_bootstrap_finished.connect(self._handle_tor_bootstrap)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(10_000)
        self._poll_timer.timeout.connect(self._poll_connection)
        self._poll_timer.start()
        application = QCoreApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.shutdown)
        self.reload_from_settings()

    def mode(self) -> str:
        return self._mode

    def state(self) -> str:
        return self._state

    def detail(self) -> str:
        return self._detail

    def active_endpoint(self) -> tuple[str, int]:
        return self._active_host, self._active_port

    def has_local_tor(self) -> bool:
        return self._bundled_tor_executable() is not None

    def selected_country(self) -> str:
        country = str(self.settings.value("privacy/vpn_country"))
        return country if country in self.COUNTRIES else self.COUNTRIES[0]

    def server_profiles(self) -> dict[str, dict[str, object]]:
        try:
            profiles = json.loads(
                str(self.settings.value("privacy/vpn_server_profiles_json") or "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            profiles = {}
        return {
            country: profile for country, profile in profiles.items()
            if country in self.COUNTRIES and isinstance(profile, dict)
        }

    def profile_for_country(self, country: str) -> dict[str, object] | None:
        profile = self.server_profiles().get(country)
        if profile and str(profile.get("host", "")).strip():
            return profile
        # Migrate the original single custom-proxy fields into the selected
        # country without inventing a public server.
        if country == self.selected_country():
            host = str(self.settings.value("privacy/proxy_host")).strip()
            if host:
                return {
                    "type": str(self.settings.value("privacy/proxy_type")),
                    "host": host,
                    "port": int(self.settings.value("privacy/proxy_port")),
                    "username": str(self.settings.value("privacy/proxy_username")),
                }
        return None

    def save_server_profile(
        self, country: str, proxy_type: str, host: str, port: int,
        username: str = "",
    ) -> None:
        if country not in self.COUNTRIES:
            return
        profiles = self.server_profiles()
        if host.strip():
            profiles[country] = {
                "type": "http" if proxy_type == "http" else "socks5",
                "host": host.strip(), "port": int(port),
                "username": username.strip(),
            }
        else:
            profiles.pop(country, None)
        # Passwords are deliberately excluded; only ordinary configuration is
        # persisted locally.
        self.settings.save_values({
            "privacy/vpn_country": country,
            "privacy/vpn_server_profiles_json": json.dumps(profiles),
        })

    def connect_country(self, country: str, password: str = "") -> bool:
        if country not in self.COUNTRIES:
            self._set_state("disconnected", "Server not configured")
            return False
        self.settings.save_values({"privacy/vpn_country": country})
        profile = self.profile_for_country(country)
        if not profile:
            self._mode = "custom"
            self.settings.save_values({"privacy/vpn_mode": "custom"})
            self._restore_system_route()
            self._set_state("disconnected", "Server not configured")
            return False
        self.settings.save_values({
            "privacy/proxy_type": profile.get("type", "socks5"),
            "privacy/proxy_host": profile.get("host", ""),
            "privacy/proxy_port": int(profile.get("port", 1080)),
            "privacy/proxy_username": profile.get("username", ""),
        })
        return self.connect_mode("custom", password)

    def kill_switch_enabled(self) -> bool:
        return self._kill_switch

    def should_block_network(self) -> bool:
        return (
            self.kill_switch_enabled()
            and self.mode() in self.PRIVATE_MODES
            and self._state != "connected"
        )

    def set_session_password(self, password: str) -> None:
        # Kept only in memory. It is intentionally never written to QSettings.
        if password:
            self._session_password = password

    def set_kill_switch(self, enabled: bool) -> None:
        self._kill_switch = bool(enabled)
        self.settings.save_values({"privacy/vpn_kill_switch": bool(enabled)})
        self.state_changed.emit()

    def connect_mode(self, mode: str, password: str = "") -> bool:
        mode = mode.lower()
        if mode not in self.MODES:
            mode = "off"
        if mode != "tor":
            self._stop_local_tor()
        self._mode = mode
        if mode in self.PRIVATE_MODES:
            self._set_state("connecting", "Connecting to private network…")
        if password:
            self.set_session_password(password)
        self.settings.save_values({"privacy/vpn_mode": mode})
        return self._apply_mode(mode)

    def disconnect(self) -> None:
        self._mode = "off"
        self.settings.save_values({"privacy/vpn_mode": "off"})
        self._stop_local_tor()
        self._restore_system_route()
        self._set_state("disconnected", "Disconnected")

    def reload_from_settings(self, password: str = "") -> bool:
        if password:
            self.set_session_password(password)
        self.settings.sync()
        configured_mode = str(self.settings.value("privacy/vpn_mode")).lower()
        self._mode = configured_mode if configured_mode in self.MODES else "off"
        if self._mode != "tor":
            self._stop_local_tor()
        self._kill_switch = bool(
            self.settings.value("privacy/vpn_kill_switch")
        )
        if self._mode in self.PRIVATE_MODES:
            self._set_state("connecting", "Connecting to private network…")
        return self._apply_mode(self._mode)

    def _apply_mode(self, mode: str) -> bool:
        if mode == "off":
            self._restore_system_route()
            self._set_state("disconnected", "Disconnected")
            return True
        if mode == "system":
            self._restore_system_route()
            interface = self.detect_system_vpn()
            if interface:
                self._set_state(
                    "connected", f"Connected through system VPN ({interface})"
                )
                return True
            self._set_state("disconnected", "System VPN not detected")
            return False
        if mode == "tor":
            return self._connect_tor()
        return self._connect_custom_proxy()

    def _connect_tor(self) -> bool:
        host = str(self.settings.value("privacy/tor_host")).strip() or "127.0.0.1"
        configured_port = int(self.settings.value("privacy/tor_port"))
        ports = [configured_port]
        if host in {"127.0.0.1", "localhost", "::1"}:
            for fallback in (9050, 9150):
                if fallback not in ports:
                    ports.append(fallback)
        for port in ports:
            if self._probe_socks5(host, port, "", ""):
                proxy = QNetworkProxy(
                    QNetworkProxy.ProxyType.Socks5Proxy, host, port
                )
                QNetworkProxy.setApplicationProxy(proxy)
                self._active_host, self._active_port = host, port
                self._set_state("connected", "Connected through Tor")
                return True

        # No external Tor SOCKS service answered, so start the local Expert
        # Bundle shipped with this browser and finish connecting asynchronously
        # after Tor has bootstrapped. Connected is never shown before SOCKS5
        # actually responds.
        executable = self._bundled_tor_executable()
        if executable is not None:
            return self._start_local_tor(executable, host, configured_port)

        self._restore_system_route()
        checked = ", ".join(f"{host}:{port}" for port in ports)
        self._set_state(
            "disconnected",
            f"Tor service not detected. No SOCKS5 service answered at {checked}.",
        )
        return False

    def _bundled_tor_executable(self) -> Path | None:
        executable = (
            Path(__file__).resolve().parent
            / "runtime" / "tor-expert-bundle" / "tor" / "tor.exe"
        )
        return executable if executable.is_file() else None

    def _start_local_tor(self, executable: Path, host: str, port: int) -> bool:
        if self._tor_bootstrap_in_progress:
            return True
        if self._local_tor_process is not None:
            if self._local_tor_process.poll() is None:
                self._begin_tor_bootstrap_probe(host, port)
                return True
            self._local_tor_process = None

        local_data = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.AppLocalDataLocation
        )
        data_directory = Path(local_data) / "tor-data"
        data_directory.mkdir(parents=True, exist_ok=True)
        bundle_root = executable.parent.parent
        arguments = [
            str(executable),
            "--ClientOnly", "1",
            "--SocksPort", f"{host}:{port}",
            "--DataDirectory", str(data_directory),
            "--GeoIPFile", str(bundle_root / "data" / "geoip"),
            "--GeoIPv6File", str(bundle_root / "data" / "geoip6"),
            "--Log", f"notice file {data_directory / 'tor.log'}",
        ]
        try:
            self._local_tor_process = subprocess.Popen(
                arguments,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            self._set_state("error", f"Could not start local Tor: {error}")
            return False

        self._set_state("connecting", "Starting local Tor…")
        self._begin_tor_bootstrap_probe(host, port)
        return True

    def _begin_tor_bootstrap_probe(self, host: str, port: int) -> None:
        if self._tor_bootstrap_in_progress:
            return
        self._tor_bootstrap_in_progress = True

        def wait_for_socks() -> None:
            deadline = time.monotonic() + 45
            detail = "Tor did not become ready within 45 seconds"
            while time.monotonic() < deadline:
                process = self._local_tor_process
                if process is None or process.poll() is not None:
                    exit_code = None if process is None else process.returncode
                    detail = f"Local Tor stopped before connecting (exit {exit_code})"
                    break
                if self._probe_socks5(host, port, "", ""):
                    self._tor_bootstrap_finished.emit(True, port, "")
                    return
                time.sleep(0.25)
            self._tor_bootstrap_finished.emit(False, port, detail)

        threading.Thread(target=wait_for_socks, daemon=True).start()

    @Slot(bool, int, str)
    def _handle_tor_bootstrap(self, available: bool, port: int, detail: str) -> None:
        self._tor_bootstrap_in_progress = False
        if self.mode() != "tor":
            return
        host = str(self.settings.value("privacy/tor_host")).strip() or "127.0.0.1"
        if available:
            proxy = QNetworkProxy(
                QNetworkProxy.ProxyType.Socks5Proxy, host, port
            )
            QNetworkProxy.setApplicationProxy(proxy)
            self._active_host, self._active_port = host, port
            self._set_state("connected", "Connected through local Tor")
            return
        self._restore_system_route()
        self._stop_local_tor()
        self._set_state("error", detail)

    def _stop_local_tor(self) -> None:
        process = self._local_tor_process
        self._local_tor_process = None
        self._tor_bootstrap_in_progress = False
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    @Slot()
    def shutdown(self) -> None:
        """Stop only the Tor daemon launched by this browser instance."""
        self._stop_local_tor()

    def _connect_custom_proxy(self) -> bool:
        host = str(self.settings.value("privacy/proxy_host")).strip()
        port = int(self.settings.value("privacy/proxy_port"))
        proxy_type = str(self.settings.value("privacy/proxy_type")).lower()
        username = str(self.settings.value("privacy/proxy_username")).strip()
        password = self._session_password
        if not host or not (1 <= port <= 65535):
            self._restore_system_route()
            self._set_state("disconnected", "Custom proxy is not configured")
            return False

        if proxy_type == "http":
            available = self._probe_http(host, port, username, password)
            qt_type = QNetworkProxy.ProxyType.HttpProxy
        else:
            available = self._probe_socks5(host, port, username, password)
            qt_type = QNetworkProxy.ProxyType.Socks5Proxy
        if not available:
            self._restore_system_route()
            self._set_state("error", "Custom proxy unavailable or rejected")
            return False

        proxy = QNetworkProxy(qt_type, host, port, username, password)
        QNetworkProxy.setApplicationProxy(proxy)
        self._active_host, self._active_port = host, port
        self._set_state("connected", "Connected through custom proxy")
        return True

    def _poll_connection(self) -> None:
        if self.mode() == "system":
            interface = self.detect_system_vpn()
            if interface:
                self._set_state(
                    "connected", f"Connected through system VPN ({interface})"
                )
            else:
                self._set_state("disconnected", "System VPN not detected")
            return
        if self.mode() not in self.PRIVATE_MODES:
            return
        if self._state != "connected" or self._poll_in_progress:
            return
        mode = self.mode()
        host, port = self._active_host, self._active_port
        proxy_type = str(self.settings.value("privacy/proxy_type")).lower()
        username = str(self.settings.value("privacy/proxy_username")).strip()
        password = self._session_password
        self._poll_in_progress = True

        def probe() -> None:
            if mode == "tor" or proxy_type != "http":
                available = self._probe_socks5(
                    host, port, "" if mode == "tor" else username,
                    "" if mode == "tor" else password,
                )
            else:
                available = self._probe_http(
                    host, port, username, password
                )
            self._poll_finished.emit(mode, available)

        threading.Thread(target=probe, daemon=True).start()

    @Slot(str, bool)
    def _handle_poll_result(self, mode: str, available: bool) -> None:
        self._poll_in_progress = False
        if mode != self.mode():
            return
        if not available:
            # Keep the dead explicit proxy installed. This prevents an
            # accidental direct fallback even before the kill-switch request
            # interceptor sees subsequent traffic.
            self._set_state("error", "Private connection lost")

    def detect_system_vpn(self) -> str:
        """Conservatively identify a running VPN-style network adapter."""
        markers = (
            " vpn", "vpn ", "openvpn", "wireguard", "wintun", "mullvad",
            "proton", "nordlynx", "nordvpn", "forticlient", "anyconnect",
            "globalprotect", "tap-windows", "tailscale tunnel", "ikev2",
            "l2tp", "pptp", "wan miniport",
        )
        flags_enum = QNetworkInterface.InterfaceFlag
        for interface in QNetworkInterface.allInterfaces():
            flags = interface.flags()
            if not (flags & flags_enum.IsUp and flags & flags_enum.IsRunning):
                continue
            if flags & flags_enum.IsLoopBack:
                continue
            if interface.type() == QNetworkInterface.InterfaceType.Ppp:
                return interface.humanReadableName() or interface.name()
            label = f" {interface.name()} {interface.humanReadableName()} ".lower()
            if any(marker in label for marker in markers):
                return interface.humanReadableName() or interface.name()
        return ""

    @staticmethod
    def _probe_socks5(
        host: str, port: int, username: str, password: str
    ) -> bool:
        try:
            with socket.create_connection((host, port), timeout=0.8) as connection:
                connection.settimeout(0.8)
                methods = b"\x00\x02" if username else b"\x00"
                connection.sendall(b"\x05" + bytes([len(methods)]) + methods)
                response = connection.recv(2)
                if len(response) != 2 or response[0] != 5 or response[1] == 255:
                    return False
                if response[1] == 2:
                    user = username.encode("utf-8")[:255]
                    secret = password.encode("utf-8")[:255]
                    connection.sendall(
                        b"\x01" + bytes([len(user)]) + user
                        + bytes([len(secret)]) + secret
                    )
                    auth = connection.recv(2)
                    return len(auth) == 2 and auth[1] == 0
                return response[1] == 0
        except (OSError, OverflowError):
            return False

    @staticmethod
    def _probe_http(
        host: str, port: int, username: str, password: str
    ) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.0) as connection:
                connection.settimeout(1.0)
                headers = [
                    "CONNECT example.com:443 HTTP/1.1",
                    "Host: example.com:443",
                    "Proxy-Connection: close",
                ]
                if username:
                    token = base64.b64encode(
                        f"{username}:{password}".encode("utf-8")
                    ).decode("ascii")
                    headers.append(f"Proxy-Authorization: Basic {token}")
                connection.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))
                response = connection.recv(96)
                return response.startswith(b"HTTP/") and b" 200 " in response.split(b"\r\n", 1)[0]
        except (OSError, OverflowError):
            return False

    def _restore_system_route(self) -> None:
        baseline = VpnProxyManager._baseline_proxy or QNetworkProxy(
            QNetworkProxy.ProxyType.NoProxy
        )
        QNetworkProxy.setApplicationProxy(QNetworkProxy(baseline))
        self._active_host, self._active_port = "", 0

    def _set_state(self, state: str, detail: str) -> None:
        changed = state != self._state or detail != self._detail
        self._state, self._detail = state, detail
        if changed:
            self.state_changed.emit()
