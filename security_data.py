"""Local SQLite storage for malware scans, quarantine, and dashboard activity."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from qtpy.QtCore import QObject, Signal


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class SecurityData(QObject):
    changed = Signal()

    def __init__(self, database_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.database_path = Path(database_path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS security_scans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL, original_path TEXT NOT NULL,
                    source_url TEXT NOT NULL, size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    verdict TEXT NOT NULL DEFAULT 'unknown',
                    local_summary TEXT NOT NULL DEFAULT '',
                    local_findings TEXT NOT NULL DEFAULT '[]',
                    signature_status TEXT NOT NULL DEFAULT 'Unknown',
                    publisher TEXT NOT NULL DEFAULT '',
                    vt_status TEXT NOT NULL DEFAULT 'waiting',
                    vt_malicious INTEGER NOT NULL DEFAULT 0,
                    vt_suspicious INTEGER NOT NULL DEFAULT 0,
                    vt_total INTEGER NOT NULL DEFAULT 0,
                    vt_detections TEXT NOT NULL DEFAULT '[]',
                    analysis_id TEXT NOT NULL DEFAULT '',
                    quarantined INTEGER NOT NULL DEFAULT 0,
                    override_kept INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS security_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id INTEGER, event_type TEXT NOT NULL,
                    title TEXT NOT NULL, detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS quarantine_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_id INTEGER NOT NULL, original_path TEXT NOT NULL,
                    quarantine_path TEXT NOT NULL, filename TEXT NOT NULL,
                    detection TEXT NOT NULL, size INTEGER NOT NULL,
                    quarantined_at TEXT NOT NULL
                );
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def create_scan(self, filename: str, path: str, url: str, size: int) -> int:
        timestamp = now_iso()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO security_scans(filename,original_path,source_url,size,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (filename, path, url, int(size), "waiting", timestamp, timestamp),
            )
            scan_id = int(cursor.lastrowid)
            db.execute(
                "INSERT INTO security_activity(scan_id,event_type,title,detail,created_at) VALUES(?,?,?,?,?)",
                (scan_id, "queued", "Waiting for security scan", filename, timestamp),
            )
        self.changed.emit()
        return scan_id

    def update_scan(self, scan_id: int, **values) -> None:
        allowed = {
            "sha256", "status", "verdict", "local_summary", "local_findings",
            "signature_status", "publisher", "vt_status", "vt_malicious",
            "vt_suspicious", "vt_total", "vt_detections", "analysis_id",
            "quarantined", "override_kept", "original_path",
        }
        clean = {key: value for key, value in values.items() if key in allowed}
        if not clean:
            return
        for key in ("local_findings", "vt_detections"):
            if key in clean and not isinstance(clean[key], str):
                clean[key] = json.dumps(clean[key])
        clean["updated_at"] = now_iso()
        columns = ", ".join(f"{key}=?" for key in clean)
        with self._connect() as db:
            db.execute(
                f"UPDATE security_scans SET {columns} WHERE id=?",
                (*clean.values(), int(scan_id)),
            )
        self.changed.emit()

    def add_activity(self, scan_id: int | None, event_type: str, title: str, detail: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO security_activity(scan_id,event_type,title,detail,created_at) VALUES(?,?,?,?,?)",
                (scan_id, event_type, title, detail, now_iso()),
            )
        self.changed.emit()

    def scan(self, scan_id: int) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM security_scans WHERE id=?", (int(scan_id),)).fetchone()
        return self._decode(row)

    def scans(self, limit: int = 200) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM security_scans ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [self._decode(row) for row in rows]

    def latest_for_path(self, path: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM security_scans WHERE original_path=? ORDER BY id DESC LIMIT 1",
                (str(path),),
            ).fetchone()
        return self._decode(row) if row else None

    @staticmethod
    def _decode(row) -> dict:
        result = dict(row)
        for key in ("local_findings", "vt_detections"):
            try:
                result[key] = json.loads(result.get(key) or "[]")
            except (TypeError, json.JSONDecodeError):
                result[key] = []
        return result

    def activities(self, limit: int = 100) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM security_activity ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_activities(self) -> None:
        with self._connect() as db:
            db.execute("DELETE FROM security_activity")
        self.changed.emit()

    def stats(self) -> dict[str, int]:
        with self._connect() as db:
            row = db.execute("""
                SELECT COUNT(*) files_downloaded,
                    SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) files_scanned,
                    SUM(CASE WHEN verdict='malware' THEN 1 ELSE 0 END) threats_detected,
                    SUM(CASE WHEN quarantined=1 THEN 1 ELSE 0 END) quarantined,
                    SUM(CASE WHEN override_kept=1 THEN 1 ELSE 0 END) warnings_overridden,
                    SUM(CASE WHEN status IN ('not_scanned','unavailable','error','waiting') THEN 1 ELSE 0 END) not_scanned,
                    SUM(CASE WHEN vt_status IN ('known','completed') THEN 1 ELSE 0 END) vt_checks
                FROM security_scans
            """).fetchone()
        return {key: int(row[key] or 0) for key in row.keys()}

    def verdict_counts(self) -> dict[str, int]:
        counts = {"clean": 0, "suspicious": 0, "malware": 0, "not_scanned": 0}
        with self._connect() as db:
            rows = db.execute("SELECT verdict, status, COUNT(*) amount FROM security_scans GROUP BY verdict,status").fetchall()
        for row in rows:
            key = row["verdict"] if row["verdict"] in counts else "not_scanned"
            if row["status"] != "completed" and key == "clean":
                key = "not_scanned"
            counts[key] += int(row["amount"])
        return counts

    def daily_counts(self, days: int = 30) -> list[tuple[str, int, int]]:
        start = (datetime.now().astimezone().date() - timedelta(days=days - 1)).isoformat()
        with self._connect() as db:
            rows = db.execute("""
                SELECT substr(updated_at,1,10) day,
                    SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) scanned,
                    SUM(CASE WHEN verdict='malware' THEN 1 ELSE 0 END) threats
                FROM security_scans WHERE substr(updated_at,1,10)>=?
                GROUP BY day ORDER BY day
            """, (start,)).fetchall()
        values = {row["day"]: (int(row["scanned"] or 0), int(row["threats"] or 0)) for row in rows}
        result = []
        for offset in range(days):
            day = (datetime.now().astimezone().date() - timedelta(days=days - 1 - offset)).isoformat()
            scanned, threats = values.get(day, (0, 0))
            result.append((day, scanned, threats))
        return result

    def add_quarantine(self, scan_id: int, original: str, stored: str, filename: str, detection: str, size: int) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO quarantine_items(scan_id,original_path,quarantine_path,filename,detection,size,quarantined_at) VALUES(?,?,?,?,?,?,?)",
                (scan_id, original, stored, filename, detection, int(size), now_iso()),
            )
            item_id = int(cursor.lastrowid)
        self.update_scan(scan_id, quarantined=1)
        return item_id

    def quarantine_items(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM quarantine_items ORDER BY id DESC").fetchall()
        return [dict(row) for row in rows]

    def remove_quarantine(self, item_id: int, restored_path: str | None = None) -> None:
        with self._connect() as db:
            row = db.execute("SELECT scan_id FROM quarantine_items WHERE id=?", (item_id,)).fetchone()
            db.execute("DELETE FROM quarantine_items WHERE id=?", (item_id,))
        if row:
            values = {"quarantined": 0}
            if restored_path:
                values["original_path"] = restored_path
            self.update_scan(int(row["scan_id"]), **values)
