from PySide6.QtCore import Qt
from pathlib import Path
import sys
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap


def apply_theme(app):
    """Keep the entire application light, including Qt's unstyled controls."""
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    app.setStyle('Fusion')
    palette = QPalette()
    colors = {
        'Window': '#f7f7f8', 'WindowText': '#303238', 'Base': '#ffffff',
        'AlternateBase': '#f4f4f5', 'Text': '#303238', 'Button': '#ffffff',
        'ButtonText': '#303238', 'BrightText': '#ffffff', 'Light': '#ffffff',
        'Midlight': '#eeeeef', 'Mid': '#c7c8cb', 'Dark': '#8b8d92',
        'Shadow': '#62656b', 'Highlight': '#dedfe2', 'HighlightedText': '#303238',
        'Link': '#454950', 'LinkVisited': '#62656b', 'ToolTipBase': '#ffffff',
        'ToolTipText': '#303238', 'PlaceholderText': '#797d84', 'Accent': '#62656b',
    }
    for role, color in colors.items():
        palette.setColor(getattr(QPalette.ColorRole, role), QColor(color))
    for role in ('WindowText', 'Text', 'ButtonText'):
        palette.setColor(QPalette.ColorGroup.Disabled, getattr(QPalette.ColorRole, role), QColor('#96999f'))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)


