from __future__ import annotations

from .design import COLORS, CONTROL_HEIGHT, RADII, SPACE, TYPE


def build_style(ui_family: str = "Segoe UI", mono_family: str = "Consolas") -> str:
    c = COLORS
    return f"""
QMainWindow, QDialog, QWidget {{
    background: {c["bg"]}; color: {c["text"]};
    font-family: "{ui_family}"; font-size: {TYPE["body"][0]}px;
}}
QDialog {{ border-radius: {RADII["md"]}px; }}
QToolBar {{
    background: {c["surface"]}; border: none;
    border-bottom: 1px solid {c["border"]};
    spacing: {SPACE["sm"]}px; padding: {SPACE["sm"]}px {SPACE["md"]}px;
}}
QToolButton, QPushButton {{
    background: {c["surface_raised"]}; border: 1px solid {c["border"]};
    border-radius: {RADII["sm"]}px; color: {c["text"]};
    padding: {SPACE["sm"]}px {SPACE["md"]}px;
}}
QToolButton:hover, QPushButton:hover {{ border-color: {c["border_strong"]}; }}
QToolButton:disabled, QPushButton:disabled {{
    background: {c["surface"]}; color: {c["text_dim"]};
}}
QPushButton#primaryButton {{ background: {c["accent"]}; color: {c["accent_text"]}; }}
QPushButton#dangerButton {{ background: {c["danger"]}; color: #FFFFFF; }}
QPushButton#popupFieldLabel {{ background: transparent; border: none; padding: 0; }}
QPushButton#popupFieldLabel:hover {{ color: {c["accent_hover"]}; }}
QToolButton#expandTextButton {{ padding: 2px; }}
QToolButton#removeManagedTaskButton {{ background: {c["danger"]}; color: #FFFFFF; }}
QPushButton#addManagedTaskButton {{ background: #1E3A5F; color: #DBEAFE; }}
QLabel#sectionTitle {{
    color: {c["text"]}; font-size: {TYPE["heading"][0]}px;
    font-weight: {TYPE["heading"][1]}; padding: {SPACE["xs"]}px 0;
}}
QLabel#mutedLabel {{ color: {c["text_muted"]}; font-size: {TYPE["caption"][0]}px; }}
QLabel#managedTaskHeading {{ color: #BFDBFE; font-weight: 700; }}
QFrame#managedTaskEditor, QFrame#workspaceCard {{
    background: {c["surface"]}; border: 1px solid {c["border"]};
    border-radius: {RADII["md"]}px;
}}
QFrame#workspaceCard[selected="true"] {{
    background: #24324A; border-color: {c["success"]};
}}
QFrame#workspaceRail {{ border: none; border-radius: 2px; background: {c["text_dim"]}; }}
QFrame#workspaceRail[state="loaded"] {{ background: {c["warning"]}; }}
QFrame#workspaceRail[state="selected"] {{ background: {c["success"]}; }}
QLabel#workspaceName {{ color: {c["text"]}; font-weight: 600; background: transparent; }}
QLabel#workspaceStatusText {{ color: {c["text_muted"]}; font-size: 11px; background: transparent; }}
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox {{
    background: {c["surface_sunken"]}; border: 1px solid {c["border"]};
    border-radius: {RADII["sm"]}px; color: {c["text"]};
    padding: {SPACE["sm"]}px; selection-background-color: {c["accent"]};
    min-height: {CONTROL_HEIGHT - 16}px;
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QTextBrowser:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {c["focus"]}; background: {c["surface_raised"]};
}}
QPlainTextEdit[invalid="true"] {{ border: 1px solid {c["danger"]}; }}
QPlainTextEdit#promptEditor, QTextBrowser#logView, QPlainTextEdit#schemaEditor {{
    font-family: "{mono_family}"; font-size: {TYPE["body"][0]}px;
}}
QComboBox QAbstractItemView {{
    background: {c["surface_raised"]}; color: {c["text"]};
    selection-background-color: {c["accent"]};
}}
QListWidget, QTreeWidget, QTreeView {{
    background: {c["surface_sunken"]};
    alternate-background-color: {c["surface_sunken"]};
    border: 1px solid {c["border"]}; border-radius: {RADII["md"]}px;
    color: {c["text"]};
}}
QListWidget#paletteList {{ background: transparent; border: none; }}
QListWidget#paletteList::item {{ background: transparent; border: none; }}
QListWidget::item, QTreeWidget::item {{ padding: {SPACE["sm"]}px; }}
QListWidget::item:selected, QTreeWidget::item:selected, QTreeView::item:selected {{
    background: {c["accent"]}; color: {c["accent_text"]};
}}
QHeaderView::section {{
    background: {c["surface"]}; color: {c["text"]}; border: none;
    border-right: 1px solid {c["border"]}; padding: {SPACE["sm"]}px;
    font-weight: {TYPE["label"][1]};
}}
QDockWidget {{ color: {c["text"]}; font-weight: {TYPE["label"][1]}; }}
QDockWidget::title {{
    background: {c["surface"]}; padding: {SPACE["xs"]}px;
    border-bottom: 1px solid {c["border"]};
}}
QDockWidget::close-button {{
    background: {c["danger"]}; border: none; border-radius: {RADII["sm"] // 2}px;
}}
QWidget#dockWidthHandle {{ background: {c["border_strong"]}; }}
QWidget#dockWidthHandle:hover {{ background: {c["focus"]}; }}
QLabel#workspaceStatus {{ color: {c["text_dim"]}; font-size: 17px; font-weight: 700; }}
QLabel#workspaceStatus[state="running"] {{ color: #60A5FA; }}
QLabel#workspaceStatus[state="attention"] {{ color: {c["warning"]}; }}
QLabel#workspaceStatus[state="failed"] {{ color: {c["danger"]}; }}
QLabel#workspaceStatus[state="unread"] {{ color: {c["focus"]}; }}
QDialog#imagePreview, QLabel#imagePreviewCanvas {{
    background: {c["surface_sunken"]}; color: {c["text"]};
}}
QListWidget#workspaceList {{ background: transparent; border: none; }}
QListWidget#workspaceList::item {{ background: transparent; border: none; }}
QListWidget#workspaceList::item:selected {{ background: transparent; }}
QMenu {{
    background: {c["surface_raised"]}; color: {c["text"]};
    border: 1px solid {c["border"]}; border-radius: {RADII["md"]}px;
    padding: {SPACE["xs"]}px;
}}
QMenu::item {{ padding: {SPACE["sm"]}px {SPACE["md"]}px; }}
QMenu::item:selected {{ background: {c["accent"]}; color: {c["accent_text"]}; }}
QToolTip {{
    background: {c["surface_raised"]}; color: {c["text"]};
    border: 1px solid {c["border_strong"]}; border-radius: {RADII["sm"]}px;
    padding: {SPACE["xs"]}px {SPACE["sm"]}px;
}}
QProgressBar {{
    background: {c["surface_sunken"]}; border: none;
    border-radius: {RADII["sm"] // 2}px; max-height: 4px;
}}
QProgressBar::chunk {{ background: {c["accent"]}; }}
QStatusBar {{
    background: {c["surface"]}; color: {c["text_muted"]};
    border-top: 1px solid {c["border"]};
}}
QLabel#activityLine {{
    background: {c["surface_raised"]}; border: 1px solid {c["border"]};
    border-radius: {RADII["sm"]}px;
    padding: {SPACE["sm"]}px {SPACE["md"]}px;
    font-family: "{mono_family}"; font-size: {TYPE["caption"][0]}px;
}}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {c["border_strong"]}; border-radius: 5px; min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{ background: {c["text_dim"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QSplitter::handle {{ background: {c["border"]}; }}
QFrame#rejectionCard {{
    background: {c["surface_raised"]};
    border: 1px solid {c["border"]};
    border-radius: {RADII["md"]}px;
}}
QTableWidget#diffTable {{
    background: {c["surface_sunken"]};
    border: 1px solid {c["border"]};
    border-radius: {RADII["sm"]}px;
}}
QTableWidget#diffTable QHeaderView::section {{
    background: {c["surface"]};
    color: {c["text_muted"]};
    border: 0;
    padding: 2px 6px;
}}
QTreeWidget#skillsTree {{
    background: {c["surface_sunken"]};
    border: 1px solid {c["border"]};
    border-radius: {RADII["sm"]}px;
}}
"""


APP_STYLE = build_style()
