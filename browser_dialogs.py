"""Functional browser dialogs and the native Qt WebEngine find bar."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from qtpy.QtCore import Qt, QUrl, Signal
from qtpy.QtGui import (
    QColor, QDesktopServices, QKeyEvent, QKeySequence, QPainter, QPen, QShortcut
)
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qtpy.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from qtpy.QtWebEngineWidgets import QWebEngineView

from browser_data import BookmarkManager, DownloadManager, HistoryManager, SettingsManager
from theme_manager import ThemeManager
from site_privacy_manager import PERMISSION_LABELS
from privacy_timeline import PrivacyTimelineManager
from security_center import SecurityCenterWidget
from browser_maintenance import ClearBrowsingDataManager, DiagnosticsManager

class _ThemedDialog(QDialog):
    def configure_theme(self, theme_manager: ThemeManager | None, incognito: bool = False) -> None:
        self.theme_manager = theme_manager
        self.theme_incognito = incognito
        if theme_manager is not None:
            theme_manager.theme_changed.connect(self.apply_dialog_theme)
            self.apply_dialog_theme()

    def apply_dialog_theme(self, *_args) -> None:
        if self.theme_manager is not None:
            self.setStyleSheet(
                self.theme_manager.dialog_stylesheet(self.theme_incognito)
            )


class _TimelineEventRow(QWidget):
    """One restrained timeline event with a painted line and marker."""

    def __init__(self, event: dict, theme_manager, incognito: bool, parent=None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.incognito = incognito
        layout = QHBoxLayout(self)
        layout.setContentsMargins(28, 3, 3, 7)
        card = QFrame()
        card.setObjectName("timelineCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(13, 10, 13, 10)
        card_layout.setSpacing(3)
        occurred = datetime.fromisoformat(str(event["occurred_at"]))
        # Windows' strftime does not support %-I, so strip the leading zero.
        time_label = QLabel(occurred.strftime("%I:%M %p").lstrip("0"))
        time_label.setObjectName("timelineTime")
        title = QLabel(str(event["display_title"]))
        title.setObjectName("timelineEventTitle")
        icon = QLabel()
        icon_name = {
            "trackers": "shield.svg",
            "ads": "adblock.svg",
            "permissions": "lock.svg",
            "network": "vpn.svg",
        }.get(str(event["category"]), "site-info.svg")
        icon.setPixmap(
            self.theme_manager.icon(
                str(Path(__file__).resolve().parent / "assets" / icon_name),
                self.incognito,
            ).pixmap(15, 15)
        )
        title_row = QWidget()
        title_layout = QHBoxLayout(title_row)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)
        title_layout.addWidget(icon)
        title_layout.addWidget(title, 1)
        site = QLabel(str(event["site"]))
        site.setObjectName("timelineSite")
        card_layout.addWidget(time_label)
        card_layout.addWidget(title_row)
        card_layout.addWidget(site)
        layout.addWidget(card)
        self.apply_theme()

    def apply_theme(self) -> None:
        palette = self.theme_manager.palette(self.incognito)
        self.setStyleSheet(f"""
QFrame#timelineCard {{ background: {palette.control}; border: 1px solid {palette.border};
    border-radius: 11px; }}
