"""Official VirusTotal v3 client with secure key lookup and quota throttling."""

from __future__ import annotations

import os
from pathlib import Path
import threading
import time

import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder

try:
    import keyring
except ImportError:  # The scanner remains honest/unavailable without keyring.
    keyring = None


class VirusTotalError(RuntimeError):
    pass


class VirusTotalNotFound(VirusTotalError):
    pass


class VirusTotalRateLimit(VirusTotalError):
    def __init__(self, retry_after: int) -> None:
        super().__init__("VirusTotal rate limit reached; scan remains queued")
        self.retry_after = retry_after


class VirusTotalClient:
    BASE = "https://www.virustotal.com/api/v3"
    SERVICE = "Python Browser VirusTotal"
    ACCOUNT = "api-key"

    def __init__(self, *, public_api: bool = True) -> None:
        self.public_api = public_api
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._validated = False
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "PythonBrowser-SecurityScanner/1.0"})

    @staticmethod
    def secure_storage_available() -> bool:
        return keyring is not None

    def api_key(self) -> str:
        environment = os.environ.get("VT_API_KEY", "").strip()
        if environment:
            return environment
        if keyring is None:
            return ""
        try:
            return (keyring.get_password(self.SERVICE, self.ACCOUNT) or "").strip()
        except Exception:
            return ""

    def set_api_key(self, value: str) -> bool:
        if keyring is None:
            return False
        try:
            if value.strip():
                keyring.set_password(self.SERVICE, self.ACCOUNT, value.strip())
            else:
                keyring.delete_password(self.SERVICE, self.ACCOUNT)
            self._validated = False
            return True
        except Exception:
            return False

    def configured(self) -> bool:
        return bool(self.api_key())

    def validated(self) -> bool:
        return self.configured() and self._validated

    def validate_key(self) -> bool:
        try:
            self._request("GET", f"{self.BASE}/files/{'0' * 64}")
        except VirusTotalNotFound:
            return True
        except VirusTotalError:
            return False
        return True

    def _throttle(self) -> None:
        # VirusTotal's public API permits four requests per minute. Premium
        # accounts can disable this conservative public-tier throttle.
        minimum = 15.1 if self.public_api else 0.05
        with self._lock:
            delay = minimum - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()

    def _request(self, method: str, url: str, **kwargs):
        key = self.api_key()
        if not key:
            raise VirusTotalError("VirusTotal unavailable — no API key configured")
        headers = dict(kwargs.pop("headers", {})); headers["x-apikey"] = key
        timeout = kwargs.pop("timeout", (20, 300))
        response = None
        for attempt in range(2):
            self._throttle()
            response = self.session.request(method, url, headers=headers, timeout=timeout, **kwargs)
            if response.status_code != 429:
                break
            retry_after = min(120, max(15, int(response.headers.get("Retry-After", "15") or 15)))
            if method.upper() != "GET" or attempt:
                raise VirusTotalRateLimit(retry_after)
            time.sleep(retry_after)
        assert response is not None
        if response.status_code not in {401, 403}:
            self._validated = True
        if response.status_code == 404:
            raise VirusTotalNotFound("Hash is not known to VirusTotal")
        if response.status_code in {401, 403}:
            raise VirusTotalError("VirusTotal rejected the configured API key or account tier")
        try:
            response.raise_for_status()
        except requests.RequestException as error:
            raise VirusTotalError(f"VirusTotal request failed: HTTP {response.status_code}") from error
        try:
            return response.json()
        except ValueError as error:
            raise VirusTotalError("VirusTotal returned an invalid response") from error

    def file_report(self, sha256: str) -> dict:
        payload = self._request("GET", f"{self.BASE}/files/{sha256}")
        return self._normalize(payload, "known")

    def upload(self, path: Path) -> str:
        path = Path(path)
        upload_url = f"{self.BASE}/files"
        if path.stat().st_size > 32 * 1024 * 1024:
            upload_url = str(self._request("GET", f"{self.BASE}/files/upload_url").get("data", ""))
            if not upload_url.startswith("https://"):
                raise VirusTotalError("VirusTotal did not provide a valid large-file upload URL")
        payload = None
        for attempt in range(2):
            try:
                with path.open("rb") as handle:
                    encoder = MultipartEncoder(
                        fields={"file": (path.name, handle, "application/octet-stream")}
                    )
                    payload = self._request(
                        "POST", upload_url,
                        data=encoder, headers={"Content-Type": encoder.content_type},
                    )
                break
            except VirusTotalRateLimit as error:
                if attempt:
                    raise
                time.sleep(error.retry_after)
        if payload is None:
            raise VirusTotalError("VirusTotal upload did not complete")
        analysis_id = str(payload.get("data", {}).get("id", ""))
        if not analysis_id:
            raise VirusTotalError("VirusTotal did not return an analysis ID")
        return analysis_id

    def analysis(self, analysis_id: str) -> dict:
        payload = self._request("GET", f"{self.BASE}/analyses/{analysis_id}")
        attributes = payload.get("data", {}).get("attributes", {})
        status = str(attributes.get("status", "unknown"))
        result = self._normalize(payload, status)
        result["analysis_id"] = analysis_id
        return result

    @staticmethod
    def _normalize(payload: dict, status: str) -> dict:
        attributes = payload.get("data", {}).get("attributes", {})
        stats = attributes.get("last_analysis_stats") or attributes.get("stats") or {}
        engines = attributes.get("last_analysis_results") or attributes.get("results") or {}
        detections = []
        for engine, result in engines.items():
            if result.get("category") in {"malicious", "suspicious"}:
                detections.append({
                    "engine": result.get("engine_name") or engine,
                    "category": result.get("category"),
                    "name": result.get("result") or "Detected",
                })
        total = sum(int(value or 0) for value in stats.values())
        return {
            "status": status,
            "malicious": int(stats.get("malicious", 0) or 0),
            "suspicious": int(stats.get("suspicious", 0) or 0),
            "total": total,
            "detections": detections,
        }
