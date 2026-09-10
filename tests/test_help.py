"""Offline help navigation, original notices and installed source discovery."""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTabWidget, QProgressDialog

from adf import __version__
from adf.app import MainWindow
from adf.help_widgets import HelpDialog

ROOT = Path(__file__).resolve().parents[1]


class HelpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.old_hook = sys.excepthook
        self.errors = []
        sys.excepthook = lambda kind, error, tb: self.errors.append(error)
        self.window = MainWindow(smoke=True)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        sys.excepthook = self.old_hook
        self.assertEqual(self.errors, [])

    def test_offline_search_original_license_and_independent_windows(self):
        with patch('adf.help_widgets.QDesktopServices.openUrl') as external:
            self.window.actions['help'].trigger()
            dialog = self.window.help_dialogs['guide']
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog.page.topics.currentItem().text(), '처음 시작하기')
            dialog.page.search.setText('전체화면')
            self.assertIn('F11', dialog.page.browser.toPlainText())
            self.assertIn('슬라이드', dialog.page.browser.toPlainText())
            dialog.page.search.setText('unmatched help query')
            self.assertIn('검색 결과가 없습니다', dialog.page.browser.toPlainText())
            dialog.page.search.clear()
            self.assertTrue(all(not dialog.page.topics.item(i).isHidden() for i in range(dialog.page.topics.count())))
            dialog.page.topics.setCurrentRow(0)
            self.assertIn('시작 메뉴', dialog.page.browser.toPlainText())
            dialog.page.search.setText('전체화면')
            self.window.actions['licenses'].trigger()
            licenses = self.window.help_dialogs['licenses']
            self.assertIsNot(licenses, dialog)
            self.assertIn('보증 없이', licenses.page.browser.toPlainText())
            licenses.page.open_link(QUrl('AGPL-3.0.txt'))
            original = (ROOT/'LICENSES/AGPL-3.0.txt').read_text(encoding='utf-8')
            self.assertEqual(licenses.page.browser.toPlainText().strip(), original.strip())
            external.assert_not_called()
            self.window.actions['sources'].trigger()
            sources = self.window.help_dialogs['sources']
            licenses.page.open_link(QUrl('../SOURCES/'))
            self.assertIs(self.window.help_dialogs['sources'], sources)
            external.assert_not_called()
            self.window.actions['help'].trigger()
            self.assertIs(self.window.help_dialogs['guide'], dialog)
            self.assertEqual(dialog.page.search.text(), '전체화면')
            for item, title in [(dialog, '사용 안내'), (licenses, '오픈소스 라이선스'), (sources, '소스코드')]:
                self.assertTrue(item.isVisible())
                self.assertIn(title, item.windowTitle())
                self.assertEqual(item.findChildren(QTabWidget), [])
            QTest.qWait(20)
            QTest.keyClick(sources, Qt.Key.Key_Escape)
            self.assertNotIn('sources', self.window.help_dialogs)
            self.assertTrue(dialog.isVisible() and licenses.isVisible())
            self.window.close()
            self.assertEqual(self.window.help_dialogs, {})

    def test_installed_sources_are_local_and_missing_sources_are_not_promised(self):
        with tempfile.TemporaryDirectory(prefix='adf-help-') as directory:
            root = Path(directory)
            (root/'docs').mkdir()
            (root/'LICENSES').mkdir()
            shutil.copyfile(ROOT/'docs/사용안내.html', root/'docs/사용안내.html')
            shutil.copyfile(ROOT/'LICENSES/README.md', root/'LICENSES/README.md')
            missing = HelpDialog(root, 'sources', self.window)
            self.assertFalse(missing.sources_available)
            self.assertIsNone(missing.source_browser)
            missing.deleteLater()
            (root/'SOURCES').mkdir()
            for prefix in ('ADF-Source', 'ADF-ThirdParty-Sources'):
                with zipfile.ZipFile(root/f'SOURCES/{prefix}-{__version__}.zip', 'w') as archive:
                    archive.writestr('ADF/main.py', 'print("로컬 소스")\n')
            installed = HelpDialog(root, 'sources', self.window)
            self.assertTrue(installed.sources_available)
            with patch('adf.help_widgets.QDesktopServices.openUrl') as external:
                installed.show()
                browser = installed.source_browser
                item = browser.tree.findItems('main.py', Qt.MatchFlag.MatchExactly | Qt.MatchFlag.MatchRecursive)[0]
                browser.tree.setCurrentItem(item)
                self.assertEqual(browser.preview.toPlainText(), 'print("로컬 소스")\n')
                saved = root/'saved.py'
                with patch('adf.source_widgets.QFileDialog.getSaveFileName', return_value=(str(saved), '')):
                    browser.save_button.click()
                self.assertEqual(saved.read_text(encoding='utf-8'), 'print("로컬 소스")\n')
                self.assertIn('저장했습니다', browser.status.text())
                external.assert_not_called()
                installed.close()
            installed.deleteLater()
            self.app.processEvents()

    def test_intro_opens_usable_help_and_app_close_closes_help(self):
        self.window.show_intro(first_run=True)
        intro = self.window.intro_dialog
        intro.help_button.click()
        self.assertIsNone(self.window.intro_dialog)
        self.assertTrue(self.window.help_dialogs['guide'].isVisible())
        self.assertIsNone(QApplication.activeModalWidget())
        self.window.close()
        self.assertEqual(self.window.help_dialogs, {})

    def test_first_run_tour_skip_persists_and_help_replays_every_step(self):
        from PySide6.QtCore import QSettings
        from adf.intro_widgets import STEPS
        original = self.window.settings
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/'first-run.ini')
            self.window.settings = QSettings(path, QSettings.Format.IniFormat)
            try:
                self.window.show_first_run_intro()
                tour = self.window.intro_dialog
                self.assertTrue(tour.isVisible())
                self.assertTrue(tour.highlights)
                tour.skip_button.click()
                self.assertIsNone(self.window.intro_dialog)
                self.window.settings = QSettings(path, QSettings.Format.IniFormat)
                self.window.show_first_run_intro()
                self.assertIsNone(self.window.intro_dialog)
                self.window.actions['intro'].trigger()
                tour = self.window.intro_dialog
                for index in range(len(STEPS)):
                    self.assertEqual(tour.index, index)
                    self.assertTrue(tour.highlights)
                    self.assertTrue(tour.rect().contains(tour.panel.geometry()))
                    if index:
                        tour.back_button.click()
                        self.assertEqual(tour.index, index-1)
                        tour.start_button.click()
                        self.assertEqual(tour.index, index)
                    tour.start_button.click()
                self.assertIsNone(self.window.intro_dialog)
                self.window.show_first_run_intro()
                self.assertIsNone(self.window.intro_dialog)
            finally:
                self.window.settings = original

    def test_tour_repositions_with_window_and_escape_or_close_exits(self):
        self.window.actions['intro'].trigger()
        tour = self.window.intro_dialog
        self.window.resize(1020, 700)
        self.app.processEvents()
        self.assertEqual(tour.size(), self.window.size())
        self.assertTrue(tour.rect().contains(tour.panel.geometry()))
        self.assertTrue(all(tour.rect().contains(r.toRect()) for r in tour.highlights))
        QTest.keyClick(tour, Qt.Key.Key_Escape)
        self.assertIsNone(self.window.intro_dialog)
        self.window.actions['intro'].trigger()
        self.window.close()
        self.assertIsNone(self.window.intro_dialog)

    def test_cancel_source_copy_preserves_existing_file(self):
        from adf.source_widgets import SourceBrowser
        with tempfile.TemporaryDirectory(prefix='adf-source-copy-') as directory:
            root = Path(directory)
            with zipfile.ZipFile(root/'source.zip', 'w') as archive:
                archive.writestr('original.txt', b'a'*(2*1024*1024))
            browser = SourceBrowser(root, ['source.zip'], self.window)
            destination = root/'keep.zip'
            destination.write_bytes(b'existing user file')
            canceled = []
            def cancel():
                dialog = QApplication.activeModalWidget()
                self.assertIsInstance(dialog, QProgressDialog)
                dialog.cancel()
                canceled.append(True)
            with patch('adf.source_widgets.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
                QTimer.singleShot(0, cancel)
                browser.save_button.click()
            self.assertTrue(canceled)
            self.assertEqual(destination.read_bytes(), b'existing user file')
            self.assertIn('취소', browser.status.text())
            browser.deleteLater()
