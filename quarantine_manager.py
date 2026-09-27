"""Move blocked downloads into a non-user-facing quarantine directory."""

from __future__ import annotations

from pathlib import Path
import shutil
import uuid


class QuarantineManager:
    def __init__(self, security_data, directory: Path) -> None:
        self.data = security_data
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def quarantine(self, scan: dict, detection: str) -> dict | None:
        source = Path(scan["original_path"])
        if not source.is_file():
            return None
        stored = self.directory / f"{uuid.uuid4().hex}.quarantine"
        shutil.move(str(source), str(stored))
        item_id = self.data.add_quarantine(
            int(scan["id"]), str(source), str(stored), scan["filename"],
            detection, int(scan["size"]),
        )
        self.data.add_activity(scan["id"], "quarantined", "Threat quarantined", scan["filename"])
        return {"id": item_id, "quarantine_path": str(stored)}

    def restore(self, item: dict) -> Path:
        source = Path(item["quarantine_path"])
        target = Path(item["original_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target = target.with_name(f"{target.stem}-restored{target.suffix}")
        shutil.move(str(source), str(target))
        self.data.remove_quarantine(int(item["id"]), str(target))
        self.data.update_scan(int(item["scan_id"]), override_kept=1)
        self.data.add_activity(item["scan_id"], "restored", "Known threat restored", item["filename"])
        return target

    def delete(self, item: dict) -> None:
        path = Path(item["quarantine_path"])
        if path.exists():
            path.unlink()
        self.data.remove_quarantine(int(item["id"]))
        self.data.add_activity(item["scan_id"], "deleted", "Quarantined threat deleted", item["filename"])
