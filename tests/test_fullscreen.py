"""Fullscreen reading, edge controls, and restoration of the working layout."""
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pymupdf
from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf.app import MainWindow


class FullscreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle('Fusion')
        from adf.theme import STYLE
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        animation = patch('adf.fullscreen.motion_enabled', return_value=True)
        animation.start()
        self.addCleanup(animation.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='adf-fullscreen-')
        self.source = Path(self.temp.name)/'reader.pdf'
        with pymupdf.open() as doc:
            for i in range(8):
                doc.new_page(width=360, height=480).insert_text((40, 60), f'Read page {i+1}')
            doc.save(self.source)
        self.original = self.source.read_bytes()
        self.window = MainWindow(smoke=True)
        self.errors = []
        self.callback_errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, error, tb: self.callback_errors.append(error)
        self.window.error = lambda error: self.errors.append(str(error))
        self.window.show()
        self.window.open_path(self.source)
        self.window.set_view_mode('single')
        self.app.processEvents()
        self.full = self.window.fullscreen

    def tearDown(self):
        self.full.exit()
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        with patch.object(self.window, 'maybe_save', return_value=True):
            self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        sys.excepthook = self.old_hook
        self.assertEqual(self.source.read_bytes(), self.original)
        self.temp.cleanup()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.callback_errors, [])

    def move(self, point):
        view = self.window.view
        QTest.mouseMove(view.viewport(), point)
        self.app.processEvents()

    def enter(self):
        self.window.actions['fullscreen'].trigger()
        QTest.qWait(60)
        self.assertTrue(self.full.active)
        self.move(self.window.view.viewport().rect().center())

    def test_button_enters_document_only_screen_and_escape_restores(self):
        window = self.window
        row = window.docbar.layout()
        self.assertGreater(row.indexOf(window.fullscreen_button), row.indexOf(window.view_buttons['grid']))
        window.view.goto(3)
        before = window.geometry()
        sizes = window.reader_splitter.sizes()
        QTest.mouseClick(window.fullscreen_button, Qt.MouseButton.LeftButton)
        QTest.qWait(80)
        self.assertTrue(window.isFullScreen())
        self.assertTrue(self.full.active)
        self.assertEqual(window.geometry().size(), window.screen().geometry().size())
        self.assertEqual(window.view.size(), window.size())
        self.assertEqual(window.view.viewport().size(), window.size())
        for widget in (window.menuBar(), window.toolbar, window.sidebar, window.docbar,
                       window.page_nav, window.statusBar(), window.page_sidebar.toggle):
            self.assertFalse(widget.isVisible(), widget.objectName())
        QTest.keyClick(window.view.viewport(), Qt.Key.Key_Escape)
        QTest.qWait(60)
        self.assertFalse(window.isFullScreen())
        self.assertFalse(self.full.active)
        self.assertEqual(window.geometry(), before)
        self.assertEqual(window.reader_splitter.sizes(), sizes)
        self.assertEqual(window.current, 3)
        self.assertTrue(window.toolbar.isVisible())
        self.assertTrue(window.sidebar.isVisible())
        self.assertTrue(window.page_nav.isVisible())

    def test_each_edge_slides_independently_without_resizing_pdf(self):
        self.enter()
        view = self.window.view
        bounds, scale = view.geometry(), view.transform().m11()
        edges = {'top': QPoint(view.width()//2, 1), 'left': QPoint(1, view.height()//2),
                 'bottom': QPoint(view.width()//2, view.height()-2)}
        for edge, point in edges.items():
            with self.subTest(edge=edge):
                self.move(point)
                QTest.qWait(230)
                panel = self.full.panels[edge]
                self.assertTrue(panel.isVisible())
                self.assertAlmostEqual(panel.progress, 1.)
                self.assertTrue(all(other.isHidden() for name, other in self.full.panels.items() if name != edge))
                self.assertEqual(view.geometry(), bounds)
                self.assertEqual(view.transform().m11(), scale)
                self.move(view.viewport().rect().center())
                QTest.qWait(550)
                self.assertTrue(panel.isHidden())
                self.assertEqual(view.geometry(), bounds)
        # Reverse a closing animation from its current position.
        self.move(edges['bottom'])
        QTest.qWait(220)
        self.move(view.viewport().rect().center())
        QTest.qWait(280)
        panel = self.full.panels['bottom']
        self.assertGreater(panel.progress, 0)
        self.assertLess(panel.progress, 1)
        self.move(edges['bottom'])
        QTest.qWait(230)
        self.assertTrue(panel.opened)
        self.assertAlmostEqual(panel.progress, 1.)

    def test_navigation_popup_and_keyboard_remain_usable(self):
        self.enter()
        window, view = self.window, self.window.view
        self.move(QPoint(view.width()//2, view.height()-2))
        QTest.qWait(220)
        edit = window.page_spin.lineEdit()
        QTest.mouseClick(edit, Qt.MouseButton.LeftButton)
        edit.selectAll()
        QTest.keyClicks(edit, '5')
        QTest.keyClick(edit, Qt.Key.Key_Return)
        self.assertEqual(window.current, 4)
        self.move(view.viewport().rect().center())
        QTest.qWait(550)
        self.assertTrue(self.full.panels['bottom'].isVisible())
        view.setFocus()
        self.move(QPoint(view.width()//2, 1))
        QTest.qWait(220)
        window.zoom.showPopup()
        self.move(view.viewport().rect().center())
        QTest.qWait(550)
        self.assertTrue(self.full.panels['top'].isVisible())
        window.zoom.hidePopup()
        view.setFocus()
        QTest.qWait(550)
        self.assertTrue(self.full.panels['top'].isHidden())
        window.show_search()
        QTest.qWait(220)
        self.assertTrue(window.search_input.isVisible())
        QTest.keyClick(window.search_input, Qt.Key.Key_Escape)
        self.assertFalse(self.full.active)
        self.assertEqual(window.current, 4)

    def test_double_click_exits_and_collapsed_sidebar_and_zoom_restore(self):
        window = self.window
        window.page_sidebar.set_expanded(False)
        window.view.set_zoom(1.25)
        self.enter()
        self.move(QPoint(1, window.view.height()//2))
        QTest.qWait(220)
        self.assertTrue(window.sidebar.isVisible())
        item = window.thumbnails.item(1)
        QTest.mouseClick(window.thumbnails.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.thumbnails.visualItemRect(item).center())
        self.assertEqual(window.current, 1)
        self.assertTrue(window.sidebar.isVisible())
        self.move(window.view.viewport().rect().center())
        window.view.goto(6)
        QTest.mouseDClick(window.view.viewport(), Qt.MouseButton.LeftButton,
                         pos=window.view.viewport().rect().center())
        QTest.mouseRelease(window.view.viewport(), Qt.MouseButton.LeftButton,
                           pos=window.view.viewport().rect().center())
        QTest.qWait(60)
        self.assertFalse(self.full.active)
        self.assertFalse(window.page_sidebar.expanded)
        self.assertTrue(window.sidebar.isHidden())
        self.assertEqual(window.view.transform().m11(), 1.25)
        self.assertEqual(window.current, 6)
        self.enter()
        window.close_document()
        self.assertFalse(self.full.active)
        self.assertTrue(window.empty_workspace.isVisible())

    def test_maximized_window_and_visible_dock_restore_on_f11(self):
        window = self.window
        window.showMaximized()
        window.show_stamps()
        QTest.qWait(40)
        self.enter()
        self.assertFalse(window.stamp_dock.isVisible())
        QTest.keyClick(window.view.viewport(), Qt.Key.Key_F11)
        QTest.qWait(60)
        self.assertFalse(self.full.active)
        self.assertTrue(window.isMaximized())
        self.assertTrue(window.stamp_dock.isVisible())
