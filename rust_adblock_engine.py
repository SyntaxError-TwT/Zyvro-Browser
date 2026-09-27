"""ctypes adapter for Zyvro's native adblock-rust bridge."""

from __future__ import annotations

import ctypes
import json
from pathlib import Path
import sys
import threading


class RustAdblockEngine:
    """Thread-safe Python owner for the Rust ad-block engine."""

    display_name = "Zyvro Ad Blocker"

    def __init__(self, runtime_path: Path | None = None) -> None:
        self.runtime_path = Path(runtime_path or self._default_runtime_path())
        self.version = ""
        self.error = ""
        self.available = False
        self.rule_count = 0
        self.list_count = 0
        self._library = None
        self._handle = None
        self._youtube_main_script = ""
        self._youtube_isolated_script = ""
        self._close_lock = threading.Lock()
        self._load()

    @staticmethod
    def _default_runtime_path() -> Path:
        root = Path(__file__).resolve().parent
        packaged = root / "runtime" / "adblock"
        if any((packaged / name).is_file() for name in RustAdblockEngine._library_names()):
            return packaged
        return root / "rust" / "zyvro-adblock" / "target" / "release"

    @staticmethod
    def _library_names() -> tuple[str, ...]:
        if sys.platform == "darwin":
            return ("libzyvro_adblock.dylib", "zyvro_adblock.dylib")
        if sys.platform.startswith("linux"):
            return ("libzyvro_adblock.so", "zyvro_adblock.so")
        return ("zyvro_adblock.dll",)

    @staticmethod
    def _data_path() -> Path:
        return Path(__file__).resolve().parent / "assets" / "adblock"

    def _load(self) -> None:
        try:
            library_path = next(
                (
                    self.runtime_path / name
                    for name in self._library_names()
                    if (self.runtime_path / name).is_file()
                ),
                None,
            )
            if library_path is None:
                expected = ", ".join(self._library_names())
                raise FileNotFoundError(
                    f"native ad blocker not found in {self.runtime_path} "
                    f"(expected {expected})"
                )
            library = ctypes.CDLL(str(library_path))
            self._configure_api(library)
            data_path = self._data_path()
            handle = library.zyvro_adblock_create(
                str(data_path / "lists").encode("utf-8"),
                str(data_path / "resources.json").encode("utf-8"),
            )
            if not handle:
                raise RuntimeError(self._last_error(library))
            self._library = library
            self._handle = handle
            raw_version = library.zyvro_adblock_version()
            self.version = raw_version.decode("utf-8") if raw_version else "adblock-rust"
            self.list_count = int(library.zyvro_adblock_list_count(handle))
            scriptlets = data_path / "scriptlets"
            self._youtube_main_script = (
                scriptlets / "youtube-main.js"
            ).read_text(encoding="utf-8")
            self._youtube_isolated_script = (
                scriptlets / "youtube-isolated.js"
            ).read_text(encoding="utf-8")
            self.available = True
        except (OSError, RuntimeError, ValueError) as error:
            self.error = str(error)
            self.available = False

    @staticmethod
    def _configure_api(library) -> None:
        library.zyvro_adblock_version.argtypes = []
        library.zyvro_adblock_version.restype = ctypes.c_char_p
        library.zyvro_adblock_last_error.argtypes = []
        library.zyvro_adblock_last_error.restype = ctypes.c_void_p
        library.zyvro_adblock_free_string.argtypes = [ctypes.c_void_p]
        library.zyvro_adblock_free_string.restype = None
        library.zyvro_adblock_create.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        library.zyvro_adblock_create.restype = ctypes.c_void_p
        library.zyvro_adblock_destroy.argtypes = [ctypes.c_void_p]
        library.zyvro_adblock_destroy.restype = None
        library.zyvro_adblock_list_count.argtypes = [ctypes.c_void_p]
        library.zyvro_adblock_list_count.restype = ctypes.c_size_t
        library.zyvro_adblock_check.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p,
            ctypes.c_char_p, ctypes.c_char_p,
        ]
        library.zyvro_adblock_check.restype = ctypes.c_int
        library.zyvro_adblock_cosmetic_json.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p,
        ]
        library.zyvro_adblock_cosmetic_json.restype = ctypes.c_void_p
        library.zyvro_adblock_generic_json.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        ]
        library.zyvro_adblock_generic_json.restype = ctypes.c_void_p

    @staticmethod
    def _consume_string(library, pointer) -> str:
        if not pointer:
            return ""
        try:
            return ctypes.string_at(pointer).decode("utf-8", errors="replace")
        finally:
            library.zyvro_adblock_free_string(pointer)

    def _last_error(self, library=None) -> str:
        library = library or self._library
        if library is None:
            return self.error or "native ad blocker unavailable"
        return self._consume_string(library, library.zyvro_adblock_last_error())

    def should_block(
        self,
        url: str,
        _request_host: str,
        initiator_host: str,
        resource_type: str,
        method: str = "GET",
        source_url: str = "",
    ) -> bool:
        if not self.available or not self._handle:
            return False
        if not source_url and initiator_host:
            source_url = f"https://{initiator_host}/"
        result = self._library.zyvro_adblock_check(
            self._handle,
            str(url).encode("utf-8"),
            str(source_url or url).encode("utf-8"),
            str(resource_type or "other").encode("utf-8"),
            str(method or "GET").encode("ascii", errors="ignore"),
        )
        if result < 0:
            self.error = self._last_error()
            return False
        return result == 1

    def cosmetic_resources(self, url: str) -> dict:
        if not self.available or not self._handle:
            return {}
        pointer = self._library.zyvro_adblock_cosmetic_json(
            self._handle, str(url).encode("utf-8")
        )
        if not pointer:
            self.error = self._last_error()
            return {}
        try:
            return json.loads(self._consume_string(self._library, pointer))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.error = str(error)
            return {}

    def generic_selectors(
        self, classes: list[str], ids: list[str], exceptions: list[str]
    ) -> list[str]:
        if not self.available or not self._handle:
            return []
        pointer = self._library.zyvro_adblock_generic_json(
            self._handle,
            json.dumps(classes).encode("utf-8"),
            json.dumps(ids).encode("utf-8"),
            json.dumps(exceptions).encode("utf-8"),
        )
        if not pointer:
            self.error = self._last_error()
            return []
        try:
            value = json.loads(self._consume_string(self._library, pointer))
            return [str(selector) for selector in value if selector]
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            self.error = str(error)
            return []

    def supplemental_scriptlets(self, host: str) -> tuple[str, str]:
        """Return generated uAssets scriptlets for YouTube's in-page ads.

        adblock-rust handles the filter parsing and all request decisions. The
        bundled generated scripts fill the current resource-name gap for
        uAssets' trusted YouTube scriptlets and execute only on YouTube hosts.
        """
        host = str(host).lower().strip(".")
        youtube_hosts = (
            "youtube.com", "youtube-nocookie.com", "youtubekids.com"
        )
        if any(host == name or host.endswith(f".{name}") for name in youtube_hosts):
            return self._youtube_main_script, self._youtube_isolated_script
        return "", ""

    def close(self) -> None:
        with self._close_lock:
            if self._library is not None and self._handle:
                self._library.zyvro_adblock_destroy(self._handle)
                self._handle = None
            self.available = False

    def __del__(self) -> None:
        try:
            self.close()
        except (AttributeError, OSError):
            pass


def native_runtime_directory() -> Path:
    """Public helper used by tests and build diagnostics."""
    return RustAdblockEngine._default_runtime_path()
