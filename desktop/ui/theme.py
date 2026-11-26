"""Dark CAD theme, QSS stylesheet, translucent node fills, and Port/Component color palette."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget

from shared.models import ComponentType, PortType

BG_WINDOW = "#0B1120"
BG_SURFACE = "#0F172A"
BG_ELEVATED = "#1E293B"
BG_INPUT = "#0F172A"
BORDER_COLOR = "#334155"
BORDER_FOCUS = "#38BDF8"
TEXT_PRIMARY = "#F8FAFC"
TEXT_SECONDARY = "#CBD5E1"
TEXT_MUTED = "#94A3B8"
ACCENT_PRIMARY = "#38BDF8"
ACCENT_SUCCESS = "#22C55E"
ACCENT_WARNING = "#F59E0B"
ACCENT_DANGER = "#EF4444"

NODE_FILL_RGB = (30, 41, 59)
DEFAULT_NODE_ALPHA = 165  # ~65% opacity

COMPONENT_TYPE_COLORS: dict[ComponentType, str] = {
    ComponentType.PASSIVE_SWITCH: "#64748B",
    ComponentType.ACTIVE_SWITCH: "#38BDF8",
    ComponentType.ACCESS_POINT: "#22C55E",
    ComponentType.FIREWALL: "#EF4444",
    ComponentType.ROUTER: "#F59E0B",
    ComponentType.LWL_CONVERTER: "#FACC15",
    ComponentType.POWERLAN_ADAPTER: "#10B981",
    ComponentType.PATCH_PANEL: "#94A3B8",
    ComponentType.DEVICE: "#A855F7",
}

PORT_TYPE_COLORS: dict[PortType, str] = {
    PortType.BASE100: "#60A5FA",
    PortType.BASE1000: "#38BDF8",
    PortType.BASE2500: "#06B6D4",
    PortType.BASE10G: "#06B6D4",
    PortType.LWL_SM: "#FACC15",
    PortType.LWL_MM: "#FB923C",
    PortType.SFP: "#A855F7",
    PortType.SFP_PLUS: "#A855F7",
    PortType.POWERLAN: "#10B981",
    PortType.COAX: "#94A3B8",
    PortType.CUSTOM: "#EC4899",
}


def node_fill_color(alpha: int = DEFAULT_NODE_ALPHA) -> QColor:
    """Return translucent QColor(30, 41, 59, alpha) for ComponentNodeItem fill."""
    clamped = max(77, min(242, int(alpha)))
    return QColor(NODE_FILL_RGB[0], NODE_FILL_RGB[1], NODE_FILL_RGB[2], clamped)


def component_color(component_type: ComponentType, override_hex: str | None = None) -> QColor:
    """Return accent QColor for a ComponentType (with optional hex override)."""
    if override_hex:
        color = QColor(override_hex)
        if color.isValid():
            return color
    return QColor(COMPONENT_TYPE_COLORS.get(component_type, "#38BDF8"))


def port_color(port_type: PortType) -> QColor:
    """Return Section 4.4C color-coded QColor for a PortType."""
    return QColor(PORT_TYPE_COLORS.get(port_type, "#38BDF8"))


DARK_CAD_QSS = f"""
QMainWindow, QDialog {{
    background-color: {BG_WINDOW};
    color: {TEXT_PRIMARY};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}
QWidget {{
    color: {TEXT_PRIMARY};
}}
QMenuBar {{
    background-color: {BG_SURFACE};
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid {BORDER_COLOR};
}}
QMenuBar::item:selected {{
    background-color: {BG_ELEVATED};
}}
QMenu {{
    background-color: {BG_SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_COLOR};
}}
QMenu::item:selected {{
    background-color: #1D4ED8;
}}
QToolBar {{
    background-color: {BG_SURFACE};
    border-bottom: 1px solid {BORDER_COLOR};
    spacing: 6px;
    padding: 4px 6px;
}}
QTabWidget::pane {{
    border: 1px solid {BORDER_COLOR};
    background-color: {BG_WINDOW};
}}
QTabBar::tab {{
    background-color: {BG_SURFACE};
    color: {TEXT_MUTED};
    padding: 7px 14px;
    border: 1px solid {BORDER_COLOR};
    border-bottom: none;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border-top: 2px solid {ACCENT_PRIMARY};
}}
QDockWidget {{
    color: {TEXT_PRIMARY};
    titlebar-close-icon: none;
}}
QDockWidget::title {{
    background-color: {BG_SURFACE};
    padding: 6px;
    border-bottom: 1px solid {BORDER_COLOR};
}}
QGroupBox {{
    border: 1px solid {BORDER_COLOR};
    border-radius: 6px;
    margin-top: 10px;
    padding-top: 12px;
    font-weight: 600;
    color: {TEXT_SECONDARY};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
QPushButton {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_COLOR};
    border-radius: 5px;
    padding: 5px 12px;
}}
QPushButton:hover {{
    border-color: {ACCENT_PRIMARY};
    background-color: #273449;
}}
QPushButton:pressed {{
    background-color: #172033;
}}
QPushButton:checked {{
    background-color: #0284C7;
    border-color: {ACCENT_PRIMARY};
    color: #FFFFFF;
}}
QPushButton:disabled {{
    color: #64748B;
    background-color: #131C2E;
}}
QPushButton[primary="true"] {{
    background-color: #0284C7;
    border-color: {ACCENT_PRIMARY};
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background-color: #0369A1;
}}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {BG_INPUT};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_COLOR};
    border-radius: 4px;
    padding: 4px 8px;
    selection-background-color: #0284C7;
}}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {BORDER_FOCUS};
}}
QComboBox QAbstractItemView {{
    background-color: {BG_SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_COLOR};
    selection-background-color: #0284C7;
}}
QListWidget, QTreeWidget, QTableWidget {{
    background-color: {BG_SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_COLOR};
    gridline-color: {BORDER_COLOR};
    alternate-background-color: #131C2E;
}}
QHeaderView::section {{
    background-color: {BG_ELEVATED};
    color: {TEXT_SECONDARY};
    border: none;
    border-right: 1px solid {BORDER_COLOR};
    border-bottom: 1px solid {BORDER_COLOR};
    padding: 5px 8px;
    font-weight: 600;
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background-color: {BG_WINDOW};
    border: none;
    width: 10px;
    height: 10px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background-color: #334155;
    border-radius: 4px;
    min-height: 20px;
    min-width: 20px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0px;
    height: 0px;
}}
QStatusBar {{
    background-color: {BG_SURFACE};
    color: {TEXT_SECONDARY};
    border-top: 1px solid {BORDER_COLOR};
}}
QToolTip {{
    background-color: {BG_ELEVATED};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_COLOR};
    padding: 4px 6px;
}}
"""


def apply_dark_theme(target: QApplication | QWidget) -> None:
    """Apply the Dark CAD QSS stylesheet to a QApplication or root QWidget."""
    target.setStyleSheet(DARK_CAD_QSS)
