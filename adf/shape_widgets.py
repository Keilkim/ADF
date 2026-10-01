"""Contextual shape colors and stroke width beside the reader's toolbox."""
from PySide6.QtCore import QEvent, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QDoubleSpinBox, QFrame,
    QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QToolButton,
    QLineEdit, QSlider, QVBoxLayout, QWidget)

from .theme import ACCENT, ACCENT_HOVER
from .pen_widgets import ColorField


# Columns keep the same hue; rows move from light to vivid to deep. The red
# family leans toward rose, while vermilion retains its own signature column.
COLOR_ROWS = (
    (('흰색','#ffffff'),('실버','#eef1f5'),('안개색','#d7dce5'),('쿨그레이','#acb5c4'),
     ('슬레이트','#7a8799'),('차콜','#4c586b'),('잉크','#283345'),('검정','#000000')),
    (('옅은 주홍','#f5dfe5'),('옅은 로즈','#f8dfe7'),('옅은 레몬','#f2f1cf'),('옅은 에메랄드','#d7efe7'),
     ('옅은 청록','#d9eff3'),('옅은 파랑','#dee8fa'),('옅은 인디고','#e7e2fa'),('옅은 바이올렛','#eee2f8')),
    (('주홍',ACCENT),('로즈','#e44768'),('레몬','#d6d346'),('에메랄드','#14a88a'),
     ('청록','#16a6b6'),('파랑','#4177de'),('인디고','#7362de'),('바이올렛','#a05ad6')),
    (('크림슨','#a83647'),('짙은 로즈','#9b2e51'),('짙은 레몬','#85832a'),('짙은 에메랄드','#0e6d5e'),
     ('짙은 청록','#106d7c'),('짙은 파랑','#2b4c96'),('짙은 인디고','#4e3c96'),('짙은 바이올렛','#6a378b')),
)


def color_icon(color):
    pixmap = QPixmap(24,24)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor('#cbd5e1'),1))
    painter.setBrush(QColor(color) if color is not None else QColor('white'))
    painter.drawRoundedRect(3,3,18,18,5,5)
    if color is None:
        painter.setPen(QPen(QColor('#d53939'),1.5))
        painter.drawLine(5,19,19,5)
    painter.end()
    return QIcon(pixmap)


class ColorValueButton(QToolButton):
    """A single current-color chip, rather than a permanent rainbow of buttons."""
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.color = None
        self.setFixedSize(122,32)
        self.setToolTip(title+' 팔레트 열기')

    def set_color(self, rgb, enabled):
        self.color = QColor.fromRgbF(*rgb) if rgb is not None else None
        label = self.color.name().upper() if self.color is not None else '기존 색' if enabled else '없음'
        self.setText(label)
        self.setAccessibleName(self.title+' '+label+' · 팔레트 열기')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(ACCENT if self.hasFocus() else '#d9dee7'),1))
        painter.setBrush(QColor('#eef1f6' if self.underMouse() else '#f6f7fa'))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),7,7)
        color_icon(self.color.name() if self.color is not None else None).paint(painter,6,4,24,24)
        painter.setPen(QColor('#465064'))
        font = painter.font()
        font.setPointSizeF(8.5)
        painter.setFont(font)
        painter.drawText(QRectF(34,0,self.width()-51,self.height()),Qt.AlignmentFlag.AlignVCenter,self.text())
        painter.setPen(QPen(QColor('#8993a3'),1.2))
        painter.drawLine(self.width()-14,14,self.width()-11,17)
        painter.drawLine(self.width()-11,17,self.width()-8,14)


