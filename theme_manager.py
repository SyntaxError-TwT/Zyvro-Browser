"""Centralized browser-chrome themes and live preview coordination."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from qtpy.QtCore import QObject, Signal
from qtpy.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from qtpy.QtWidgets import QApplication


CHECKMARK_IMAGE = (Path(__file__).resolve().parent / "assets" / "checkmark.svg").as_posix()


@dataclass(frozen=True)
class ThemePalette:
    window: str
    toolbar: str
    pane: str
    tab_bar: str
    tab: str
    tab_hover: str
    tab_selected: str
    address: str
    address_focus: str
    panel: str
    control: str
    control_hover: str
    text: str
    muted: str
    border: str
    accent: str
    selection: str
    tooltip: str
    accent_start: str
    accent_mid: str
    accent_end: str
    gradient_tabs: bool = False


LIGHT = ThemePalette(
    window="#f3f5f8", toolbar="#ffffff", pane="#ffffff", tab_bar="#eef1f5",
    tab="transparent", tab_hover="#e2e6ec", tab_selected="#ffffff",
    address="#f5f7fa", address_focus="#ffffff", panel="#ffffff",
    control="#edf0f5", control_hover="#dfe4eb", text="#202124",
    muted="#68707d", border="#d5dbe4", accent="#4f7fd9", selection="#3975d1",
    tooltip="#ffffff", accent_start="#4f7fd9", accent_mid="#4f7fd9",
    accent_end="#4f7fd9",
)

DARK = ThemePalette(
    window="#171a21", toolbar="#20242d", pane="#171a21", tab_bar="#1c1b22",
    tab="transparent", tab_hover="#34333b", tab_selected="#42414d",
    address="#12151b", address_focus="#171b22", panel="#252a33",
    control="#343b47", control_hover="#444c5a", text="#edf1f7",
    muted="#aab3c0", border="#3a414e", accent="#5b8def", selection="#3975d1",
    tooltip="#303642", accent_start="#5b8def", accent_mid="#5b8def",
    accent_end="#5b8def",
)

NEON = replace(
    DARK,
    window="#121821", toolbar="#18212c", pane="#121821", tab_bar="#111720",
    tab_hover="#22303d", tab_selected="#24313d", address="#0f151d",
    address_focus="#131d27", panel="#1b2530", control="#253340",
    control_hover="#304250", border="#344654", accent="#55d6d0",
    selection="#745ed9", tooltip="#1d2934", accent_start="#55d6d0",
    accent_mid="#7767dc", accent_end="#9f6ad8",
)

RAINBOW = replace(
    DARK,
    window="#191921", toolbar="#22232d", pane="#191921", tab_bar="#1c1c25",
    tab_hover="#30313d", tab_selected="#363744", address="#14151c",
    address_focus="#1a1b24", panel="#282934", control="#373846",
    control_hover="#454759", border="#414351", accent="#6f8cff",
    selection="#7f65d9", tooltip="#30313c", accent_start="#9567e8",
    accent_mid="#5298ef", accent_end="#e06fb5", gradient_tabs=True,
)

PRIVATE_BASE = replace(
    DARK,
    window="#14121a", toolbar="#1d1925", pane="#14121a", tab_bar="#15121b",
    tab_hover="#382f43", tab_selected="#443650", address="#110f16",
    address_focus="#18131f", panel="#241f2c", control="#342d3e",
    control_hover="#453a50", text="#f1ebf7", muted="#c4b8ce", border="#44394f",
    tooltip="#2d2635",
)


def normalized_theme(theme: str) -> str:
    value = str(theme).strip().lower()
    return value if value in {"system", "light", "dark", "neon", "rainbow"} else "system"


def system_theme() -> str:
    app = QApplication.instance()
    if app is not None:
        hints = app.styleHints()
        scheme = getattr(hints, "colorScheme", lambda: None)()
        if scheme is not None and getattr(scheme, "name", "") in {"Light", "Dark"}:
            return scheme.name.lower()
        if app.palette().color(QPalette.ColorRole.Window).lightness() < 128:
            return "dark"
    return "light"


def theme_palette(theme: str, incognito: bool = False) -> ThemePalette:
    theme = normalized_theme(theme)
    resolved = system_theme() if theme == "system" else theme
    source = {"light": LIGHT, "dark": DARK, "neon": NEON, "rainbow": RAINBOW}[resolved]
    if not incognito:
        return source
    # Incognito always keeps the private dark base while inheriting the chosen accent.
    return replace(
        PRIVATE_BASE,
        accent=source.accent,
        selection=source.selection,
        accent_start=source.accent_start,
        accent_mid=source.accent_mid,
        accent_end=source.accent_end,
        gradient_tabs=source.gradient_tabs,
    )


def browser_stylesheet(theme: str, incognito: bool = False) -> str:
    p = theme_palette(theme, incognito)
    selected_background = (
        f"qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 {p.tab_selected},"
        f"stop:.45 #30394a,stop:1 {p.tab_selected})"
        if p.gradient_tabs else p.tab_selected
    )
    glow = f"border-bottom: 2px solid {p.accent};" if normalized_theme(theme) in {"neon", "rainbow"} else ""
    return f"""
