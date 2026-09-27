"""Themed Security Center dashboard for local and VirusTotal scan results."""

from __future__ import annotations

from datetime import datetime
import threading

from qtpy.QtCore import QEasingCurve, Qt, Signal, QVariantAnimation
from qtpy.QtGui import QColor, QPainter, QPen
from qtpy.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QScrollArea, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)


class DonutChart(QWidget):
    def __init__(self, data, theme_manager, incognito=False, parent=None) -> None:
        super().__init__(parent)
        self.data = data
        self.theme_manager = theme_manager
        self.incognito = incognito
        self.setMinimumSize(230, 180)
        self.progress = 1.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(420)
        self.animation.setStartValue(0.0); self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(lambda value: (setattr(self, "progress", float(value)), self.update()))

    def animate(self) -> None:
        self.animation.stop(); self.animation.start()

    def paintEvent(self, _event) -> None:
        counts = self.data.verdict_counts()
        values = [counts[key] for key in ("clean", "suspicious", "malware", "not_scanned")]
        colors = ["#4dbb7a", "#e2ad4f", "#e26060", "#7a8493"]
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(34, 20, -34, -20)
        edge = min(rect.width(), rect.height()); rect.setWidth(edge); rect.setHeight(edge)
        rect.moveCenter(self.rect().center())
        total = sum(values)
        if not total:
            painter.setPen(QPen(QColor("#59616d"), 16)); painter.drawEllipse(rect); return
        start = 90 * 16
        for value, color in zip(values, colors):
            span = -round(360 * 16 * value / total * self.progress)
            painter.setPen(QPen(QColor(color), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(rect, start, span); start += span


class ActivityChart(QWidget):
    def __init__(self, data, theme_manager, metric_index: int, incognito=False, parent=None) -> None:
        super().__init__(parent)
        self.data = data
        self.theme_manager = theme_manager
        # QWidget already has a virtual metric() method.  Never shadow it with
        # an integer: Qt calls metric() while constructing a QPainter.
        self.metric_index = metric_index
        self.incognito = incognito
        self.setMinimumSize(260, 180)
        self.progress = 1.0
        self.animation = QVariantAnimation(self); self.animation.setDuration(420)
        self.animation.setStartValue(0.0); self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(lambda value: (setattr(self, "progress", float(value)), self.update()))

    def animate(self) -> None:
        self.animation.stop(); self.animation.start()

    def paintEvent(self, _event) -> None:
        palette = self.theme_manager.palette(self.incognito); points = self.data.daily_counts(30)
        values = [row[self.metric_index] for row in points]; maximum = max(1, max(values, default=0))
        painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = self.rect().adjusted(12, 18, -12, -18)
        painter.setPen(QPen(QColor(palette.border), 1)); painter.drawLine(area.bottomLeft(), area.bottomRight())
        width = max(2, area.width() / max(1, len(values)) - 2)
        color = QColor("#e26060" if self.metric_index == 2 else palette.accent)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(color)
        for index, value in enumerate(values):
            height = area.height() * value / maximum * self.progress
            x = area.left() + index * area.width() / max(1, len(values))
            painter.drawRoundedRect(int(x), int(area.bottom() - height), int(width), int(height), 2, 2)


class SecurityCenterWidget(QWidget):
    """Full local dashboard; it reads SecurityData and never invents results."""

    STAT_LABELS = (
        ("files_downloaded", "Files Downloaded"), ("files_scanned", "Files Scanned"),
        ("threats_detected", "Threats Detected"), ("quarantined", "Quarantined"),
        ("warnings_overridden", "Warnings Overridden"), ("not_scanned", "Not Scanned"),
        ("vt_checks", "VirusTotal Checks"),
    )
    key_validation_finished = Signal(bool)

    def __init__(self, scanner, data, theme_manager, parent=None, incognito=False) -> None:
        super().__init__(parent); self.scanner = scanner; self.data = data; self.theme_manager = theme_manager; self.incognito = incognito
        outer = QVBoxLayout(self); scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(); self.layout = QVBoxLayout(content); self.layout.setContentsMargins(16, 14, 16, 18); self.layout.setSpacing(14)
        scroll.setWidget(content); outer.addWidget(scroll)

        self.header = QFrame(); self.header.setObjectName("securityHeader"); header_layout = QVBoxLayout(self.header)
        self.state_label = QLabel(); self.state_label.setObjectName("securityState"); self.title_label = QLabel(); self.title_label.setObjectName("securityTitle")
        self.description_label = QLabel(); self.description_label.setWordWrap(True)
        header_layout.addWidget(self.state_label); header_layout.addWidget(self.title_label); header_layout.addWidget(self.description_label)
        self.layout.addWidget(self.header)

        disclosure = QLabel("Downloaded files up to 500 MB may be automatically submitted to VirusTotal for malware analysis.")
        disclosure.setObjectName("securityDisclosure"); disclosure.setWordWrap(True); self.layout.addWidget(disclosure)

        stats_widget = QWidget(); stats = QGridLayout(stats_widget); stats.setContentsMargins(0, 0, 0, 0); stats.setSpacing(8)
        self.stat_values = {}
        for index, (key, label) in enumerate(self.STAT_LABELS):
            card = QFrame(); card.setObjectName("securityCard"); card_layout = QVBoxLayout(card)
            value = QLabel("0"); value.setObjectName("securityNumber"); card_layout.addWidget(QLabel(label)); card_layout.addWidget(value)
            self.stat_values[key] = value; stats.addWidget(card, index // 4, index % 4)
        self.layout.addWidget(stats_widget)

        charts = QFrame(); charts.setObjectName("securityCard"); chart_layout = QHBoxLayout(charts)
        chart_layout.addLayout(self._chart_column("Download Analysis", DonutChart(data, theme_manager, incognito)))
        chart_layout.addLayout(self._chart_column("Threat Activity — 30 Days", ActivityChart(data, theme_manager, 2, incognito)))
        chart_layout.addLayout(self._chart_column("Downloads Scanned", ActivityChart(data, theme_manager, 1, incognito)))
        self.charts = charts; self.layout.addWidget(charts)

        self.layout.addWidget(self._heading("Recent Scan Results"))
        self.scans = QTableWidget(0, 7); self.scans.setHorizontalHeaderLabels(["Filename", "Result", "VirusTotal", "Engines", "SHA-256", "Signature", "Scanned"])
        self.scans.horizontalHeader().setStretchLastSection(True); self.scans.setMinimumHeight(210); self.layout.addWidget(self.scans)

        self.layout.addWidget(self._heading("Quarantine"))
        self.quarantine_table = QTableWidget(0, 4); self.quarantine_table.setHorizontalHeaderLabels(["Filename", "Detection", "Size", "Date"])
        self.quarantine_table.horizontalHeader().setStretchLastSection(True); self.quarantine_table.setMinimumHeight(140); self.layout.addWidget(self.quarantine_table)
        quarantine_actions = QHBoxLayout(); restore = QPushButton("Restore"); delete = QPushButton("Delete Permanently")
        restore.clicked.connect(self.restore_selected); delete.clicked.connect(self.delete_selected); quarantine_actions.addWidget(restore); quarantine_actions.addWidget(delete); quarantine_actions.addStretch(); self.layout.addLayout(quarantine_actions)

        self.layout.addWidget(self._heading("Recent Security Activity")); self.activity = QVBoxLayout(); self.layout.addLayout(self.activity)

        key_card = QFrame(); key_card.setObjectName("securityCard"); key_layout = QVBoxLayout(key_card)
        key_layout.addWidget(self._heading("VirusTotal API")); self.key_status = QLabel(); self.key_status.setWordWrap(True); key_layout.addWidget(self.key_status)
        self.api_key = QLineEdit(); self.api_key.setEchoMode(QLineEdit.EchoMode.Password); self.api_key.setPlaceholderText("Enter API key — stored in the operating system credential vault")
        save_key = QPushButton("Save Securely"); save_key.clicked.connect(self.save_key); key_layout.addWidget(self.api_key); key_layout.addWidget(save_key, alignment=Qt.AlignmentFlag.AlignLeft)
        key_layout.addWidget(QLabel("Public API accounts are quota-limited. Follow your VirusTotal account terms; commercial use requires an appropriate license."))
        self.layout.addWidget(key_card)

        self.data.changed.connect(self.refresh); self.theme_manager.theme_changed.connect(self.apply_theme)
        self.scanner.availability_changed.connect(self.refresh)
        self.key_validation_finished.connect(self._key_validated)
        self.refresh(); self.apply_theme()

    @staticmethod
    def _heading(text: str) -> QLabel:
        label = QLabel(text); label.setObjectName("securityHeading"); return label

    @staticmethod
    def _chart_column(title: str, chart: QWidget) -> QVBoxLayout:
        layout = QVBoxLayout(); heading = QLabel(title); heading.setAlignment(Qt.AlignmentFlag.AlignCenter); layout.addWidget(heading); layout.addWidget(chart); return layout

    def refresh(self, *_args) -> None:
        stats = self.data.stats()
        for key, label in self.stat_values.items(): label.setText(str(stats.get(key, 0)))
        scans = self.data.scans()
        active_antivirus = getattr(self.scanner, "active_antivirus", [])
        antivirus_names = ", ".join(
            product["name"] for product in active_antivirus
        )
        validated = getattr(self.scanner.vt, "validated", None)
        key_configured = self.scanner.vt.configured()
        configured = key_configured and (
            bool(validated()) if callable(validated) else True
        )
        if any(scan["override_kept"] and scan["verdict"] == "malware" for scan in scans):
            state, title, description, color = "WARNING", "A known threat was downloaded", "A file identified as malware was kept despite a security warning.", "#e26060"
        elif not key_configured and not active_antivirus:
            state, title, description, color = "PROTECTION UNAVAILABLE", "Malware scanning is currently unavailable.", "Local checks remain active, but VirusTotal is unavailable until a valid API key is configured.", "#d88d45"
        elif not configured and not active_antivirus:
            state, title, description, color = "VERIFYING", "Malware scanner is starting.", "The secured VirusTotal API key is being validated. Local checks remain active.", "#5f8fe8"
        elif any(scan["verdict"] in {"suspicious", "unknown", "not_scanned"} or scan["status"] in {"waiting", "unavailable", "error"} for scan in scans):
            state, title, description, color = "UNSAFE", "You have risk in downloading malware", "One or more downloaded files could not be verified as safe.", "#d8a445"
        else:
            state, title, description, color = "SAFE", "You are protected", "No unresolved malware threats have been detected.", "#4dbb7a"
        self.state_label.setText(state); self.title_label.setText(title); self.description_label.setText(description); self.header.setProperty("stateColor", color)
        self.header.setStyleSheet(f"QFrame#securityHeader{{border-left:5px solid {color};}}")
        if active_antivirus:
            self.key_status.setText(
                f"Local antivirus active: {antivirus_names}. VirusTotal API is on standby and is not used while antivirus protection is active."
            )
        elif configured:
            self.key_status.setText("VirusTotal configured and validated")
        elif key_configured:
            self.key_status.setText("VirusTotal API key secured — validating…")
        else:
            self.key_status.setText("VirusTotal unavailable — no API key is configured")

        self.scans.setRowCount(len(scans))
        for row, scan in enumerate(scans):
            if scan["vt_status"] == "skipped_antivirus":
                vt = "Not used — local antivirus active"
            elif scan["vt_status"] == "unavailable":
                vt = "VirusTotal unavailable"
            elif scan["vt_status"] in {"waiting", "checking", "uploading", "queued"}:
                vt = scan["vt_status"].replace("_", " ").title()
            else:
                vt = f"{scan['vt_malicious']} detections"
            engines = f"{scan['vt_malicious'] + scan['vt_suspicious']} / {scan['vt_total']}" if scan["vt_total"] else "—"
            values = [scan["filename"], scan["verdict"].replace("_", " ").title(), vt, engines, scan["sha256"], scan["signature_status"], self._time(scan["updated_at"])]
            for column, value in enumerate(values): self.scans.setItem(row, column, QTableWidgetItem(str(value)))

        items = self.data.quarantine_items(); self.quarantine_table.setRowCount(len(items))
        for row, item in enumerate(items):
            values = [item["filename"], item["detection"], f"{item['size'] / 1024:.1f} KB", self._time(item["quarantined_at"])]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value)); cell.setData(Qt.ItemDataRole.UserRole, item["id"]); self.quarantine_table.setItem(row, column, cell)
        while self.activity.count():
            item = self.activity.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for event in self.data.activities(20):
            card = QLabel(f"{self._time(event['created_at'])}\n{event['title']}\n{event['detail']}"); card.setObjectName("securityActivity"); self.activity.addWidget(card)
        self.charts.update()
        for chart_type in (DonutChart, ActivityChart):
            for widget in self.charts.findChildren(chart_type):
                widget.animate()

    @staticmethod
    def _time(value: str) -> str:
        try: return datetime.fromisoformat(value).strftime("%b %d, %I:%M %p").replace(" 0", " ")
        except ValueError: return value

    def _selected_quarantine(self) -> dict | None:
        row = self.quarantine_table.currentRow()
        if row < 0: return None
        item_id = int(self.quarantine_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        return next((item for item in self.data.quarantine_items() if item["id"] == item_id), None)

    def restore_selected(self) -> None:
        item = self._selected_quarantine()
        if not item: return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Critical)
        box.setWindowTitle("Restore Known Threat?")
        box.setText("Restoring this file may infect or damage your computer. Restore it anyway?")
        restore = box.addButton("Restore", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is restore:
            self.scanner.quarantine.restore(item)

    def delete_selected(self) -> None:
        item = self._selected_quarantine()
        if not item: return
        if QMessageBox.question(self, "Delete Permanently", "Permanently delete this quarantined file?") == QMessageBox.StandardButton.Yes: self.scanner.quarantine.delete(item)

    def save_key(self) -> None:
        if self.scanner.vt.set_api_key(self.api_key.text()):
            self.api_key.clear(); self.refresh()
            self.key_status.setText("Validating VirusTotal API key…")
            threading.Thread(
                target=lambda: self.key_validation_finished.emit(
                    self.scanner.vt.validate_key()
                ),
                daemon=True,
            ).start()
        else:
            QMessageBox.warning(self, "Secure Storage Unavailable", "The API key was not saved. Set the VT_API_KEY environment variable instead; plaintext settings are not used.")
            self.refresh()

    def _key_validated(self, valid: bool) -> None:
        self.key_status.setText(
            "VirusTotal API key validated" if valid
            else "VirusTotal unavailable — the API key was rejected"
        )
        self.refresh()

    def apply_theme(self, *_args) -> None:
        p = self.theme_manager.palette(self.incognito)
        self.setStyleSheet(f"""
QFrame#securityHeader, QFrame#securityCard {{ background:{p.panel}; border:1px solid {p.border}; border-radius:12px; }}
QFrame#securityHeader {{ padding:10px; }} QLabel#securityState {{ font-size:13px; font-weight:800; color:{p.accent}; }}
QLabel#securityTitle {{ font-size:24px; font-weight:750; }} QLabel#securityHeading {{ font-size:17px; font-weight:700; }}
QLabel#securityNumber {{ font-size:25px; font-weight:750; color:{p.accent}; }}
QLabel#securityDisclosure {{ background:{p.control}; border:1px solid {p.border}; border-radius:9px; padding:10px; }}
QLabel#securityActivity {{ background:{p.control}; border-left:3px solid {p.accent}; border-radius:7px; padding:9px; }}
""")
