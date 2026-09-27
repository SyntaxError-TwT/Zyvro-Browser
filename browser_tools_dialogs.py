"""Compact UI for maintenance, tab, performance, and diagnostic tools."""

from __future__ import annotations

from pathlib import Path

from qtpy.QtCore import Qt, QTimer, QUrl
from qtpy.QtGui import QDesktopServices
from qtpy.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout,
)
from qtpy.QtWebEngineCore import QWebEnginePage

from browser_maintenance import (
    BackupManager, ClearBrowsingDataManager, DiagnosticsManager, UpdateChecker,
)


class _ToolDialog(QDialog):
    def __init__(self, window, title: str, size=(700, 500)) -> None:
        super().__init__(window)
        self.browser_window = window
        self.setWindowTitle(title)
        self.resize(*size)
        self.setStyleSheet(window.theme_manager.dialog_stylesheet(window.incognito))
        window.theme_manager.theme_changed.connect(
            lambda *_: self.setStyleSheet(
                window.theme_manager.dialog_stylesheet(window.incognito)
            )
        )


class ClearBrowsingDataDialog(_ToolDialog):
    def __init__(self, window) -> None:
        super().__init__(window, "Clear Browsing Data", (520, 490))
        root = QVBoxLayout(self)
        title = QLabel("Clear Browsing Data")
        title.setStyleSheet("font-size: 21px; font-weight: 650;")
        root.addWidget(title)
        self.time_range = QComboBox()
        for label, value in (
            ("Last hour", "hour"), ("Last 24 hours", "day"),
            ("Last 7 days", "week"), ("All time", "all"),
        ):
            self.time_range.addItem(label, value)
        root.addWidget(QLabel("Time range"))
        root.addWidget(self.time_range)
        self.categories: dict[str, QCheckBox] = {}
        for key, label, checked in (
            ("history", "Browsing history", True),
            ("cookies", "Cookies and site data", True),
            ("cache", "Cached files", True),
            ("permissions", "Saved site permissions", False),
            ("download_list", "Download list", False),
            ("privacy_records", "Privacy records and timeline", False),
        ):
            box = QCheckBox(label)
            box.setChecked(checked)
            self.categories[key] = box
            root.addWidget(box)
        note = QLabel(
            "Downloaded files are never deleted. Qt clears all cookies/cache when "
            "those categories are selected because Chromium does not expose their timestamps."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        root.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        cancel = QPushButton("Cancel")
        clear = QPushButton("Clear Data")
        cancel.clicked.connect(self.reject)
        clear.clicked.connect(self._clear)
        row.addWidget(cancel)
        row.addWidget(clear)
        root.addLayout(row)

    def _clear(self) -> None:
        selected = {key for key, box in self.categories.items() if box.isChecked()}
        if not selected:
            QMessageBox.information(self, "Nothing selected", "Choose at least one data type.")
            return
        if QMessageBox.question(
            self, "Clear selected data?",
            "This cannot be undone. Downloaded files will remain on disk.",
        ) != QMessageBox.StandardButton.Yes:
            return
        manager = ClearBrowsingDataManager(self.browser_window.profile_manager, self)
        result = manager.clear(str(self.time_range.currentData()), selected)
        self.browser_window.history_manager.changed.emit()
        self.browser_window.download_manager.changed.emit()
        self.browser_window.privacy_timeline_manager.changed.emit()
        QMessageBox.information(
            self, "Browsing data cleared",
            "Selected browser records were removed. Downloaded files were not touched."
        )
        self.accept()


class BackupDialog(_ToolDialog):
    def __init__(self, window) -> None:
        super().__init__(window, "Export or Import Browser Data", (540, 320))
        self.manager = BackupManager(
            window.settings_manager, window.bookmark_manager,
            window.site_permission_manager, self,
        )
        root = QVBoxLayout(self)
        title = QLabel("Export and Import")
        title.setStyleSheet("font-size: 21px; font-weight: 650;")
        root.addWidget(title)
        description = QLabel(
            "Back up bookmarks, ordinary settings, and saved site permissions. "
            "History, cookies, passwords, proxy passwords, and API keys are excluded."
        )
        description.setWordWrap(True)
        root.addWidget(description)
        export = QPushButton("Export Backup…")
        import_button = QPushButton("Import Backup…")
        export.clicked.connect(self.export_backup)
        import_button.clicked.connect(self.import_backup)
        root.addWidget(export)
        root.addWidget(import_button)
        root.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        root.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)

    def export_backup(self) -> str:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Browser Backup", "python-browser-backup.json",
            "JSON backup (*.json)",
        )
        if path:
            self.manager.export_file(path)
            QMessageBox.information(self, "Backup exported", f"Saved to:\n{path}")
        return path

    def import_backup(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Browser Backup", "", "JSON backup (*.json)"
        )
        if not path:
            return
        if QMessageBox.question(
            self, "Import backup?",
            "Settings and site permissions will be updated. Bookmarks will be merged."
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.manager.import_file(path)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Import failed", str(error))
            return
        self.browser_window.profile_manager.refresh_privacy_settings()
        self.browser_window._apply_settings()
        QMessageBox.information(
            self, "Backup imported",
            f"Imported {result['bookmarks']} bookmarks, {result['settings']} settings, "
            f"and {result['permissions']} permission rules."
        )


class TabOverviewDialog(_ToolDialog):
    def __init__(self, window) -> None:
        super().__init__(window, "Search Tabs", (880, 540))
        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search open tabs by title or address")
        self.search.textChanged.connect(self.refresh)
        duplicate = QPushButton("Close Duplicate Tabs")
        duplicate.clicked.connect(self.close_duplicates)
        top.addWidget(self.search, 1)
        top.addWidget(duplicate)
        root.addLayout(top)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Title", "Address", "Pinned", "Group", "Window"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.activate_selected)
        root.addWidget(self.table)
        row = QHBoxLayout()
        row.addStretch()
        activate = QPushButton("Switch to Tab")
        close = QPushButton("Close Tab")
        activate.clicked.connect(self.activate_selected)
        close.clicked.connect(self.close_selected)
        row.addWidget(close)
        row.addWidget(activate)
        root.addLayout(row)
        self.refresh()

    def _matching_tabs(self):
        needle = self.search.text().casefold().strip()
        rows = []
        for window in sorted(self.browser_window._open_windows, key=id):
            for index in range(window.tabs.count()):
                page = window.tabs.widget(index)
                if not hasattr(page, "browser"):
                    continue
                title = str(getattr(page, "tab_title", page.browser.title() or "New Tab"))
                url = page.browser.url().toDisplayString()
                if needle and needle not in title.casefold() and needle not in url.casefold():
                    continue
                rows.append((window, page, title, url))
        return rows

    def refresh(self, *_args) -> None:
        rows = self._matching_tabs()
        self._rows = rows
        self.table.setRowCount(len(rows))
        for row, (window, page, title, url) in enumerate(rows):
            values = (
                title, url, "Yes" if page.pinned else "No",
                page.tab_group or "—", "Incognito" if window.incognito else "Normal",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                self.table.setItem(row, column, item)

    def _selected(self):
        row = self.table.currentRow()
        if 0 <= row < len(getattr(self, "_rows", [])):
            window, page, _title, _url = self._rows[row]
            return window, page
        return None

    def activate_selected(self, *_args) -> None:
        selected = self._selected()
        if selected:
            window, page = selected
            index = window.tabs.indexOf(page)
            if index >= 0:
                window.tabs.setCurrentIndex(index)
                window.showNormal()
                window.raise_()
                window.activateWindow()
                self.accept()

    def close_selected(self) -> None:
        selected = self._selected()
        if selected:
            window, page = selected
            window.close_tab(window.tabs.indexOf(page))
            self.refresh()

    def close_duplicates(self) -> None:
        duplicates = []
        seen = set()
        # Keep pinned tabs and the first occurrence of each canonical URL.
        rows = self._matching_tabs()
        rows.sort(key=lambda row: (not bool(row[1].pinned), id(row[0])))
        for window, page, _title, url in rows:
            key = url.rstrip("/").casefold()
            if key and key in seen and not page.pinned:
                duplicates.append((window, page))
            else:
                seen.add(key)
        if not duplicates:
            QMessageBox.information(self, "No duplicates", "No duplicate open tabs were found.")
            return
        if QMessageBox.question(
            self, "Close duplicates?", f"Close {len(duplicates)} duplicate tab(s)?"
        ) != QMessageBox.StandardButton.Yes:
            return
        for window, page in duplicates:
            index = window.tabs.indexOf(page)
            if index >= 0 and window.tabs.count() > 1:
                window.close_tab(index)
        self.refresh()


class TaskManagerDialog(_ToolDialog):
    def __init__(self, window) -> None:
        super().__init__(window, "Browser Task Manager", (850, 520))
        root = QVBoxLayout(self)
        title = QLabel("Browser Task Manager")
        title.setStyleSheet("font-size: 21px; font-weight: 650;")
        root.addWidget(title)
        note = QLabel(
            "Qt WebEngine does not expose reliable per-tab Chromium CPU or memory metrics. "
            "The real lifecycle, audio, capture, loading, and DevTools states are shown below."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Tab", "Renderer PID", "Lifecycle", "Loading", "Audio", "Capture", "DevTools"]
        )
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        root.addWidget(self.table)
        row = QHBoxLayout()
        row.addStretch()
        activate = QPushButton("Activate")
        sleep = QPushButton("Sleep / Wake")
        stop = QPushButton("End Tab Task")
        activate.clicked.connect(self.activate_selected)
        sleep.clicked.connect(self.toggle_sleep)
        stop.clicked.connect(self.stop_selected)
        row.addWidget(activate)
        row.addWidget(sleep)
        row.addWidget(stop)
        root.addLayout(row)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def refresh(self) -> None:
        selected_page = self._selected()
        pages = []
        for window in self.browser_window._open_windows:
            for index in range(window.tabs.count()):
                page = window.tabs.widget(index)
                if hasattr(page, "browser"):
                    pages.append((window, page))
        self.table.setRowCount(len(pages))
        self._rows = pages
        for row, (window, page) in enumerate(pages):
            web = page.browser
            lifecycle = web.page().lifecycleState().name
            values = (
                getattr(page, "tab_title", web.title() or "New Tab"),
                str(web.page().renderProcessPid() or "—"), lifecycle,
                "Yes" if web.page().isLoading() else "No",
                "Yes" if web.page().recentlyAudible() else "No",
                "Camera / mic" if web.media_audio_active or web.media_video_active else "No",
                "Open" if page.devtools_dock is not None else "No",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                self.table.setItem(row, column, item)
            if page is selected_page:
                self.table.selectRow(row)

    def _selected(self):
        row = self.table.currentRow()
        return self._rows[row][1] if 0 <= row < len(getattr(self, "_rows", [])) else None

    def _selected_pair(self):
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(getattr(self, "_rows", [])) else None

    def activate_selected(self) -> None:
        pair = self._selected_pair()
        if pair:
            window, page = pair
            page.browser.page().setLifecycleState(QWebEnginePage.LifecycleState.Active)
            window.tabs.setCurrentIndex(window.tabs.indexOf(page))
            window.raise_()
            window.activateWindow()
            self.refresh()

    def toggle_sleep(self) -> None:
        page = self._selected()
        if page:
            current = page.browser.page().lifecycleState()
            state = (
                QWebEnginePage.LifecycleState.Active
                if current != QWebEnginePage.LifecycleState.Active
                else QWebEnginePage.LifecycleState.Frozen
            )
            page.browser.page().setLifecycleState(state)
            self.refresh()

    def stop_selected(self) -> None:
        pair = self._selected_pair()
        if pair:
            window, page = pair
            page.browser.stop()
            try:
                page.browser.page().triggerAction(QWebEnginePage.WebAction.Stop)
                # Discarding is Qt/Chromium's supported way to release an
                # inactive renderer without closing the browser or tab. The
                # page reloads when the user activates it again.
                window._set_page_lifecycle(
                    page, QWebEnginePage.LifecycleState.Discarded
                )
            except RuntimeError:
                pass
            self.refresh()


class DiagnosticsDialog(_ToolDialog):
    def __init__(self, window) -> None:
        super().__init__(window, "Local Diagnostics", (760, 610))
        root = QVBoxLayout(self)
        title = QLabel("Local Diagnostics")
        title.setStyleSheet("font-size: 21px; font-weight: 650;")
        root.addWidget(title)
        self.report = DiagnosticsManager(window.profile_manager).report()
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(self.report)
        root.addWidget(viewer, 1)
        copy = QPushButton("Copy Diagnostic Report")
        copy.clicked.connect(self.copy_report)
        root.addWidget(copy, alignment=Qt.AlignmentFlag.AlignRight)

    def copy_report(self) -> None:
        QApplication.clipboard().setText(self.report)
        QMessageBox.information(self, "Copied", "The privacy-safe diagnostic report was copied.")


class UpdateDialog(_ToolDialog):
    def __init__(self, window) -> None:
        super().__init__(window, "Check for Updates", (540, 330))
        self.result = None
        root = QVBoxLayout(self)
        title = QLabel("Browser Updates")
        title.setStyleSheet("font-size: 21px; font-weight: 650;")
        root.addWidget(title)
        self.status = QLabel("Ready to check the configured HTTPS update manifest.")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.check = QPushButton("Check for Updates")
        self.download = QPushButton("Download & Verify Update")
        self.download.setEnabled(False)
        self.check.clicked.connect(self.check_now)
        self.download.clicked.connect(self.download_update)
        root.addWidget(self.check)
        root.addWidget(self.download)
        note = QLabel("Updates are never downloaded, installed, or restarted without your action.")
        note.setWordWrap(True)
        root.addWidget(note)
        root.addStretch()

    def check_now(self) -> None:
        try:
            self.result = UpdateChecker().check(
                str(self.browser_window.settings_manager.value("updates/manifest_url") or ""),
                str(self.browser_window.settings_manager.value("updates/ed25519_public_key") or ""),
            )
        except (ValueError, RuntimeError) as error:
            self.status.setText(str(error) + " Configure the URL in Settings → Developer.")
            self.download.setEnabled(False)
            return
        if self.result["available"]:
            self.status.setText(
                f"Version {self.result['version']} is available.\n{self.result['notes']}"
            )
            self.download.setEnabled(bool(self.result["download_url"]))
        else:
            self.status.setText("You are using the latest version listed by the configured source.")
            self.download.setEnabled(False)

    def download_update(self) -> None:
        if not self.result or not self.result.get("download_url"):
            return
        filename = Path(QUrl(self.result["download_url"]).path()).name or "python-browser-update.bin"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Verified Browser Update", filename, "All files (*)"
        )
        if not path:
            return
        try:
            verified = UpdateChecker.download_and_verify(self.result, path)
        except (OSError, ValueError, RuntimeError) as error:
            if getattr(self.browser_window, "security_data", None) is not None:
                self.browser_window.security_data.add_activity(
                    None, "update_verification_failed",
                    "Browser update verification failed", str(error),
                )
            QMessageBox.critical(self, "Update blocked", str(error))
            return
        QMessageBox.information(
            self, "Update verified",
            f"The Ed25519 signature and SHA-256 digest were verified.\n\nSaved to:\n{verified}\n\n"
            "The browser will not install or restart automatically."
        )