QMainWindow {{ background: {p.window}; color: {p.text}; }}
QToolBar#navigationBar, QToolBar#bookmarksBar {{
    background: {p.toolbar}; border: none; border-bottom: 1px solid {p.border};
    spacing: 6px; padding: 3px 8px;
}}
QToolBar QToolButton {{ background: transparent; border: none; border-radius: 8px; color: {p.text}; padding: 4px; }}
QToolBar QToolButton:hover {{ background: {p.control}; }}
QToolBar QToolButton:pressed {{ background: {p.control_hover}; }}
QToolButton#downloadButton[active="true"] {{ background: {p.control}; border-bottom: 2px solid {p.accent}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; background: {p.address};
    border: 1px solid {p.border}; border-radius: 4px; }}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent};
    image: url("{CHECKMARK_IMAGE}"); }}
QLineEdit {{ min-height: 28px; background: {p.address}; color: {p.text};
    border: 1px solid {p.border}; border-radius: 9px; padding: 0 10px;
    selection-background-color: {p.selection}; }}
QLineEdit:hover {{ border-color: {p.muted}; }}
QLineEdit:focus {{ background: {p.address_focus}; border: 1px solid {p.accent}; }}
QTabWidget::pane {{ border: none; background: {p.pane}; }}
QTabWidget::tab-bar {{ left: 4px; }}
QTabBar {{ background: {p.tab_bar}; qproperty-drawBase: 0; }}
QTabBar::tab {{ min-width: 150px; max-width: 230px; min-height: 29px;
    background: {p.tab}; color: {p.muted}; border: none; border-radius: 8px;
    margin: 4px 2px; padding: 1px 10px; }}
QTabBar::tab:hover {{ background: {p.tab_hover}; color: {p.text}; }}
QTabBar::tab:selected {{ background: {selected_background}; color: {p.text}; {glow} }}
QToolButton#newTabButton, QToolButton#tabCloseButton {{ background: transparent; border: none; border-radius: 7px; }}
QToolButton#newTabButton:hover, QToolButton#tabCloseButton:hover {{ background: {p.control}; }}
QToolTip {{ background: {p.tooltip}; color: {p.text}; border: 1px solid {p.border}; padding: 5px; }}
QWidget#privateIndicator {{ background: #443654; border: 1px solid #665176; border-radius: 9px; }}
QLabel#privateIndicatorText {{ color: #eee5ff; font-weight: 600; font-size: 12px; }}
QFrame#securityInterstitial {{ background: {p.window}; }}
QFrame#securityInterstitialCard {{ background: {p.panel}; border: 1px solid #713b43;
    border-radius: 14px; }}
QLabel#securityInterstitialHeading {{ color: {p.text}; font-size: 25px; font-weight: 700; }}
QLabel#securityInterstitialMessage {{ color: {p.text}; font-size: 15px; }}
QLabel#securityInterstitialDetail {{ color: {p.muted}; }}
QFrame#securityInterstitial QPushButton {{ background: {p.control}; color: {p.text};
    border: 1px solid {p.border}; border-radius: 8px; padding: 8px 13px; }}
QFrame#securityInterstitial QPushButton:hover {{ background: {p.control_hover}; }}
QPushButton#securityProceedButton {{ background: #7f3039; color: white; border-color: #a44751; }}
"""


def dialog_stylesheet(theme: str, incognito: bool = False) -> str:
    p = theme_palette(theme, incognito)
    return f"""
QDialog {{ background: {p.window}; color: {p.text}; }}
QWidget {{ color: {p.text}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea#privacyScroll, QScrollArea#privacyScroll > QWidget > QWidget,
QWidget#privacySettingsContent {{ background: {p.window}; }}
QGroupBox {{ border: 1px solid {p.border}; border-radius: 9px;
    margin-top: 12px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ color: {p.text}; subcontrol-origin: margin;
    left: 10px; padding: 0 5px; }}
QLabel {{ color: {p.text}; }}
QLineEdit, QComboBox, QSpinBox {{ background: {p.address}; color: {p.text};
    border: 1px solid {p.border}; border-radius: 7px; padding: 6px 8px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {p.accent}; }}
QComboBox QAbstractItemView {{ background: {p.panel}; color: {p.text}; selection-background-color: {p.selection}; }}
QPushButton {{ background: {p.control}; color: {p.text}; border: 1px solid {p.border};
    border-radius: 7px; padding: 7px 12px; }}
QPushButton:hover {{ background: {p.control_hover}; border-color: {p.accent}; }}
QTableWidget, QListWidget {{ background: {p.pane}; alternate-background-color: {p.toolbar};
    color: {p.text}; gridline-color: {p.border}; border: 1px solid {p.border}; }}
QHeaderView::section {{ background: {p.control}; color: {p.text}; border: none; padding: 7px; }}
QTabWidget::pane {{ border: 1px solid {p.border}; }}
QTabBar::tab {{ min-width: 90px; background: {p.control}; color: {p.muted};
    border: none; padding: 7px 10px; }}
QTabBar::tab:selected {{ background: {p.panel}; color: {p.text}; border-bottom: 2px solid {p.accent}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; background: {p.address};
    border: 1px solid {p.border}; border-radius: 4px; }}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent};
    image: url("{CHECKMARK_IMAGE}"); }}
