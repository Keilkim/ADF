"""Real PDF preparation, permission preservation and cancelable UI loading."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pymupdf
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf.app import MainWindow
from adf.document import PasswordRequired, PdfDocument
from adf.loading import prepare_pdf
from adf.progress_widgets import TaskProgressDialog


class LoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='adf-load-test-')
        self.folder = Path(self.temp.name)
        self.source = self.folder/'input.pdf'
        with pymupdf.open() as doc:
            for i in range(3):
                page = doc.new_page(width=420+i*10, height=595)
                page.insert_text((30, 40), f'Loading page {i+1}')
            doc.save(self.source)
        self.old_hook = sys.excepthook
        self.errors = []
        sys.excepthook = lambda kind, error, tb: self.errors.append(error)

    def tearDown(self):
        sys.excepthook = self.old_hook
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def test_progress_is_measured_per_stage_and_never_advances_with_time(self):
        dialog = TaskProgressDialog('Test')
        dialog.show()
        dialog.update_progress(dict(stage='Pages', completed=7, total=31))
        self.assertEqual(dialog.bar.value(), 22)
        QTest.qWait(110)
        self.assertEqual(dialog.bar.value(), 22)
        dialog.update_progress(dict(stage='Model loading', detail='Unknown duration'))
        self.assertEqual((dialog.bar.minimum(), dialog.bar.maximum()), (0, 0))
        self.assertNotIn('%', dialog.count.text())
        dialog.update_progress(dict(stage='Pages', completed=31, total=31))
        self.assertEqual(dialog.bar.value(), 100)
        dialog.done(1)
        dialog.deleteLater()

    def test_worker_counts_actual_bytes_pages_and_preserves_original(self):
        updates = []
        original = self.source.read_bytes()
        task = dict(source=str(self.source), progress=str(self.folder/'progress.json'),
                    result=str(self.folder/'result.json'))
        with patch('adf.loading.ProgressWriter', return_value=lambda *args, **kw: updates.append((args, kw))):
            result = prepare_pdf(task)
        self.assertEqual(result['bytes'], len(original))
        self.assertEqual(Path(result['prepared']).read_bytes(), original)
        self.assertEqual(result['sizes'], [[420, 595], [430, 595], [440, 595]])
        page_updates = [args for args, kw in updates if args[0] == '페이지 확인']
        self.assertEqual([(args[1], args[2]) for args in page_updates], [(1, 3), (2, 3), (3, 3)])
        self.assertTrue((self.folder/'preview.png').read_bytes().startswith(b'\x89PNG'))
        self.assertEqual(self.source.read_bytes(), original)

    def test_prepared_document_keeps_password_permissions_and_size_invalidation(self):
        protected = self.folder/'protected.pdf'
        with pymupdf.open(self.source) as doc:
            doc.save(protected, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                     owner_pw='owner', user_pw='reader', permissions=pymupdf.PDF_PERM_PRINT)
        task = dict(source=str(protected), progress=str(self.folder/'progress.json'),
                    result=str(self.folder/'result.json'))
        with self.assertRaises(PasswordRequired):
            prepare_pdf(task)
        result = prepare_pdf(dict(task, password='reader'))
        with PdfDocument() as doc:
            doc.open_prepared(bytearray(Path(result['prepared']).read_bytes()), protected, 'reader', result['sizes'])
            self.assertFalse(doc.editable)
            self.assertFalse(doc.permissions & pymupdf.PDF_PERM_COPY)
        with PdfDocument() as doc:
            doc.open_prepared(self.source.read_bytes(), self.source, None, result['sizes'])
            self.assertEqual(doc.page_size(0), (420, 595))
            doc.rotate([0], 90)
            self.assertEqual(doc.page_size(0), (595, 420))

    def test_cancel_open_preserves_existing_dirty_document(self):
        window = MainWindow(smoke=True)
        try:
            self.assertTrue(window.open_path(self.source))
            window.document.rotate([0], 90)
            original_document = window.document
            original_bytes = original_document.doc.tobytes(no_new_id=True)
            cancel = QTimer(window)
            cancel.setInterval(10)
            ticks = []
            def stop_loading():
                ticks.append(1)
                if window.loading_dialog is not None:
                    window.loading_dialog.reject()
                    cancel.stop()
            cancel.timeout.connect(stop_loading)
            cancel.start()
            with patch.object(window, 'maybe_save', return_value=True), patch.object(window, 'error') as error:
                self.assertFalse(window.open_path(self.source))
                error.assert_not_called()
            self.assertTrue(ticks)
            self.assertIs(window.document, original_document)
            self.assertTrue(window.document.dirty)
            self.assertEqual(window.document.doc.tobytes(no_new_id=True), original_bytes)
            self.assertIsNone(window.loading_dialog)
        finally:
            for timer in window.findChildren(QTimer):
                timer.stop()
            with patch.object(window, 'maybe_save', return_value=True):
                window.close()
            window.deleteLater()
            self.app.processEvents()
