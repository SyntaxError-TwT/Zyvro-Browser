"""Custom Chrome/Firefox-inspired browser popup menu."""

from __future__ import annotations

from qtpy.QtCore import Qt, QTimer, Signal
from qtpy.QtGui import QColor, QIcon, QLinearGradient, QPainter
from qtpy.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from theme_manager import theme_palette


class GradientSeparator(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(3)
        self.colors = ("#5b8def", "#5b8def", "#5b8def")

    def set_colors(self, colors: tuple[str, str, str]) -> None:
        self.colors = colors
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        gradient.setColorAt(0.0, QColor(self.colors[0]))
        gradient.setColorAt(0.5, QColor(self.colors[1]))
        gradient.setColorAt(1.0, QColor(self.colors[2]))
        painter.fillRect(self.rect(), gradient)


class MenuActionRow(QFrame):
    clicked = Signal()

    def __init__(self, icon: QIcon, text: str, shortcut: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("menuActionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(38)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        row.setSpacing(10)
        icon_label = QLabel()
        self.icon_label = icon_label
        icon_label.setFixedSize(24, 24)
        icon_label.setPixmap(icon.pixmap(21, 21))
        label = QLabel(text)
        label.setObjectName("actionLabel")
        shortcut_label = QLabel(shortcut)
        shortcut_label.setObjectName("shortcutLabel")
        row.addWidget(icon_label)
        row.addWidget(label)
        row.addStretch()
        row.addWidget(shortcut_label)

    def set_icon(self, icon: QIcon) -> None:
        self.icon_label.setPixmap(icon.pixmap(21, 21))

    def mouseReleaseEvent(self, event) -> None:
        self.setProperty("pressed", False)
        self.style().unpolish(self)
        self.style().polish(self)
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setProperty("pressed", True)
            self.style().unpolish(self)
            self.style().polish(self)
        super().mousePressEvent(event)


class ZoomRow(QFrame):
    zoom_in = Signal()
    zoom_out = Signal()

    def __init__(self, minus_icon: QIcon, plus_icon: QIcon, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("zoomRow")
        self.setFixedHeight(42)
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 4, 10, 4)
        label = QLabel("Zoom")
        self.minus = QPushButton()
        self.percent = QLabel("100%")
        self.plus = QPushButton()
        self.minus.setIcon(minus_icon)
        self.plus.setIcon(plus_icon)
        for button in (self.minus, self.plus):
            button.setObjectName("zoomButton")
            button.setFixedSize(30, 28)
        self.percent.setObjectName("zoomPercent")
        self.percent.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.percent.setFixedWidth(52)
        row.addWidget(label)
        row.addStretch()
        row.addWidget(self.minus)
        row.addWidget(self.percent)
        row.addWidget(self.plus)
        self.minus.clicked.connect(self.zoom_out)
        self.plus.clicked.connect(self.zoom_in)

    def set_percentage(self, percentage: int) -> None:
        self.percent.setText(f"{percentage}%")


class BrowserMenu(QWidget):
    """Custom popup panel with grouped actions and persistent zoom controls."""

    def __init__(self, callbacks: dict[str, callable], icons: dict[str, QIcon], parent=None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.callbacks = callbacks
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(324)

        wrapper = QVBoxLayout(self)
        wrapper.setContentsMargins(10, 10, 10, 10)
        panel = QFrame()
        panel.setObjectName("browserMenuPanel")
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 7)
        shadow.setColor(QColor(0, 0, 0, 120))
        panel.setGraphicsEffect(shadow)
        wrapper.addWidget(panel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(7, 8, 7, 8)
        layout.setSpacing(1)

        identity = QFrame()
        identity.setObjectName("identityRow")
        identity_layout = QHBoxLayout(identity)
        identity_layout.setContentsMargins(10, 8, 10, 8)
        profile = QLabel()
        self.profile_icon = profile
        profile.setPixmap(icons["profile"].pixmap(24, 24))
        identity_text = QVBoxLayout()
        title = QLabel("No Sign In Required")
        title.setObjectName("identityTitle")
        subtitle = QLabel("Browse without creating an account")
        subtitle.setObjectName("identitySubtitle")
        identity_text.addWidget(title)
        identity_text.addWidget(subtitle)
        identity_layout.addWidget(profile)
        identity_layout.addLayout(identity_text)
        layout.addWidget(identity)
        self.gradient_separator = GradientSeparator()
        layout.addWidget(self.gradient_separator)

        self._add_action(layout, icons["new_tab"], "New Tab", "Ctrl+T", "new_tab")
        self._add_action(layout, icons["new_window"], "New Window", "Ctrl+N", "new_window")
        self._add_action(
            layout,
            icons["private"],
            "New Incognito Window",
            "Ctrl+Shift+N",
            "new_incognito",
        )
        layout.addWidget(self._neutral_separator())

        self._add_action(layout, icons["history"], "History", "Ctrl+H", "history")
        self._add_action(
            layout, icons["recently_closed"], "Recently Closed",
            "Ctrl+Shift+T", "recently_closed",
        )
        self._add_action(layout, icons["download"], "Downloads", "Ctrl+J", "downloads")
        self._add_action(layout, icons["bookmark"], "Bookmarks", "", "bookmarks")
        layout.addWidget(self._neutral_separator())

        self._add_action(layout, icons["print"], "Print", "Ctrl+P", "print")
        self._add_action(layout, icons["save"], "Save Page As…", "Ctrl+S", "save")
        self._add_action(layout, icons["find"], "Find in Page", "Ctrl+F", "find")
        self._add_action(layout, icons["tools"], "Browser Tools", "", "tools")
        self.zoom_row = ZoomRow(icons["minus"], icons["plus"])
        self.zoom_row.zoom_out.connect(callbacks["zoom_out"])
        self.zoom_row.zoom_in.connect(callbacks["zoom_in"])
        layout.addWidget(self.zoom_row)
        layout.addWidget(self._neutral_separator())

        self._add_action(layout, icons["settings"], "Settings", "", "settings")
        self._add_action(layout, icons["nuke_data"], "Nuke Data", "", "nuke_data")
        self._add_action(layout, icons["exit"], "Exit", "", "exit")
        self.set_theme("dark")
        self.adjustSize()
        self._icons = icons

    def _add_action(
        self,
        layout: QVBoxLayout,
        icon: QIcon,
        label: str,
        shortcut: str,
        callback_name: str,
    ) -> None:
        row = MenuActionRow(icon, label, shortcut)
        if not hasattr(self, "action_rows"):
            self.action_rows = {}
        self.action_rows[callback_name] = row
        row.clicked.connect(
            lambda name=callback_name: self._run_normal_action(name)
        )
        layout.addWidget(row)

    @staticmethod
    def _neutral_separator() -> QFrame:
        separator = QFrame()
        separator.setObjectName("neutralSeparator")
        separator.setFixedHeight(1)
        return separator

    def _run_normal_action(self, name: str) -> None:
        self.close()
        QTimer.singleShot(0, self.callbacks[name])

    def update_zoom(self, factor: float) -> None:
        self.zoom_row.set_percentage(round(factor * 100))

    def set_icons(self, icons: dict[str, QIcon]) -> None:
        self._icons = icons
        self.profile_icon.setPixmap(icons["profile"].pixmap(24, 24))
        mapping = {
            "new_tab": "new_tab", "new_window": "new_window",
            "new_incognito": "private", "history": "history",
            "recently_closed": "recently_closed",
            "downloads": "download", "bookmarks": "bookmark",
            "print": "print", "save": "save", "find": "find",
            "tools": "tools",
            "settings": "settings", "exit": "exit",
            "nuke_data": "nuke_data",
        }
        for action_name, icon_name in mapping.items():
            self.action_rows[action_name].set_icon(icons[icon_name])
        self.zoom_row.minus.setIcon(icons["minus"])
        self.zoom_row.plus.setIcon(icons["plus"])

    def set_theme(self, theme: str, incognito: bool = False) -> None:
        palette = theme_palette(theme, incognito)
        panel = palette.panel
        text = palette.text
        muted = palette.muted
        hover = palette.control_hover
        separator = palette.border
        control = palette.control
        self.gradient_separator.set_colors(
            (palette.accent_start, palette.accent_mid, palette.accent_end)
        )
        self.setStyleSheet(
            f"""
            QFrame#browserMenuPanel {{ background: {panel}; border-radius: 13px; }}
            QFrame#identityRow {{ background: transparent; }}
            QLabel {{ color: {text}; background: transparent; border: none; }}
            QLabel#identityTitle {{ font-weight: 600; font-size: 13px; }}
            QLabel#identitySubtitle, QLabel#shortcutLabel {{ color: {muted}; font-size: 11px; }}
            QFrame#menuActionRow {{ background: transparent; border-radius: 8px; }}
            QFrame#menuActionRow:hover {{ background: {hover}; }}
            QFrame#menuActionRow[pressed="true"] {{ background: {control}; }}
            QFrame#neutralSeparator {{ background: {separator}; margin: 5px 8px; }}
            QFrame#zoomRow {{ background: transparent; }}
            QPushButton#zoomButton {{ background: {control}; color: {text}; border: none;
                border-radius: 7px; font-size: 16px; }}
            QPushButton#zoomButton:hover {{ background: {hover}; }}
            QLabel#zoomPercent {{ color: {text}; }}
            """
        )
