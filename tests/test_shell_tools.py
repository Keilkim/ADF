"""Explorer selection handoff and independent PDF tool window integration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
from PySide6.QtTest import QTest

from adf.app import MainWindow
from adf.document import merge_pdfs
from adf.shell_request import read_shell_request
from adf.theme import STYLE
from adf.tool_window import ToolWindowController


class ShellToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='adf-shell-tool-test-')
        self.root = Path(self.temporary.name)
        self.requests = self.root/'ShellRequests'
        self.requests.mkdir()
        self.first = self.root/"한글 & quote's 첫 문서.pdf"
        self.second = self.root/'두 번째.PDF'
        for filename, label in ((self.first, 'first'), (self.second, 'second')):
            with pymupdf.open() as document:
                for i in range(3):
                    document.new_page().insert_text((40, 60), f'{label}-{i+1}')
                document.set_toc([[1, 'Parent', 1], [2, 'Child', 3]])
                document.save(filename)
        self.originals = {p: p.read_bytes() for p in (self.first, self.second)}
        self.controller = None
        self.errors = []
        self.warning = patch.object(QMessageBox, 'warning', side_effect=lambda *args: self.errors.append(str(args[-1])))
        self.warning.start()

    def tearDown(self):
        if self.controller:
            if self.controller.worker:
                self.controller.cancel_worker()
                self.wait_finished()
            self.controller.close_tool()
            if self.controller.dialog:
                self.controller.dialog.deleteLater()
            self.controller.deleteLater()
            self.app.processEvents()
        self.warning.stop()
        self.temporary.cleanup()

    def request(self, operation, files):
        path = self.requests/f'request-{uuid.uuid4()}.json'
        path.write_text(json.dumps({'operation': operation, 'files': [str(p) for p in files]}, ensure_ascii=False), encoding='utf-8')
        return path

    def read_request(self, path):
        with patch('adf.shell_request.request_directory', return_value=self.requests):
            return read_shell_request(path)

    def start(self, operation, files):
        self.controller = ToolWindowController(operation, [str(p) for p in files])
        self.controller.show_tool()
        self.app.processEvents()
        return self.controller

    def wait_finished(self):
        deadline = time.monotonic() + 15
        while self.controller.worker and time.monotonic() < deadline:
            QTest.qWait(15)
        self.assertIsNone(self.controller.worker, 'Export process did not finish')

    def test_request_preserves_all_unicode_paths_order_and_is_consumed(self):
        path = self.request('merge', [self.second, self.first])
        self.assertEqual(self.read_request(path), ('merge', [str(self.second), str(self.first)]))
        self.assertFalse(path.exists())

    def test_selection_rules_reject_multiple_split_single_merge_and_nonpdf(self):
        text = self.root/'other.txt'
        text.write_text('not a PDF', encoding='utf-8')
        for operation, files in [('split', [self.first, self.second]), ('merge', [self.first]), ('merge', [self.first, text])]:
            with self.subTest(operation=operation, files=files):
                path = self.request(operation, files)
                with self.assertRaises(ValueError):
                    self.read_request(path)
                self.assertFalse(path.exists())

    def test_request_outside_private_folder_is_never_read_or_deleted(self):
        path = self.root/f'request-{uuid.uuid4()}.json'
        path.write_text('keep me', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.read_request(path)
        self.assertEqual(path.read_text(encoding='utf-8'), 'keep me')

    def test_oversized_and_malformed_requests_are_not_executed(self):
        path = self.request('merge', [self.first, self.second])
        path.write_text('{invalid JSON', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.read_request(path)
        self.assertFalse(path.exists())
        path = self.request('merge', [self.first, self.second])
        with patch('adf.shell_request.MAX_REQUEST_BYTES', 12):
            with self.assertRaises(ValueError):
                self.read_request(path)
        self.assertTrue(path.exists())

    def test_merge_window_is_standalone_and_save_cancel_keeps_selection(self):
        controller = self.start('merge', [self.second, self.first])
        self.assertTrue(controller.dialog.isVisible())
        self.assertFalse(any(isinstance(w, MainWindow) and w.isVisible() for w in QApplication.topLevelWidgets()))
        with patch.object(QFileDialog, 'getSaveFileName', return_value=('', '')):
            controller.dialog.accept()
        self.assertTrue(controller.dialog.isVisible())
        self.assertEqual(controller.dialog.paths, [str(self.second.resolve()), str(self.first.resolve())])
        self.assertIsNone(controller.worker)

    def test_merge_selected_pages_and_duplicate_files_preserve_order_via_worker(self):
        controller = self.start('merge', [self.first, self.second, self.first])
        from PySide6.QtCore import Qt
        for row, selection in enumerate(([2], [1, 0], [0])):
            controller.dialog.file_list.item(row).setData(0, Qt.ItemDataRole.UserRole+3, selection)
        output = self.root/'선택 병합.pdf'
        with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(output), '')):
            controller.dialog.accept()
            self.wait_finished()
        self.assertFalse(self.errors, self.errors)
        with pymupdf.open(output) as document:
            self.assertEqual([p.get_text().strip() for p in document], ['first-3', 'second-2', 'second-1', 'first-1'])
            self.assertTrue(document.get_toc())
        self.assertTrue(controller.dialog.isVisible())
        self.assertIn('병합 완료', controller.result_label.text())
        for path, original in self.originals.items():
            self.assertEqual(path.read_bytes(), original)

    def test_standalone_split_saves_ranges_without_opening_reader(self):
        controller = self.start('split', [self.first])
        controller.dialog.mode.setCurrentIndex(1)
        controller.dialog.range_input.setText('1, 2-3')
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(self.root)):
            controller.dialog.accept()
            self.wait_finished()
        self.assertFalse(self.errors, self.errors)
        self.assertEqual(len(controller.result_paths), 2)
        for path, expected in zip(controller.result_paths, [1, 2]):
            with pymupdf.open(path) as document:
                self.assertEqual(document.page_count, expected)
        self.assertFalse(any(isinstance(w, MainWindow) and w.isVisible() for w in QApplication.topLevelWidgets()))

    def test_invalid_merge_range_does_not_create_output(self):
        output = self.root/'invalid.pdf'
        with self.assertRaises(ValueError):
            merge_pdfs([self.first, self.second], output, selected_ranges=[[100], None])
        self.assertFalse(output.exists())

    def test_cli_merge_and_split_start_only_their_tool_windows(self):
        base = Path(__file__).resolve().parents[1]
        for operation, paths in [('merge', [self.second, self.first]), ('split', [self.first])]:
            with self.subTest(operation=operation):
                report = self.root/f'{operation}.json'
                process = subprocess.run([sys.executable, str(base/'main.py'), f'--{operation}', *map(str, paths), '--tool-smoke-test', str(report)],
                    cwd=base, capture_output=True, timeout=20)
                self.assertEqual(process.returncode, 0, process.stderr.decode(errors='replace'))
                result = json.loads(report.read_text(encoding='utf-8'))
                self.assertTrue(result['ok'], result)
                self.assertTrue(result['standalone'])
                self.assertFalse(result['main_window_visible'])
                self.assertEqual(result['files'], list(map(str, paths)))


if __name__ == '__main__':
    unittest.main()
