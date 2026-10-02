"""Desktop update cards stay opaque and follow the notification area's display."""
import os
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import QApplication, QWidget

from adf.update_toast import UpdateToast, notification_screen


def test_background_card_paints_an_opaque_brand_surface():
    app = QApplication.instance() or QApplication([])
    toast = UpdateToast()
    try:
        toast.present('XDF 새 버전 0.3.41', '업데이트를 설치할 준비가 됐습니다.', '업데이트')
        toast.animation.stop()
        app.processEvents()
        image = toast.grab().toImage()
        assert not toast.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        for x, y in [(16, 10), (160, 10), (160, image.height() - 10)]:
            color = image.pixelColor(x, y)
            assert color.alpha() == 255
            assert color.name() == '#fffaf7'
    finally:
        toast.dismiss()


def test_tray_display_takes_priority_over_primary_display():
    tray = Mock()
    tray.geometry.return_value = QRect(-160, 1030, 24, 24)
    screen = Mock()
    with patch('adf.update_toast.QGuiApplication') as gui:
        gui.screenAt.return_value = screen
        assert notification_screen(tray) is screen
        gui.screenAt.assert_called_once_with(tray.geometry().center())
        gui.primaryScreen.assert_not_called()


def test_hidden_tray_uses_taskbar_origin_with_friendly_monitor_names_and_mixed_dpi():
    tray = Mock()
    tray.geometry.return_value = QRect()
    primary, taskbar = Mock(), Mock()
    primary.name.return_value = 'Main monitor'
    primary.geometry.return_value = QRect(0, 0, 1920, 1080)
    taskbar.name.return_value = 'LG IPS FULLHD (2)'
    taskbar.geometry.return_value = QRect(-2560, 100, 1280, 720)
    taskbar.devicePixelRatio.return_value = 2.0
    with patch('adf.update_toast.QGuiApplication') as gui, \
            patch('adf.update_toast.windows_taskbar_origin', return_value=QPoint(-2560, 100)):
        gui.screens.return_value = [primary, taskbar]
        assert notification_screen(tray) is taskbar
        gui.screenAt.assert_not_called()
        gui.primaryScreen.assert_not_called()


def test_missing_taskbar_falls_back_to_primary_display():
    with patch('adf.update_toast.QGuiApplication') as gui, \
            patch('adf.update_toast.windows_taskbar_origin', return_value=None):
        assert notification_screen() is gui.primaryScreen.return_value


def test_windows_card_follows_taskbar_even_when_its_owner_is_on_another_display():
    app = QApplication.instance() or QApplication([])
    owner = QWidget()
    owner.setGeometry(500, 100, 900, 700)
    screen = Mock()
    screen.availableGeometry.return_value = QRect(-1600, 0, 1600, 850)
    with patch('adf.update_toast.sys.platform', 'win32'), \
            patch('adf.update_toast.notification_screen', return_value=screen):
        toast = UpdateToast(owner)
        try:
            toast.present('XDF 업데이트', '알림 위치 확인', '업데이트')
            toast.animation.stop()
            toast.reposition()
            area = screen.availableGeometry()
            assert toast.isWindow()
            assert toast.pos() == QPoint(area.right() - toast.width() - 16,
                                         area.bottom() - toast.height() - 16)
            # A waiting card must move after the taskbar/display configuration changes.
            screen.availableGeometry.return_value = QRect(2000, 100, 1000, 700)
            toast.reposition()
            assert toast.x() > 2000
            assert toast.position_timer.isActive()
            toast.dismiss()
            assert not toast.position_timer.isActive()
        finally:
            toast.dismiss()
            owner.close()