def icon(name, color='#525b6a'):
    pm = QPixmap(24, 24)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    lines = {
        'open': [(3,8,9,8),(9,8,11,5),(11,5,20,5),(20,5,20,9),(3,8,3,20),(3,20,19,20),(19,20,22,10),(22,10,7,10),(7,10,3,20)],
        'save': [(5,3,18,3),(18,3,21,6),(21,6,21,21),(21,21,3,21),(3,21,3,3),(3,3,5,3),(7,3,7,9),(7,9,17,9),(17,9,17,3),(7,21,7,14),(7,14,17,14),(17,14,17,21)],
        'save_as': [(7,4,7,2),(7,2,19,2),(19,2,22,5),(22,5,22,17),(22,17,20,17),
                    (3,7,15,7),(15,7,18,10),(18,10,18,22),(18,22,3,22),(3,22,3,7),
                    (6,7,6,12),(6,12,13,12),(13,12,13,7),(6,22,6,17),(6,17,15,17),(15,17,15,22)],
        'eraser': [(3,14,13,4),(13,4,21,12),(21,12,11,22),(11,22,7,22),(7,22,3,18),(3,18,3,14),
                   (8,9,16,17),(7,22,21,22)],
        'options_arrow': [(6,6,18,18),(10,18,18,18),(18,10,18,18)],
        'magnet': [],
        'ocr': [(8,3,8,1),(8,1,19,1),(19,1,22,4),(22,4,22,17),
                (4,11,4,5),(4,5,14,5),(14,5,18,9),(18,9,18,11),(14,5,14,9),(14,9,18,9)],
        'pen': [(4,16,15,5),(15,5,19,9),(19,9,8,20),(8,20,3,21),(3,21,4,16),(4,16,8,20),(14,6,18,10),(16,4,18,2),(18,2,22,6),(22,6,20,8)],
        'capture': [(8,3,3,3),(3,3,3,8),(16,3,21,3),(21,3,21,8),(3,16,3,21),(3,21,8,21),(21,16,21,21),(21,21,16,21),(8,12,16,12),(12,8,12,16)],
        'merge': [(4,4,8,4),(8,4,12,11),(20,4,16,4),(16,4,12,11),(12,11,12,21),(8,17,12,21),(12,21,16,17)],
        'split': [(12,3,12,11),(12,11,5,18),(12,11,19,18),(5,13,5,18),(5,18,10,18),(14,18,19,18),(19,18,19,13)],
        'rotate': [(5,9,5,3),(5,9,11,9),(5,8,10,4),(10,4,17,5),(17,5,21,11),(21,11,19,18),(19,18,12,21),(12,21,6,18)],
        'trash': [(4,6,20,6),(9,6,9,3),(9,3,15,3),(15,3,15,6),(6,6,7,21),(7,21,17,21),(17,21,18,6),(10,10,10,17),(14,10,14,17)],
        'plus': [(12,5,12,19),(5,12,19,12)],
        'minus': [(5,12,19,12)],
        'fullscreen': [(9,4,4,4),(4,4,4,9),(15,4,20,4),(20,4,20,9),(4,15,4,20),(4,20,9,20),(20,15,20,20),(20,20,15,20)],
        'fullscreen_exit': [(4,9,9,9),(9,9,9,4),(20,9,15,9),(15,9,15,4),(9,20,9,15),(9,15,4,15),(15,20,15,15),(15,15,20,15)],
        'undo': [(8,5,3,10),(3,10,8,15),(3,10,15,10),(15,10,20,13),(20,13,20,19)],
        'redo': [(16,5,21,10),(21,10,16,15),(21,10,9,10),(9,10,4,13),(4,13,4,19)],
        'number': [(9,3,7,21),(17,3,15,21),(4,9,21,9),(3,16,20,16)],
        'image': [(3,3,21,3),(21,3,21,21),(21,21,3,21),(3,21,3,3),(3,17,9,11),(9,11,14,16),(14,16,17,12),(17,12,21,16)],
        'text': [(4,4,20,4),(12,4,12,21),(8,21,16,21),(4,4,4,8),(20,4,20,8)],
        'compress': [(4,4,10,10),(10,5,10,10),(5,10,10,10),(20,20,14,14),(14,19,14,14),(19,14,14,14)],
        'left': [(15,5,8,12),(8,12,15,19)],
        'right': [(9,5,16,12),(16,12,9,19)],
        'search': [(15,15,21,21)],
        'doc': [(5,3,15,3),(15,3,20,8),(20,8,20,21),(20,21,5,21),(5,21,5,3),(15,3,15,8),(15,8,20,8),(8,12,16,12),(8,16,16,16)],
        'replace': [(4,7,20,7),(16,3,20,7),(20,7,16,11),(20,17,4,17),(8,13,4,17),(4,17,8,21)],
        'single': [(6,3,18,3),(18,3,18,21),(18,21,6,21),(6,21,6,3),(9,8,15,8),(9,12,15,12)],
        'continuous': [(6,1,18,1),(18,1,18,10),(18,10,6,10),(6,10,6,1),(6,14,18,14),(18,14,18,23),(18,23,6,23),(6,23,6,14)],
        'spread': [(2,4,10,4),(10,4,10,20),(10,20,2,20),(2,20,2,4),(14,4,22,4),(22,4,22,20),(22,20,14,20),(14,20,14,4)],
        'grid': [(3,3,10,3),(10,3,10,10),(10,10,3,10),(3,10,3,3),(14,3,21,3),(21,3,21,10),(21,10,14,10),(14,10,14,3),(3,14,10,14),(10,14,10,21),(10,21,3,21),(3,21,3,14),(14,14,21,14),(21,14,21,21),(21,21,14,21),(14,21,14,14)],
        'spread_continuous': [(2,1,10,1),(10,1,10,10),(10,10,2,10),(2,10,2,1),(14,1,22,1),(22,1,22,10),(22,10,14,10),(14,10,14,1),(2,14,10,14),(10,14,10,23),(10,23,2,23),(2,23,2,14),(14,14,22,14),(22,14,22,23),(22,23,14,23),(14,23,14,14)],
        'start_left': [(2,4,10,4),(10,4,10,20),(10,20,2,20),(2,20,2,4),(14,4,22,4),(22,4,22,20),(22,20,14,20),(14,20,14,4),(5,10,7,8),(7,8,7,16)],
        'start_right': [(2,4,10,4),(10,4,10,20),(10,20,2,20),(2,20,2,4),(14,4,22,4),(22,4,22,20),(22,20,14,20),(14,20,14,4),(17,10,19,8),(19,8,19,16)],
        'print': [(7,8,7,3),(7,3,17,3),(17,3,17,8),(4,17,3,17),(3,17,3,8),(3,8,21,8),(21,8,21,17),(21,17,19,17),(7,14,17,14),(17,14,17,21),(17,21,7,21),(7,21,7,14)],
        'compare': [(2,4,10,4),(10,4,10,20),(10,20,2,20),(2,20,2,4),(14,4,22,4),(22,4,22,20),(22,20,14,20),(14,20,14,4),(5,9,7,9),(5,13,7,13),(17,9,19,9),(17,14,19,14),(18,13,18,15)],
        'stamp': [(4,17,20,17),(20,17,21,21),(21,21,3,21),(3,21,4,17),(8,17,9,11),(9,11,8,5),(8,5,10,3),(10,3,14,3),(14,3,16,5),(16,5,15,11),(15,11,16,17)],
    }
    # The existing arrow turns counter-clockwise; mirror it for right rotation.
    # A three-column grid remains distinct from two continuous facing rows.
    lines['grid'] = [line for x in (3,10,17) for y in (3,10,17)
                     for line in ((x,y,x+4,y),(x+4,y,x+4,y+4),(x+4,y+4,x,y+4),(x,y+4,x,y))]
    lines['rotate_left'] = lines['rotate']
    lines['rotate'] = [(24-x1,y1,24-x2,y2) for x1,y1,x2,y2 in lines['rotate_left']]
    for line in lines.get(name, lines['doc']):
        p.drawLine(*line)
    if name == 'search':
        p.drawEllipse(3,3,13,13)
    if name == 'image':
        p.drawEllipse(14,6,3,3)
    if name == 'magnet':
        p.translate(12, 12)
        p.rotate(-30)
        p.translate(-12, -12)
        path = QPainterPath()
        path.moveTo(5, 3); path.lineTo(5, 13)
        path.cubicTo(5, 23, 19, 23, 19, 13)
        path.lineTo(19, 3); path.lineTo(15, 3); path.lineTo(15, 13)
        path.cubicTo(15, 17, 9, 17, 9, 13)
        path.lineTo(9, 3); path.closeSubpath()
        p.drawPath(path)
        p.drawLine(5, 7, 9, 7); p.drawLine(15, 7, 19, 7)
    if name == 'ocr':
        eye = QPainterPath()
        eye.moveTo(2, 17)
        eye.cubicTo(7, 10, 15, 10, 20, 17)
        eye.cubicTo(15, 24, 7, 24, 2, 17)
        p.drawPath(eye)
        p.drawEllipse(8, 14, 6, 6)
    p.end()
    return QIcon(pm)