QToolTip {{ background: {p.tooltip}; color: {p.text}; border: 1px solid {p.border}; }}
"""


def find_bar_stylesheet(theme: str, incognito: bool = False) -> str:
    p = theme_palette(theme, incognito)
    return f"""
QFrame#findPanel {{ background: {p.panel}; border: 1px solid {p.border}; border-radius: 8px; }}
QLineEdit {{ background: {p.address}; color: {p.text}; border: none; padding: 5px 7px; }}
QPushButton {{ background: transparent; color: {p.text}; border: none; border-radius: 5px; padding: 4px 7px; }}
QPushButton:hover {{ background: {p.control_hover}; }}
QLabel {{ color: {p.muted}; }}
"""


def popup_stylesheet(theme: str, incognito: bool = False) -> str:
    p = theme_palette(theme, incognito)
    return f"""
QDialog {{ background: transparent; color: #f1f3f7; border: none; }}
QLabel, QCheckBox {{ color: #f1f3f7; }}
QCheckBox::indicator {{ width: 18px; height: 18px; background: #15181e;
    border: 1px solid #5a6270; border-radius: 4px; }}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent};
    image: url("{CHECKMARK_IMAGE}"); }}
QLabel#privacyPopupTitle {{ font-size: 17px; font-weight: 650; }}
QComboBox {{ background: #15181e; color: #f1f3f7; border: 1px solid #3c4350;
    border-radius: 7px; padding: 6px 8px; }}
QComboBox QAbstractItemView {{ background: #22242a; color: #f1f3f7;
    selection-background-color: {p.selection}; }}
QPushButton {{ background: {p.accent}; color: white; border: none; border-radius: 8px; padding: 8px 12px; }}
QPushButton:hover {{ background: {p.selection}; }}
"""


class ThemeManager(QObject):
    """Coordinates persisted selection, temporary preview, and OS changes."""

    theme_changed = Signal(str)

    def __init__(self, initial_theme: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._committed = normalized_theme(initial_theme)
        self._preview: str | None = None
        app = QApplication.instance()
        if app is not None:
            if hasattr(app.styleHints(), "colorSchemeChanged"):
                app.styleHints().colorSchemeChanged.connect(self._system_changed)
            app.paletteChanged.connect(self._system_changed)

    def active_theme(self) -> str:
        return self._preview or self._committed

    def preview(self, theme: str) -> None:
        self._preview = normalized_theme(theme)
        self.theme_changed.emit(self.active_theme())

    def commit(self, theme: str) -> None:
        self._committed = normalized_theme(theme)
        self._preview = None
        self.theme_changed.emit(self.active_theme())

    def cancel_preview(self) -> None:
        if self._preview is not None:
            self._preview = None
            self.theme_changed.emit(self.active_theme())

    def _system_changed(self, *_args) -> None:
        if self.active_theme() == "system":
            self.theme_changed.emit("system")

    def browser_stylesheet(self, incognito: bool = False) -> str:
        return browser_stylesheet(self.active_theme(), incognito)

    def dialog_stylesheet(self, incognito: bool = False) -> str:
        return dialog_stylesheet(self.active_theme(), incognito)

    def find_stylesheet(self, incognito: bool = False) -> str:
        return find_bar_stylesheet(self.active_theme(), incognito)

    def popup_stylesheet(self, incognito: bool = False) -> str:
        return popup_stylesheet(self.active_theme(), incognito)

    def palette(self, incognito: bool = False) -> ThemePalette:
        return theme_palette(self.active_theme(), incognito)

    def icon(self, path: str, incognito: bool = False, color: str | None = None) -> QIcon:
        """Tint monochrome SVG assets for reliable contrast in every theme."""
        palette = self.palette(incognito)
        # The bundled SVGs already have suitable light strokes for every
        # dark-based theme. Returning the SVG QIcon preserves vector scaling on
        # high-DPI Windows displays instead of shrinking it to one 24px bitmap.
        if palette.text != LIGHT.text:
            return QIcon(path)

        # Light mode needs dark strokes. Supply several raster sizes so Qt can
        # choose a sharp high-DPI representation instead of scaling one pixmap.
        source_icon = QIcon(path)
        result = QIcon()
        tint = QColor(color or palette.text)
        for edge in (16, 20, 24, 32, 40, 48, 64):
            source = source_icon.pixmap(edge, edge)
            tinted = QPixmap(source.size())
            tinted.fill(QColor(0, 0, 0, 0))
            painter = QPainter(tinted)
            painter.drawPixmap(0, 0, source)
            painter.setCompositionMode(
                QPainter.CompositionMode.CompositionMode_SourceIn
            )
            painter.fillRect(tinted.rect(), tint)
            painter.end()
            result.addPixmap(tinted)
        return result
