"""Black / charcoal / crimson / restrained-gold palette and the control-panel stylesheet."""

from __future__ import annotations

from PySide6.QtGui import QColor, QFont

BG = "#0a0a0d"
PANEL = "#131318"
PANEL_2 = "#1a1a20"
BORDER = "#2a2a32"
TEXT = "#ebe8e2"
MUTED = "#8e8b86"
CRIMSON = "#c8102e"
CRIMSON_BRIGHT = "#e8344f"
GOLD = "#c9a24b"
GOLD_DIM = "#8f7433"
OK = "#c9a24b"          # healthy state uses the gold accent (no green: keeps the palette tight)
WARN = "#e8344f"

FONT_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Inter", "Helvetica Neue", "Arial", "DejaVu Sans"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Courier New"]


def color(hex_code: str, alpha: int = 255) -> QColor:
    c = QColor(hex_code)
    c.setAlpha(alpha)
    return c


def make_font(pixel_size: int, *, weight: QFont.Weight = QFont.Weight.Normal,
              spacing: float = 0.0, mono: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(MONO_FAMILIES if mono else FONT_FAMILIES)
    font.setPixelSize(max(6, int(pixel_size)))
    font.setWeight(weight)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


STYLESHEET = f"""
* {{ color: {TEXT}; font-size: 13px; }}
QWidget {{ background: {BG}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="title"] {{ font-size: 22px; font-weight: 300; letter-spacing: 1px; }}
QLabel[role="section"] {{ color: {GOLD}; font-size: 11px; font-weight: 600; letter-spacing: 2px; }}
QLabel[role="big"] {{ font-size: 30px; font-weight: 300; }}
QLabel[role="banner"] {{ background: {PANEL_2}; border: 1px solid {GOLD_DIM}; border-left: 3px solid {CRIMSON};
    padding: 10px 12px; border-radius: 4px; }}
QLabel[state="warn"] {{ color: {WARN}; }}
QLabel[state="ok"] {{ color: {GOLD}; }}
QLabel[state="off"] {{ color: {MUTED}; }}

QFrame[card="true"] {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 8px; }}
QFrame[card="true"] QLabel {{ background: transparent; }}

QListWidget#nav {{ background: {PANEL}; border: none; border-right: 1px solid {BORDER}; outline: none; padding: 6px; }}
QListWidget#nav::item {{ padding: 11px 14px; margin: 2px 0; border-radius: 6px; color: {MUTED}; letter-spacing: 1px; }}
QListWidget#nav::item:selected {{ background: {PANEL_2}; color: {TEXT}; border-left: 3px solid {CRIMSON}; }}
QListWidget#nav::item:hover:!selected {{ background: {PANEL_2}; color: {TEXT}; }}

QPushButton {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {GOLD_DIM}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {PANEL_2}; }}
QPushButton[primary="true"] {{ background: {CRIMSON}; border-color: {CRIMSON}; color: white; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {CRIMSON_BRIGHT}; }}
QPushButton[danger="true"] {{ border-color: {CRIMSON}; }}

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 5px; padding: 5px 8px;
    selection-background-color: {CRIMSON}; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus {{ border-color: {GOLD_DIM}; }}
QComboBox QAbstractItemView {{ background: {PANEL_2}; selection-background-color: {CRIMSON}; border: 1px solid {BORDER}; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {GOLD_DIM}; border-radius: 3px; background: {PANEL_2}; }}
QCheckBox::indicator:checked {{ background: {CRIMSON}; border-color: {CRIMSON}; }}

QTableWidget, QListWidget, QTreeWidget {{ background: {PANEL}; alternate-background-color: #16161c;
    border: 1px solid {BORDER}; border-radius: 6px; gridline-color: transparent; outline: none; }}
QTableWidget::item, QListWidget::item {{ padding: 4px 6px; }}
QTableWidget::item:selected, QListWidget::item:selected {{ background: #3a1620; color: {TEXT}; }}
QHeaderView::section {{ background: {PANEL_2}; color: {GOLD}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px 8px; font-size: 11px; font-weight: 600; letter-spacing: 1px; }}
QTableCornerButton::section {{ background: {PANEL_2}; border: none; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {GOLD_DIM}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER}; spacing: 4px; padding: 4px 8px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 6px 10px; }}
QToolButton:hover {{ border-color: {GOLD_DIM}; background: {PANEL_2}; }}
QToolButton:checked {{ background: #3a1620; border-color: {CRIMSON}; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; }}
QMenuBar {{ background: {PANEL}; }}
QMenuBar::item:selected {{ background: {PANEL_2}; }}
QMenu {{ background: {PANEL_2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: #3a1620; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
QProgressBar {{ background: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 4px; text-align: center; height: 14px; }}
QProgressBar::chunk {{ background: {CRIMSON}; border-radius: 3px; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding: 14px 12px 10px 12px; background: {PANEL}; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {GOLD}; letter-spacing: 2px; font-size: 11px; }}
QToolTip {{ background: {PANEL_2}; color: {TEXT}; border: 1px solid {GOLD_DIM}; padding: 4px; }}
QScrollArea {{ border: none; background: transparent; }}
QSplitter::handle {{ background: {BORDER}; }}
"""
