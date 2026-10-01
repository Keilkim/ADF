"""Editing tools that stay inside the reader and can be moved to either side."""
from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGraphicsDropShadowEffect, QToolButton, QVBoxLayout, QWidget


class ToolboxGrip(QWidget):
    def __init__(self, toolbox):
        super().__init__(toolbox)
        self.toolbox = toolbox
        self.offset = None
        self.setFixedSize(38, 20)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip('끌어서 도구함 이동 · 두 번 클릭하면 반대쪽으로 이동')
        self.setAccessibleName('편집 도구함 이동')

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(QPen(QColor('#94a3b8'), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for y in (7, 12):
            for x in (13, 19, 25):
                painter.drawPoint(x, y)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.offset = event.globalPosition().toPoint() - self.toolbox.mapToGlobal(QPoint())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.offset is not None:
            target = self.toolbox.parentWidget().mapFromGlobal(event.globalPosition().toPoint() - self.offset)
            self.toolbox.move_clamped(target)
            event.accept()

    def mouseReleaseEvent(self, event):
        if self.offset is not None:
            self.offset = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.toolbox.remember_position()
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.offset = None
            self.toolbox.side = 'right' if self.toolbox.side == 'left' else 'left'
            self.toolbox.offset = None
            self.toolbox.position()
            self.toolbox.remember_position()
            event.accept()


class FloatingToolbox(QFrame):
    positionChanged = Signal()
    objectOptionsRequested = Signal()
    cancelRequested = Signal()

    def __init__(self, view, actions, settings, options):
        super().__init__(view.viewport())
        self.settings = settings
        # Move the previous left-center default once; subsequent manual moves
        # still persist across launches.
        if settings.value('toolbox_placement_version', 0, type=int) < 2:
            settings.setValue('toolbox_side', 'right')
            settings.setValue('toolbox_height', 0.)
            settings.setValue('toolbox_placement_version', 2)
        self.side = settings.value('toolbox_side', 'right')
        self.fraction = max(0., min(1., settings.value('toolbox_height', 0., type=float)))
        # Distance from the nearer side, so a dragged toolbox stays where it was put.
        self.offset = settings.value('toolbox_offset', None)
        self.offset = None if self.offset in (None, '') else max(0, int(float(self.offset)))
        self.setObjectName('floatingToolbox')
        self.setProperty('floating', True)
        self.setAccessibleName('편집 도구함')
        self.setStyleSheet('''
            QFrame#floatingToolbox { background: #ffffff; border: 1px solid #d9e0e9; border-radius: 14px; }
            QFrame#toolboxDivider { background: #e2e8f0; border: none; }
            QToolButton { padding: 5px; border-radius: 9px; }
            QToolButton#drawingTool { padding: 5px; }
        ''')
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(30, 41, 59, 28))
        self.setGraphicsEffect(shadow)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 8)
        layout.setSpacing(3)
        self.grip = ToolboxGrip(self)
        layout.addWidget(self.grip)
        self.buttons = {}
        for group in (('image', 'text', 'object_tool'), ('pen', 'eraser'), ('snap', 'stamps')):
            if self.buttons:
                line = QFrame()
                line.setObjectName('toolboxDivider')
                line.setFixedSize(26, 1)
                layout.addWidget(line, 0, Qt.AlignmentFlag.AlignHCenter)
            for key in group:
                if key in ('pen', 'eraser', 'object_tool'):
                    from .pen_widgets import ToolOptionsButton
                    button = ToolOptionsButton(self)
                    if key == 'object_tool':
                        button.optionsRequested.connect(self.objectOptionsRequested.emit)
                        button.options.setAccessibleName('도형 속성 열기 및 닫기')
                        button.options.setToolTip('도형 속성 · 채움색, 외곽선색, 선 두께')
                    else:
                        button.optionsRequested.connect(lambda tool=key: options(tool))
                        button.options.setAccessibleName(actions[key].text() + ' 옵션')
                        button.options.setToolTip(actions[key].text() + ' 옵션')
                    button.setStyleSheet(button.styleSheet()+'\nQToolButton#drawingTool { padding: 5px; }')
                else:
                    button = QToolButton(self)
                button.setDefaultAction(actions[key])
                button.setAccessibleName(actions[key].text())
                button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                button.setFixedSize(38, 38)
                layout.addWidget(button)
                self.buttons[key] = button
        self.adjustSize()
        self.parentWidget().installEventFilter(self)
        self.installEventFilter(self)
        for widget in self.findChildren(QWidget):
            widget.installEventFilter(self)
        self.position()

    def move_clamped(self, point):
        viewport = self.parentWidget()
        self.move(max(8, min(point.x(), viewport.width()-self.width()-8)),
                  max(8, min(point.y(), viewport.height()-self.height()-8)))
        self.raise_()
        self.positionChanged.emit()

    def position(self):
        viewport = self.parentWidget()
        offset = self.offset if self.offset is not None else 36 if self.side == 'left' else 14
        x = offset if self.side == 'left' else viewport.width()-self.width()-offset
        y = 8 + max(0, viewport.height()-self.height()-16)*self.fraction
        self.move_clamped(QPoint(x, round(y)))

    def remember_position(self):
        viewport = self.parentWidget()
        self.side = 'left' if self.geometry().center().x() < viewport.width()/2 else 'right'
        self.fraction = max(0., min(1., (self.y()-8)/max(1, viewport.height()-self.height()-16)))
        self.offset = self.x() if self.side == 'left' else viewport.width()-self.width()-self.x()
        self.settings.setValue('toolbox_offset', self.offset)
        self.settings.setValue('toolbox_side', self.side)
        self.settings.setValue('toolbox_height', self.fraction)

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.parentWidget() and kind in (QEvent.Type.Resize, QEvent.Type.Show):
            self.position()
        if watched is not self.parentWidget():
            if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.RightButton:
                self.grip.offset = None
                self.grip.setCursor(Qt.CursorShape.OpenHandCursor)
                self.cancelRequested.emit()
                event.accept()
                return True
            if (kind == QEvent.Type.ContextMenu or
                    kind in (QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick)
                    and event.button() == Qt.MouseButton.RightButton):
                event.accept()
                return True
        return False
