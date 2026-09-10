"""Exercise print choices through PDF output and extraction through the worker."""
import hashlib
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
from PySide6.QtCore import QMarginsF, QRectF, QSizeF, QTimer, Qt
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from adf.app import MainWindow
from adf.dialogs import PrintOptionsDialog, SplitDialog
from adf.printing import print_pages, print_rect
from adf.theme import STYLE


class PrintGeometryTests(unittest.TestCase):
    def test_disjoint_ranges_and_current_are_exact(self):
        self.assertEqual(print_pages('range', 12, 4, ranges='1,3,5-6,3,12-끝'), [0, 2, 4, 5, 11])
        self.assertEqual(print_pages('current', 12, 4), [4])
        self.assertEqual(print_pages('selected', 12, 4, [8, 2]), [2, 8])
        for value in ('', '0', '13', '8-3', '1,,3', 'abc'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                print_pages('range', 12, 4, ranges=value)

    def test_physical_size_fit_shrink_and_custom_preserve_ratio(self):
        area = QRectF(20, 40, 500, 700)
        page = QSizeF(250, 350)
        for dpi in (72, 300, 600):
            with self.subTest(dpi=dpi):
                scaled_area = QRectF(area.x(), area.y(), area.width()*dpi/72, area.height()*dpi/72)
                fit = print_rect(page, scaled_area, dpi)
                self.assertEqual(fit.size(), scaled_area.size())
                for mode, factor in (('shrink', 1), ('actual', 1), ('custom', .5)):
                    rect = print_rect(page, scaled_area, dpi, mode, 50)
                    self.assertAlmostEqual(rect.width(), 250*dpi/72*factor)
                    self.assertEqual(rect.center(), scaled_area.center())
                    self.assertAlmostEqual(rect.width()/rect.height(), 250/350)
        large = QSizeF(1000, 1400)
        self.assertEqual(print_rect(large, area, 72, 'shrink'), area)


class PrintAndExtractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle('Fusion')
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='adf-print-test-')
        self.root = Path(self.temp.name)
        self.source = self.root/'source.pdf'
        self.colors = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0), (1, 0, 1), (0, 1, 1)]
        with pymupdf.open() as doc:
            for i, color in enumerate(self.colors):
                page = doc.new_page(width=360, height=480)
                page.draw_rect((40, 40, 320, 440), color=color, fill=color)
                page.insert_text((50, 60), f'Page {i+1}')
            doc.save(self.source)
        self.original_hash = hashlib.sha256(self.source.read_bytes()).digest()
        self.window = MainWindow(smoke=True)
        self.errors = []
        self.window.error = lambda error: self.errors.append(str(error))
        self.window.show()
        self.window.open_path(self.source)
        self.app.processEvents()

    def tearDown(self):
        if self.window.worker:
            self.window.worker.kill()
            self.window.worker.waitForFinished(3000)
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        with patch.object(self.window, 'maybe_save', return_value=True):
            self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), self.original_hash)
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def print_pdf(self, target, ranges='', scale='fit', percent=100, reverse=False):
        output = self.root/f'{target}-{scale}-{percent}-{reverse}.pdf'

        def choose(dialog):
            dialog.target.setCurrentIndex(dialog.target.findData(target))
            dialog.ranges.setText(ranges)
            dialog.scale.setCurrentIndex(dialog.scale.findData(scale))
            dialog.percent.setValue(percent)
            dialog.accept()
            return dialog.result()

        def printer(dialog):
            device = dialog.printer()
            device.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            device.setOutputFileName(str(output))
            device.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
            device.setPageMargins(QMarginsF(12, 16, 20, 24), QPageLayout.Unit.Millimeter)
            if reverse:
                device.setPageOrder(QPrinter.PageOrder.LastPageFirst)
            return QDialog.DialogCode.Accepted

        with patch.object(PrintOptionsDialog, 'exec', choose), patch('adf.app.QPrintDialog.exec', printer):
            self.window.print_document()
        return output

    def assert_artwork(self, output, expected_pages):
        with pymupdf.open(output) as doc:
            self.assertEqual(doc.page_count, len(expected_pages))
            for page, index in zip(doc, expected_pages):
                self.assertAlmostEqual(page.rect.width, 595, delta=1)
                self.assertAlmostEqual(page.rect.height, 842, delta=1)
                pm = page.get_pixmap(colorspace=pymupdf.csRGB)
                color = pm.pixel(pm.width//2, pm.height//2)
                for observed, expected in zip(color, self.colors[index]):
                    self.assertAlmostEqual(observed, expected*255, delta=8)

    def test_disjoint_current_and_selected_print_the_requested_artwork(self):
        self.assert_artwork(self.print_pdf('range', '1,3,5-6'), [0, 2, 4, 5])
        self.window.view.goto(3)
        self.assert_artwork(self.print_pdf('current'), [3])
        self.window.thumbnails.clearSelection()
        for index in (1, 4):
            self.window.thumbnails.item(index).setSelected(True)
        self.assert_artwork(self.print_pdf('selected', reverse=True), [4, 1])

    def test_actual_and_custom_size_are_preserved_in_pdf_output(self):
        for mode, percent, factor in (('actual', 100, 1), ('shrink', 100, 1), ('custom', 50, .5)):
            with self.subTest(mode=mode):
                with pymupdf.open(self.print_pdf('current', scale=mode, percent=percent)) as doc:
                    image = doc[0].get_image_info()[0]
                    rect = pymupdf.Rect(image['bbox'])
                    self.assertAlmostEqual(rect.width, 360*factor, delta=.1)
                    self.assertAlmostEqual(rect.height, 480*factor, delta=.1)
                    printable = pymupdf.Rect(12*72/25.4, 16*72/25.4,
                                             doc[0].rect.width-20*72/25.4, doc[0].rect.height-24*72/25.4)
                    self.assertAlmostEqual((rect.x0+rect.x1)/2, (printable.x0+printable.x1)/2, delta=.2)
                    self.assertAlmostEqual((rect.y0+rect.y1)/2, (printable.y0+printable.y1)/2, delta=.2)

    def test_mixed_a3_pages_fit_inside_a4_printable_area(self):
        source = self.root/'large.pdf'
        with pymupdf.open() as doc:
            for width, height in ((842, 1191), (1191, 842)):
                doc.new_page(width=width, height=height).draw_rect((0, 0, width, height), fill=(1, 0, 0))
            doc.save(source)
        self.window.open_path(source)
        with pymupdf.open(self.print_pdf('all')) as doc:
            self.assertEqual(doc.page_count, 2)
            for page in doc:
                self.assertAlmostEqual(page.rect.width, 595, delta=1)
                self.assertAlmostEqual(page.rect.height, 842, delta=1)
                rect = pymupdf.Rect(page.get_image_info()[0]['bbox'])
                self.assertGreaterEqual(rect.x0, 12*72/25.4-.2)
                self.assertGreaterEqual(rect.y0, 16*72/25.4-.2)
                self.assertLessEqual(rect.x1, page.rect.width-20*72/25.4+.2)
                self.assertLessEqual(rect.y1, page.rect.height-24*72/25.4+.2)

    def test_invalid_range_and_cancel_never_open_the_printer(self):
        dialog = PrintOptionsDialog(6, 2, [2], self.window)
        dialog.target.setCurrentIndex(dialog.target.findData('range'))
        for value in ('', '7', '4-1', '1,,3'):
            dialog.ranges.setText(value)
            self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
            dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        dialog.deleteLater()
        with patch.object(PrintOptionsDialog, 'exec', return_value=QDialog.DialogCode.Rejected), \
             patch('adf.app.QPrintDialog.exec') as native:
            self.window.print_document()
            native.assert_not_called()

    def test_navigation_grows_to_fit_large_page_numbers(self):
        spin = self.window.page_spin
        spin.blockSignals(True)
        for count in (100, 1000, 10000):
            spin.setMaximum(count)
            spin.setValue(count)
            self.app.processEvents()
            edit = spin.lineEdit()
            self.assertGreaterEqual(edit.width()-4, edit.fontMetrics().horizontalAdvance(str(count)))
        spin.blockSignals(False)

    def test_extract_uses_sidebar_selection_and_unsaved_edits(self):
        self.window.edit(lambda: self.window.document.rotate([2], 90))
        self.window.thumbnails.clearSelection()
        for index in (0, 2, 4):
            self.window.thumbnails.item(index).setSelected(True)
        output = self.root/'chosen-name.pdf'

        def accept(dialog):
            self.assertEqual(dialog._get_groups(), [[0, 2, 4]])
            self.assertTrue(dialog.extract)
            dialog.accept()
            return dialog.result()

        with patch.object(SplitDialog, 'exec', accept), patch.object(self.window, 'output_path', return_value=str(output)):
            self.window.actions['extract'].trigger()
            deadline = time.monotonic()+10
            while self.window.worker and time.monotonic() < deadline:
                QTest.qWait(15)
        self.assertIsNone(self.window.worker)
        with pymupdf.open(output) as doc:
            self.assertEqual(doc.page_count, 3)
            self.assertEqual([page.get_text().strip() for page in doc], ['Page 1', 'Page 3', 'Page 5'])
            self.assertEqual(doc[1].rotation, 90)
        self.assertTrue(self.window.document.dirty)
        self.assertEqual(self.window.document.page_count, 6)


if __name__ == '__main__':
    unittest.main()
