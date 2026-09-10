"""Clipboard feedback that remains visible in fullscreen without taking focus."""
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QAccessible, QAccessibleEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class CopyNotice(QFrame):
    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('copyNotice')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(4)
        self.title = QLabel()
        self.title.setObjectName('copyNoticeTitle')
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.hint = QLabel('클립보드에 저장됨 · Ctrl+V로 붙여넣기')
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint)
        self.setStyleSheet('''
            QFrame#copyNotice { background: #39393d; border: none; border-radius: 14px; }
            QLabel { color: #dedee3; background: transparent; font-size: 9pt; }
            QLabel#copyNoticeTitle { color: white; font-size: 10pt; font-weight: 600; }
        ''')
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(2600)
        self.timer.timeout.connect(self.hide)
        parent.installEventFilter(self)
        self.hide()

    def announce(self, text):
        self.title.setText(text)
        self.setAccessibleName(text+' · '+self.hint.text())
        self.adjustSize()
        self.place()
        self.show()
        self.raise_()
        self.timer.start()
        QAccessible.updateAccessibility(QAccessibleEvent(self, QAccessible.Event.Alert))

    def place(self):
        parent = self.parentWidget()
        self.move(max(8, (parent.width()-self.width())//2), max(8, parent.height()-self.height()-24))

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.place()
        elif event.type() == QEvent.Type.Hide:
            self.timer.stop()
            self.hide()
        return super().eventFilter(watched, event)
