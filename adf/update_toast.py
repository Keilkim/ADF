"""A bouncing card at the bottom right that asks to update.

Inside a window it sits in the window's corner; without a parent (the background
agent) it floats above the taskbar at the bottom right of the screen.
"""
from datetime import datetime, timedelta

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent, QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

SNOOZE_HOUR = 9


def snooze_until(now=None):
    """'Later' rests for the rest of today and returns at 9 the next morning."""
    now = now or datetime.now()
    return (now + timedelta(days=1)).replace(hour=SNOOZE_HOUR, minute=0, second=0, microsecond=0)


def snooze(settings):
    settings.setValue('updates/snooze_until', snooze_until().timestamp())
    settings.sync()


def snooze_remaining(settings):
    """Seconds until the notice may show again; shared by XDF's windows and the background agent."""
    settings.sync()
    return max(0.0, settings.value('updates/snooze_until', 0.0, type=float) - datetime.now().timestamp())


def manual_reason(service):
    """The notification title and the sentence that say why XDF does not install this version itself."""
    return {
        'blocked': (f'XDF 새 버전 {service.package.version}',
                    'Windows 스마트 앱 컨트롤은 서명되지 않은 업데이트를 막으므로 자동으로 설치하지 않습니다.'),
        'security': ('XDF 업데이트를 설치하지 못했습니다', 'Windows 보안 설정이 업데이트 설치 프로그램을 막았습니다.'),
        'mismatch': ('XDF 업데이트를 받지 못했습니다',
                     '받은 업데이트 파일이 두 번 모두 공개된 파일과 달라 자동으로 받지 않습니다.'),
    }.get(service.reason, ('XDF 업데이트를 설치하지 못했습니다', '자동 업데이트를 설치하지 못했습니다.'))


def toast_content(service, following=False):
    """(title, text, button, enabled) for the service's state, or None when nothing waits."""
    package = service.package
    if package is None or service.state not in ('available', 'downloading', 'ready', 'manual'):
        return None
    if service.state == 'manual':
        title, reason = manual_reason(service)
        return title, reason, '다운로드 페이지', True
    if service.state == 'ready':
        return (f'XDF {package.version} 업데이트 준비 완료',
                '지금 업데이트하면 XDF를 잠시 닫았다가 보던 문서를 다시 엽니다.', '업데이트', True)
    if following and service.state == 'downloading':
        return (f'XDF {package.version} 받는 중 · {service.percent}%',
                '다 받으면 바로 설치합니다. 계속 작업할 수 있습니다.', '받는 중…', False)
    return (f'XDF 새 버전 {package.version}이 나왔습니다',
            '업데이트를 누르면 받아서 바로 설치합니다. 받는 동안 계속 작업할 수 있습니다.', '업데이트', True)


class UpdateToast(QFrame):
    updateRequested = Signal()
    laterRequested = Signal()
    MARGIN_RIGHT = 20
    MARGIN_BOTTOM = 64
    REMIND_INTERVAL = 10 * 60 * 1000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('updateToast')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if parent is None:
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setWindowTitle('XDF 업데이트')
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
            QPushButton#updateToastProceed { background: #d83c20; color: white; border: none; font-weight: 600; }
            QPushButton#updateToastProceed:hover { background: #b8321a; }
            QPushButton#updateToastProceed:disabled { background: #6a6a70; color: #d0d0d4; }
        ''')
        self.animation = QPropertyAnimation(self, b'pos', self)
        self.reminder = QTimer(self)
        self.reminder.setInterval(self.REMIND_INTERVAL)
        self.reminder.timeout.connect(self.hop)
        if parent is not None:
            parent.installEventFilter(self)
        self.hide()

    def resting_pos(self):
        parent = self.parentWidget()
        if parent is None:
            area = QGuiApplication.primaryScreen().availableGeometry()
            return QPoint(area.right() - self.width() - 16, area.bottom() - self.height() - 16)
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
