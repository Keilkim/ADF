"""XDF in the background: a tray icon that keeps an eye on updates while no window is open.

Windows starts it at sign-in with --background (the Run key below). It has no
document window: its icon sits at the bottom right of the taskbar, a click
opens XDF and a right click opens its menu. When a new version waits it drops
the update notice at the corner of the screen.

It owns a hidden window titled TITLE so that XDF and the installer find it and
close it with WM_CLOSE before replacing XDF's files.
"""
from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QSettings, QStandardPaths, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

TITLE = 'ADF Background Agent'
RUN_KEY = r'HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'ADF'
WM_CLOSE = 0x0010


def supported():
    """Only an installed Windows XDF runs in the background; it is what updates itself."""
    return (sys.platform == 'win32' and getattr(sys, 'frozen', False)
            and (Path(sys.executable).parent/'unins000.exe').is_file())


def _find():
    if sys.platform != 'win32':
        return 0
    import ctypes
    # Qt's Windows window title includes QApplication.applicationDisplayName,
    # although QWidget.windowTitle() still returns TITLE without that suffix.
    for title in (TITLE, TITLE + ' - XDF'):
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd:
            return hwnd
    return 0


def running():
    return bool(_find())


def stop():
    """Ask the background agent to quit, as before an update replaces XDF's files."""
    hwnd = _find()
    if hwnd:
        import ctypes
        ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    return bool(hwnd)


def enabled(settings):
    return settings.value('background/enabled', True, type=bool)


def set_autostart(on):
    run = QSettings(RUN_KEY, QSettings.Format.NativeFormat)
    if on:
        run.setValue(RUN_VALUE, f'"{sys.executable}" --background')
    else:
        run.remove(RUN_VALUE)
    run.sync()


def start():
    QProcess.startDetached(sys.executable, ['--background'])


def ensure(settings):
    """Keep sign-in start matching the setting, and start the agent when it should run."""
    if not supported():
        return
    on = enabled(settings)
    set_autostart(on)
    if on and not running():
        start()


def open_adf(*arguments):
    QProcess.startDetached(sys.executable, list(arguments))


class Agent(QWidget):
    def __init__(self, settings, service=None):
        super().__init__()
        from .update_toast import UpdateToast
        self.settings = settings
        self.setWindowTitle(TITLE)
        self.winId()  # A native, never shown window that WM_CLOSE reaches.
        icon = QIcon(str(Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))/'assets/xdf.ico'))
        self.install_when_ready = False
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip('XDF')
        self.tray.activated.connect(self.activated)
        self.menu = QMenu()
        self.menu.addAction('XDF 열기', lambda: open_adf())
        self.menu.addAction('지금 업데이트 확인', self.check_now)
        self.menu.addSeparator()
        self.autostart = self.menu.addAction('로그인할 때 자동 실행', self.toggle_autostart)
        self.autostart.setCheckable(True)
        self.autostart.setChecked(enabled(settings))
        self.auto_update = self.menu.addAction('업데이트 자동 확인', self.toggle_auto_update)
        self.auto_update.setCheckable(True)
        self.menu.addSeparator()
        self.menu.addAction('백그라운드 실행 끝내기', QApplication.instance().quit)
        self.menu.aboutToShow.connect(self.sync_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.show()
        self.toast = UpdateToast()
        self.toast.updateRequested.connect(self.update_requested)
        self.toast.laterRequested.connect(self.later)
        self.snooze_timer = QTimer(self)
        self.snooze_timer.setSingleShot(True)
        self.snooze_timer.timeout.connect(self.refresh)
        # Waking the PC does not advance timers; a regular look catches 9 o'clock and stale checks.
        self.clock = QTimer(self)
        self.clock.setInterval(5 * 60 * 1000)
        self.clock.timeout.connect(self.tick)
        self.clock.start()
        if service is None:
            from .updates import UpdateService
            folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))/'updates'
            service = UpdateService(settings, folder, parent=self)
        self.updates = service
        service.changed.connect(self.refresh)
        service.start()
        self.refresh()

    def sync_menu(self):
        self.settings.sync()
        self.autostart.setChecked(enabled(self.settings))
        self.auto_update.setChecked(self.updates.enabled)

    def activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            open_adf()

    def check_now(self):
        self.updates.start()
        self.updates.check()
        self.tray.showMessage('XDF', '새 버전이 있는지 확인하고 있습니다.', self.tray.icon(), 3000)

    def toggle_autostart(self):
        checked = self.autostart.isChecked()
        self.settings.setValue('background/enabled', checked)
        self.settings.sync()
        if supported():
            set_autostart(checked)

    def toggle_auto_update(self):
        self.updates.set_enabled(self.auto_update.isChecked())

    def tick(self):
        self.updates.check_if_stale()
        self.refresh()

    def refresh(self):
        from .update_toast import snooze_remaining, toast_content
        service = self.updates
        if self.install_when_ready and service.state == 'ready':
            self.install_when_ready = False
            self.toast.dismiss()
            self.install()
            return
        if self.install_when_ready and service.state not in ('available', 'downloading'):
            self.install_when_ready = False
        content = toast_content(service, following=self.install_when_ready)
        remaining = snooze_remaining(self.settings)
        if remaining > 0 and not self.install_when_ready:
            self.snooze_timer.start(min(int(remaining * 1000) + 1000, 2**31 - 1))
            content = None
        if content is None:
            self.toast.dismiss()
            self.tray.setToolTip('XDF')
        else:
            self.toast.present(*content)
            if self.settings.value('updates/background_notified', '') != service.package.version:
                self.settings.setValue('updates/background_notified', service.package.version)
                self.settings.sync()
            self.tray.setToolTip('XDF · ' + content[0])

    def update_requested(self):
        service = self.updates
        if service.state == 'manual':
            from .updates import RELEASES
            QDesktopServices.openUrl(QUrl(f'{RELEASES}/tag/v{service.package.version}'))
            self.toast.dismiss()
        elif service.state == 'ready':
            self.toast.dismiss()
            self.install()
        elif service.state in ('available', 'downloading'):
            self.install_when_ready = True
            if service.state == 'available':
                service.requested = True
                service.download()
            self.refresh()

    def install(self):
        """XDF installs from a window: it asks about unsaved work and closes the other windows first."""
        open_adf('--update-now')

    def later(self):
        from .update_toast import snooze
        snooze(self.settings)
        self.toast.dismiss()
        self.refresh()
        self.tray.showMessage('XDF', '내일 아침 9시에 다시 알려 드립니다.', self.tray.icon(), 3000)

    def closeEvent(self, event):
        # XDF or the installer asked the agent to quit.
        self.updates.shutdown()
        self.tray.hide()
        QApplication.instance().quit()
        event.accept()


def main():
    app = QApplication(sys.argv[:1])
    app.setApplicationName('ADF')
    app.setOrganizationName('ADF')
    app.setApplicationDisplayName('XDF')
    app.setQuitOnLastWindowClosed(False)
    if running():
        return 0
    app.setFont(QFont('Malgun Gothic' if sys.platform == 'win32' else 'Apple SD Gothic Neo', 10))
    agent = Agent(QSettings('ADF', 'ADF'))
    app.aboutToQuit.connect(agent.updates.shutdown)
    return app.exec()
