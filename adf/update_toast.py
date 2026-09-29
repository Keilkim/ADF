"""A bouncing card at the bottom right of the window that asks to update."""
from datetime import datetime, timedelta

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

SNOOZE_HOUR = 9


def snooze_until(now=None):
    """'Later' rests for the rest of today and returns at 9 the next morning."""
    now = now or datetime.now()
    return (now + timedelta(days=1)).replace(hour=SNOOZE_HOUR, minute=0, second=0, microsecond=0)


class UpdateToast(QFrame):
    updateRequested = Signal()
    laterRequested = Signal()
    MARGIN_RIGHT = 20
    MARGIN_BOTTOM = 64
    REMIND_INTERVAL = 10 * 60 * 1000

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('updateToast')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 16, 14)
        layout.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName('updateToastTitle')
        layout.addWidget(self.title)
        self.text = QLabel()
        self.text.setWordWrap(True)
        layout.addWidget(self.text)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 8, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch()
        self.later = QPushButton('나중에 다시 보기')
        self.later.setObjectName('updateToastLater')
        self.later.setToolTip('내일 아침 9시에 다시 알려 드립니다')
        self.later.clicked.connect(self.laterRequested)
        buttons.addWidget(self.later)
        self.proceed = QPushButton('업데이트')
        self.proceed.setObjectName('updateToastProceed')
        self.proceed.clicked.connect(self.updateRequested)
        buttons.addWidget(self.proceed)
        layout.addLayout(buttons)
        self.setFixedWidth(320)
        self.setStyleSheet('''
            QFrame#updateToast { background: #39393d; border: none; border-radius: 14px; }
            QLabel { color: #dedee3; background: transparent; font-size: 9pt; }
            QLabel#updateToastTitle { color: white; font-size: 10.5pt; font-weight: 600; }
            QPushButton { border-radius: 8px; padding: 6px 12px; font-size: 9pt; }
            QPushButton#updateToastLater { background: transparent; color: #c9c9cf; border: 1px solid #5c5c62; }
            QPushButton#updateToastLater:hover { background: #4a4a4f; }
            QPushButton#updateToastProceed { background: white; color: #26262a; border: none; font-weight: 600; }
            QPushButton#updateToastProceed:hover { background: #e6e6ea; }
            QPushButton#updateToastProceed:disabled { background: #6a6a70; color: #d0d0d4; }
        ''')
        self.animation = QPropertyAnimation(self, b'pos', self)
        self.reminder = QTimer(self)
        self.reminder.setInterval(self.REMIND_INTERVAL)
        self.reminder.timeout.connect(self.hop)
        parent.installEventFilter(self)
        self.hide()

    def resting_pos(self):
        parent = self.parentWidget()
        return QPoint(max(8, parent.width() - self.width() - self.MARGIN_RIGHT),
                      max(8, parent.height() - self.height() - self.MARGIN_BOTTOM))

    def present(self, title, text, action, enabled=True):
        """Show or refresh the card; it drops in with bounces only when it was hidden."""
        self.title.setText(title)
        self.text.setText(text)
        self.proceed.setText(action)
        self.proceed.setEnabled(enabled)
        self.setAccessibleName(f'{title} · {text}')
        self.adjustSize()
        if self.isVisible():
            if self.animation.state() != QPropertyAnimation.State.Running:
                self.move(self.resting_pos())
            return
        rest = self.resting_pos()
        self.move(rest.x(), rest.y() - 90)
        self.show()
        self.raise_()
        self.drop()
        self.reminder.start()
        QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.Alert))

    def drop(self):
        # Falls onto its place and bounces a few times, smaller each time: "통~ 통통통통".
        rest = self.resting_pos()
        self.animation.stop()
        self.animation.setDuration(1300)
        self.animation.setKeyValues([])
        self.animation.setStartValue(QPoint(rest.x(), rest.y() - 90))
        self.animation.setEndValue(rest)
        self.animation.setEasingCurve(QEasingCurve.Type.OutBounce)
        self.animation.start()

    def hop(self):
        """A reminder while it waits: a jump and four smaller hops."""
        if not self.isVisible():
            return
        rest = self.resting_pos()
        self.raise_()
        self.animation.stop()
        self.animation.setDuration(1100)
        self.animation.setEasingCurve(QEasingCurve.Type.Linear)
        heights = [0, 22, 0, 12, 0, 8, 0, 5, 0, 2, 0]
        for index, height in enumerate(heights):
            self.animation.setKeyValueAt(index / (len(heights) - 1), QPoint(rest.x(), rest.y() - height))
        self.animation.start()

    def dismiss(self):
        self.animation.stop()
        self.reminder.stop()
        self.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self.animation.stop()
            self.move(self.resting_pos())
        return super().eventFilter(watched, event)
