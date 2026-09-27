"""Focused checks for navigation, archive, and signed-update protections."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from qtpy.QtCore import QUrl

from browser_maintenance import UpdateChecker
from malware_scanner import MalwareScanner
from navigation_security import NavigationSecurityManager


class _Settings:
    def __init__(self, **values): self.values = values
    def value(self, key): return self.values.get(key)


class _Response:
    def __init__(self, payload: bytes): self.payload = payload; self.offset = 0
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, amount=-1):
        if amount < 0:
            result, self.payload = self.payload, b""
            return result
        result, self.payload = self.payload[:amount], self.payload[amount:]
        return result


class SecurityFeatureTests(unittest.TestCase):
    def test_local_host_blocklist_and_session_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            manager = NavigationSecurityManager(
                _Settings(**{
                    "security/https_only_enabled": True,
                    "security/malicious_site_protection": True,
                }), Path(temporary) / "security.sqlite3",
            )
            self.assertEqual(manager.import_hosts("0.0.0.0 malware.example\n"), 1)
            self.assertTrue(manager.is_malicious("sub.malware.example"))
            manager.allow_malicious_for_session("sub.malware.example")
            self.assertFalse(manager.is_malicious("sub.malware.example"))
            self.assertTrue(manager.should_upgrade(QUrl("http://example.com/a")))
            self.assertFalse(manager.should_upgrade(QUrl("http://127.0.0.1/a")))

    def test_zip_bomb_ratio_is_detected_without_extracting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "large-ratio.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
                output.writestr("payload.txt", b"A" * 2_000_000)
            findings = MalwareScanner._archive_findings(archive)
            self.assertTrue(any("ZIP bomb" in item["text"] for item in findings))

    def test_signed_manifest_and_package_hash_are_required(self) -> None:
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        package = b"verified browser update"
        manifest = {
            "version": "9.0.0",
            "download_url": "https://updates.example/browser.bin",
            "sha256": hashlib.sha256(package).hexdigest(),
            "notes": "Security update",
        }
        canonical = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        manifest["signature"] = base64.b64encode(private.sign(canonical)).decode()
        encoded = json.dumps(manifest).encode()
        with patch("browser_maintenance.urlopen", return_value=_Response(encoded)):
            result = UpdateChecker().check(
                "https://updates.example/manifest.json",
                base64.b64encode(public).decode(),
            )
        self.assertTrue(result["signature_verified"])
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "update.bin"
            with patch("browser_maintenance.urlopen", return_value=_Response(package)):
                UpdateChecker.download_and_verify(result, target)
            self.assertEqual(target.read_bytes(), package)


if __name__ == "__main__":
    unittest.main()