STYLE = '''
QWidget { color: #28313e; font-family: "Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo"; font-size: 10pt; }
QMainWindow, QDialog { background: #f7f7f8; }
QMenuBar { background: #f6f7f9; padding: 3px 9px; }
QMenuBar::item { padding: 4px 9px; border-radius: 4px; }
QMenuBar::item:selected { background: #e8ebef; }
QMenu { background: #fafafa; border: none; border-radius: 12px; padding: 7px; }
QMenu::item { padding: 9px 25px; border: none; border-radius: 8px; }
QMenu::item:selected { background: #d9d9dc; color: #303238; }
QMenu::item:disabled { color: #a2a8b1; }
QToolBar { background: #f6f7f9; border: none; spacing: 4px; padding: 9px 15px; }
QToolBar::separator { background: #dfe3e9; width: 1px; margin: 7px 10px; }
QToolButton { border: none; border-radius: 9px; padding: 7px 10px; background: transparent; }
QToolButton:hover { background: #e8e8ea; }
QToolButton:pressed, QToolButton:checked { background: #d5d5d8; border: none; color: #303238; }
QPushButton { background: #eeeeef; border: none; border-radius: 9px; padding: 8px 16px; min-height: 18px; }
QPushButton:hover { background: #e5e5e8; border: none; }
QPushButton:pressed, QPushButton:checked { background: #d5d5d8; border: none; }
QPushButton:default, QPushButton#primary { background: #dedee1; border: none; color: #303238; font-weight: 600; }
QPushButton#primary:hover, QPushButton:default:hover { background: #eeeeef; }
QPushButton:disabled, QToolButton:disabled { color: #a1a8b4; background: transparent; }
QPushButton:focus, QToolButton:focus { background: #d5d5d8; border: none; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QPlainTextEdit { background: #eeeeef; border: none; border-radius: 9px; padding: 6px 8px; selection-background-color: #dedfe2; selection-color: #303238; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus { background: #e2e2e5; border: none; }
QComboBox::drop-down { border: none; width: 23px; }
QComboBox QAbstractItemView { background: #fafafa; border: none; outline: none; selection-background-color: #d5d5d8; selection-color: #303238; }
QListWidget { background: transparent; border: none; outline: none; }
QListWidget::item { padding: 8px; margin: 3px 8px; border: none; border-radius: 10px; }
QListWidget::item:hover { background: #ececee; }
QListWidget::item:selected { background: #d5d5d8; border: none; color: #303238; }
QListWidget::item:focus { background: #d5d5d8; border: none; }
QScrollArea, QGraphicsView { border: none; background: #e8ebef; }
QScrollBar:vertical { background: transparent; width: 11px; margin: 2px; }
QScrollBar::handle:vertical { background: #bdc4cf; border-radius: 4px; min-height: 35px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 11px; margin: 2px; }
QScrollBar::handle:horizontal { background: #bdc4cf; border-radius: 4px; min-width: 35px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QStatusBar { background: #f6f7f9; color: #707988; padding: 3px 12px; border: none; }
QLabel#muted { color: #77808e; }
QWidget#emptyWorkspace { background: #e8ebef; }
QLabel#emptyTitle { font-size: 13pt; font-weight: 600; color: #667180; }
QLabel#title { font-size: 27pt; font-weight: 650; color: #222c3b; }
QLabel#eyebrow { color: #62656b; font-size: 9pt; font-weight: 600; }
QLabel#introBrand { color: #62656b; font-size: 13pt; font-weight: 700; }
QLabel#introTitle { color: #222c3b; font-size: 20pt; font-weight: 650; }
QLabel#introSection { font-weight: 600; }
QLabel#introDescription { color: #667180; }
QFrame#introCard { background: white; border: 1px solid #e1e5ec; border-radius: 9px; }
QFrame#card { background: white; border: 1px solid #e2e6ed; border-radius: 12px; }
QFrame#sidebar { background: #f4f5f8; border: none; }
QFrame#pageSidebarRail { background: #f4f5f8; border: none; }
QToolButton#pageSidebarToggle {
    background: #e8e8ea; border: none; border-radius: 10px; padding: 0;
}
QToolButton#pageSidebarToggle:hover { background: #dddddf; border: none; }
QToolButton#pageSidebarToggle:pressed { background: #e0e1e4; }
QToolButton#pageSidebarToggle:focus { background: #d5d5d8; border: none; }
QFrame#pagebar { background: #f8f9fb; border: none; }
QWidget#fullscreenStage { background: #e8ebef; }
QFrame#fullscreenPanel { background: #f8f9fb; border: none; }
QLabel#badge { background: #eeeeef; color: #62656b; border-radius: 5px; padding: 4px 9px; font-size: 9pt; }
QGroupBox { border: 1px solid #dfe3ea; border-radius: 8px; margin-top: 14px; padding: 15px; }
QGroupBox::title { subcontrol-origin: margin; left: 13px; padding: 0 5px; }
QCheckBox { spacing: 8px; padding: 5px 0; }
QCheckBox::indicator { width: 16px; height: 16px; }
QProgressBar { border: none; background: #e4e5e7; border-radius: 4px; text-align: center; }
QProgressBar::chunk { background: #797d84; border-radius: 4px; }
QSplitter::handle { background: #e0e4ea; width: 1px; }
QToolTip { background: white; color: #303238; border: 1px solid #d7d8dc; padding: 6px 9px; }
QToolBar#readerToolbar { padding: 5px 14px; background: #f8f9fb; }
QToolBar#pdfTools { padding: 5px 14px; background: #f0f2f6; border: none; }
QFrame#unifiedToolbar { background: #f8f9fb; border: none; }
QFrame#unifiedToolbar QToolButton { font-size: 9pt; padding: 6px 8px; }
QFrame#unifiedToolbar QComboBox { font-size: 9pt; padding-top: 5px; padding-bottom: 5px; }
QFrame#toolbarSeparator { color: #dfe3e9; max-width: 1px; margin: 6px; }
QFrame#viewGroupSeparator { background: #dfe3e9; border: none; margin: 0; }
QDialog#compressionDialog QSpinBox:disabled, QDialog#compressionDialog QDoubleSpinBox:disabled,
QDialog#compressionDialog QLineEdit:disabled {
    color: #939ba7; background: #eceff3; border-color: #e0e4e9;
}
QDialog#compressionDialog QLabel:disabled { color: #939ba7; }
QFrame#contextToolbar { background: #f1f1f3; border: none; }
QFrame#contextToolbar QLineEdit, QFrame#contextToolbar QPlainTextEdit, QFrame#contextToolbar QComboBox,
QFrame#contextToolbar QDoubleSpinBox, QFrame#contextToolbar QPushButton {
    font-size: 9pt; padding-top: 5px; padding-bottom: 5px;
}
QWidget#numberSettings, QScrollArea#numberSettingsScroll,
QScrollArea#numberSettingsScroll QWidget#qt_scrollarea_viewport {
    background: #ffffff;
}
QScrollArea#numberSettingsScroll {
    border: 1px solid #dfe3ea; border-radius: 10px;
}
QFrame#exportResult { background: #eaf4ed; border: 1px solid #cee1d4; border-radius: 7px; }
QWidget#numberSettings { border: 1px solid #dfe3ea; border-radius: 10px; }
QToolButton#numberPosition { background: #eeeeef; border: none; border-radius: 9px; padding: 2px; }
QToolButton#numberPosition:hover { background: #eeeeef; border-color: #a3a6ad; }
QToolButton#numberPosition:checked { background: #d5d5d8; border: none; }
QLabel#numberExample { background: #f0f0f2; color: #454950; border-radius: 7px; padding: 12px; font-size: 12pt; }
'''

_asset_dir = (Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent)) / 'assets').as_posix()
STYLE += '''
QDialog#compressionDialog QCheckBox::indicator { background: white; border: 1px solid #bfc1c6; border-radius: 4px; }
QDialog#compressionDialog QCheckBox::indicator:checked { background: #62656b; border-color: #62656b; image: url("ASSETS/check-white.svg"); }
QWidget#numberSettings QCheckBox::indicator { background: white; border: 1px solid #bfc1c6; border-radius: 4px; }
QWidget#numberSettings QCheckBox::indicator:checked { background: #62656b; border-color: #62656b; image: url("ASSETS/check-white.svg"); }
QComboBox::down-arrow { image: url("ASSETS/arrow-down.svg"); width: 12px; height: 8px; }
QSpinBox, QDoubleSpinBox { padding-right: 22px; }
QSpinBox::up-button, QDoubleSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 22px; border: none; margin: 2px; }
QSpinBox::down-button, QDoubleSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; border: none; margin: 2px; }
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow { image: url("ASSETS/arrow-up.svg"); width: 10px; height: 6px; }
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow { image: url("ASSETS/arrow-down.svg"); width: 10px; height: 6px; }
'''.replace('ASSETS',_asset_dir)