class ShapeColorMenu(QWidget):
    def __init__(self, selector):
        super().__init__(selector,Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.selector = selector
        self.setObjectName('shapeColorMenu')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self.setAccessibleName(selector.title+' 팔레트')
        self.setFixedWidth(260)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8,6,8,10)
        panel = QFrame()
        panel.setObjectName('shapeColorPanel')
        outer.addWidget(panel)
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(18)
        shadow.setOffset(0,3)
        shadow.setColor(QColor(30,41,59,28))
        panel.setGraphicsEffect(shadow)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12,10,12,12)
        layout.setSpacing(8)
        heading = QHBoxLayout()
        heading.addWidget(QLabel(selector.title))
        heading.addStretch()
        self.none_button = QToolButton()
        self.none_button.setText('없음')
        self.none_button.setIcon(color_icon(None))
        self.none_button.setIconSize(QSize(16,16))
        self.none_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.none_button.setAccessibleName(selector.title+' 없음')
        self.none_button.clicked.connect(lambda: selector.choose(None))
        heading.addWidget(self.none_button)
        layout.addLayout(heading)
        self.field = ColorField(QColor('#000000'))
        self.field.setFixedHeight(86)
        self.field.changed.connect(self.stage)
        layout.addWidget(self.field)
        self.hue = QSlider(Qt.Orientation.Horizontal)
        self.hue.setObjectName('shapeHue')
        self.hue.setRange(0,359)
        self.hue.setAccessibleName(selector.title+' 색조')
        self.hue.valueChanged.connect(self.change_hue)
        layout.addWidget(self.hue)
        self.preset_toggle = QToolButton()
        self.preset_toggle.setObjectName('shapePresetToggle')
        self.preset_toggle.setCheckable(True)
        self.preset_toggle.setText('기본색 펼치기')
        self.preset_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.preset_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.preset_toggle.setAccessibleName('기본색 팔레트 펼치기 및 접기')
        self.preset_toggle.toggled.connect(self.toggle_presets)
        layout.addWidget(self.preset_toggle,0,Qt.AlignmentFlag.AlignLeft)
        self.preset_grid = QWidget()
        colors = QGridLayout(self.preset_grid)
        colors.setContentsMargins(0,0,0,0)
        colors.setSpacing(3)
        self.buttons = []
        for row, entries in enumerate(COLOR_ROWS):
            for column, (name,color) in enumerate(entries):
                button = QToolButton()
                button.setObjectName('shapeSwatch')
                button.setIcon(color_icon(color))
                button.setIconSize(QSize(24,24))
                button.setFixedSize(24,24)
                button.setCheckable(True)
                button.setToolTip(name+' · '+color.upper())
                button.setAccessibleName(selector.title+' '+name)
                button.clicked.connect(lambda checked=False, value=color: selector.choose(value))
                colors.addWidget(button,row,column)
                self.buttons.append((button,color))
        self.preset_grid.hide()
        layout.addWidget(self.preset_grid)
        footer = QHBoxLayout()
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setAccessibleName(selector.title+' HEX 코드')
        self.hex.setPlaceholderText('#RRGGBB')
        self.hex.returnPressed.connect(self.apply_hex)
        footer.addWidget(self.hex,1)
        self.apply = QToolButton()
        self.apply.setObjectName('shapeColorApply')
        self.apply.setText('적용')
        self.apply.setAccessibleName(selector.title+' 적용')
        self.apply.clicked.connect(self.apply_hex)
        footer.addWidget(self.apply)
        layout.addLayout(footer)
        self.setStyleSheet('''
            QWidget#shapeColorMenu { background: transparent; }
            QFrame#shapeColorPanel { background: white; border: 1px solid #d9dee7; border-radius: 12px; }
            QLabel { border: none; background: transparent; }
            QToolButton { border: 1px solid transparent; border-radius: 6px; padding: 3px 6px; }
            QToolButton:hover { background: #eef1f6; }
            QToolButton:focus { border-color: @ACCENT@; }
            QToolButton#shapeSwatch { padding: 0; border-radius: 4px; }
            QToolButton#shapeSwatch:checked { border-color: #697587; }
            QToolButton#shapePresetToggle { background: transparent; color: #7b8798; padding: 1px 0;
                border: none; font-size: 9pt; }
            QToolButton#shapePresetToggle:hover { color: #465064; }
            QToolButton#shapeColorApply { background: @ACCENT@; color: white; padding: 5px 10px; }
            QToolButton#shapeColorApply:hover { background: @ACCENT_HOVER@; }
            QLineEdit { background: #f1f3f7; padding: 5px 8px; border-radius: 6px; }
            QSlider#shapeHue { min-height: 18px; }
            QSlider#shapeHue::groove:horizontal { height: 6px; border: none; border-radius: 3px;
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #e44768,stop:0.17 #d6d346,
                stop:0.33 #14a88a,stop:0.5 #16a6b6,stop:0.67 #4177de,stop:0.83 #a05ad6,stop:1 #e44768); }
            QSlider#shapeHue::handle:horizontal { width: 12px; margin: -3px 0; background: white;
                border: 1px solid #aeb8c6; border-radius: 6px; }
        '''.replace('@ACCENT@',ACCENT).replace('@ACCENT_HOVER@',ACCENT_HOVER))

    def stage(self, color):
        self.field.set_color(color)
        self.hue.blockSignals(True)
        self.hue.setValue(round(self.field.hue*359))
        self.hue.blockSignals(False)
        self.hex.setText(color.name().upper())
        for button,value in self.buttons:
            button.setChecked(value == color.name())

    def change_hue(self, value):
        self.field.hue = value/360
        self.stage(self.field.color())

    def apply_hex(self):
        text = self.hex.text().strip().lstrip('#')
        color = QColor('#'+text)
        if len(text) == 6 and color.isValid():
            self.selector.choose(color.name())
        else:
            self.hex.setText(self.field.color().name().upper())

    def popup(self):
        self.stage(self.selector.current or QColor('#000000'))
        self.position()
        self.show()
        self.hex.setFocus()
        self.hex.selectAll()

    def toggle_presets(self, checked):
        self.preset_grid.setVisible(checked)
        self.preset_toggle.setText('기본색 접기' if checked else '기본색 펼치기')
        self.preset_toggle.setArrowType(Qt.ArrowType.UpArrow if checked else Qt.ArrowType.DownArrow)
        if self.isVisible():
            self.position()

    def position(self):
        self.adjustSize()
        viewport = self.selector.parentWidget().parentWidget()
        bounds = viewport.rect().translated(viewport.mapToGlobal(QPoint()))
        anchor = self.selector.button.mapToGlobal(self.selector.button.rect().bottomRight())
        x = max(bounds.left()+8,min(anchor.x()-self.width()+1,bounds.right()-self.width()-8))
        y = anchor.y()+5
        if y+self.height() > bounds.bottom()-8:
            y = anchor.y()-self.selector.button.height()-self.height()-5
        self.move(QPoint(x,max(bounds.top()+8,min(y,bounds.bottom()-self.height()-8))))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.selector.button.setFocus()
            event.accept()
        else:
            super().keyPressEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().event(event)


