"""System notifications for updates that are waiting to be installed."""
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class UpdateNotifier(QObject):
    """Notify through the system, which queues ADF's notice with other apps' notices.

    Windows shows it at the bottom right and keeps it in the notification centre.
    The tray icon exists only while an update waits, so the notice stays clickable.
    """
    clicked = Signal()

    def __init__(self, icon, parent=None):
        super().__init__(parent)
        self.icon = icon
        self.tray = None
        self.menu = None
        self.primary = None

    def show(self, title, text, action):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return False
        if self.tray is None:
            self.tray = QSystemTrayIcon(self.icon, self)
            self.tray.messageClicked.connect(self.clicked)
            self.tray.activated.connect(self.activated)
            self.menu = QMenu()
            self.primary = self.menu.addAction(action, self.clicked.emit)
            self.menu.addAction('알림 아이콘 숨기기', self.hide)
            self.tray.setContextMenu(self.menu)
        self.primary.setText(action)
        self.tray.setToolTip('ADF · '+title)
        self.tray.show()
        self.tray.showMessage(title, text, self.icon, 15000)
        return True

    def activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.clicked.emit()

    def hide(self):
        if self.tray is not None:
            self.tray.hide()
