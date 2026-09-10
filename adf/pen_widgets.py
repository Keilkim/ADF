"""Borderless drawing popover with an inline, keyboard-accessible color picker."""
from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame,
    QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QSizePolicy, QSlider, QToolButton, QVBoxLayout, QWidget)

from .ink import KINDS
import math

COLORS = [('검정', '#252525'), ('빨강', '#d53939'), ('파랑', '#2869cf'), ('초록', '#27824b'),
          ('주황', '#e28738'), ('노랑', '#e5bd3d'), ('보라', '#8e62bb'), ('분홍', '#cd6398')]


class ColorField(QWidget):
    changed = Signal(QColor)

    def __init__(self, color, parent=None):
        super().__init__(parent)
        self.setFixedHeight(100)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('색상 선택판 · 방향키로 채도와 밝기 조절')
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.hue = 0.
        self.set_color(color)

    def set_color(self, color):
        hue, self.saturation, self.value, _ = color.getHsvF()
        if hue >= 0:
            self.hue = hue
        self.update()

    def color(self):
        return QColor.fromHsvF(self.hue, self.saturation, self.value)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.setClipPath(path)
        color = QLinearGradient(0, 0, self.width(), 0)
        color.setColorAt(0, QColor('white'))
        color.setColorAt(1, QColor.fromHsvF(self.hue, 1, 1))
        painter.fillRect(self.rect(), color)
        shade = QLinearGradient(0, 0, 0, self.height())
        shade.setColorAt(0, QColor(0, 0, 0, 0))
        shade.setColorAt(1, QColor('black'))
        painter.fillRect(self.rect(), shade)
        point = QPointF(max(7, min(self.width()-7, self.saturation*self.width())),
                        max(7, min(self.height()-7, (1-self.value)*self.height())))
        painter.setPen(QPen(QColor(0, 0, 0, 90), 4))
        painter.drawEllipse(point, 5, 5)
        painter.setPen(QPen(QColor('white'), 2))
        painter.drawEllipse(point, 5, 5)

    def pick(self, position):
        self.saturation = max(0., min(1., position.x()/max(1, self.width()-1)))
        self.value = max(0., min(1., 1-position.y()/max(1, self.height()-1)))
        self.update()
        self.changed.emit(self.color())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.pick(event.position())
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            self.pick(event.position())

    def keyPressEvent(self, event):
        step = .1 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else .01
        dx, dy = {Qt.Key.Key_Left: (-step, 0), Qt.Key.Key_Right: (step, 0),
                  Qt.Key.Key_Up: (0, step), Qt.Key.Key_Down: (0, -step)}.get(event.key(), (0, 0))
        if dx or dy:
            self.saturation = max(0., min(1., self.saturation+dx))
            self.value = max(0., min(1., self.value+dy))
            self.update()
            self.changed.emit(self.color())
            event.accept()
        else:
            super().keyPressEvent(event)


class KindPicker(QWidget):
    currentIndexChanged = Signal(int)

    def __init__(self, current, parent=None):
        super().__init__(parent)
        self.keys = list(KINDS)
        self.current = self.findData(current)
        self.buttons = QButtonGroup(self)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for index, key in enumerate(self.keys):
            button = QToolButton()
            button.setText(KINDS[key][0])
            button.setMinimumHeight(28)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setCheckable(True)
            button.setChecked(index == self.current)
            button.clicked.connect(lambda checked=False, i=index: self.setCurrentIndex(i))
            self.buttons.addButton(button)
            row.addWidget(button, 1)

    def findData(self, key):
        return self.keys.index(key)

    def count(self):
        return len(self.keys)

    def itemData(self, index):
        return self.keys[index]

    def setCurrentIndex(self, index):
        if index != self.current:
            self.current = index
            self.buttons.buttons()[index].setChecked(True)
            self.currentIndexChanged.emit(index)


