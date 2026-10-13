"""Documents in tabs and windows: opening, tearing off, joining and closing."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf.app import MainWindow
from adf import workspace as documents


def pdf(path, text):
    with pymupdf.open() as doc:
        doc.new_page(width=300, height=400).insert_text((40, 60), text, fontsize=14)
        doc.save(path)
    return path


class WorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        from adf.theme import apply_theme
        apply_theme(cls.app)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='adf-workspace-test-')
        self.root = Path(self.directory.name)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.files = [pdf(self.root / f'{name}.pdf', name) for name in ('first', 'second', 'third')]
        self.workspace = documents.Workspace(lambda **options: MainWindow(smoke=True, **options),
                                             self.settings, smoke=True)
        self.first = self.workspace.start()
        self.app.processEvents()

    def tearDown(self):
        with patch.object(MainWindow, 'maybe_save', return_value=True):
            for frame in list(self.workspace.frames):
                frame.close()
        self.app.processEvents()
        self.directory.cleanup()

    def frame(self):
        self.assertEqual(len(self.workspace.frames), 1)
        return self.workspace.frames[0]

    def test_one_document_keeps_the_title_and_two_become_tabs(self):
        frame = self.frame()
        self.assertTrue(self.first.open_path(self.files[0]))
        self.assertIs(frame.current_page(), self.first)
        self.assertFalse(frame.title_bar.tabs.isVisible())
        self.assertIn('first.pdf', frame.windowTitle())
        # A second document opens beside it, in the same window.
        self.assertTrue(self.first.open_path(self.files[1]))
        self.assertEqual(len(frame.pages()), 2)
        second = frame.current_page()
        self.assertIsNot(second, self.first)
        self.assertEqual(Path(second.document.path).name, 'second.pdf')
        self.assertEqual(Path(self.first.document.path).name, 'first.pdf')
        self.assertTrue(frame.title_bar.tabs.isVisible())
        self.assertEqual([frame.title_bar.tabs.tabText(i) for i in range(2)], ['first.pdf', 'second.pdf'])
        self.assertIn('second.pdf', frame.windowTitle())
        # Only the visible tab's page is shown and owns the update service.
        self.assertTrue(second.isVisible())
        self.assertFalse(self.first.isVisible())
        self.assertIsNone(second.updates)
        # Opening the same file again shows its tab instead of a copy.
        frame.set_current(self.first)
        self.assertTrue(self.workspace.open_paths([str(self.files[1])], self.first))
        self.assertEqual(len(frame.pages()), 2)
        self.assertIs(frame.current_page(), second)

    def test_unsaved_mark_follows_the_tab(self):
        frame = self.frame()
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        second = frame.current_page()
        second.edit(lambda: second.document.rotate([0], 90))
        self.assertEqual(frame.title_bar.tabs.tabText(1), '● second.pdf')
        self.assertEqual(frame.title_bar.tabs.tabText(0), 'first.pdf')

    def test_shortcuts_reach_only_the_visible_tab(self):
        frame = self.frame()
        frame.show()
        frame.activateWindow()
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        second = frame.current_page()
        frame.windowHandle().requestActivate()
        self.assertTrue(QTest.qWaitForWindowActive(frame))
        QTest.keyClick(second.view, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(second.document.doc[0].rotation, 90)
        self.assertEqual(self.first.document.doc[0].rotation, 0)
        frame.cycle(1)
        self.assertIs(frame.current_page(), self.first)

    def test_window_mode_opens_each_document_in_its_own_window(self):
        self.settings.setValue(documents.OPEN_MODE, documents.WINDOW)
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        self.assertEqual(len(self.workspace.frames), 2)
        self.assertTrue(all(len(frame.pages()) == 1 for frame in self.workspace.frames))
        self.assertTrue(all(not frame.title_bar.tabs.isVisible() for frame in self.workspace.frames))

    def test_tab_tears_off_into_a_window_and_joins_back(self):
        frame = self.frame()
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        second = frame.current_page()
        window = self.workspace.detach(frame, second, None, False)
        self.assertEqual(len(self.workspace.frames), 2)
        self.assertEqual(frame.pages(), [self.first])
        self.assertEqual(window.pages(), [second])
        self.assertFalse(frame.title_bar.tabs.isVisible())
        self.assertTrue(second.document.page_count)
        self.workspace.merge(window, frame)
        self.app.processEvents()
        self.assertEqual(self.workspace.frames, [frame])
        self.assertEqual(frame.pages(), [self.first, second])
        self.assertIs(frame.current_page(), second)
        self.assertEqual(Path(second.document.path).name, 'second.pdf')

    def test_dragging_a_tab_off_the_strip_detaches_it(self):
        frame = self.frame()
        frame.show()
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        tabs = frame.title_bar.tabs
        self.app.processEvents()
        requests = []
        tabs.detachRequested.connect(requests.append)
        start = tabs.tabRect(1).center()
        QTest.mousePress(tabs, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
        QTest.mouseMove(tabs, start + QPoint(0, 80))
        self.assertEqual(requests, [1])
        self.assertEqual(len(self.workspace.frames), 2)

    def test_window_dropped_on_tabs_joins_them(self):
        frame = self.frame()
        frame.show()
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        self.first.open_path(self.files[2])
        third = frame.current_page()
        window = self.workspace.detach(frame, third, None, False)
        self.app.processEvents()
        tabs = frame.title_bar.tabs
        # Over the first tab's left half, a placeholder shows it would land first.
        left_of_first = tabs.mapToGlobal(tabs.tabRect(0).topLeft() + QPoint(5, 10))
        self.workspace.track_move(window, left_of_first)
        self.assertEqual(tabs.ghost, 0)
        self.assertEqual(tabs.tabText(0), 'third.pdf')
        self.assertEqual(frame.pages(), [self.first, frame.page_at(2)])
        self.assertLess(window.windowOpacity(), 1)
        # Past the last tab it moves to the end.
        end = tabs.mapToGlobal(tabs.tabRect(2).topRight() + QPoint(-3, 10))
        self.workspace.track_move(window, end)
        self.assertEqual(tabs.ghost, 2)
        # A move that ends elsewhere leaves both windows alone.
        self.workspace.finish_move(window, frame.title_bar.mapToGlobal(QPoint(0, 400)), True)
        self.app.processEvents()
        self.assertEqual(len(self.workspace.frames), 2)
        self.assertEqual(tabs.ghost, -1)
        self.assertEqual(tabs.count(), 2)
        self.assertEqual(window.windowOpacity(), 1)
        self.workspace.track_move(window, left_of_first)
        self.workspace.finish_move(window, left_of_first, True)
        self.app.processEvents()
        self.assertEqual(len(self.workspace.frames), 1)
        self.assertEqual(frame.pages()[0], third)
        self.assertEqual(tabs.count(), 3)

    def test_placeholder_turns_a_single_window_title_into_tabs(self):
        frame = self.frame()
        frame.show()
        self.first.open_path(self.files[0])
        other = self.workspace.new_window()
        other.open_path(self.files[1])
        # On Windows a single document's window keeps a title bar of XDF's own.
        frame.custom = True
        frame.update_chrome()
        self.assertFalse(frame.title_bar.tabs.isVisible())
        point = frame.title_bar.mapToGlobal(QPoint(300, 15))
        self.workspace.track_move(self.workspace.frame_of(other), point)
        self.assertTrue(frame.title_bar.tabs.isVisible())
        self.assertEqual(frame.title_bar.tabs.tabText(1), 'second.pdf')
        self.workspace.finish_move(self.workspace.frame_of(other), QPoint(-5000, -5000), True)
        self.assertFalse(frame.title_bar.tabs.isVisible())

    def test_closing_a_tab_keeps_the_others_and_hands_over_updates(self):
        frame = self.frame()
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        second = frame.current_page()
        from unittest.mock import Mock
        service = self.first.updates = Mock()
        with patch.object(MainWindow, 'start_updates') as start:
            frame.close_page(self.first)
        service.shutdown.assert_called_once()
        start.assert_called_once()
        self.assertEqual(frame.pages(), [second])
        self.assertTrue(frame.isVisible())
        # Closing the last document of a window leaves its empty start page.
        second.close_document()
        self.assertEqual(frame.pages(), [second])
        self.assertFalse(second.document.page_count)

    def test_close_document_closes_its_tab(self):
        frame = self.frame()
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        second = frame.current_page()
        second.close_document()
        self.assertEqual(frame.pages(), [self.first])

    def test_unsaved_changes_keep_the_window_open(self):
        frame = self.frame()
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        with patch.object(MainWindow, 'maybe_save', return_value=False):
            self.assertFalse(frame.close())
        self.assertEqual(len(frame.pages()), 2)

    def test_files_dropped_on_the_title_bar_open_as_tabs(self):
        frame = self.frame()
        self.first.open_path(self.files[0])
        self.workspace.open_paths([str(self.files[1]), str(self.files[2])], frame.current_page(), documents.TAB)
        self.assertEqual([Path(page.document.path).name for page in frame.pages()],
                         ['first.pdf', 'second.pdf', 'third.pdf'])

    def test_failed_open_leaves_no_empty_tab(self):
        frame = self.frame()
        self.first.open_path(self.files[0])
        broken = self.root / 'broken.pdf'
        broken.write_bytes(b'not a pdf')
        with patch.object(MainWindow, 'error'):
            self.assertFalse(self.first.open_path(broken))
        self.app.processEvents()
        self.assertEqual(frame.pages(), [self.first])

    def test_title_bar_drags_except_on_tabs_and_buttons(self):
        frame = self.frame()
        frame.resize(1200, 800)
        self.first.open_path(self.files[0])
        self.first.open_path(self.files[1])
        bar = frame.title_bar
        self.app.processEvents()
        tab = bar.tabs.tabRect(0).center()
        self.assertFalse(bar.is_caption(bar.tabs.mapTo(bar, tab)))
        self.assertTrue(bar.is_caption(QPoint(bar.width() - 400, 10)))

    def test_forwarded_launch_opens_in_the_running_workspace(self):
        with patch.object(documents, 'server_name', return_value=f'xdf-test-{os.getpid()}'):
            self.workspace.listen()
            self.assertIsNotNone(self.workspace.server)
            self.first.open_path(self.files[0])
            # The second launch blocks on its socket; serve it from a helper thread's view.
            from PySide6.QtCore import QThread

            class Launch(QThread):
                result = None

                def run(inner):
                    inner.result = documents.forward_to_running([str(self.files[1])])

            launch = Launch()
            launch.start()
            for _ in range(200):
                self.app.processEvents()
                if launch.isFinished():
                    break
                QTest.qWait(10)
            launch.wait(3000)
            QTest.qWait(50)
            self.assertTrue(launch.result)
            self.assertEqual(len(self.frame().pages()), 2)
            self.workspace.server.server.close()


if __name__ == '__main__':
    unittest.main()