class ShapeColorPalette(QWidget):
    changed = Signal(object)

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.title = title
        self.current = None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(6)
        layout.addWidget(QLabel(title))
        layout.addStretch()
        self.button = ColorValueButton(title)
        self.button.clicked.connect(self.open_menu)
        layout.addWidget(self.button)
        self.menu = ShapeColorMenu(self)
        self.none_button = self.menu.none_button

    def set_color(self, rgb, enabled):
        self.current = QColor.fromRgbF(*rgb) if rgb is not None else None
        self.button.set_color(rgb,enabled)

    def open_menu(self):
        self.menu.popup()

    def choose(self, color):
        self.menu.hide()
        rgb = None if color is None else (QColor(color).redF(),QColor(color).greenF(),QColor(color).blueF())
        self.changed.emit(rgb)


class ShapeProperties(QFrame):
    changed = Signal(object)

    def __init__(self, view, toolbox):
        super().__init__(view.viewport())
        self.view = view
        self.toolbox = toolbox
        self.target = None
        self.options = toolbox.buttons['object_tool'].options
        self.setObjectName('shapeProperties')
        self.setAccessibleName('도형 속성')
        self.setFixedWidth(212)
        self.setStyleSheet('''
            QFrame#shapeProperties { background: #ffffff; border: 1px solid #d9e0e9; border-radius: 14px; }
            QLabel { background: transparent; border: none; }
            QToolButton { padding: 2px; border: 1px solid transparent; border-radius: 7px; }
            QToolButton:checked { background: #f7e3e6; border-color: #dfa29e; }
            QToolButton:hover { background: #eef2f7; }
            QToolButton:focus { border-color: '''+ACCENT+'''; }
            QDoubleSpinBox { padding: 4px 18px 4px 7px; }
        ''')
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0,3)
        shadow.setColor(QColor(30,41,59,28))
        self.setGraphicsEffect(shadow)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12,10,12,12)
        layout.setSpacing(10)
        title = QLabel('도형 속성')
        title.setStyleSheet('font-weight: 600;')
        layout.addWidget(title)
        self.fill = ShapeColorPalette('채움색')
        self.stroke = ShapeColorPalette('외곽선색')
        layout.addWidget(self.fill)
        layout.addWidget(self.stroke)
        self.fill.changed.connect(lambda color: self.changed.emit(dict(fill=color)))
        self.stroke.changed.connect(lambda color: self.changed.emit(dict(stroke=color)))
        width = QHBoxLayout()
        width.addWidget(QLabel('선 두께'))
        width.addStretch()
        self.stroke_width = QDoubleSpinBox()
        self.stroke_width.setRange(0.,1000.)
        self.stroke_width.setDecimals(2)
        self.stroke_width.setSingleStep(.5)
        self.stroke_width.setSuffix(' pt')
        self.stroke_width.setKeyboardTracking(False)
        self.stroke_width.setFixedWidth(100)
        self.stroke_width.setAccessibleName('도형 외곽선 두께')
        self.stroke_width.setToolTip('0 pt는 확대 배율과 관계없이 가장 가는 선입니다')
        self.stroke_width.valueChanged.connect(lambda value: self.changed.emit(dict(width=value)))
        width.addWidget(self.stroke_width)
        layout.addLayout(width)
        self.adjustSize()
        self.hide()
        toolbox.positionChanged.connect(self.position)
        self.parentWidget().installEventFilter(self)

    def set_target(self, target):
        self.target = target if target is not None and target['kind'] == 'path' else None
        self.options.setEnabled(self.target is not None)
        if self.target is None:
            self.hide()
            return
        self.fill.set_color(target['fill'],target['has_fill'])
        self.stroke.set_color(target['stroke'],target['has_stroke'])
        # Removing both would make the object impossible to select again.
        self.fill.none_button.setEnabled(target['has_stroke'])
        self.stroke.none_button.setEnabled(target['has_fill'])
        self.stroke_width.blockSignals(True)
        self.stroke_width.setValue(target['width'])
        self.stroke_width.setEnabled(target['has_stroke'])
        self.stroke_width.blockSignals(False)
        if self.isVisible():
            self.position()

    def popup(self):
        if self.target is None:
            return
        self.show()
        self.position()
        self.fill.button.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, event):
        self.fill.menu.hide()
        self.stroke.menu.hide()
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def position(self):
        viewport = self.parentWidget()
        box = self.toolbox.geometry()
        x = box.left()-self.width()-10 if box.center().x() > viewport.width()/2 else box.right()+11
        self.move(QPoint(max(8,min(x,viewport.width()-self.width()-8)),
                         max(8,min(box.top(),viewport.height()-self.height()-8))))
        self.raise_()

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.parentWidget() and kind in (QEvent.Type.Resize,QEvent.Type.Show):
            self.position()
        if self.isVisible():
            # Let color dialogs and other child popups handle their own input.
            child_window = QApplication.activeModalWidget() or QApplication.activePopupWidget()
            if child_window in (self.fill.menu,self.stroke.menu) or child_window is not None and self.isAncestorOf(child_window):
                return False
            if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.hide()
                self.view.setFocus()
                event.accept()
                return True
            if kind in (QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonDblClick,QEvent.Type.TabletPress):
                if isinstance(watched,QWidget) and (watched is self.options or self.options.isAncestorOf(watched)):
                    return False
                if not self.rect().contains(self.mapFromGlobal(event.globalPosition().toPoint())):
                    self.hide()
        return False
