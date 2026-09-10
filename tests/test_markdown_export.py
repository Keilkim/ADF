"""Markdown preserves reading order/assets and never publishes canceled jobs."""
import json
import base64
import io
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import unquote
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pymupdf
from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf.app import MainWindow
from adf.markdown_export import convert_document, LayoutOcrEngine, TableMarkup, markdown_text
from adf.markdown_widgets import export_markdown, publish_bundle, MarkdownOptionsDialog

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT/'.tools/ocr-models'


from adf.release_checks import make_layout_fixture


class MarkdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='adf-markdown-test-')
        self.folder = Path(self.temp.name)
        self.old_hook = sys.excepthook
        self.errors = []
        sys.excepthook = lambda kind, error, tb: self.errors.append(error)

    def tearDown(self):
        self.temp.cleanup()
        sys.excepthook = self.old_hook
        self.assertEqual(self.errors, [])

    def close_window(self, window):
        for timer in window.findChildren(QTimer):
            timer.stop()
        with patch.object(window, 'maybe_save', return_value=True):
            window.close()
        window.deleteLater()
        self.app.processEvents()

    def test_table_and_text_cannot_inject_html_or_active_markdown(self):
        value = markdown_text('<script>alert(1)</script> ![x](https://example.invalid/a)')
        self.assertNotIn('<script>', value)
        self.assertNotIn('![x](', value)
        table = TableMarkup()
        table.feed('<table><tr><td>A|B</td><td>&lt;img src=x onerror=alert(1)&gt;</td></tr></table>')
        rendered = table.markdown()
        self.assertIn(r'A\|B', rendered)
        self.assertNotIn('<img', rendered)
        merged = TableMarkup()
        merged.feed('<table onclick="evil()"><tr><td colspan="2">Heading</td></tr><tr><td>A</td><td>B</td></tr></table>')
        self.assertIn('colspan="2"', merged.markdown())
        self.assertNotIn('onclick', merged.markdown())

    def test_publish_rejects_existing_folder_and_missing_assets(self):
        stage = self.folder/'staged'
        stage.mkdir()
        (stage/'document.md').write_text('![image](images/missing.png)', encoding='utf-8')
        with self.assertRaises(ValueError):
            publish_bundle(stage, self.folder/'output')
        self.assertFalse((self.folder/'output').exists())
        destination = self.folder/'existing'
        destination.mkdir()
        (destination/'keep.txt').write_text('original', encoding='utf-8')
        with self.assertRaises(FileExistsError):
            publish_bundle(stage, destination)
        self.assertEqual((destination/'keep.txt').read_text(), 'original')

    def test_simple_options_default_embedded_and_unique_dated_names(self):
        document = SimpleNamespace(path=str(self.folder/'보고서 (최종).pdf'), page_count=3)
        first = MarkdownOptionsDialog(document, 1, [0, 2])
        second = MarkdownOptionsDialog(document, 1, [0, 2])
        try:
            self.assertEqual(first.image_storage.currentData(), 'embedded')
            self.assertNotEqual(first.bundle_name(), second.bundle_name())
            first.image_storage.setCurrentIndex(1)
            first.accept()
            options = first.options
            self.assertEqual(options['image_storage'], 'files')
            self.assertRegex(options['markdown_name'], r'^\d{8}_보고서 \(최종\)_[a-f0-9]{12}\.md$')
            self.assertEqual(Path(options['output']).name+'.md', options['markdown_name'])
            self.assertIn('images/', first.output_preview.text())
        finally:
            first.deleteLater()
            second.deleteLater()

    def test_embedded_bytes_match_files_and_relative_links_survive_moving_bundle(self):
        from PIL import Image

        class Engine:
            def regions(self, pixels):
                h, w = pixels.shape[:2]
                return [dict(label='chart', box=[0, 0, w, h], score=1)]

            def recognize(self, pixels):
                return []

        source = self.folder/'source.pdf'
        with pymupdf.open() as document:
            page = document.new_page(width=100, height=100)
            page.draw_rect((10, 10, 90, 90), color=(1, 0, 0), fill=(0, 1, 0))
            document.save(source)
        name = '20260909_보고서 (최종)#50%_a1b2c3d4e5f6.md'
        results = {}
        for storage in ['files', 'embedded']:
            output = self.folder/storage
            result = convert_document(source, output, MODELS, engine=Engine(),
                image_storage=storage, markdown_name=name)
            self.assertEqual(result['markdown_file'], name)
            results[storage] = (output/name).read_text(encoding='utf-8')
        # Moving both MD and images together requires no absolute path rewrite.
        moved = self.folder/'다른 저장 위치'
        publish_bundle(self.folder/'files', moved, name, 'files')
        links = re.findall(r'\]\(((?:images|pages)/[^)]+)\)', results['files'])
        embedded = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', results['embedded'])
        self.assertEqual(len(links), 2)
        self.assertEqual(len(embedded), len(links))
        for link, data in zip(links, embedded):
            path = moved/unquote(link)
            self.assertTrue(path.is_file())
            self.assertTrue(path.name.startswith(name[:-3]+'_p00001'))
            self.assertEqual(path.read_bytes(), base64.b64decode(data, validate=True))
            with Image.open(path) as image:
                image.verify()
        self.assertNotIn(str(self.folder), results['files'])
        self.assertFalse(list((self.folder/'embedded').glob('**/*.png')))

    def test_invalid_output_names_and_image_mode_never_start_conversion(self):
        for name in ['../escape.md', 'CON.md', 'bad/name.md', 'report.pdf']:
            with self.assertRaises(ValueError):
                convert_document('not-opened.pdf', self.folder/'output', MODELS, markdown_name=name)
        with self.assertRaises(ValueError):
            convert_document('not-opened.pdf', self.folder/'output', MODELS, image_storage='unknown')
        self.assertFalse((self.folder/'output').exists())

    def test_restricted_pdf_is_rejected_before_models_or_output(self):
        source = self.folder/'locked.pdf'
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(source, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner',
                     user_pw='reader', permissions=pymupdf.PDF_PERM_PRINT)
        with patch('adf.markdown_export.LayoutOcrEngine') as engine:
            with self.assertRaises(PermissionError):
                convert_document(source, self.folder/'output', MODELS, password='reader')
            engine.assert_not_called()
        self.assertFalse((self.folder/'output').exists())

    @unittest.skipUnless((MODELS/'pp_doc_layoutv3.onnx').is_file(), 'Run scripts/prepare-ocr.py for real model tests')
    def test_real_offline_korean_scan_layout_table_and_images(self):
        native, scanned = make_layout_fixture(self.folder)
        original = scanned.read_bytes()
        with pymupdf.open(scanned) as doc:
            self.assertEqual(doc[0].get_text().strip(), '')
        with patch('socket.socket.connect', side_effect=AssertionError('OCR must remain offline')):
            result = convert_document(scanned, self.folder/'output', MODELS)
        markdown = (self.folder/'output/document.md').read_text(encoding='utf-8')
        self.assertEqual(result['ocr_pages'], 1)
        self.assertGreaterEqual(result['tables'], 1)
        self.assertGreaterEqual(result['figures'], 2)
        self.assertIn('한글 문장을 정확하게 읽습니다', markdown)
        self.assertIn('| 1분기 | 120 | 95% |', markdown)
        self.assertIn('| 3분기 | 240 | 99% |', markdown)
        self.assertLess(markdown.index('왼쪽 단락'), markdown.index('오른쪽 단락'))
        images = re.findall(r'data:image/png;base64,([A-Za-z0-9+/=]+)', markdown)
        self.assertGreaterEqual(len(images), 4)
        from PIL import Image
        for data in images:
            with Image.open(io.BytesIO(base64.b64decode(data, validate=True))) as image:
                image.verify()
        self.assertFalse((self.folder/'output/images').exists())
        self.assertFalse((self.folder/'output/pages').exists())
        self.assertEqual(scanned.read_bytes(), original)

    @unittest.skipUnless((MODELS/'pp_doc_layoutv3.onnx').is_file(), 'Run scripts/prepare-ocr.py for real worker tests')
    def test_worker_exports_current_edits_and_cancels_during_ocr(self):
        native, scanned = make_layout_fixture(self.folder)
        window = MainWindow(smoke=True)
        try:
            self.assertTrue(window.open_path(native))
            window.document.rotate([0], 180)
            output = self.folder/'completed'
            result = export_markdown(window, dict(pages=[0], output=str(output), force_ocr=False, include_pages=True, image_storage='files'))
            self.assertEqual(result['pages'], 1)
            self.assertTrue((output/'document.md').is_file())
            self.assertTrue(window.document.dirty)
            from PIL import Image
            exported = Image.open(output/'pages/document_p00001.png')
            current = window.document.doc[0].get_pixmap(matrix=pymupdf.Matrix(3, 3))
            self.assertEqual(exported.tobytes(), current.samples)
            exported.close()
            with patch.object(window, 'maybe_save', return_value=True):
                self.assertTrue(window.open_path(scanned))
            cancel = QTimer(window)
            cancel.setInterval(20)
            canceled = []
            def cancel_inference():
                dialog = window.markdown_progress
                if dialog and '글자 인식' in dialog.stage.text():
                    canceled.append(True)
                    QTest.keyClick(dialog, Qt.Key.Key_Escape)
                    cancel.stop()
            cancel.timeout.connect(cancel_inference)
            cancel.start()
            pending = self.folder/'canceled'
            result = export_markdown(window, dict(pages=[0], output=str(pending), force_ocr=True, include_pages=True))
            self.assertIsNone(result)
            self.assertTrue(canceled)
            self.assertFalse(pending.exists())
            self.assertFalse(list(self.folder.glob('.adf-md-*')))
            self.assertIsNone(window.worker)
            self.assertIsNone(window.markdown_progress)
        finally:
            self.close_window(window)
