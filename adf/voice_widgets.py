"""Small voice markers, an anchored memo card, and the toolbar controls."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QButtonGroup, QFrame, QGraphicsItem, QGraphicsObject,
                               QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

from .theme import icon


class VoiceMarkButton(QToolButton):
    """A short moving line follows the button's inner rounded outline."""
    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.setDefaultAction(action)
        self.setText('STT 마킹하기')
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setAccessibleName('STT 마킹하기 · 마우스 음성 메모')
        self.phase = 0.
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.tick)
        action.changed.connect(self.sync)

    def sync(self):
        from .fullscreen import motion_enabled
        self.setText('STT 마킹하기')
        if self.isChecked() and self.isVisible() and self.isEnabled() and motion_enabled():
            self.timer.start()
        else:
            self.timer.stop()
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.sync()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def tick(self):
        self.phase = (self.phase + .018) % 1.
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.isChecked():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outline = QPainterPath()
        outline.addRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 7, 7)
        painter.setPen(QPen(QColor('#fdba9d'), 1.2))
        painter.drawPath(outline)
        trail = QPainterPath()
        for index in range(25):
            distance = ((self.phase + index / 160.) % 1.) * outline.length()
            point = outline.pointAtPercent(outline.percentAtLength(distance))
            if index == 0:
                trail.moveTo(point)
            else:
                trail.lineTo(point)
        painter.setPen(QPen(QColor('#e85d36'), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawPath(trail)


class VoiceVisibility(QWidget):
    visibilityChanged = Signal(str)

    def __init__(self, initial='half', parent=None):
        super().__init__(parent)
        self.setAccessibleName('마킹 표시 조절기')
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 0, 3, 0)
        row.setSpacing(2)
        label = QLabel('마킹 표시')
        row.addWidget(label)
        self.group = QButtonGroup(self)
        self.buttons = {}
        for key, text, glyph in [('hidden', '숨김', 'eye_off'), ('half', '50%', 'eye_half'), ('full', '선명', 'eye')]:
            button = QToolButton()
            button.setText(text)
            button.setIcon(icon(glyph))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setCheckable(True)
            button.setAccessibleName('마킹 ' + text)
            button.setToolTip({'hidden': '마킹 숨김 · 마킹 위치에 마우스를 올리면 다시 표시',
                               'half': '마킹을 50% 투명도로 표시 · 호버하면 선명하게 표시',
                               'full': '마킹을 선명하게 표시'}[key])
            self.group.addButton(button)
            button.clicked.connect(lambda checked=False, mode=key: self.visibilityChanged.emit(mode))
            self.buttons[key] = button
            row.addWidget(button)
        self.buttons[initial if initial in self.buttons else 'half'].setChecked(True)


class VoiceMarker(QGraphicsObject):
    hovered = Signal(object)
    left = Signal()
    clicked = Signal(object)

    def __init__(self, note, parent, visibility='half', draft=False):
        super().__init__(parent)
        self.note = note
        self.visibility = visibility
        self.draft = draft
        self.hovering = False
        self.level = 0.
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setZValue(90)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def boundingRect(self):
        return QRectF(-15, -15, 30, 30)

    def paint(self, painter, option, widget=None):
        alpha = 1. if self.draft or self.hovering else {'hidden': 0., 'half': .5, 'full': 1.}[self.visibility]
        if not alpha:
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(alpha)
        painter.setPen(QPen(QColor('#e85d36'), 1.3))
        painter.setBrush(QColor('#fff4ed'))
        painter.drawEllipse(QRectF(-12, -12, 24, 24))
        if self.draft:
            painter.setPen(QPen(QColor('#ef4444'), 2.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawArc(QRectF(-14, -14, 28, 28), 90*16, -round(max(.025, self.level)*360*16))
        icon('voice', '#b94425').paint(painter, -8, -8, 16, 16)

    def hoverEnterEvent(self, event):
        self.hovering = True
        self.update()
        self.hovered.emit(self)

    def hoverLeaveEvent(self, event):
        self.hovering = False
        self.update()
        self.left.emit()

    def mousePressEvent(self, event):
        self.clicked.emit(self)
        event.accept()


class VoiceLevel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(100, 22)
        self.level = 0.
        self.setAccessibleName('마이크 입력 크기')

    def set_level(self, level):
        self.level = level
        self.setAccessibleDescription(f'마이크 입력 크기 {round(level*100)}%')
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        active = math.ceil(self.level * 16)
        for index in range(16):
            height = 5 + (index % 4) * 4
            painter.setBrush(QColor('#e85d36' if index < active else '#e2e8f0'))
            painter.drawRoundedRect(QRectF(index*6, (22-height)/2, 3, height), 1.5, 1.5)


class VoiceCard(QFrame):
    applyRequested = Signal()
    cancelRequested = Signal()
    playRequested = Signal()
    deleteRequested = Signal()
    retryRequested = Signal()
    entered = Signal()
    left = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('voiceCard')
        self.setAccessibleName('음성 메모')
        self.setStyleSheet('''
            QFrame#voiceCard { background: #fff; border: 1px solid #e2d8d0; border-radius: 12px; }
            QLabel { background: transparent; border: none; }
            QPlainTextEdit { background: #f8fafc; border: none; border-radius: 7px; padding: 5px; }
            QPushButton { padding: 5px 8px; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(6)
        self.record_row = QWidget()
        record = QHBoxLayout(self.record_row)
        record.setContentsMargins(0, 0, 0, 0)
        record.setSpacing(7)
        self.time_label = QLabel('녹음 중  00:00')
        self.time_label.setStyleSheet('color: #dc462d;')
        self.level = VoiceLevel()
        self.apply_button = QPushButton('적용')
        self.apply_button.clicked.connect(self.applyRequested.emit)
        self.cancel_button = QPushButton('취소')
        self.cancel_button.clicked.connect(self.cancelRequested.emit)
        for widget in (self.time_label, self.level, self.apply_button, self.cancel_button):
            record.addWidget(widget)
        layout.addWidget(self.record_row)
        self.memo_row = QWidget()
        memo = QHBoxLayout(self.memo_row)
        memo.setContentsMargins(0, 0, 0, 0)
        memo.setSpacing(8)
        self.play_button = QToolButton()
        self.play_button.setFixedSize(34, 34)
        self.play_button.clicked.connect(self.playRequested.emit)
        self.set_playing(False)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setAccessibleName('음성 메모 전사문 · 읽기 전용')
        self.text.setFixedSize(270, 100)
        self.delete_button = QToolButton()
        self.delete_button.setIcon(icon('trash', '#dc462d'))
        self.delete_button.setFixedSize(32, 34)
        self.delete_button.setToolTip('메모 삭제 · 다시 녹음해서 새로 마킹')
        self.delete_button.setAccessibleName('음성 메모 삭제')
        self.delete_button.clicked.connect(self.deleteRequested.emit)
        for widget in (self.play_button, self.text, self.delete_button):
            memo.addWidget(widget)
        layout.addWidget(self.memo_row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setMaximumWidth(360)
        self.status.setStyleSheet('color: #64748b; font-size: 8pt;')
        layout.addWidget(self.status)
        self.retry_button = QPushButton('전사·문장 정리 다시 시도')
        self.retry_button.clicked.connect(self.retryRequested.emit)
        layout.addWidget(self.retry_button)
        self.hide()

    def recording(self):
        self.record_row.show()
        self.memo_row.hide()
        self.status.setText('말씀한 내용을 기록합니다 · 다른 PDF 위치 클릭으로 적용 · Esc 취소')
        self.retry_button.hide()
        self.time_label.setText('녹음 중  00:00')
        self.level.set_level(0.)
        self.adjustSize()

    def present(self, note, editable=True, progress=''):
        self.record_row.hide()
        self.memo_row.show()
        self.delete_button.setEnabled(editable)
        self.text.setPlainText(note.get('text') or (('전사 처리 중…' if progress else '전사 대기')
                               if note.get('status') == 'pending' else '전사문이 없습니다.'))
        self.status.setText(progress or note.get('error') or
                            ('전사 대기 · 다시 시도로 처리할 수 있습니다' if note.get('status') == 'pending' else
                             f"{note.get('duration', 0):.1f}초 · 수정하려면 삭제 후 다시 녹음"))
        self.retry_button.setVisible(editable and (note.get('status') == 'error' or (note.get('status') == 'pending' and not progress)))
        self.adjustSize()

    def set_playing(self, playing):
        self.play_button.setIcon(icon('stop' if playing else 'play'))
        self.play_button.setToolTip('재생 중지' if playing else '녹음 재생')
        self.play_button.setAccessibleName('재생 중지' if playing else '녹음 재생')

    def enterEvent(self, event):
        self.entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.left.emit()
        super().leaveEvent(event)
