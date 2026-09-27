"""Windows antivirus discovery and honest local scan integration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time


POPULAR_ANTIVIRUS_NAMES = {
    "windows defender": "Microsoft Defender",
    "microsoft defender": "Microsoft Defender",
    "norton": "Norton",
    "symantec": "Norton / Symantec",
    "mcafee": "McAfee",
    "bitdefender": "Bitdefender",
    "kaspersky": "Kaspersky",
    "avast": "Avast",
    "avg": "AVG",
    "eset": "ESET",
    "malwarebytes": "Malwarebytes",
    "trend micro": "Trend Micro",
    "sophos": "Sophos",
    "webroot": "Webroot",
    "avira": "Avira",
    "f-secure": "F-Secure",
    "panda": "Panda Dome",
    "comodo": "Comodo",
    "crowdstrike": "CrowdStrike Falcon",
    "sentinelone": "SentinelOne",
    # Chinese consumer and enterprise security products. Windows Security
    # Center may report an English brand, transliteration, or Chinese name,
    # depending on the installed build and language.
    "360 total security": "360 Total Security",
    "360 antivirus": "360 Total Security",
    "360 safe guard": "360 Total Security",
    "360安全卫士": "360 Total Security",
    "360杀毒": "360 Total Security",
    "qihoo": "360 Total Security",
    "qihu": "360 Total Security",
    "huorong": "Huorong Internet Security",
    "火绒": "Huorong Internet Security",
    "tencent pc manager": "Tencent PC Manager",
    "tencent computer manager": "Tencent PC Manager",
    "腾讯电脑管家": "Tencent PC Manager",
    "电脑管家": "Tencent PC Manager",
    "kingsoft antivirus": "Kingsoft Antivirus",
    "kingsoft internet security": "Kingsoft Antivirus",
    "kingsoft duba": "Kingsoft Antivirus",
    "jinshan": "Kingsoft Antivirus",
    "金山毒霸": "Kingsoft Antivirus",
    "rising antivirus": "Rising Antivirus",
    "rising internet security": "Rising Antivirus",
    "瑞星": "Rising Antivirus",
    "jiangmin": "Jiangmin Antivirus",
    "kv antivirus": "Jiangmin Antivirus",
    "江民": "Jiangmin Antivirus",
    "baidu antivirus": "Baidu Antivirus",
    "百度杀毒": "Baidu Antivirus",
    "micropoint": "Micropoint Security",
    "微点": "Micropoint Security",
    "antiy": "Antiy AVL",
    "安天": "Antiy AVL",
    "sangfor endpoint": "Sangfor Endpoint Secure",
    "深信服": "Sangfor Endpoint Secure",
    "topsec": "Topsec Endpoint Security",
    "天融信": "Topsec Endpoint Security",
    "venustech": "Venustech Endpoint Security",
    "启明星辰": "Venustech Endpoint Security",
}

# Runtime fallback signatures are used only when a product is missing from
# Windows Security Center. A matching service must be running or a matching
# process must exist; an installed but stopped executable is not called active.
RUNTIME_SIGNATURES = {
    "Huorong Internet Security": (
        "hipsdaemon", "hipstray", "hipsmain", "wsctrlsvc", "hrwscctrl",
        "\\huorong\\", "\\sysdiag\\",
    ),
    "360 Total Security": (
        "360tray", "360sd", "360rp", "qhactivedefense", "qhwatchdog",
        "zhudongfangyu", "\\360\\",
    ),
    "Tencent PC Manager": (
        "qqpctray", "qqpcrtp", "qqpcmgr", "tencent pc manager",
        "tencent computer manager",
    ),
    "Kingsoft Antivirus": (
        "kxescore", "kxetray", "ksafe", "kingsoft antivirus",
        "kingsoft internet security",
    ),
    "Rising Antivirus": (
        "ravmond", "ravservice", "rstray", "rising antivirus",
    ),
    "Jiangmin Antivirus": (
        "kvmon", "kvcenter", "kvsrv", "jiangmin antivirus",
    ),
    "Baidu Antivirus": (
        "bavsvc", "bavtray", "baidu antivirus",
    ),
    "Micropoint Security": (
        "mpmon", "micropoint security",
    ),
    "Antiy AVL": ("antiy", "avlguard"),
    "Sangfor Endpoint Secure": ("sangfor", "sfavservice"),
    "Topsec Endpoint Security": ("topsec",),
    "Venustech Endpoint Security": ("venustech",),
}


class AntivirusDetector:
    """Reads products registered with Windows Security Center.

    Microsoft Defender exposes a supported per-file command-line scanner.
    Other active products are identified as real-time protection providers;
    the browser does not invent unsupported vendor command lines.
    """

    @staticmethod
    def canonical_name(raw_name: str) -> str:
        lower = str(raw_name).lower().strip()
        return next(
            (label for key, label in POPULAR_ANTIVIRUS_NAMES.items() if key in lower),
            str(raw_name).strip() or "Unknown antivirus",
        )

    @staticmethod
    def runtime_product_name(*values: str) -> str | None:
        combined = " ".join(str(value or "") for value in values).lower()
        return next(
            (
                product
                for product, signatures in RUNTIME_SIGNATURES.items()
                if any(signature in combined for signature in signatures)
            ),
            None,
        )

    def detect(self) -> list[dict]:
        if os.name != "nt":
            return []
        script = (
            "$items=Get-CimInstance -Namespace root/SecurityCenter2 "
            "-ClassName AntiVirusProduct -ErrorAction SilentlyContinue | "
            "Select-Object displayName,productState,pathToSignedProductExe;"
            "$items | ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            payload = json.loads(completed.stdout.strip() or "[]")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return []
        rows = payload if isinstance(payload, list) else [payload]
        products = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw_name = str(row.get("displayName") or "Unknown antivirus").strip()
            state = int(row.get("productState") or 0)
            state_byte = (state >> 8) & 0xFF
            # SecurityCenter2 commonly reports 0x10/0x11 for enabled products;
            # lower values represent inactive/passive registrations.
            active = state_byte in {0x10, 0x11}
            canonical = self.canonical_name(raw_name)
            products.append({
                "name": canonical,
                "reported_name": raw_name,
                "active": active,
                "product_state": state,
                "defender": "defender" in raw_name.lower(),
                "detection_source": "Windows Security Center",
            })
        registered_names = {product["name"] for product in products}
        for runtime in self._runtime_products():
            if runtime["name"] not in registered_names:
                products.append(runtime)
        return products

    def _runtime_products(self) -> list[dict]:
        script = (
            "$s=Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | "
            "Where-Object {$_.State -eq 'Running'} | "
            "Select-Object @{n='kind';e={'service'}},Name,DisplayName,PathName,State;"
            "$p=Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
            "Select-Object @{n='kind';e={'process'}},Name,ExecutablePath;"
            "$all=@($s)+@($p);$all | ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=12,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            payload = json.loads(completed.stdout.strip() or "[]")
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
            return []
        rows = payload if isinstance(payload, list) else [payload]
        matches: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            product = self.runtime_product_name(
                row.get("Name", ""), row.get("DisplayName", ""),
                row.get("PathName", ""), row.get("ExecutablePath", ""),
            )
            if not product:
                continue
            component = str(row.get("Name") or row.get("DisplayName") or "runtime component")
            existing = matches.get(product)
            if existing:
                existing["runtime_components"].append(component)
                continue
            matches[product] = {
                "name": product,
                "reported_name": f"{product} (runtime fallback)",
                "active": True,
                "product_state": 0,
                "defender": False,
                "detection_source": "Running service/process fallback",
                "runtime_components": [component],
            }
        return list(matches.values())

    def active_products(self) -> list[dict]:
        return [product for product in self.detect() if product["active"]]

    def scan(self, path: Path, products: list[dict]) -> dict:
        names = [product["name"] for product in products]
        defender = next((product for product in products if product["defender"]), None)
        if defender:
            return self._scan_with_defender(Path(path), defender["name"])
        # Active third-party products inspect newly written downloads through
        # their registered real-time protection. There is no shared Windows API
        # for starting every vendor's private command-line scanner.
        time.sleep(0.5)
        if not Path(path).exists():
            return {
                "status": "malware", "products": names,
                "summary": f"{', '.join(names)} removed or quarantined the downloaded file",
            }
        return {
            "status": "protected",
            "products": names,
            "summary": f"Active real-time protection: {', '.join(names)}; no alert was reported",
        }

    @staticmethod
    def _defender_executable() -> Path | None:
        platform = Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft/Windows Defender/Platform"
        if platform.is_dir():
            candidates = sorted(platform.glob("*/MpCmdRun.exe"), reverse=True)
            if candidates:
                return candidates[0]
        fallback = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Windows Defender/MpCmdRun.exe"
        return fallback if fallback.is_file() else None

    def _scan_with_defender(self, path: Path, name: str) -> dict:
        executable = self._defender_executable()
        if executable is None:
            return {
                "status": "unavailable", "products": [name],
                "summary": "Microsoft Defender is active, but its scanner could not be started",
            }
        try:
            completed = subprocess.run(
                [str(executable), "-Scan", "-ScanType", "3", "-File", str(path), "-DisableRemediation"],
                capture_output=True, text=True, timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return {
                "status": "unavailable", "products": [name],
                "summary": "Microsoft Defender file scan did not complete",
            }
        output = f"{completed.stdout}\n{completed.stderr}".lower()
        threat = completed.returncode == 2 or "threat" in output and "no threats" not in output
        if threat or not path.exists():
            return {
                "status": "malware", "products": [name],
                "summary": "Microsoft Defender reported or removed a threat",
            }
        if completed.returncode == 0:
            return {
                "status": "clean", "products": [name],
                "summary": "Microsoft Defender completed an on-demand file scan with no reported threat",
            }
        return {
            "status": "unavailable", "products": [name],
            "summary": f"Microsoft Defender scan returned code {completed.returncode}",
        }
