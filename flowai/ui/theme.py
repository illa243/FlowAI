APP_STYLE = """
QMainWindow, QDialog, QWidget {
    background: #111827;
    color: #E5E7EB;
    font-size: 13px;
}
QToolBar {
    background: #0B1220;
    border: none;
    border-bottom: 1px solid #263247;
    spacing: 6px;
    padding: 6px;
}
QToolButton, QPushButton {
    background: #1F2937;
    border: 1px solid #344259;
    border-radius: 6px;
    color: #F3F4F6;
    padding: 7px 11px;
}
QToolButton:hover, QPushButton:hover { background: #2A3A52; }
QToolButton:pressed, QPushButton:pressed { background: #334155; }
QToolButton#expandTextButton {
    background: #172033;
    border: 1px solid #4B5E7A;
    border-radius: 4px;
    padding: 2px;
}
QToolButton#expandTextButton:hover {
    background: #4C3AC7;
    border-color: #A78BFA;
}
QFrame#managedTaskEditor {
    background: #101827;
    border: 1px solid #344259;
    border-radius: 7px;
}
QLabel#managedTaskHeading {
    color: #BFDBFE;
    font-weight: 700;
}
QToolButton#removeManagedTaskButton {
    background: #7F1D1D;
    border-color: #B91C1C;
    color: #FFFFFF;
    padding: 1px;
    font-size: 16px;
}
QToolButton#removeManagedTaskButton:disabled {
    background: #374151;
    border-color: #4B5563;
    color: #9CA3AF;
}
QPushButton#addManagedTaskButton {
    background: #1E3A5F;
    border-color: #3B82F6;
    color: #DBEAFE;
    font-weight: 600;
}
QToolButton#runButton {
    background: #15803D;
    border-color: #22C55E;
    color: #FFFFFF;
    font-weight: 700;
}
QToolButton#runButton:hover { background: #16A34A; }
QToolButton#stopButton {
    background: #B91C1C;
    border-color: #EF4444;
    color: #FFFFFF;
    font-weight: 700;
}
QToolButton#stopButton:hover { background: #DC2626; }
QToolButton#runButton:disabled, QToolButton#stopButton:disabled {
    background: #374151;
    border-color: #4B5563;
    color: #9CA3AF;
}
QToolButton#filesButton {
    background: #1E3A5F;
    border-color: #3B82F6;
    color: #DBEAFE;
    font-weight: 600;
}
QToolButton#filesButton:hover { background: #1D4ED8; color: #FFFFFF; }
QToolButton#filesButton:disabled {
    background: #374151;
    border-color: #4B5563;
    color: #9CA3AF;
}
QToolButton#accountButton {
    background: #172033;
    border: 1px solid #3B4B66;
    border-radius: 18px;
    font-weight: 600;
    padding: 4px 12px 4px 5px;
}
QToolButton#accountButton:hover { background: #24324A; border-color: #8B7BFF; }
QPushButton#primaryButton {
    background: #6D4AFF;
    border-color: #8067FF;
    font-weight: 600;
}
QPushButton#dangerButton { background: #7F1D1D; border-color: #B91C1C; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {
    background: #0B1220;
    border: 1px solid #344259;
    border-radius: 5px;
    color: #F9FAFB;
    padding: 6px;
    selection-background-color: #6D4AFF;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {
    border-color: #8B7BFF;
}
QComboBox QAbstractItemView {
    background: #172033;
    color: #F9FAFB;
    selection-background-color: #6D4AFF;
}
QLabel#sectionTitle {
    color: #FFFFFF;
    font-size: 16px;
    font-weight: 700;
    padding: 4px 0;
}
QLabel#mutedLabel { color: #94A3B8; }
QPushButton#popupFieldLabel {
    background: transparent;
    border: none;
    color: #E5E7EB;
    padding: 0;
    text-align: left;
}
QPushButton#popupFieldLabel:hover { color: #A78BFA; }
QDockWidget {
    color: #E5E7EB;
    font-weight: 600;
}
QDockWidget::title {
    background: #172033;
    padding: 3px;
    border-bottom: 1px solid #263247;
}
QDockWidget::close-button {
    background: #B91C1C;
    border: 1px solid #EF4444;
    border-radius: 3px;
}
QDockWidget::close-button:hover { background: #EF4444; }
QWidget#dockWidthHandle {
    background: #334155;
    border-left: 2px solid #64748B;
    border-right: 2px solid #172033;
}
QWidget#dockWidthHandle:hover { background: #8B7BFF; }
QScrollArea { border: none; }
QListWidget {
    background: #0B1220;
    border: 1px solid #263247;
    border-radius: 5px;
}
QListWidget::item { padding: 5px; }
QListWidget::item:selected { background: #4C3AC7; }
QStatusBar {
    background: #0B1220;
    color: #A5B4FC;
    border-top: 1px solid #263247;
}
QProgressBar {
    background: #263247;
    border: none;
    border-radius: 3px;
}
QProgressBar::chunk { background: #7C3AED; border-radius: 3px; }
QSplitter::handle { background: #263247; }
QMenu { background: #172033; color: #F9FAFB; border: 1px solid #344259; }
QMenu::item:selected { background: #4C3AC7; }
QToolTip { background: #172033; color: #F9FAFB; border: 1px solid #64748B; }
"""