class StrokeSample(QWidget):
    """Use the drawing renderer so the preview shares each tool's appearance."""
    def __init__(self, pen, parent=None):
        super().__init__(parent)
        self.pen = pen
        self.setAccessibleName('선 굵기 및 지우개 크기 미리보기')
        self.setFixedHeight(48)

    def refresh(self):
        self.setFixedHeight(88 if self.pen.tool == 'eraser' else 48)
        self.update()

    def paintEvent(self, event):
        from .pen import paint_strokes
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('white'))
        painter.drawRoundedRect(QRectF(self.rect()), 10, 10)
        pen = self.pen
        eraser = pen.tool == 'eraser'
        width = pen.eraser_width if eraser else pen.width
        scale = min(pen.view.transform().m11(), (self.height()-20)/max(1, width))
        if eraser:
            diameter = width*scale
            painter.setBrush(QColor(225, 226, 229, 140))
            painter.setPen(QPen(QColor('#77787c'), 1))
            painter.drawEllipse(QPointF(self.width()/2, self.height()/2), diameter/2, diameter/2)
        else:
            points = [(16+(self.width()-76)*i/48, self.height()/2+math.sin(i/48*math.pi*2)*5) for i in range(49)]
            paint_strokes(painter, [points], [[.8]*len(points)], pen.color, width*scale, pen.kind)
        painter.setPen(QColor('#88898d'))
        font = painter.font()
        font.setPointSizeF(8)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, 0, -8, -5),
                         Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom, f'{round(scale*100)}%')


class ToolOptionsButton(QToolButton):
    """The main target toggles its action; the small corner opens settings."""
    optionsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        from .theme import icon
        self.setObjectName('drawingTool')
        self.options = QToolButton(self)
        self.options.setObjectName('drawingToolOptions')
        self.options.setIcon(icon('options_arrow'))
        self.options.setIconSize(QSize(10, 10))
        self.options.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.options.clicked.connect(self.optionsRequested.emit)
        self.setStyleSheet('''
            QToolButton#drawingTool { padding-right: 20px; }
            QToolButton#drawingToolOptions { border: none; border-radius: 5px; padding: 0; background: transparent; }
            QToolButton#drawingToolOptions:hover, QToolButton#drawingToolOptions:focus { background: #c8c8cc; }
        ''')

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.options.setGeometry(self.width()-18, self.height()-18, 18, 18)


