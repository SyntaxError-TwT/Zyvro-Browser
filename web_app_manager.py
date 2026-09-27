"""Locally installed standalone web apps and Windows launch shortcuts."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

from qtpy.QtCore import QObject, Signal
from qtpy.QtGui import QIcon


class WebAppManager(QObject):
    """Store web-app definitions and launch each app in an isolated process."""

    changed = Signal()

    def __init__(self, data_directory: Path, project_directory: Path, parent=None) -> None:
        super().__init__(parent)
        self.data_directory = Path(data_directory) / "web_apps"
        self.data_directory.mkdir(parents=True, exist_ok=True)
        self.project_directory = Path(project_directory)
        self.manifest_path = self.data_directory / "installed.json"

    def installed(self) -> list[dict]:
        try:
            decoded = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []
        return [dict(item) for item in decoded if isinstance(item, dict)] if isinstance(decoded, list) else []

    def get(self, app_id: str) -> dict | None:
        return next((item for item in self.installed() if item.get("id") == app_id), None)

    def install(self, name: str, url: str, icon: QIcon | None = None) -> dict:
        clean_name = " ".join(str(name).split()).strip()[:80] or "Web App"
        apps = self.installed()
        existing = next((item for item in apps if item.get("url") == url), None)
        app_id = str(existing.get("id")) if existing else uuid.uuid4().hex
        icon_path = self.data_directory / f"{app_id}.png"
        if icon is not None and not icon.isNull():
            icon.pixmap(128, 128).save(str(icon_path), "PNG")
        record = {
            "id": app_id,
            "name": clean_name,
            "url": str(url),
            "icon": str(icon_path) if icon_path.exists() else "",
            "installed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }
        apps = [item for item in apps if item.get("id") != app_id]
        apps.append(record)
        self._save(apps)
        self.create_windows_shortcuts(record)
        self.changed.emit()
        return record

    def uninstall(self, app_id: str) -> bool:
        record = self.get(app_id)
        if record is None:
            return False
        self._remove_windows_shortcuts(record)
        icon = Path(str(record.get("icon") or ""))
        if icon.is_file():
            try:
                icon.unlink()
            except OSError:
                pass
        self._save([item for item in self.installed() if item.get("id") != app_id])
        self.changed.emit()
        return True

    def launch(self, app_id: str) -> bool:
        if self.get(app_id) is None:
            return False
        command = self._launch_command(app_id)
        flags = 0x08000000 if os.name == "nt" else 0
        subprocess.Popen(
            command,
            cwd=str(self.project_directory),
            creationflags=flags,
            close_fds=True,
        )
        return True

    def create_windows_shortcuts(self, record: dict) -> list[Path]:
        if os.name != "nt":
            return []
        safe_name = self._safe_filename(str(record.get("name") or "Web App"))
        home = Path.home()
        desktop = home / "Desktop" / f"{safe_name}.lnk"
        start_menu = (
            Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs"
            / "Zyvro Web Apps" / f"{safe_name}.lnk"
        )
        start_menu.parent.mkdir(parents=True, exist_ok=True)
        desktop.parent.mkdir(parents=True, exist_ok=True)
        command = self._launch_command(str(record["id"]))
        target, arguments = command[0], subprocess.list2cmdline(command[1:])
        environment = os.environ.copy()
        environment.update({
            "PY_BROWSER_SHORTCUT_TARGET": target,
            "PY_BROWSER_SHORTCUT_ARGS": arguments,
            "PY_BROWSER_SHORTCUT_WORKDIR": str(self.project_directory),
            "PY_BROWSER_SHORTCUT_ICON": target,
            "PY_BROWSER_SHORTCUT_DESCRIPTION": f"Open {record['name']} as a standalone web app",
            "PY_BROWSER_SHORTCUT_PATHS": json.dumps([str(desktop), str(start_menu)]),
        })
        script = (
            "$w=New-Object -ComObject WScript.Shell;"
            "$paths=ConvertFrom-Json $env:PY_BROWSER_SHORTCUT_PATHS;"
            "foreach($p in $paths){$s=$w.CreateShortcut($p);"
            "$s.TargetPath=$env:PY_BROWSER_SHORTCUT_TARGET;"
            "$s.Arguments=$env:PY_BROWSER_SHORTCUT_ARGS;"
            "$s.WorkingDirectory=$env:PY_BROWSER_SHORTCUT_WORKDIR;"
            "$s.IconLocation=$env:PY_BROWSER_SHORTCUT_ICON;"
            "$s.Description=$env:PY_BROWSER_SHORTCUT_DESCRIPTION;$s.Save()}"
        )
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env=environment,
            capture_output=True,
            text=True,
            creationflags=0x08000000,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or "Could not create web-app shortcuts")
        return [desktop, start_menu]

    def _remove_windows_shortcuts(self, record: dict) -> None:
        if os.name != "nt":
            return
        safe_name = self._safe_filename(str(record.get("name") or "Web App"))
        paths = [
            Path.home() / "Desktop" / f"{safe_name}.lnk",
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Zyvro Web Apps" / f"{safe_name}.lnk",
            # Clean up shortcuts created by pre-Zyvro development builds.
            Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Python Browser Apps" / f"{safe_name}.lnk",
        ]
        for path in paths:
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass

    def _launch_command(self, app_id: str) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--app-id", app_id]
        pythonw = self.project_directory / ".venv" / "Scripts" / "pythonw.exe"
        executable = pythonw if pythonw.exists() else Path(sys.executable)
        return [str(executable), str(self.project_directory / "scratch_browser.py"), "--app-id", app_id]

    def _save(self, apps: list[dict]) -> None:
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(apps, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.manifest_path)

    @staticmethod
    def _safe_filename(value: str) -> str:
        clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value).strip().rstrip(".")
        return clean[:80] or "Web App"
