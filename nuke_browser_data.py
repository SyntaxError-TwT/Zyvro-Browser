"""Post-exit deletion helper for Python Browser's explicitly scoped data."""

from __future__ import annotations

import argparse
import ctypes
import os
from pathlib import Path
import shutil
import sys
import time


ALLOWED_FILES = {
    "browser_data.sqlite3",
    "browser_data.sqlite3-shm",
    "browser_data.sqlite3-wal",
}
ALLOWED_DIRECTORIES = {
    "webengine", "quarantine", "tor-data", "web_apps", "favicons"
}


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def allowed_target(path: Path) -> bool:
    """Refuse broad or unexpected deletion targets."""
    resolved = path.resolve(strict=False)
    roots = set()
    for variable in ("APPDATA", "LOCALAPPDATA"):
        value = os.environ.get(variable)
        if value:
            roots.add(Path(value).resolve(strict=False))
    if sys.platform == "darwin":
        roots.update(
            {
                (Path.home() / "Library" / "Application Support").resolve(strict=False),
                (Path.home() / "Library" / "Caches").resolve(strict=False),
            }
        )
    if not any(root.parts and _inside(resolved, root) for root in roots):
        return False
    return (
        resolved.name in ALLOWED_FILES
        or resolved.name in ALLOWED_DIRECTORIES
    )


def wait_for_process(pid: int) -> None:
    if os.name != "nt":
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError:
                return
            except PermissionError:
                return
            time.sleep(0.25)
        return
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(pid))
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, 30_000)
        ctypes.windll.kernel32.CloseHandle(handle)


def remove_target(path: Path) -> None:
    if not allowed_target(path):
        return
    for _attempt in range(40):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return
        except OSError:
            time.sleep(0.25)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--target", action="append", default=[])
    arguments = parser.parse_args(argv)
    targets = [Path(value) for value in arguments.target]
    if not targets or any(not allowed_target(target) for target in targets):
        return 2
    wait_for_process(arguments.pid)
    for target in targets:
        remove_target(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