class PenMenu(QWidget):
    def __init__(self, pen, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.pen = pen
        self.setObjectName('penPopover')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoMouseReplay)
        self.setAccessibleName('필기 설정')
        self.blocked_gesture = False
        self.setFixedWidth(304)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 7, 8, 11)
        self.panel = QFrame()
        self.panel.setObjectName('penPanel')
        outer.addWidget(self.panel)
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 32))
        self.panel.setGraphicsEffect(shadow)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)
        header = QHBoxLayout()
        self.title = QLabel('펜')
        self.title.setObjectName('penTitle')
        header.addWidget(self.title)
        header.addStretch()
        done = QToolButton()
        done.setText('완료')
        done.setAccessibleName('필기 설정 닫기')
        done.clicked.connect(self.hide)
        header.addWidget(done)
        layout.addLayout(header)
        self.color_panel = QWidget()
        settings = QVBoxLayout(self.color_panel)
        settings.setContentsMargins(0, 0, 0, 0)
        settings.setSpacing(7)
        self.kind = KindPicker(pen.kind)
        self.kind.setAccessibleName('펜 종류')
        self.kind.currentIndexChanged.connect(self.change_kind)
        settings.addWidget(self.kind)
        self.field = ColorField(QColor(pen.color))
        self.field.changed.connect(self.set_color)
        settings.addWidget(self.field)
        self.hue = QSlider(Qt.Orientation.Horizontal)
        self.hue.setObjectName('hueSlider')
        self.hue.setAccessibleName('색상 · 방향키로 조절')
        self.hue.setRange(0, 359)
        self.hue.valueChanged.connect(self.change_hue)
        settings.addWidget(self.hue)
        swatches = QGridLayout()
        swatches.setSpacing(4)
        self.colors = QButtonGroup(self)
        for index, (name, color) in enumerate(COLORS):
            button = QToolButton()
            button.setAccessibleName(name+' 펜')
            button.setToolTip(name)
            button.setCheckable(True)
            button.setFixedSize(28, 28)
            button.setProperty('inkColor', color)
            pm = QPixmap(28, 28)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawEllipse(3, 3, 22, 22)
            painter.end()
            button.setIcon(QIcon(pm))
            button.setIconSize(QSize(24, 24))
            button.clicked.connect(lambda checked=False, value=color: self.set_color(QColor(value)))
            self.colors.addButton(button)
            swatches.addWidget(button, 0, index)
        settings.addLayout(swatches)
        color_row = QHBoxLayout()
        color_row.addWidget(QLabel('색상 코드'))
        color_row.addStretch()
        self.hex = QLineEdit()
        self.hex.setAccessibleName('펜 색상 HEX 코드')
        self.hex.setMaxLength(7)
        self.hex.setFixedWidth(96)
        self.hex.editingFinished.connect(self.apply_hex)
        color_row.addWidget(self.hex)
        settings.addLayout(color_row)
        layout.addWidget(self.color_panel)
        self.width_label = QLabel()
        layout.addWidget(self.width_label)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.valueChanged.connect(self.change_width)
        layout.addWidget(self.slider)
        self.preview = StrokeSample(pen)
        layout.addWidget(self.preview)
        self.hint = QLabel()
        self.hint.setObjectName('penHint')
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.setStyleSheet('''
            QWidget#penPopover { background: transparent; }
            QFrame#penPanel { background: #fafafa; border: none; border-radius: 16px; }
            QLabel { background: transparent; color: #333437; }
            QLabel#penTitle { font-size: 11pt; font-weight: 600; }
            QLabel#penHint { color: #77787c; font-size: 9pt; }
            QFrame#penSegments { background: #eeeeef; border: none; border-radius: 13px; }
            QToolButton { border: none; border-radius: 10px; padding: 4px; color: #444548; }
            QToolButton:hover, QToolButton:focus { background: #e8e8ea; border: none; }
            QToolButton:checked, QToolButton:pressed { background: #d5d5d8; border: none; color: #252528; }
            QComboBox, QLineEdit { background: #eeeeef; border: none; border-radius: 8px; padding: 5px 8px; }
            QComboBox:focus, QLineEdit:focus { background: #e2e2e5; border: none; }
            QComboBox QAbstractItemView { background: #fafafa; border: none; outline: none; padding: 6px; selection-background-color: #d5d5d8; }
            QComboBox QAbstractItemView::item { padding: 8px; border: none; border-radius: 8px; }
            QSlider { min-height: 22px; background: transparent; }
            QSlider::groove:horizontal { height: 6px; background: #e0e0e3; border: none; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #717276; border-radius: 3px; }
            QSlider::handle:horizontal { width: 20px; margin: -7px 0; border: none; border-radius: 10px; background: #77787d; }
            QSlider::handle:horizontal:hover, QSlider::handle:horizontal:focus { background: #535459; }
            QSlider#hueSlider::groove:horizontal { height: 12px; border-radius: 6px;
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #ed6464,stop:0.17 #e6df63,stop:0.33 #72d976,stop:0.5 #65dadd,stop:0.67 #6b75df,stop:0.83 #d371d9,stop:1 #ed6464); }
            QSlider#hueSlider::sub-page:horizontal { background: transparent; }
            QSlider#hueSlider::handle:horizontal { margin: -4px 0; background: #fafafa; }
        ''')
        self.set_color(QColor(pen.color))
        self.change_tool(pen.tool)
        QApplication.instance().installEventFilter(self)

    def popup(self, position):
        self.pen.cancel()
        self.adjustSize()
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        x = max(bounds.left(), min(position.x()-8, bounds.right()-self.width()+1))
        y = position.y()+5
        if y+self.height() > bounds.bottom():
            y = max(bounds.top(), position.y()-self.height())
        self.move(QPoint(x, y))
        self.show()
        (self.kind.buttons.buttons()[self.kind.current] if self.pen.tool == 'pen' else self.slider).setFocus()

    def change_tool(self, tool):
        self.pen.cancel()
        self.pen.tool = tool
        eraser = tool == 'eraser'
        self.setFixedWidth(248 if eraser else 304)
        self.title.setText('지우개' if eraser else '펜')
        self.color_panel.setVisible(not eraser)
        self.slider.blockSignals(True)
        self.slider.setRange(4, 72) if eraser else self.slider.setRange(1, 60)
        self.slider.setValue(round(self.pen.eraser_width if eraser else self.pen.width*5))
        self.slider.blockSignals(False)
        self.slider.setAccessibleName('지우개 지름' if eraser else '펜 굵기 · 0.2 pt 단위')
        self.change_width(self.slider.value())
        self.hint.setText('문지른 필기만 지우기 · Ctrl+Z 취소' if eraser else '누른 채 잠시 멈추면 직선으로 보정')
        self.panel.layout().activate()
        self.layout().activate()
        self.setFixedHeight(self.sizeHint().height())

    def change_width(self, value):
        if self.pen.tool == 'eraser':
            self.pen.eraser_width = value
            self.width_label.setText(f'지우개 크기  {value} pt')
        else:
            self.pen.width = value/5
            self.width_label.setText(f'굵기  {self.pen.width:.1f} pt')
        if self.pen.enabled:
            self.pen.update_cursor()
        self.preview.refresh()

    def change_kind(self, index):
        self.pen.kind = self.kind.itemData(index)
        self.slider.setValue(round(KINDS[self.pen.kind][1]*5))
        self.preview.refresh()

    def set_color(self, color):
        self.pen.color = color.name()
        self.field.set_color(color)
        self.hue.blockSignals(True)
        self.hue.setValue(round(self.field.hue*359))
        self.hue.blockSignals(False)
        self.hex.setText(color.name().upper())
        self.colors.setExclusive(False)
        for button in self.colors.buttons():
            button.setChecked(button.property('inkColor') == color.name())
        self.colors.setExclusive(True)
        self.preview.refresh()

    def change_hue(self, value):
        self.field.hue = value/360
        self.set_color(self.field.color())

    def apply_hex(self):
        text = self.hex.text().strip().lstrip('#')
        color = QColor('#'+text)
        if len(text) == 6 and color.isValid():
            self.set_color(color)
        else:
            self.hex.setText(self.pen.color.upper())

    def eventFilter(self, watched, event):
        kind = event.type()
        presses = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick, QEvent.Type.TabletPress)
        releases = (QEvent.Type.MouseButtonRelease, QEvent.Type.TabletRelease)
        moves = (QEvent.Type.MouseMove, QEvent.Type.TabletMove)
        if kind in presses:
            self.blocked_gesture = False
            if self.isVisible() and (watched is self.pen.view.viewport() or
                                     not self.frameGeometry().contains(event.globalPosition().toPoint())):
                # Allow child combo popups to handle their own selection.
                popup = QApplication.activePopupWidget()
                if popup is not None and popup is not self and self.isAncestorOf(popup):
                    return False
                self.blocked_gesture = True
                self.pen.cancel()
                self.hide()
                event.accept()
                return True
        elif self.blocked_gesture and kind in releases+moves:
            if kind in releases:
                self.blocked_gesture = False
            event.accept()
            return True
        if self.isVisible() and kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            popup = QApplication.activePopupWidget()
            if popup is None or popup is self:
                self.hide()
                event.accept()
                return True
        return super().eventFilter(watched, event)
