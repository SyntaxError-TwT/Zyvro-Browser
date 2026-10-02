"""Create Zyvro's Ed25519-signed update manifest without persisting the key."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--download-url", required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()

    private_value = os.environ.get("ZYVRO_UPDATE_PRIVATE_KEY", "").strip()
    if not private_value:
        raise SystemExit("ZYVRO_UPDATE_PRIVATE_KEY is required")
    package = arguments.package.resolve()
    if not package.is_file():
        raise SystemExit(f"Update package not found: {package}")
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    payload = {
        "version": arguments.version,
        "download_url": arguments.download_url,
        "notes": arguments.notes,
        "sha256": digest,
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    private_key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(private_value, validate=True)
    )
    payload["signature"] = base64.b64encode(
        private_key.sign(canonical)
    ).decode("ascii")
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(arguments.output)
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
