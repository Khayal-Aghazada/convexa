"""Colors, fonts and the stylesheet. Follows the Windows light/dark setting."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication


@dataclass(frozen=True)
class Palette:
    dark: bool
    bg: str
    surface: str
    surface_hover: str
    border: str
    text: str
    muted: str
    faint: str
    accent: str
    accent_text: str
    danger: str
    success: str
    shadow: int  # alpha


LIGHT = Palette(False, bg="#FFFFFF", surface="#F5F5F7", surface_hover="#EDEDF1", border="#E7E7EC",
                text="#16161A", muted="#6E6E76", faint="#A1A1AA", accent="#6366F1", accent_text="#FFFFFF",
                danger="#E5484D", success="#16A34A", shadow=55)
DARK = Palette(True, bg="#1B1B1F", surface="#26262B", surface_hover="#2F2F35", border="#323238",
               text="#F2F2F4", muted="#A0A0A8", faint="#6B6B73", accent="#8B8DFF", accent_text="#0E0E12",
               danger="#FF6369", success="#3DD68C", shadow=140)

CATEGORY_COLORS = {"document": "#3B82F6", "spreadsheet": "#10B981", "presentation": "#F97316",
                   "image": "#EC4899", "pdf": "#EF4444", "other": "#8B8B93"}

ICON_FONT_CANDIDATES = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
ICONS = {"upload": "", "close": "", "add": "", "folder": "", "check": "",
         "error": "", "settings": "", "chevron": "", "delete": "", "open": ""}


def palette() -> Palette:
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        return DARK if scheme == Qt.ColorScheme.Dark else LIGHT
    except Exception:
        return LIGHT


def ui_font_family() -> str:
    fams = set(QFontDatabase.families())
    for f in ("Segoe UI Variable Text", "Segoe UI Variable Display", "Segoe UI", "Inter"):
        if f in fams:
            return f
    return QFont().family()


def icon_font(size: int = 12) -> QFont:
    fams = set(QFontDatabase.families())
    for f in ICON_FONT_CANDIDATES:
        if f in fams:
            font = QFont(f)
            font.setPixelSize(size)
            return font
    font = QFont()
    font.setPixelSize(size)
    return font


def has_icon_font() -> bool:
    fams = set(QFontDatabase.families())
    return any(f in fams for f in ICON_FONT_CANDIDATES)


def glyph(name: str) -> str:
    if has_icon_font():
        return ICONS[name]
    return {"upload": "↓", "close": "×", "add": "+", "folder": "▸", "check": "✓", "error": "!",
            "settings": "⚙", "chevron": "▾", "delete": "×", "open": "↗"}[name]


def rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{int(alpha * 255)})"


def stylesheet(p: Palette) -> str:
    return f"""
* {{ color: {p.text}; outline: none; }}
QWidget#card {{ background: {p.bg}; border: 1px solid {p.border}; border-radius: 16px; }}
QLabel {{ background: transparent; }}
QLabel#title {{ font-size: 14px; font-weight: 600; }}
QLabel#muted, QLabel#sub {{ color: {p.muted}; font-size: 12px; }}
QLabel#section {{ color: {p.faint}; font-size: 11px; font-weight: 600; letter-spacing: 0.6px; }}
QLabel#status {{ color: {p.muted}; font-size: 12px; }}
QLabel#status[state="ok"] {{ color: {p.success}; }}
QLabel#status[state="error"] {{ color: {p.danger}; }}
QLabel#dropTitle {{ font-size: 14px; font-weight: 600; }}

QPushButton#icon {{ background: transparent; border: none; border-radius: 8px; color: {p.muted};
                    min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; }}
QPushButton#icon:hover {{ background: {p.surface}; color: {p.text}; }}

QPushButton#link {{ background: transparent; border: none; color: {p.muted}; font-size: 12px;
                    padding: 4px 8px; border-radius: 6px; }}
QPushButton#link:hover {{ color: {p.text}; background: {p.surface}; }}
QPushButton#link:disabled {{ color: {p.faint}; }}

QPushButton#primary {{ background: {p.accent}; color: {p.accent_text}; border: none; border-radius: 8px;
                       padding: 6px 12px; font-size: 12px; font-weight: 600; }}
QPushButton#primary:hover {{ background: {rgba(p.accent, 0.88)}; }}

QFrame#drop {{ background: {p.surface}; border: 1.5px dashed {p.border}; border-radius: 12px; }}
QFrame#drop[hover="true"] {{ background: {rgba(p.accent, 0.08)}; border-color: {p.accent}; }}

QFrame#fileRow {{ background: transparent; border-radius: 10px; }}
QFrame#fileRow:hover {{ background: {p.surface}; }}

QPushButton#chip {{ background: {p.surface}; border: 1px solid transparent; border-radius: 10px;
                    text-align: left; padding: 0px; }}
QPushButton#chip:hover {{ border-color: {p.accent}; background: {rgba(p.accent, 0.07)}; }}
QPushButton#chip:pressed {{ background: {rgba(p.accent, 0.14)}; }}
QPushButton#chip:disabled {{ background: {p.surface}; border-color: transparent; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 6px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border}; border-radius: 3px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {p.bg}; color: {p.text}; border: 1px solid {p.border}; padding: 6px 8px;
            border-radius: 6px; font-size: 12px; }}

QMenu {{ background: {p.bg}; border: 1px solid {p.border}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 7px 28px 7px 30px; border-radius: 6px; font-size: 12px; color: {p.text}; }}
QMenu::item:selected {{ background: {p.surface}; }}
QMenu::item:disabled {{ color: {p.faint}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 5px 8px; }}
QMenu::indicator {{ width: 14px; height: 14px; left: 8px; }}
QMenu::indicator:checked {{ image: none; background: {p.accent}; border-radius: 4px; }}
QMenu::indicator:unchecked {{ image: none; background: transparent; border: 1px solid {p.border};
                              border-radius: 4px; }}

QKeySequenceEdit, QLineEdit {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px;
                               padding: 6px 10px; font-size: 13px; }}
"""
