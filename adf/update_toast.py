"""A bouncing card at the bottom right that asks to update.

On Windows it floats above the taskbar on the notification area's monitor.
Other platforms keep an owned card inside the document window.
"""
from datetime import datetime, timedelta
import sys

from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPropertyAnimation, QTimer, Qt, Signal
from PySide6.QtGui import QAccessible, QAccessibleEvent, QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .brand import ACCENT, ACCENT_HOVER, ACCENT_SOFT, ACCENT_TEXT

SNOOZE_HOUR = 9


def windows_taskbar_origin():
    """Windows and Qt keep the same screen origins even at different display scales."""
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                    ('work', wintypes.RECT), ('flags', wintypes.DWORD),
                    ('device', wintypes.WCHAR * 32)]

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowW.restype = wintypes.HWND
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromWindow.restype = wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    taskbar = user32.FindWindowW('Shell_TrayWnd', None)
    if not taskbar:
        return None
    monitor = user32.MonitorFromWindow(taskbar, 0)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return QPoint(info.monitor.left, info.monitor.top)
    return None


def notification_screen(tray=None):
    if tray is not None:
        rect = tray.geometry()
        if rect.isValid():
            screen = QGuiApplication.screenAt(rect.center())
            if screen is not None:
                return screen
    origin = windows_taskbar_origin()
    if origin is not None:
        for screen in QGuiApplication.screens():
            # Qt may report a friendly monitor name rather than \\.\DISPLAYn.
            if screen.geometry().topLeft() == origin:
                return screen
    return QGuiApplication.primaryScreen()


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

    def __init__(self, parent=None, *, tray=None):
        super().__init__(parent)
        self.tray = tray
        self.desktop_notification = parent is None or sys.platform == 'win32'
        self.setObjectName('updateToast')
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if self.desktop_notification:
            self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                                | Qt.WindowType.WindowStaysOnTopHint)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setWindowTitle('XDF 업데이트')
        # Paint an opaque window even before the stylesheet's rounded card is drawn.
        # A translucent top-level QFrame can leave its entire body transparent on Windows.
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor('#fffaf7'))
        self.setPalette(palette)
        self.setAutoFillBackground(True)
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
            QFrame#updateToast { background: #fffaf7; border: 1px solid #e8d8cf; border-radius: 12px; }
            QLabel { color: #514b46; background: transparent; border: none; font-size: 9pt; }
            QLabel#updateToastTitle { color: ACCENT_TEXT; font-size: 10.5pt; font-weight: 600; }
            QPushButton { border-radius: 7px; padding: 7px 12px; font-size: 9pt; }
            QPushButton#updateToastLater { background: white; color: #514b46; border: 1px solid #d8cec7; }
            QPushButton#updateToastLater:hover { background: ACCENT_SOFT; }
            QPushButton#updateToastLater:pressed { background: #f2ded4; }
            QPushButton#updateToastProceed { background: ACCENT; color: #211b18; border: none; font-weight: 600; }
            QPushButton#updateToastProceed:hover { background: ACCENT_HOVER; color: white; }
            QPushButton#updateToastProceed:pressed { background: ACCENT_TEXT; color: white; }
            QPushButton#updateToastProceed:disabled { background: #e8ded8; color: #6b625b; }
            QPushButton:focus { border: 2px solid ACCENT_TEXT; }
        '''.replace('ACCENT_HOVER', ACCENT_HOVER).replace('ACCENT_TEXT', ACCENT_TEXT)
           .replace('ACCENT_SOFT', ACCENT_SOFT).replace('ACCENT', ACCENT))
        self.animation = QPropertyAnimation(self, b'pos', self)
        self.reminder = QTimer(self)
        self.reminder.setInterval(self.REMIND_INTERVAL)
        self.reminder.timeout.connect(self.hop)
        if parent is not None and not self.desktop_notification:
            parent.installEventFilter(self)
        if self.desktop_notification:
            # Taskbar moves and display hot-plugging must also reposition a waiting notice.
            self.position_timer = QTimer(self)
            self.position_timer.setInterval(2000)
            self.position_timer.timeout.connect(self.reposition)
        self.hide()

    def resting_pos(self):
        parent = self.parentWidget()
        if self.desktop_notification:
            screen = notification_screen(self.tray)
            if screen is None:
                return self.pos()
            area = screen.availableGeometry()
            return QPoint(max(area.left() + 8, area.right() - self.width() - 16),
                          max(area.top() + 8, area.bottom() - self.height() - 16))
        return QPoint(max(8, parent.width() - self.width() - self.MARGIN_RIGHT),
                      max(8, parent.height() - self.height() - self.MARGIN_BOTTOM))

    def reposition(self):
        if self.isVisible() and self.animation.state() != QPropertyAnimation.State.Running:
            self.move(self.resting_pos())

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
        if self.desktop_notification:
            self.position_timer.start()
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
        if self.desktop_notification:
            self.position_timer.stop()
        self.hide()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize and self.isVisible():
            self.animation.stop()
            self.move(self.resting_pos())
        return super().eventFilter(watched, event)