QLabel#timelineTime {{ color: {palette.accent}; font-size: 12px; font-weight: 650; }}
QLabel#timelineEventTitle {{ color: {palette.text}; font-weight: 650; }}
QLabel#timelineSite {{ color: {palette.muted}; font-size: 12px; }}
""")

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        palette = self.theme_manager.palette(self.incognito)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QPen(QColor(palette.border), 2))
        painter.drawLine(12, 0, 12, self.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette.accent))
        painter.drawEllipse(7, 18, 10, 10)


class PrivacyTimelineWidget(QWidget):
    """Filterable local-only privacy timeline used inside Settings."""

    FILTERS = (
        ("All", "all"), ("Trackers", "trackers"), ("Ads", "ads"),
        ("Permissions", "permissions"), ("Network", "network"),
    )

    def __init__(
        self, manager: PrivacyTimelineManager, theme_manager,
        incognito: bool = False, parent=None,
    ) -> None:
        super().__init__(parent)
        self.manager = manager
        self.theme_manager = theme_manager
        self.incognito = incognito
        self.category = "all"
        root = QVBoxLayout(self)
        heading = QLabel("Privacy Timeline")
        heading.setObjectName("privacyTimelineHeading")
        root.addWidget(heading)
        note = QLabel(
            "A local record of privacy protections. Page content, searches, "
            "passwords, and form data are never recorded."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        filters = QWidget()
        filter_layout = QHBoxLayout(filters)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(5)
        self.filter_buttons = []
        for label, category in self.FILTERS:
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(category == "all")
            button.clicked.connect(
                lambda _checked=False, selected=category: self.set_filter(selected)
            )
            self.filter_buttons.append((button, category))
            filter_layout.addWidget(button)
        filter_layout.addStretch()
        root.addWidget(filters)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content = QWidget()
        self.events_layout = QVBoxLayout(self.content)
        self.events_layout.setContentsMargins(0, 4, 4, 4)
        self.events_layout.setSpacing(0)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll, 1)
        clear = QPushButton("Clear Timeline")
        clear.clicked.connect(self.clear_timeline)
        root.addWidget(clear, alignment=Qt.AlignmentFlag.AlignRight)
        self.manager.changed.connect(self.refresh)
        self.theme_manager.theme_changed.connect(self.refresh)
        self.refresh()

    def set_filter(self, category: str) -> None:
        self.category = category
        for button, value in self.filter_buttons:
            button.setChecked(value == category)
        self.refresh()

    def refresh(self, *_args) -> None:
        while self.events_layout.count():
            item = self.events_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        events = self.manager.events(self.category)
        if not events:
            empty = QLabel("No privacy events recorded yet.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("padding: 36px;")
            self.events_layout.addWidget(empty)
        else:
            current_day = ""
            today = datetime.now().astimezone().date()
            for event in events:
                occurred = datetime.fromisoformat(str(event["occurred_at"]))
                day = occurred.date()
                day_label = "Today" if day == today else occurred.strftime("%B %d, %Y")
                if day_label != current_day:
                    heading = QLabel(day_label)
                    heading.setObjectName("timelineDayHeading")
                    heading.setStyleSheet("font-weight: 700; padding: 9px 0 5px 4px;")
                    self.events_layout.addWidget(heading)
                    current_day = day_label
                self.events_layout.addWidget(
                    _TimelineEventRow(event, self.theme_manager, self.incognito)
                )
        self.events_layout.addStretch()

    def clear_timeline(self) -> None:
        answer = QMessageBox.question(
            self, "Clear Privacy Timeline", "Clear all privacy timeline events?"
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.manager.clear()


class _EntryDialog(_ThemedDialog):
    open_url = Signal(str)

    def selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def selected_url(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 1) if row >= 0 else None
        return item.text() if item else ""

    def open_selected(self) -> None:
        url = self.selected_url()
        if url:
            self.open_url.emit(url)
            self.accept()


class HistoryDialog(_EntryDialog):
    def __init__(self, manager: HistoryManager, parent=None, theme_manager=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("History")
        self.resize(820, 520)
        self.configure_theme(theme_manager or getattr(parent, "theme_manager", None), bool(getattr(parent, "incognito", False)))

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search history")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Title", "URL", "Visited"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.open_selected)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        open_button = QPushButton("Open")
        remove_button = QPushButton("Remove")
        clear_button = QPushButton("Clear History")
        close_button = QPushButton("Close")
        open_button.clicked.connect(self.open_selected)
        remove_button.clicked.connect(self.remove_selected)
        clear_button.clicked.connect(self.clear_history)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(open_button)
        buttons.addWidget(remove_button)
        buttons.addStretch()
        buttons.addWidget(clear_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        rows = self.manager.entries(self.search.text())
        self.table.setRowCount(len(rows))
        for row_index, entry in enumerate(rows):
            title = QTableWidgetItem(entry["title"])
            title.setData(Qt.ItemDataRole.UserRole, entry["id"])
            self.table.setItem(row_index, 0, title)
            self.table.setItem(row_index, 1, QTableWidgetItem(entry["url"]))
            self.table.setItem(row_index, 2, QTableWidgetItem(entry["visited_at"]))
        self.table.resizeColumnsToContents()

    def remove_selected(self) -> None:
        entry_id = self.selected_id()
        if entry_id is not None:
            self.manager.remove(entry_id)
            self.refresh()

    def clear_history(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear History",
            "Clear all browsing history? This cannot be undone.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.manager.clear()
            self.refresh()


class BookmarksDialog(_EntryDialog):
    def __init__(self, manager: BookmarkManager, parent=None, theme_manager=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self._loading = False
        self.setWindowTitle("Bookmarks")
        self.resize(820, 500)
        self.configure_theme(theme_manager or getattr(parent, "theme_manager", None), bool(getattr(parent, "incognito", False)))

        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search bookmarks")
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Title", "URL", "Created"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.open_selected)
        self.table.itemChanged.connect(self.save_edited_row)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        open_button = QPushButton("Open")
        delete_button = QPushButton("Delete")
        close_button = QPushButton("Close")
        open_button.clicked.connect(self.open_selected)
        delete_button.clicked.connect(self.delete_selected)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(open_button)
        buttons.addWidget(delete_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        rows = self.manager.entries(self.search.text())
        self.table.setRowCount(len(rows))
        for row_index, entry in enumerate(rows):
            title = QTableWidgetItem(entry["title"])
            title.setData(Qt.ItemDataRole.UserRole, entry["id"])
            self.table.setItem(row_index, 0, title)
            self.table.setItem(row_index, 1, QTableWidgetItem(entry["url"]))
            created = QTableWidgetItem(entry["created_at"])
            created.setFlags(created.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row_index, 2, created)
        self.table.resizeColumnsToContents()
        self._loading = False

    def save_edited_row(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        row = item.row()
        id_item = self.table.item(row, 0)
        url_item = self.table.item(row, 1)
        if id_item and url_item:
            self.manager.update(
                int(id_item.data(Qt.ItemDataRole.UserRole)),
                id_item.text().strip(),
                url_item.text().strip(),
            )

    def delete_selected(self) -> None:
        bookmark_id = self.selected_id()
        if bookmark_id is not None:
            self.manager.remove(bookmark_id)
            self.refresh()


class DownloadsDialog(_ThemedDialog):
    def __init__(self, manager: DownloadManager, parent=None, theme_manager=None) -> None:
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Downloads")
        self.resize(900, 480)
        self.configure_theme(theme_manager or getattr(parent, "theme_manager", None), bool(getattr(parent, "incognito", False)))

        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["Filename", "Progress", "Status", "Destination"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        open_button = QPushButton("Open File")
        folder_button = QPushButton("Open Folder")
        cancel_button = QPushButton("Cancel Download")
        close_button = QPushButton("Close")
        open_button.clicked.connect(self.open_file)
        folder_button.clicked.connect(self.open_folder)
        cancel_button.clicked.connect(self.cancel_download)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(open_button)
        buttons.addWidget(folder_button)
        buttons.addWidget(cancel_button)
        buttons.addStretch()
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        manager.changed.connect(self.refresh)
        self.refresh()

    def selected_record(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        record_id = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return next(
            (record for record in self.manager.records() if record["id"] == record_id),
            None,
        )

    def refresh(self) -> None:
        records = self.manager.records()
        self.table.setRowCount(len(records))
        for row_index, record in enumerate(records):
            filename = QTableWidgetItem(record["filename"])
            filename.setData(Qt.ItemDataRole.UserRole, record["id"])
            total = int(record["total"])
            received = int(record["received"])
            progress = f"{int(received * 100 / total)}%" if total > 0 else "—"
            self.table.setItem(row_index, 0, filename)
            self.table.setItem(row_index, 1, QTableWidgetItem(progress))
            self.table.setItem(row_index, 2, QTableWidgetItem(record["status"]))
            self.table.setItem(row_index, 3, QTableWidgetItem(record["destination"]))
        self.table.resizeColumnsToContents()

    def open_file(self) -> None:
        record = self.selected_record()
        if record:
            self.manager.open_file(record["destination"])

    def open_folder(self) -> None:
        record = self.selected_record()
        if record:
            self.manager.open_folder(record["destination"])

    def cancel_download(self) -> None:
        record = self.selected_record()
        if record:
            self.manager.cancel(record["id"])


class FindLineEdit(QLineEdit):
    previous_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.previous_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class FindBar(QWidget):
    def __init__(self, browser: QWebEngineView, parent=None, theme_manager=None, incognito: bool = False) -> None:
        super().__init__(parent)
        self.browser = browser
        self.theme_manager = theme_manager
        self.theme_incognito = incognito
        if theme_manager is not None:
            theme_manager.theme_changed.connect(self.apply_theme)
            self.apply_theme()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 4, 10, 4)
        outer.addStretch()
        panel = QFrame()
        panel.setObjectName("findPanel")
        panel.setMaximumWidth(460)
        row = QHBoxLayout(panel)
        row.setContentsMargins(7, 4, 7, 4)
        self.input = FindLineEdit()
        self.input.setPlaceholderText("Find in page")
        self.input.setMinimumWidth(220)
        self.status = QLabel("0/0")
        previous = QPushButton("↑")
        next_button = QPushButton("↓")
        close_button = QPushButton("×")
        row.addWidget(self.input)
        row.addWidget(self.status)
        row.addWidget(previous)
        row.addWidget(next_button)
        row.addWidget(close_button)
        outer.addWidget(panel)

        self.input.textChanged.connect(lambda: self.find(False))
        self.input.returnPressed.connect(lambda: self.find(False))
        self.input.previous_requested.connect(lambda: self.find(True))
        previous.clicked.connect(lambda: self.find(True))
        next_button.clicked.connect(lambda: self.find(False))
        close_button.clicked.connect(self.close_find)
        browser.page().findTextFinished.connect(self._result_changed)
        escape = QShortcut(QKeySequence("Escape"), self)
        escape.activated.connect(self.close_find)
        self._escape_shortcut = escape
        self.hide()

    def apply_theme(self, *_args) -> None:
        if self.theme_manager is not None:
            self.setStyleSheet(
                self.theme_manager.find_stylesheet(self.theme_incognito)
            )

    def show_and_focus(self) -> None:
        self.show()
        self.input.setFocus()
        self.input.selectAll()

    def find(self, backward: bool) -> None:
        flags = QWebEnginePage.FindFlag(0)
        if backward:
            flags |= QWebEnginePage.FindFlag.FindBackward
        self.browser.page().findText(self.input.text(), flags)

    def _result_changed(self, result) -> None:
        self.status.setText(f"{result.activeMatch()}/{result.numberOfMatches()}")

    def close_find(self) -> None:
        self.browser.page().findText("")
        self.hide()
        self.browser.setFocus()


class SettingsDialog(_ThemedDialog):
    settings_applied = Signal()

    def __init__(
        self,
        settings: SettingsManager,
        history: HistoryManager,
        profile: QWebEngineProfile,
        parent=None,
        theme_manager: ThemeManager | None = None,
        vpn_manager=None,
        site_permission_manager=None,
        cookie_privacy_manager=None,
        privacy_timeline_manager=None,
        malware_scanner=None,
        security_data=None,
        initial_tab: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.history = history
        self.profile = profile
        self.vpn_manager = vpn_manager
        self.site_permission_manager = site_permission_manager
        self.cookie_privacy_manager = cookie_privacy_manager
        self.privacy_timeline_manager = privacy_timeline_manager
        self.malware_scanner = malware_scanner
        self.security_data = security_data
        self.profile_manager = getattr(parent, "profile_manager", None)
        self._initial_webrtc = bool(
            self.settings.value("privacy/webrtc_leak_protection")
        )
        self._theme_saved = False
        self.setWindowTitle("Settings")
        self.resize(860, 640)
        self.configure_theme(theme_manager or getattr(parent, "theme_manager", None), bool(getattr(parent, "incognito", False)))

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._general_tab(), "General")
        self.tabs.addTab(self._appearance_tab(), "Appearance")
        self.tabs.addTab(self._search_tab(), "Search")
        self.tabs.addTab(self._privacy_tab(), "Privacy & Security")
        self.tabs.addTab(self._clear_browsing_data_tab(), "Clear Browsing Data")
        self.tabs.addTab(self._network_settings_tab(), "Network Settings")
        self.tabs.addTab(self._site_permissions_tab(), "Site Permissions")
        if self.privacy_timeline_manager is not None:
            self.tabs.addTab(self._privacy_timeline_tab(), "Privacy Timeline")
        if self.malware_scanner is not None and self.security_data is not None:
            self.tabs.addTab(self._malware_scanner_tab(), "Malware Scanner")
        self.tabs.addTab(self._downloads_tab(), "Downloads")
        self.tabs.addTab(self._local_diagnostics_tab(), "Local Diagnostics")
        if self.security_data is not None:
            self.tabs.addTab(self._security_audit_tab(), "Security Audit Log")
        self.tabs.addTab(self._developer_tab(), "Developer")
        self.tabs.currentChanged.connect(self._settings_tab_changed)
        if initial_tab:
            for index in range(self.tabs.count()):
                if self.tabs.tabText(index) == initial_tab:
                    self.tabs.setCurrentIndex(index)
                    break
        layout.addWidget(self.tabs)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _clear_browsing_data_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)
        heading = QLabel("Clear Browsing Data")
        heading.setStyleSheet("font-size: 20px; font-weight: 650;")
        layout.addWidget(heading)
        description = QLabel(
            "Choose a time range and exactly which browser records to remove. "
            "Files saved in your Downloads folder are never deleted."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        layout.addWidget(QLabel("Time range"))
        self.clear_time_range = QComboBox()
        for label, value in (
            ("Last hour", "hour"), ("Last 24 hours", "day"),
            ("Last 7 days", "week"), ("All time", "all"),
        ):
            self.clear_time_range.addItem(label, value)
        layout.addWidget(self.clear_time_range)

        self.clear_categories: dict[str, QCheckBox] = {}
        for key, label, checked in (
            ("history", "Browsing history", True),
            ("cookies", "Cookies and site data", True),
            ("cache", "Cached files", True),
            ("permissions", "Saved site permissions", False),
            ("download_list", "Download list", False),
            ("privacy_records", "Privacy records and timeline", False),
        ):
            checkbox = QCheckBox(label)
            checkbox.setChecked(checked)
            self.clear_categories[key] = checkbox
            layout.addWidget(checkbox)

        limitation = QLabel(
            "Note: Qt WebEngine does not expose timestamps for individual cookies "
            "or cache entries. If selected, cookies or cache are cleared completely, "
            "regardless of the chosen time range."
        )
        limitation.setWordWrap(True)
        layout.addWidget(limitation)
        layout.addStretch()
        clear_button = QPushButton("Clear Selected Data")
        clear_button.clicked.connect(self._clear_selected_browsing_data)
        clear_button.setEnabled(self.profile_manager is not None)
        layout.addWidget(clear_button, alignment=Qt.AlignmentFlag.AlignRight)
        return page

    def _clear_selected_browsing_data(self) -> None:
        if self.profile_manager is None:
            return
        selected = {
            key for key, checkbox in self.clear_categories.items()
            if checkbox.isChecked()
        }
        if not selected:
            QMessageBox.information(
                self, "Nothing selected", "Choose at least one data type."
            )
            return
        if QMessageBox.question(
            self, "Clear selected data?",
            "This cannot be undone. Downloaded files will remain on disk.",
        ) != QMessageBox.StandardButton.Yes:
            return
        manager = ClearBrowsingDataManager(self.profile_manager, self)
        manager.clear(str(self.clear_time_range.currentData()), selected)
        self.history.changed.emit()
        parent = self.parent()
        if parent is not None and hasattr(parent, "download_manager"):
            parent.download_manager.changed.emit()
        if self.privacy_timeline_manager is not None:
            self.privacy_timeline_manager.changed.emit()
        QMessageBox.information(
            self, "Browsing data cleared",
            "The selected records were removed. Downloaded files were not deleted."
        )

    def _local_diagnostics_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(9)
        heading = QLabel("Local Diagnostics")
        heading.setStyleSheet("font-size: 20px; font-weight: 650;")
        layout.addWidget(heading)
        description = QLabel(
            "Browser, Qt, antivirus, network-route, profile, cache, and recent "
            "local error information. Browsing and private-session data are excluded."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        self.diagnostics_report = QTextEdit()
        self.diagnostics_report.setReadOnly(True)
        self.diagnostics_report.setPlaceholderText(
            "Open this page or click Refresh Diagnostics to generate the local report."
        )
        layout.addWidget(self.diagnostics_report, 1)
        row = QHBoxLayout()
        row.addStretch()
        refresh = QPushButton("Refresh Diagnostics")
        copy = QPushButton("Copy Diagnostic Report")
        refresh.clicked.connect(self._refresh_diagnostics)
        copy.clicked.connect(self._copy_diagnostics)
        enabled = self.profile_manager is not None
        refresh.setEnabled(enabled)
        copy.setEnabled(enabled)
        row.addWidget(refresh)
        row.addWidget(copy)
        layout.addLayout(row)
        return page

    def _security_audit_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        heading = QLabel("Security Audit Log")
        heading.setStyleSheet("font-size: 20px; font-weight: 650;")
        note = QLabel(
            "Local security events only. Page content, searches, passwords, form "
            "values, and Incognito activity are not recorded."
        )
        note.setWordWrap(True)
        self.security_audit_table = QTableWidget(0, 4)
        self.security_audit_table.setHorizontalHeaderLabels(
            ["Time", "Event", "Title", "Detail"]
        )
        self.security_audit_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.security_audit_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.security_audit_table.horizontalHeader().setStretchLastSection(True)
        buttons = QHBoxLayout()
        refresh = QPushButton("Refresh")
        clear = QPushButton("Clear Audit Log")
        refresh.clicked.connect(self._refresh_security_audit)
        clear.clicked.connect(self._clear_security_audit)
        buttons.addStretch()
        buttons.addWidget(refresh)
        buttons.addWidget(clear)
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addWidget(self.security_audit_table, 1)
        layout.addLayout(buttons)
        self._refresh_security_audit()
        return page

    def _refresh_security_audit(self) -> None:
        if self.security_data is None:
            return
        records = self.security_data.activities(500)
        self.security_audit_table.setRowCount(len(records))
        for row, record in enumerate(records):
            values = (
                str(record.get("created_at", "")),
                str(record.get("event_type", "")).replace("_", " ").title(),
                str(record.get("title", "")),
                str(record.get("detail", "")),
            )
            for column, value in enumerate(values):
                self.security_audit_table.setItem(
                    row, column, QTableWidgetItem(value)
                )
        self.security_audit_table.resizeColumnsToContents()

    def _clear_security_audit(self) -> None:
        if self.security_data is not None and self._confirm(
            "Clear the local security audit log?"
        ):
            self.security_data.clear_activities()
            self._refresh_security_audit()

    def _settings_tab_changed(self, index: int) -> None:
        if self.tabs.tabText(index) == "Security Audit Log":
            self._refresh_security_audit()
        if (
            self.tabs.tabText(index) == "Local Diagnostics"
            and not self.diagnostics_report.toPlainText()
        ):
            self._refresh_diagnostics()

    def _refresh_diagnostics(self) -> None:
        if self.profile_manager is None:
            return
        self.diagnostics_report.setPlainText(
            DiagnosticsManager(self.profile_manager).report()
        )

    def _copy_diagnostics(self) -> None:
        if not self.diagnostics_report.toPlainText():
            self._refresh_diagnostics()
        report = self.diagnostics_report.toPlainText()
        if report:
            QApplication.clipboard().setText(report)
            QMessageBox.information(
                self, "Copied", "The privacy-safe diagnostic report was copied."
            )

    def _page(self) -> tuple[QWidget, QFormLayout]:
        page = QWidget()
        form = QFormLayout(page)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setLabelAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        form.setVerticalSpacing(12)
        return page, form

    def _general_tab(self) -> QWidget:
        page, form = self._page()
        self.homepage = QLineEdit(str(self.settings.value("general/homepage")))
        self.startup = QComboBox()
        self.startup.addItem("Open homepage", "homepage")
        self.startup.addItem("Open New Tab page", "new_tab")
        self.startup.addItem("Continue where you left off", "continue")
        index = self.startup.findData(self.settings.value("general/startup"))
        self.startup.setCurrentIndex(max(0, index))
        form.addRow("Homepage", self.homepage)
        form.addRow("Startup", self.startup)
        restore_note = QLabel(
            "Tabs and normal windows are saved locally for restart and crash recovery. "
            "Incognito windows are never restored."
        )
        restore_note.setWordWrap(True)
        restore_note.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        restore_note.setMinimumHeight(48)
        form.addRow("Session restore", restore_note)
        self.sleeping_tabs = QCheckBox("Suspend inactive tabs to reduce memory use")
        self.sleeping_tabs.setChecked(
            bool(self.settings.value("performance/sleeping_tabs_enabled"))
        )
        self.sleeping_minutes = QSpinBox()
        self.sleeping_minutes.setRange(1, 240)
        self.sleeping_minutes.setSuffix(" minutes")
        self.sleeping_minutes.setValue(
            int(self.settings.value("performance/sleeping_tabs_minutes"))
        )
        form.addRow("Sleeping tabs", self.sleeping_tabs)
        form.addRow("Sleep after", self.sleeping_minutes)
        return page

    def _appearance_tab(self) -> QWidget:
        page, form = self._page()
        self.theme = QComboBox()
        self.theme.addItems(["System", "Light", "Dark", "Neon", "Rainbow"])
        self.theme.setCurrentText(str(self.settings.value("appearance/theme")).title())
        self.theme.currentTextChanged.connect(self.preview_theme)
        self.bookmarks_bar = QCheckBox("Show bookmarks bar")
        self.bookmarks_bar.setChecked(
            bool(self.settings.value("appearance/show_bookmarks_bar"))
        )
        self.default_zoom = QSpinBox()
        self.default_zoom.setRange(25, 500)
        self.default_zoom.setSuffix("%")
        self.default_zoom.setValue(int(self.settings.value("appearance/default_zoom")))
        form.addRow("Theme", self.theme)
        form.addRow("", self.bookmarks_bar)
        form.addRow("Default zoom", self.default_zoom)
        return page

    def preview_theme(self, theme: str) -> None:
        if self.theme_manager is not None:
            self.theme_manager.preview(theme)

    def _search_tab(self) -> QWidget:
        page, form = self._page()
        self.search_engine = QComboBox()
        self.search_engine.addItems(["Google", "Bing", "DuckDuckGo"])
        self.search_engine.setCurrentText(str(self.settings.value("search/engine")))
        form.addRow("Default search engine", self.search_engine)
        return page

    def _downloads_tab(self) -> QWidget:
        page, form = self._page()
        location_row = QWidget()
        row = QHBoxLayout(location_row)
        row.setContentsMargins(0, 0, 0, 0)
        self.download_location = QLineEdit(
            str(self.settings.value("downloads/location"))
        )
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.choose_download_location)
        row.addWidget(self.download_location)
        row.addWidget(browse)
        self.ask_download = QCheckBox("Ask where to save each file")
        self.ask_download.setChecked(
            bool(self.settings.value("downloads/ask_each_time"))
        )
        form.addRow("Download location", location_row)
        form.addRow("", self.ask_download)
        return page

    def _privacy_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("privacyScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("privacySettingsContent")
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        page_layout.addWidget(scroll)

        self.webrtc_protection = QCheckBox("WebRTC Leak Protection")
        self.webrtc_protection.setChecked(
            bool(self.settings.value("privacy/webrtc_leak_protection"))
        )
        webrtc_description = QLabel(
            "Helps prevent websites from exposing your local IP address through WebRTC."
        )
        webrtc_description.setWordWrap(True)

        self.tracker_protection = QCheckBox("Tracker Protection")
        self.tracker_protection.setChecked(
            bool(self.settings.value("privacy/tracker_protection_enabled"))
        )
        tracker_description = QLabel(
            "Blocks requests to many common tracking and analytics domains. "
            "Use the toolbar shield to change protection for the current site."
        )
        tracker_description.setWordWrap(True)

        self.adblock_enabled = QCheckBox("Zyvro Ad Blocker")
        self.adblock_enabled.setChecked(
            bool(self.settings.value("privacy/adblock_enabled"))
        )
        adblock_description = QLabel(
            "Uses the native Rust ad-block engine with EasyList and uAssets "
            "rules to block ads and trackers locally before they load."
        )
        adblock_description.setWordWrap(True)
        allowed_label = QLabel("Allowed sites")
        allowed_label.setStyleSheet("font-weight: 600;")
        self.adblock_allowed_sites = QListWidget()
        self.adblock_allowed_sites.setMaximumHeight(92)
        allowed = self.settings.value("privacy/adblock_allowed_sites") or []
        if isinstance(allowed, str):
            allowed = [allowed]
        self.adblock_allowed_sites.addItems(sorted(str(site) for site in allowed))
        remove_allowed = QPushButton("Remove Selected Site")
        remove_allowed.clicked.connect(self._remove_allowed_adblock_site)

        self.permissions_group = QGroupBox("Saved Site Permissions")
        permissions_layout = QVBoxLayout(self.permissions_group)
        permissions_note = QLabel(
            "Saved camera, microphone, location, notification, clipboard, "
            "popup, and autoplay choices."
        )
        permissions_note.setWordWrap(True)
        permissions_layout.addWidget(permissions_note)
        all_records = (
            self.site_permission_manager.records()
            if self.site_permission_manager is not None else []
        )
        records = [
            record for record in all_records
            if record["permission"] != "javascript"
        ]
        self.site_permissions_table = QTableWidget(len(records), 3)
        self.site_permissions_table.setHorizontalHeaderLabels(
            ["Site", "Permission", "Setting"]
        )
        self.site_permissions_table.horizontalHeader().setStretchLastSection(True)
        self.site_permissions_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        for row, record in enumerate(records):
            site_item = QTableWidgetItem(record["site"])
            site_item.setFlags(site_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            permission_item = QTableWidgetItem(
                PERMISSION_LABELS.get(record["permission"], record["permission"].title())
            )
            permission_item.setData(Qt.ItemDataRole.UserRole, record["permission"])
            permission_item.setFlags(
                permission_item.flags() & ~Qt.ItemFlag.ItemIsEditable
            )
            choice = QComboBox()
            choice.addItem("Ask", "ask")
            choice.addItem("Allow", "allow")
            choice.addItem("Block", "block")
            choice.setCurrentIndex(max(0, choice.findData(record["decision"])))
            self.site_permissions_table.setItem(row, 0, site_item)
            self.site_permissions_table.setItem(row, 1, permission_item)
            self.site_permissions_table.setCellWidget(row, 2, choice)
        permissions_layout.addWidget(self.site_permissions_table)
        permission_buttons = QWidget()
        permission_buttons_layout = QHBoxLayout(permission_buttons)
        permission_buttons_layout.setContentsMargins(0, 0, 0, 0)
        remove_permission = QPushButton("Remove Selected")
        reset_permissions = QPushButton("Reset All")
        remove_permission.clicked.connect(self._remove_selected_permissions)
        reset_permissions.clicked.connect(self._reset_permissions)
        permission_buttons_layout.addWidget(remove_permission)
        permission_buttons_layout.addWidget(reset_permissions)
        permission_buttons_layout.addStretch()
        permissions_layout.addWidget(permission_buttons)

        cookies_group = QGroupBox("Cookies")
        cookies_layout = QVBoxLayout(cookies_group)
        self.block_third_party_cookies = QCheckBox("Block Third-Party Cookies")
        self.block_third_party_cookies.setChecked(
            bool(self.settings.value("privacy/block_third_party_cookies"))
        )
        cookie_note = QLabel(
            "Reduces cross-site tracking while keeping normal first-party login cookies."
        )
        cookie_note.setWordWrap(True)
        self.clear_cookies_on_exit = QCheckBox("Clear Cookies on Exit")
        self.clear_cookies_on_exit.setChecked(
            bool(self.settings.value("privacy/clear_cookies_on_exit"))
        )
        self.cookie_exceptions = QListWidget()
        self.cookie_exceptions.setMaximumHeight(92)
        cookie_sites = self.settings.value("privacy/third_party_cookie_exceptions") or []
        if isinstance(cookie_sites, str):
            cookie_sites = [cookie_sites]
        self.cookie_exceptions.addItems(sorted(str(site) for site in cookie_sites))
        remove_cookie_exception = QPushButton("Remove Selected Exception")
        remove_cookie_exception.clicked.connect(self._remove_cookie_exception)
        cookies_layout.addWidget(self.block_third_party_cookies)
        cookies_layout.addWidget(cookie_note)
        cookies_layout.addWidget(self.clear_cookies_on_exit)
        cookies_layout.addWidget(QLabel("Sites allowed to use third-party cookies"))
        cookies_layout.addWidget(self.cookie_exceptions)
        cookies_layout.addWidget(remove_cookie_exception)

        self.vpn_group = QGroupBox("VPN / Private Network")
        vpn_form = QFormLayout(self.vpn_group)
        self.vpn_mode = QComboBox()
        self.vpn_mode.addItem("Off", "off")
        self.vpn_mode.addItem("System VPN", "system")
        self.vpn_mode.addItem("Tor", "tor")
        self.vpn_mode.addItem("Custom Proxy", "custom")
        mode_index = self.vpn_mode.findData(
            str(self.settings.value("privacy/vpn_mode"))
        )
        self.vpn_mode.setCurrentIndex(max(0, mode_index))

        self.tor_host = QLineEdit(str(self.settings.value("privacy/tor_host")))
        self.tor_port = QSpinBox()
        self.tor_port.setRange(1, 65535)
        self.tor_port.setValue(int(self.settings.value("privacy/tor_port")))

        self.proxy_type = QComboBox()
        self.proxy_type.addItem("SOCKS5", "socks5")
        self.proxy_type.addItem("HTTP Proxy", "http")
        proxy_type_index = self.proxy_type.findData(
            str(self.settings.value("privacy/proxy_type"))
        )
        self.proxy_type.setCurrentIndex(max(0, proxy_type_index))
        self.proxy_host = QLineEdit(str(self.settings.value("privacy/proxy_host")))
        self.proxy_port = QSpinBox()
        self.proxy_port.setRange(1, 65535)
        self.proxy_port.setValue(int(self.settings.value("privacy/proxy_port")))
        self.proxy_username = QLineEdit(
            str(self.settings.value("privacy/proxy_username"))
        )
        self.proxy_password = QLineEdit()
        self.proxy_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.proxy_password.setPlaceholderText("Not saved — session only")

        self.vpn_country = QComboBox()
        countries = (
            self.vpn_manager.COUNTRIES if self.vpn_manager is not None else
            ("United States", "Canada", "United Kingdom", "Germany", "Netherlands",
             "France", "Switzerland", "Japan", "Singapore", "Australia")
        )
        self.vpn_country.addItems(countries)
        self.vpn_country.setCurrentText(
            str(self.settings.value("privacy/vpn_country"))
        )

        self.vpn_kill_switch = QCheckBox("Kill Switch")
        self.vpn_kill_switch.setChecked(
            bool(self.settings.value("privacy/vpn_kill_switch"))
        )
        kill_description = QLabel(
            "Block browsing if the private connection is lost."
        )
        kill_description.setWordWrap(True)

        vpn_form.addRow("VPN Mode", self.vpn_mode)
        vpn_form.addRow("Custom location", self.vpn_country)
        vpn_form.addRow("Tor host", self.tor_host)
        vpn_form.addRow("Tor port", self.tor_port)
        vpn_form.addRow("Proxy type", self.proxy_type)
        vpn_form.addRow("Proxy host", self.proxy_host)
        vpn_form.addRow("Proxy port", self.proxy_port)
        vpn_form.addRow("Username (optional)", self.proxy_username)
        vpn_form.addRow("Password (optional)", self.proxy_password)
        vpn_form.addRow("", self.vpn_kill_switch)
        vpn_form.addRow("", kill_description)
        self.vpn_mode.currentIndexChanged.connect(self._update_vpn_fields)
        self._update_vpn_fields()

        security_group = QGroupBox("Navigation Security")
        security_layout = QVBoxLayout(security_group)
        self.https_only = QCheckBox("HTTPS-Only Mode")
        self.https_only.setChecked(
            bool(self.settings.value("security/https_only_enabled"))
        )
        https_note = QLabel(
            "Upgrade HTTP navigation to HTTPS and warn before allowing an "
            "unencrypted connection. Overrides last only for this browser run."
        )
        https_note.setWordWrap(True)
        self.malicious_site_protection = QCheckBox(
            "Phishing and Malicious-Site Protection"
        )
        self.malicious_site_protection.setChecked(
            bool(self.settings.value("security/malicious_site_protection"))
        )
        local_count = (
            self.profile_manager.navigation_security.host_count()
            if self.profile_manager is not None else 0
        )
        self.blocklist_count = QLabel(
            f"{local_count:,} malicious hosts stored locally. URLs are checked "
            "on this device and browsing history is not uploaded."
        )
        self.blocklist_count.setWordWrap(True)
        blocklist_buttons = QHBoxLayout()
        import_blocklist = QPushButton("Import Host List…")
        clear_blocklist = QPushButton("Clear Host List")
        import_blocklist.clicked.connect(self._import_security_host_list)
        clear_blocklist.clicked.connect(self._clear_security_host_list)
        blocklist_buttons.addWidget(import_blocklist)
        blocklist_buttons.addWidget(clear_blocklist)
        blocklist_buttons.addStretch()
        security_layout.addWidget(self.https_only)
        security_layout.addWidget(https_note)
        security_layout.addWidget(self.malicious_site_protection)
        security_layout.addWidget(self.blocklist_count)
        security_layout.addLayout(blocklist_buttons)

        local_note = QLabel("Your browser settings stay on this device.")
        local_note.setWordWrap(True)
        local_note.setStyleSheet("font-weight: 600; margin: 8px 0;")

        layout.addWidget(self.webrtc_protection)
        layout.addWidget(webrtc_description)
        layout.addSpacing(12)
        layout.addWidget(self.tracker_protection)
        layout.addWidget(tracker_description)
        layout.addSpacing(12)
        layout.addWidget(self.adblock_enabled)
        layout.addWidget(adblock_description)
        layout.addSpacing(6)
        layout.addWidget(allowed_label)
        layout.addWidget(self.adblock_allowed_sites)
        layout.addWidget(remove_allowed)
        layout.addSpacing(12)
        layout.addWidget(cookies_group)
        layout.addSpacing(12)
        layout.addWidget(security_group)
        layout.addSpacing(12)
        layout.addWidget(local_note)
        malware_disclosure = QLabel(
            "Downloaded files up to 500 MB may be automatically submitted to "
            "VirusTotal for malware analysis. Browser profiles, cookies, "
            "credentials, settings, and internal browser files are never submitted."
        )
        malware_disclosure.setWordWrap(True)
        malware_disclosure.setStyleSheet("font-weight: 600; margin: 8px 0;")
        layout.addWidget(malware_disclosure)

        clear_history = QPushButton("Clear Browsing History")
        clear_cookies = QPushButton("Clear Cookies")
        clear_cache = QPushButton("Clear Cache")
        clear_history.clicked.connect(self.clear_history)
        clear_cookies.clicked.connect(self.clear_cookies)
        clear_cache.clicked.connect(self.clear_cache)
        layout.addWidget(clear_history)
        layout.addWidget(clear_cookies)
        layout.addWidget(clear_cache)
        layout.addStretch()
        return page

    def _network_settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        explanation = QLabel(
            "Python Browser does not include or invent VPN servers. Configure "
            "a real HTTP/SOCKS5 proxy, run a local Tor service, or use an "
            "existing system VPN. Connection status is shown only after a real check."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        layout.addWidget(self.vpn_group)
        self.network_test_status = QLabel()
        self.network_test_status.setWordWrap(True)
        layout.addWidget(self.network_test_status)
        test_connection = QPushButton("Apply & Test Connection")
        test_connection.clicked.connect(self._apply_and_test_vpn)
        layout.addWidget(test_connection)
        layout.addStretch()
        if self.vpn_manager is not None:
            self.vpn_manager.state_changed.connect(self._update_network_status)
        self._update_network_status()
        return page

    def _update_network_status(self) -> None:
        if not hasattr(self, "network_test_status"):
            return
        if self.vpn_manager is None:
            self.network_test_status.setText("Status: Network manager unavailable")
            return
        state = self.vpn_manager.state().replace("_", " ").title()
        self.network_test_status.setText(
            f"Status: {state}\n{self.vpn_manager.detail()}"
        )

    def _apply_and_test_vpn(self) -> None:
        if self.vpn_manager is None:
            return
        mode = str(self.vpn_mode.currentData())
        self.settings.save_values({
            "privacy/vpn_mode": mode,
            "privacy/tor_host": self.tor_host.text().strip() or "127.0.0.1",
            "privacy/tor_port": self.tor_port.value(),
            "privacy/proxy_type": self.proxy_type.currentData(),
            "privacy/proxy_host": self.proxy_host.text().strip(),
            "privacy/proxy_port": self.proxy_port.value(),
            "privacy/proxy_username": self.proxy_username.text().strip(),
            "privacy/vpn_country": self.vpn_country.currentText(),
            "privacy/vpn_kill_switch": self.vpn_kill_switch.isChecked(),
        })
        self.vpn_manager.set_kill_switch(self.vpn_kill_switch.isChecked())
        if mode == "custom":
            self.vpn_manager.save_server_profile(
                self.vpn_country.currentText(), self.proxy_type.currentData(),
                self.proxy_host.text(), self.proxy_port.value(),
                self.proxy_username.text(),
            )
            self.vpn_manager.connect_country(
                self.vpn_country.currentText(), self.proxy_password.text()
            )
        elif mode == "off":
            self.vpn_manager.disconnect()
        else:
            self.vpn_manager.connect_mode(mode, self.proxy_password.text())
        self._update_network_status()

    def _site_permissions_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self.permissions_group, 1)

        javascript_group = QGroupBox("Site Settings → JavaScript")
        javascript_layout = QVBoxLayout(javascript_group)
        description = QLabel(
            "JavaScript is allowed normally. Domains listed here are blocked "
            "using Qt WebEngine's per-page JavaScript setting."
        )
        description.setWordWrap(True)
        javascript_layout.addWidget(description)
        self.javascript_exceptions = QListWidget()
        javascript_sites = sorted(
            record["site"] for record in (
                self.site_permission_manager.records()
                if self.site_permission_manager is not None else []
            )
            if record["permission"] == "javascript"
            and record["decision"] == "block"
        )
        self.javascript_exceptions.addItems(javascript_sites)
        javascript_layout.addWidget(self.javascript_exceptions)
        remove_javascript = QPushButton("Remove Selected JavaScript Exception")
        remove_javascript.clicked.connect(self._remove_javascript_exception)
        javascript_layout.addWidget(remove_javascript)
        layout.addWidget(javascript_group)
        return page

    def _privacy_timeline_tab(self) -> QWidget:
        return PrivacyTimelineWidget(
            self.privacy_timeline_manager,
            self.theme_manager,
            self.theme_incognito,
        )

    def _malware_scanner_tab(self) -> QWidget:
        return SecurityCenterWidget(
            self.malware_scanner, self.security_data,
            self.theme_manager or getattr(self.parent(), "theme_manager", None),
            self,
            incognito=self.theme_incognito,
        )

    def _update_vpn_fields(self) -> None:
        mode = self.vpn_mode.currentData()
        tor_enabled = mode == "tor"
        custom_enabled = mode == "custom"
        self.tor_host.setEnabled(tor_enabled)
        self.tor_port.setEnabled(tor_enabled)
        for widget in (
            self.proxy_type,
            self.proxy_host,
            self.proxy_port,
            self.proxy_username,
            self.proxy_password,
        ):
            widget.setEnabled(custom_enabled)

    def _remove_allowed_adblock_site(self) -> None:
        for item in self.adblock_allowed_sites.selectedItems():
            self.adblock_allowed_sites.takeItem(
                self.adblock_allowed_sites.row(item)
            )

    def _remove_selected_permissions(self) -> None:
        rows = sorted(
            {index.row() for index in self.site_permissions_table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.site_permissions_table.removeRow(row)

    def _reset_permissions(self) -> None:
        self.site_permissions_table.setRowCount(0)
        if hasattr(self, "javascript_exceptions"):
            self.javascript_exceptions.clear()

    def _remove_javascript_exception(self) -> None:
        for item in self.javascript_exceptions.selectedItems():
            self.javascript_exceptions.takeItem(
                self.javascript_exceptions.row(item)
            )

    def _remove_cookie_exception(self) -> None:
        for item in self.cookie_exceptions.selectedItems():
            self.cookie_exceptions.takeItem(self.cookie_exceptions.row(item))

    def _developer_tab(self) -> QWidget:
        page, form = self._page()
        form.addRow("Developer Tools", QLabel("F12 or Ctrl+Shift+I"))
        behavior = QComboBox()
        behavior.addItem("Dock right", "dock_right")
        behavior.setEnabled(False)
        form.addRow("Window behavior", behavior)
        self.update_manifest = QLineEdit(
            str(self.settings.value("updates/manifest_url") or "")
        )
        self.update_manifest.setPlaceholderText("https://example.com/browser-update.json")
        manifest_note = QLabel(
            "Optional HTTPS JSON source used only when you click Check for Updates. "
            "The browser never installs or restarts automatically."
        )
        manifest_note.setWordWrap(True)
        form.addRow("Update manifest", self.update_manifest)
        self.update_public_key = QLineEdit(
            str(self.settings.value("updates/ed25519_public_key") or "")
        )
        self.update_public_key.setPlaceholderText("Base64 Ed25519 public key")
        form.addRow("Update signing key", self.update_public_key)
        form.addRow("", manifest_note)
        return page

    def _import_security_host_list(self) -> None:
        if self.profile_manager is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Malicious Host List", "",
            "Host lists (*.txt *.hosts);;All files (*)",
        )
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
            amount = self.profile_manager.navigation_security.import_hosts(
                text, Path(path).name
            )
        except OSError as error:
            QMessageBox.critical(self, "Import failed", str(error))
            return
        self.blocklist_count.setText(
            f"{self.profile_manager.navigation_security.host_count():,} malicious "
            "hosts stored locally. URLs are checked on this device and browsing "
            "history is not uploaded."
        )
        QMessageBox.information(
            self, "Host list imported", f"Imported {amount:,} valid hosts."
        )

    def _clear_security_host_list(self) -> None:
        if self.profile_manager is None or not self._confirm(
            "Clear the local malicious-host list?"
        ):
            return
        self.profile_manager.navigation_security.clear_hosts()
        self.blocklist_count.setText(
            "0 malicious hosts stored locally. Import a trusted host list to enable matching."
        )

    def choose_download_location(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Choose Download Folder", self.download_location.text()
        )
        if selected:
            self.download_location.setText(selected)

    def _confirm(self, text: str) -> bool:
        return (
            QMessageBox.question(self, "Confirm", text)
            == QMessageBox.StandardButton.Yes
        )

    def clear_history(self) -> None:
        if self._confirm("Clear all browsing history?"):
            self.history.clear()

    def clear_cookies(self) -> None:
        if self._confirm("Clear all browser cookies?"):
            self.profile.cookieStore().deleteAllCookies()

    def clear_cache(self) -> None:
        if self._confirm("Clear the browser cache?"):
            self.profile.clearHttpCache()

    def save(self) -> None:
        selected_theme = self.theme.currentText().lower()
        self.settings.save_values(
            {
                "general/homepage": self.homepage.text().strip()
                or "https://www.google.com",
                "general/startup": self.startup.currentData(),
                "appearance/theme": selected_theme,
                "appearance/show_bookmarks_bar": self.bookmarks_bar.isChecked(),
                "appearance/default_zoom": self.default_zoom.value(),
                "search/engine": self.search_engine.currentText(),
                "downloads/location": self.download_location.text().strip(),
                "downloads/ask_each_time": self.ask_download.isChecked(),
                "privacy/webrtc_leak_protection": self.webrtc_protection.isChecked(),
                "privacy/tracker_protection_enabled": self.tracker_protection.isChecked(),
                "privacy/tracker_choice_remembered": True,
                "privacy/adblock_enabled": self.adblock_enabled.isChecked(),
                "privacy/adblock_allowed_sites": [
                    self.adblock_allowed_sites.item(row).text()
                    for row in range(self.adblock_allowed_sites.count())
                ],
                "privacy/vpn_mode": self.vpn_mode.currentData(),
                "privacy/tor_host": self.tor_host.text().strip() or "127.0.0.1",
                "privacy/tor_port": self.tor_port.value(),
                "privacy/proxy_type": self.proxy_type.currentData(),
                "privacy/proxy_host": self.proxy_host.text().strip(),
                "privacy/proxy_port": self.proxy_port.value(),
                "privacy/proxy_username": self.proxy_username.text().strip(),
                "privacy/vpn_kill_switch": self.vpn_kill_switch.isChecked(),
                "privacy/block_third_party_cookies": self.block_third_party_cookies.isChecked(),
                "privacy/third_party_cookie_prompted": True,
                "privacy/clear_cookies_on_exit": self.clear_cookies_on_exit.isChecked(),
                "privacy/third_party_cookie_exceptions": [
                    self.cookie_exceptions.item(row).text()
                    for row in range(self.cookie_exceptions.count())
                ],
                "privacy/vpn_country": self.vpn_country.currentText(),
                "developer/devtools_behavior": "dock_right",
                "performance/sleeping_tabs_enabled": self.sleeping_tabs.isChecked(),
                "performance/sleeping_tabs_minutes": self.sleeping_minutes.value(),
                "updates/manifest_url": self.update_manifest.text().strip(),
                "updates/ed25519_public_key": self.update_public_key.text().strip(),
                "security/https_only_enabled": self.https_only.isChecked(),
                "security/malicious_site_protection": self.malicious_site_protection.isChecked(),
            }
        )
        self._theme_saved = True
        if self.theme_manager is not None:
            self.theme_manager.commit(selected_theme)
        self.profile.setDownloadPath(self.download_location.text().strip())
        if self.vpn_manager is not None:
            self.vpn_manager.save_server_profile(
                self.vpn_country.currentText(), self.proxy_type.currentData(),
                self.proxy_host.text(), self.proxy_port.value(),
                self.proxy_username.text(),
            )
            # The password is passed directly to the manager's memory only and
            # is intentionally absent from the QSettings values above.
            self.vpn_manager.reload_from_settings(self.proxy_password.text())
        if self.site_permission_manager is not None:
            records = []
            for row in range(self.site_permissions_table.rowCount()):
                site_item = self.site_permissions_table.item(row, 0)
                permission_item = self.site_permissions_table.item(row, 1)
                choice = self.site_permissions_table.cellWidget(row, 2)
                if site_item and permission_item and isinstance(choice, QComboBox):
                    records.append({
                        "site": site_item.text(),
                        "permission": permission_item.data(Qt.ItemDataRole.UserRole),
                        "decision": choice.currentData(),
                    })
            records.extend(
                {
                    "site": self.javascript_exceptions.item(row).text(),
                    "permission": "javascript",
                    "decision": "block",
                }
                for row in range(self.javascript_exceptions.count())
            )
            self.site_permission_manager.replace_records(records)
        if (
            self.privacy_timeline_manager is not None
            and self.webrtc_protection.isChecked() != self._initial_webrtc
        ):
            state = "enabled" if self.webrtc_protection.isChecked() else "disabled"
            self.privacy_timeline_manager.record(
                "permissions", f"webrtc_{state}",
                f"WebRTC leak protection {state}", "Browser settings",
            )
        self.settings_applied.emit()
        self.accept()

    def reject(self) -> None:
        if not self._theme_saved and self.theme_manager is not None:
            self.theme_manager.cancel_preview()
        super().reject()

    def closeEvent(self, event) -> None:
        if not self._theme_saved and self.theme_manager is not None:
            self.theme_manager.cancel_preview()
        super().closeEvent(event)
