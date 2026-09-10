"""Screen density, clipped zoom rendering and responsive page thumbnails."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pymupdf
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, QMimeData
from PySide6.QtGui import QImage, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf.app import MainWindow
from adf.viewer import page_raster, PAGE_CACHE_BYTES, RENDER_OVERSAMPLE, THUMB_PIXMAP_ROLE


class RenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle('Fusion')
        from adf.theme import STYLE
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='adf-render-test-')
        self.source = Path(self.temp.name)/'render.pdf'
        with pymupdf.open() as doc:
            for width, height in [(595, 842), (842, 595), (595, 842)]:
                page = doc.new_page(width=width, height=height)
                for y in range(40, int(height)-20, 20):
                    page.insert_text((40, y), 'Sharp PDF text 0123456789', fontsize=9)
                    page.draw_line((35, y+3), (width-35, y+3), width=.25)
            doc[2].set_rotation(90)
            doc.save(self.source)
        self.window = MainWindow(smoke=True)
        self.errors = []
        self.window.error = lambda e: self.errors.append(str(e))
        self.window.view.renderError.connect(self.errors.append)
        self.window.thumbnails.renderError.connect(self.errors.append)
        self.window.show()
        self.window.open_path(self.source)
        self.app.processEvents()
        QTest.qWait(80)

    def tearDown(self):
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        with patch.object(self.window, 'maybe_save', return_value=True):
            self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def test_fixed_thumbnails_reflow_into_only_complete_columns(self):
        source = Path(self.temp.name) / 'many.pdf'
        with pymupdf.open() as document:
            for width, height in [(595, 842), (842, 595)] * 6:
                document.new_page(width=width, height=height)
            document.save(source)
        self.window.open_path(source)
        self.window.resize(2000, 1000)
        thumbs = self.window.thumbnails
        previous_width = thumbs.thumbnail_rect(0).width()
        self.assertGreater(previous_width, 180)
        for width, expected_columns in ((248, 1), (467, 1), (468, 2), (696, 3),
                                        (924, 4), (1152, 5), (468, 2), (248, 1)):
            available = self.window.reader_splitter.width() - self.window.reader_splitter.handleWidth()
            self.window.reader_splitter.setSizes([width, available - width])
            QTest.qWait(70)
            self.assertEqual(thumbs.columns, expected_columns)
            first_row_y = thumbs.visualItemRect(thumbs.item(0)).y()
            first_row = [thumbs.visualItemRect(thumbs.item(i)) for i in range(thumbs.count())
                         if thumbs.visualItemRect(thumbs.item(i)).y() == first_row_y]
            self.assertEqual(len(first_row), expected_columns)
            for row in range(thumbs.count()):
                rect = thumbs.thumbnail_rect(row)
                cell = thumbs.visualItemRect(thumbs.item(row))
                self.assertAlmostEqual(rect.width(), previous_width, delta=.01)
                self.assertGreaterEqual(rect.left(), 0)
                self.assertLessEqual(rect.right(), thumbs.viewport().width())
                self.assertAlmostEqual(rect.center().x(), cell.center().x(), delta=1)
            self.assertEqual(thumbs.horizontalScrollBar().maximum(), 0)
            for button in self.window.side_buttons.values():
                self.assertTrue(self.window.sidebar.rect().contains(button.geometry()))
            thumb = thumbs.item(0).data(THUMB_PIXMAP_ROLE)
            self.assertIsNotNone(thumb)
            expected = thumbs.thumbnail_rect(0).width() * thumbs.viewport().devicePixelRatioF() * RENDER_OVERSAMPLE
            self.assertGreaterEqual(thumb.width(), expected-1)
        self.assertEqual(thumbs.thumbnail_rect(0).width(), previous_width)

    def test_page_pixels_follow_device_density_and_zoom_above_old_limit(self):
        view = self.window.view
        view.set_mode('single')
        for density in (1, 1.5, 2):
            with patch.object(view.viewport(), 'devicePixelRatioF', return_value=density):
                for zoom in (1, 2, 4):
                    view.set_zoom(zoom)
                    view.render_visible()
                    item = view.pages[view.current]
                    self.assertIsNotNone(item.pixmap)
                    observed = item.pixmap.width()/item.pixmap_rect.width()
                    self.assertAlmostEqual(observed, zoom*density*RENDER_OVERSAMPLE, delta=.01)
                    visible = item.mapRectFromScene(view.mapToScene(view.viewport().rect()).boundingRect()).intersected(item.rect)
                    self.assertTrue(item.pixmap_rect.adjusted(-.01, -.01, .01, .01).contains(visible))
                    self.assertLessEqual(view.cache_bytes, PAGE_CACHE_BYTES)

    def test_grid_selection_drop_order_and_collapse_restore(self):
        self.window.resize(1600, 1000)
        splitter = self.window.reader_splitter
        splitter.setSizes([700, splitter.width()-700-splitter.handleWidth()])
        QTest.qWait(60)
        thumbs = self.window.thumbnails
        self.assertEqual(thumbs.columns, 3)
        for row, modifiers in ((0, Qt.KeyboardModifier.NoModifier), (2, Qt.KeyboardModifier.ControlModifier)):
            QTest.mouseClick(thumbs.viewport(), Qt.MouseButton.LeftButton, modifiers,
                             thumbs.visualItemRect(thumbs.item(row)).center())
        self.assertEqual(self.window.selected_pages(), [0, 2])
        width = self.window.page_sidebar.rail.width()
        self.window.page_sidebar.set_expanded(False)
        self.app.processEvents()
        self.window.page_sidebar.set_expanded(True)
        QTest.qWait(30)
        self.assertEqual(self.window.page_sidebar.rail.width(), width)
        self.assertEqual(self.window.selected_pages(), [0, 2])
        self.assertEqual(thumbs.columns, 3)
        # Drop the second page after the third tile using its right half.
        thumbs.clearSelection()
        thumbs.item(1).setSelected(True)
        tile = thumbs.visualItemRect(thumbs.item(2))
        point = QPointF(tile.right()-8, tile.center().y())
        self.assertEqual(thumbs.insertion_index_at(point), 3)
        mime = QMimeData()
        event = QDropEvent(point, Qt.DropAction.MoveAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        thumbs.dropEvent(event)
        self.assertEqual([page.rotation for page in self.window.document.doc], [0, 90, 0])
        self.window.undo()
        self.assertEqual([page.rotation for page in self.window.document.doc], [0, 0, 90])

    def test_clipped_rotated_page_matches_the_same_area_of_full_pdf_render(self):
        for rotation in (0, 90, 180, 270):
            page = self.window.document.doc[0]
            page.set_rotation(rotation)
            scale = 2.125
            clip = pymupdf.Rect(20.2, 31.6, 199.7, 300.1)
            pixmap, extent = page_raster(page, scale, clip)
            whole = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
            image = QImage(whole.samples, whole.width, whole.height, whole.stride, QImage.Format.Format_RGB888).copy()
            expected = image.copy(round(extent.x()*scale), round(extent.y()*scale), pixmap.width(), pixmap.height())
            # MuPDF clips the outermost partial pixel; inspect the interior.
            actual = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
            self.assertEqual(actual.copy(1, 1, actual.width()-2, actual.height()-2),
                             expected.copy(1, 1, expected.width()-2, expected.height()-2))

    def test_clipped_image_tracks_scroll_without_changing_document(self):
        view = self.window.view
        view.set_mode('single')
        view.set_zoom(4)
        view.render_visible()
        before = QRectF(view.pages[0].pixmap_rect)
        view.verticalScrollBar().setValue(view.verticalScrollBar().maximum())
        view.render_visible()
        after = view.pages[0].pixmap_rect
        self.assertGreater(after.top(), before.top())
        self.assertGreaterEqual(after.bottom(), view.pages[0].rect.bottom())
        self.assertFalse(self.window.document.dirty)

    def test_zoom_cache_is_limited_by_bytes_and_count(self):
        view = self.window.view
        view.set_mode('single')
        for step in range(22):
            view.set_zoom(1 + step*.04)
            view.render_visible()
            self.assertLessEqual(len(view.cache), 18)
            self.assertLessEqual(view.cache_bytes, PAGE_CACHE_BYTES)
            self.assertIsNotNone(view.pages[0].pixmap)

    def test_hover_gap_keeps_current_thumbnail_size_and_center(self):
        thumbs = self.window.thumbnails
        size = thumbs.thumbnail_size(0)
        normal = thumbs.item(0).sizeHint().height()
        thumbs.set_hover_slot(1)
        self.assertEqual(thumbs.thumbnail_size(0), size)
        self.assertEqual(thumbs.item(0).sizeHint().height(), normal+40)
        self.assertAlmostEqual(thumbs.insert_button.geometry().center().x(), thumbs.viewport().rect().center().x(), delta=1)
        thumbs.set_hover_slot(None)
        self.assertEqual(thumbs.item(0).sizeHint().height(), normal)
