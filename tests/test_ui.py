"""Offscreen integration checks for user-facing document workflows."""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QTimer, Qt, QMimeData, QUrl
from PySide6.QtGui import QWheelEvent, QImage, QDragEnterEvent, QDragMoveEvent, QDropEvent, QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog, QMenu, QMessageBox
from PySide6.QtPrintSupport import QPrinter

from adf.app import MainWindow
from adf.dialogs import (
    CompressionDialog, MergeDialog, MergePageRangeDialog, NumberingDialog, PagePickerDialog,
    SplitDialog, TextEditDialog, PrintOptionsDialog,
)


class DesktopWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        # Qt's Windows offscreen backend may have no system font database.
        # Measure the toolbar with the same fonts used by the desktop app.
        from PySide6.QtGui import QFontDatabase
        if sys.platform == 'win32' and cls.app.platformName() == 'offscreen':
            for family, filename in [('Segoe UI', 'segoeui.ttf'), ('Malgun Gothic', 'malgun.ttf')]:
                if family not in QFontDatabase.families():
                    QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows'))/'Fonts'/filename))
        from adf.theme import apply_theme
        apply_theme(cls.app)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="adf-ui-test-")
        self.root = Path(self.directory.name)
        self.source = self.root / "source.pdf"
        with pymupdf.open() as doc:
            for index in range(6):
                page = doc.new_page(width=360, height=480)
                page.insert_text((40, 60), f"needle page {index + 1}", fontsize=16)
                page.insert_text((40, 120), f"Original content {index + 1}", fontsize=12)
            doc.save(self.source)
        self.source_hash = hashlib.sha256(self.source.read_bytes()).digest()
        self.callback_errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, error, tb: self.callback_errors.append(error)
        self.window = MainWindow(smoke=True)
        self.errors = []
        self.window.error = lambda error: self.errors.append(str(error))
        self.window.show()
        self.assertTrue(self.window.open_path(self.source))
        self.app.processEvents()

    def tearDown(self):
        if self.window.worker:
            self.window.worker.kill()
            self.window.worker.waitForFinished(3000)
        for timer in self.window.findChildren(QTimer):
            timer.stop()
        with patch.object(self.window, "maybe_save", return_value=True):
            self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        sys.excepthook = self.old_hook
        self.directory.cleanup()
        self.assertEqual(self.errors, [], "The UI reported an operation error")
        self.assertEqual(self.callback_errors, [], "A Qt callback raised an exception")

    def wait_until(self, predicate, seconds=8):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(15)
        self.assertTrue(predicate(), "The asynchronous UI operation did not finish")

    def assert_source_unchanged(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), self.source_hash)

    def test_page_navigation_targets_visible_page_for_toolbar_edit(self):
        self.window.view.set_mode("single")
        self.window.view.goto(2)
        self.assertEqual(self.window.current, 2)
        self.assertEqual(self.window.selected_pages(), [2])
        self.window.actions["rotate"].trigger()
        self.assertEqual(self.window.document.doc[2].rotation, 90)
        self.assertEqual(self.window.document.doc[0].rotation, 0)
        self.window.undo()
        self.assertEqual(self.window.document.doc[2].rotation, 0)
        self.window.redo()
        self.assertEqual(self.window.document.doc[2].rotation, 90)
        self.assert_source_unchanged()

    def test_spread_and_grid_keep_explicit_right_page_navigation(self):
        for mode in ("spread", "spread_continuous", "grid"):
            with self.subTest(mode=mode):
                self.window.view.set_mode(mode)
                self.window.view.goto(2)
                QTest.qWait(100)
                self.assertEqual(self.window.current, 2)
                self.assertEqual(self.window.page_spin.value(), 3)

    def test_all_view_modes_and_zoom_are_bounded(self):
        for mode in ("single", "continuous", "spread", "spread_continuous", "grid"):
            self.window.view.set_mode(mode)
            self.window.view.goto(3)
            self.window.view.fit("page")
            QTest.qWait(25)
            shown = [page for page in self.window.view.pages if page.isVisible()]
            self.assertEqual(len(shown), 1 if mode == "single" else 2 if mode == "spread" else 6)
            self.assertGreater(self.window.view.transform().m11(), 0)
        self.window.view.set_zoom(999)
        self.assertEqual(self.window.view.transform().m11(), 4)
        self.window.view.set_zoom(0.001)
        self.assertEqual(self.window.view.transform().m11(), 0.12)
        self.window.zoom.setEditText("125%")
        self.window.zoom_selected()
        self.assertEqual(self.window.view.transform().m11(), 1.25)
        self.window.zoom.setEditText("invalid")
        self.window.zoom_selected()
        self.assertEqual(self.window.view.transform().m11(), 1.25)

    def test_search_navigation_and_close_clear_stale_results(self):
        self.window.show_search()
        self.window.search_input.setText("needle")
        self.wait_until(lambda: self.window.search_index == 0)
        self.assertEqual(len(self.window.search_matches), 6)
        self.window.next_match()
        self.assertEqual(self.window.search_index, 1)
        self.assertEqual(self.window.current, 1)
        self.window.next_match(-1)
        self.assertEqual(self.window.current, 0)
        self.window.close_document()
        self.assertEqual(self.window.search_matches, [])
        self.assertEqual(self.window.document.page_count, 0)
        self.assertEqual(self.window.view.pages, [])
        self.assertEqual(self.window.thumbnails.count(), 0)
        self.window.next_match()
        QTest.qWait(50)
        self.assertFalse(self.window.actions["save"].isEnabled())
        self.assertTrue(self.window.open_path(self.source))

    def test_drag_text_selection_and_copy(self):
        self.window.view.set_mode("single")
        self.assertTrue(self.window.actions['select_tool'].isChecked())
        self.assertEqual(self.window.view.dragMode(), self.window.view.DragMode.NoDrag)
        self.window.view.fit("page")
        QTest.qWait(30)
        page_item = self.window.view.pages[0]
        start = self.window.view.mapFromScene(page_item.mapToScene(QPointF(35, 40)))
        finish = self.window.view.mapFromScene(page_item.mapToScene(QPointF(260, 70)))
        viewport = self.window.view.viewport()
        text_point = self.window.view.mapFromScene(page_item.mapToScene(QPointF(50, 55)))
        QTest.mouseMove(viewport, text_point)
        self.assertEqual(viewport.cursor().shape(), Qt.CursorShape.IBeamCursor)
        QTest.mouseMove(viewport, start)
        self.assertEqual(viewport.cursor().shape(), Qt.CursorShape.ArrowCursor)
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(viewport, finish, delay=10)
        self.assertFalse(self.window.view.selection_item.finished)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=finish)
        self.assertIn("needle page 1", self.window.clip_text)
        selection = self.window.view.selection_item
        self.assertTrue(selection.finished)
        self.assertFalse(selection.text_path.isEmpty())
        self.window.copy_text()
        self.assertIn("needle page 1", QApplication.clipboard().text())
        self.window.actions['delete'].trigger()
        self.assertEqual(self.window.document.page_count, 6)
        self.window.escape()
        self.assertIsNone(self.window.view.selection_item)
        self.assertFalse(selection.timer.isActive())

    def test_selection_highlight_tracks_rotated_text_and_clears_with_selection(self):
        view = self.window.view
        view.set_mode('single')
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                self.window.document.rotate([0], (rotation-self.window.document.doc[0].rotation) % 360)
                view.load(self.window.document)
                view.fit('page')
                self.app.processEvents()
                page = self.window.document.doc[0]
                area = pymupdf.Rect(35, 40, 260, 70)*page.rotation_matrix
                points = [view.mapFromScene(view.pages[0].mapToScene(QPointF(*p))) for p in (area.top_left, area.bottom_right)]
                with patch('adf.selection_widgets.motion_enabled', return_value=True):
                    QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=points[0])
                    QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=points[1])
                selection = view.selection_item
                self.assertIn('needle page 1', self.window.clip_text)
                self.assertFalse(selection.text_path.isEmpty())
                highlight = selection.text_path.boundingRect()
                self.assertTrue(selection.rect().adjusted(-2, -2, 2, 2).contains(highlight))
                self.assertTrue(selection.timer.isActive())
                phase = selection.phase
                QTest.qWait(130)
                self.assertNotEqual(selection.phase, phase)
                # Reflow and zoom retain the same page-local selection geometry.
                view.set_zoom(1.2)
                self.assertEqual(selection.text_path.boundingRect(), highlight)
                self.assertIs(selection.parentItem(), view.pages[0])
                QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
                self.assertIsNone(view.selection_item)
                self.assertFalse(selection.timer.isActive())
                self.assertEqual(self.window.clip_text, '')

    def test_capture_boundary_honors_reduced_motion_and_clears_on_document_close(self):
        view = self.window.view
        view.set_mode('single')
        self.window.change_pointer('region_tool')
        self.app.processEvents()
        points = [view.mapFromScene(view.pages[0].mapToScene(QPointF(x, y))) for x, y in ((30, 30), (260, 150))]
        with patch('adf.selection_widgets.motion_enabled', return_value=False):
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=points[0])
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=points[1])
        selection = view.selection_item
        self.assertTrue(selection.finished)
        self.assertTrue(selection.text_path.isEmpty())
        self.assertFalse(selection.timer.isActive())
        self.window.close_document()
        self.assertIsNone(view.selection_item)
        QApplication.clipboard().clear()

    def test_clipboard_notice_is_visible_in_fullscreen_and_does_not_take_focus(self):
        self.window.clip_text = 'clipboard feedback'
        self.window.view.setFocus()
        before = QApplication.focusWidget()
        self.window.copy_text()
        notice = self.window.copy_notice
        self.assertTrue(notice.isVisible())
        self.assertIn('텍스트', notice.title.text())
        self.assertIn('클립보드', notice.accessibleName())
        self.assertEqual(QApplication.clipboard().text(), 'clipboard feedback')
        self.assertIs(QApplication.focusWidget(), before)
        self.assertTrue(notice.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.window.fullscreen.enter()
        self.app.processEvents()
        self.window.clip_text = 'clipboard feedback'
        self.window.copy_text()
        self.assertTrue(notice.isVisible())
        self.assertTrue(self.window.view.viewport().rect().contains(notice.geometry()))
        QTest.qWait(notice.timer.interval()+100)
        self.assertFalse(notice.isVisible())
        self.window.copy_text()
        self.assertTrue(notice.isVisible())
        self.window.close_document()
        self.assertFalse(notice.isVisible())
        QApplication.clipboard().clear()

    def test_default_image_selection_copy_delete_and_undo(self):
        picture = io.BytesIO()
        Image.new('RGBA', (80, 40), (220, 40, 60, 128)).save(picture, format='PNG')
        self.window.edit(lambda: self.window.document.add_image(0, (80, 180, 240, 260), picture.getvalue()))
        view = self.window.view
        view.set_mode('single')
        view.goto(0)
        QTest.qWait(30)
        point = view.mapFromScene(view.pages[0].mapToScene(QPointF(160, 220)))
        QTest.mouseMove(view.viewport(), point)
        self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.ArrowCursor)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        xref = self.window.document.doc[0].get_image_info(xrefs=True)[0]['xref']
        self.assertEqual(self.window.selected_image, (0, xref))
        self.assertEqual(self.window.clip_text, '')
        try:
            self.window.copy_text()
            copied = QApplication.clipboard().image()
            self.assertEqual((copied.width(), copied.height()), (80, 40))
            self.assertAlmostEqual(copied.pixelColor(40, 20).alpha(), 128, delta=1)
        finally:
            QApplication.clipboard().clear()
        self.window.actions['delete'].trigger()
        self.assertEqual(self.window.document.page_count, 6)
        rendered = self.window.document.doc[0].get_pixmap(colorspace=pymupdf.csRGB)
        self.assertEqual(rendered.pixel(160, 220), (255, 255, 255))
        self.window.undo()
        self.assertEqual(self.window.document.doc[0].get_image_info()[0]['width'], 80)
        self.assert_source_unchanged()

    def test_image_selection_in_readonly_pdf_respects_copy_permission(self):
        picture = io.BytesIO()
        Image.new('RGB', (80, 40), 'blue').save(picture, format='PNG')
        source = self.root/'restricted-image.pdf'
        with pymupdf.open() as doc:
            doc.new_page(width=360, height=480).insert_image((80, 180, 240, 260), stream=picture.getvalue())
            doc.save(source, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw='reader',
                     owner_pw='owner', permissions=pymupdf.PDF_PERM_PRINT)
        with patch('adf.app.QInputDialog.getText', return_value=('reader', True)):
            self.assertTrue(self.window.open_path(source))
        view = self.window.view
        view.set_mode('single')
        QTest.qWait(30)
        point = view.mapFromScene(view.pages[0].mapToScene(QPointF(160, 220)))
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertIsNotNone(self.window.selected_image)
        self.assertFalse(self.window.actions['delete'].isEnabled())
        QApplication.clipboard().setText('keep clipboard')
        self.window.copy_text()
        self.assertEqual(QApplication.clipboard().text(), 'keep clipboard')
        self.assertIn('허용하지 않습니다', self.window.statusBar().currentMessage())
        self.window.copy_region()
        self.assertEqual(QApplication.clipboard().text(), 'keep clipboard')
        self.assertFalse(self.window.actions['region_tool'].isEnabled())
        self.assertFalse(self.window.actions['copy_region'].isEnabled())

    def test_region_copy_includes_vector_chart_table_image_and_ink_at_any_rotation(self):
        source = self.root/'visuals.pdf'
        picture = io.BytesIO()
        Image.new('RGB', (30, 30), 'red').save(picture, format='PNG')
        with pymupdf.open() as doc:
            for rotation in (0, 90, 180, 270):
                page = doc.new_page(width=360, height=480)
                page.set_cropbox(pymupdf.Rect(10, 20, 350, 460))
                page.insert_text((45, 55), 'Table and chart', fontsize=12)
                page.draw_rect((40, 65, 220, 150), color=(0, 0, 0))
                page.draw_line((40, 105), (220, 105), color=(0, 0, 0))
                page.draw_line((120, 65), (120, 150), color=(0, 0, 0))
                page.insert_image((55, 75, 85, 100), stream=picture.getvalue())
                page.draw_polyline([(130, 140), (155, 115), (190, 125), (210, 80)], color=(0, 0, 1), width=3)
                from adf.ink import write_ink
                write_ink(page, [[(60, 125), (100, 135)]], '#27824b', 3)
                page.set_rotation(rotation)
            doc.save(source)
        self.window.open_path(source)
        self.window.set_view_mode('single')
        view = self.window.view
        try:
            for index in range(4):
                view.goto(index)
                self.app.processEvents()
                self.window.change_pointer('region_tool')
                page = self.window.document.doc[index]
                bounds = pymupdf.Rect(30, 35, 240, 160)*page.rotation_matrix
                start, end = [view.mapFromScene(view.pages[index].mapToScene(QPointF(*p)))
                              for p in (bounds.top_left, bounds.bottom_right)]
                QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
                QTest.mouseMove(view.viewport(), end)
                QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
                copied = QApplication.clipboard().image()
                self.assertFalse(copied.isNull())
                from adf.viewer import page_raster
                selected = view.selected_region()
                expected, _ = page_raster(page, 3, selected[1])
                self.assertEqual(copied, expected.toImage())
                rgb = copied.convertToFormat(QImage.Format.Format_RGB888)
                # Colored pixels prove this did not copy only the PDF text.
                self.assertIn(bytes((255, 0, 0)), bytes(rgb.constBits()))
                self.assertIn(bytes((0, 0, 255)), bytes(rgb.constBits()))
                self.assertFalse(self.window.document.dirty)
            self.window.escape()
            self.assertFalse(view.copy_region_mode)
        finally:
            QApplication.clipboard().clear()

    def test_text_region_can_also_be_copied_as_image_and_copy_mode_switches_cleanly(self):
        view = self.window.view
        view.set_mode('single')
        view.goto(0)
        self.app.processEvents()
        start, end = [view.mapFromScene(view.pages[0].mapToScene(QPointF(x, y)))
                      for x, y in ((30, 40), (240, 70))]
        try:
            QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
            self.window.copy_text()
            self.assertIn('needle page 1', QApplication.clipboard().text())
            self.window.actions['copy_region'].trigger()
            self.assertFalse(QApplication.clipboard().image().isNull())
            self.window.clear_content_selection()
            self.window.actions['copy_region'].trigger()
            self.assertTrue(view.copy_region_mode)
            self.window.toggle_text(True)
            self.assertFalse(view.copy_region_mode)
            self.assertEqual(self.window.pointer_mode, 'select_tool')
            self.window.change_pointer('region_tool')
            self.window.close_document()
            self.assertIsNone(view.selected_region())
            self.assertFalse(self.window.actions['copy_region'].isEnabled())
        finally:
            QApplication.clipboard().clear()

    def test_text_drag_over_background_image_and_selection_reset(self):
        picture = io.BytesIO()
        Image.new('RGB', (90, 120), 'white').save(picture, format='PNG')
        source = self.root/'ocr.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=360, height=480)
            page.insert_image(page.rect, stream=picture.getvalue())
            page.insert_text((40, 60), 'Selectable OCR text', fontsize=16)
            doc.save(source)
        self.assertTrue(self.window.open_path(source))
        view = self.window.view
        view.set_mode('single')
        QTest.qWait(30)
        def point(x, y):
            return view.mapFromScene(view.pages[0].mapToScene(QPointF(x, y)))
        QTest.mouseMove(view.viewport(), point(50, 55))
        self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.IBeamCursor)
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point(180, 250))
        self.assertIsNotNone(self.window.selected_image)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=point(35, 40))
        QTest.mouseMove(view.viewport(), point(300, 70))
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=point(300, 70))
        self.assertIn('Selectable OCR text', self.window.clip_text)
        self.assertIsNone(self.window.selected_image)
        self.window.actions['hand_tool'].trigger()
        self.assertEqual(view.dragMode(), view.DragMode.ScrollHandDrag)
        QTest.mouseMove(view.viewport(), point(50, 55))
        self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.OpenHandCursor)
        self.assertEqual(self.window.clip_text, '')
        self.assertIsNone(view.selection_item)
        self.window.actions['select_tool'].trigger()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point(180, 250))
        self.assertIsNotNone(self.window.selected_image)
        self.window.escape()
        self.assertIsNone(self.window.selected_image)

    def test_rotated_image_selection_clears_on_page_navigation(self):
        picture = io.BytesIO()
        Image.new('RGB', (80, 40), 'blue').save(picture, format='PNG')
        self.window.edit(lambda: self.window.document.add_image(0, (80, 180, 240, 260), picture.getvalue()))
        self.window.edit(lambda: self.window.document.rotate([0], 90))
        view = self.window.view
        view.set_mode('single')
        view.goto(0)
        QTest.qWait(30)
        text_shown = pymupdf.Point(50, 55)*self.window.document.doc[0].rotation_matrix
        text_point = view.mapFromScene(view.pages[0].mapToScene(QPointF(text_shown.x, text_shown.y)))
        QTest.mouseMove(view.viewport(), text_point)
        self.assertEqual(view.viewport().cursor().shape(), Qt.CursorShape.IBeamCursor)
        shown = pymupdf.Point(160, 220)*self.window.document.doc[0].rotation_matrix
        point = view.mapFromScene(view.pages[0].mapToScene(QPointF(shown.x, shown.y)))
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertIsNotNone(self.window.selected_image)
        view.goto(1)
        self.assertIsNone(self.window.selected_image)
        self.assertTrue(all(not page.highlights for page in view.pages))

    def test_image_placement_commit_cancel_and_undo(self):
        image = io.BytesIO()
        Image.new("RGBA", (80, 40), (220, 40, 60, 160)).save(image, format="PNG")
        self.window.view.set_mode("single")
        self.window.view.goto(2)
        self.window.begin_image(image.getvalue())
        self.assertIsNotNone(self.window.view.placement)
        self.window.view.placement.setPos(35, 80)
        index, rectangle = self.window.view.image_rect()
        self.assertEqual(index, 2)
        self.assertEqual((rectangle.x0, rectangle.y0), (35, 80))
        self.assertTrue(self.window.commit_image())
        self.assertEqual(len(self.window.document.doc[2].get_images()), 1)
        self.assertIsNone(self.window.view.placement)
        self.assertTrue(self.window.document.dirty)
        self.window.undo()
        self.assertEqual(len(self.window.document.doc[2].get_images()), 0)
        self.window.begin_image(image.getvalue())
        self.window.cancel_image()
        self.assertIsNone(self.window.view.placement)
        self.assertIsNone(self.window.image_data)
        self.assert_source_unchanged()

    def test_image_drag_and_corner_resize_keep_visible_preview(self):
        image = io.BytesIO()
        Image.new("RGB", (80, 40), (200, 25, 80)).save(image, format="PNG")
        self.window.view.set_mode("single")
        self.window.begin_image(image.getvalue())
        self.app.processEvents()
        placement = self.window.view.placement
        old_position = placement.pos()
        viewport = self.window.view.viewport()
        center = self.window.view.mapFromScene(placement.mapToScene(
            QPointF(placement.size.width() / 2, placement.size.height() / 2)))
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=center)
        QTest.mouseMove(viewport, center + QPoint(30, 20), delay=10)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=center + QPoint(30, 20))
        self.assertGreater(placement.x(), old_position.x())
        self.assertGreater(placement.y(), old_position.y())
        old_width = placement.size.width()
        corner = self.window.view.mapFromScene(placement.mapToScene(
            QPointF(placement.size.width(), placement.size.height())))
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=corner)
        QTest.mouseMove(viewport, corner + QPoint(25, 12), delay=10)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=corner + QPoint(25, 12))
        self.assertGreater(placement.size.width(), old_width)
        self.assertAlmostEqual(placement.size.width() / placement.size.height(), 2, delta=0.03)
        self.window.cancel_image()

    def test_rotated_page_image_commit_matches_preview_rectangle(self):
        image = io.BytesIO()
        Image.new("RGB", (80, 40), (200, 25, 80)).save(image, format="PNG")
        for rotation in (90, 180, 270):
            with self.subTest(rotation=rotation):
                self.window.edit(lambda: self.window.document.rotate([0], rotation))
                self.window.begin_image(image.getvalue())
                placement = self.window.view.placement
                expected = pymupdf.Rect(placement.x(), placement.y(),
                    placement.x() + placement.size.width(), placement.y() + placement.size.height())
                self.assertTrue(self.window.commit_image())
                page = self.window.document.doc[0]
                actual = pymupdf.Rect(page.get_image_info()[0]["bbox"]) * page.rotation_matrix
                for observed, desired in zip(actual, expected):
                    self.assertAlmostEqual(observed, desired, delta=0.01)
                self.window.undo()
                self.window.undo()

    def test_large_document_renders_only_visible_pages_and_thumbnails(self):
        large = self.root / "large.pdf"
        with pymupdf.open() as doc:
            for index in range(500):
                page = doc.new_page(width=595, height=842)
                page.insert_text((50, 60), f"Large document page {index + 1}")
            doc.save(large)
        started = time.monotonic()
        self.assertTrue(self.window.open_path(large))
        opened_ms = (time.monotonic() - started) * 1000
        self.wait_until(lambda: bool(self.window.view.cache) and bool(self.window.thumbnails.cache))
        first_render_ms = (time.monotonic() - started) * 1000
        self.assertEqual(self.window.document.page_count, 500)
        self.assertLessEqual(len(self.window.view.cache), 18)
        self.assertLess(len(self.window.thumbnails.cache), 30)
        self.assertLess(sum(page.pixmap is not None for page in self.window.view.pages), 20)
        print(f"\n500 pages: opened {opened_ms:.0f} ms, first page + thumbnails {first_render_ms:.0f} ms; "
              f"rendered pages {len(self.window.view.cache)}, thumbnails {len(self.window.thumbnails.cache)}")

    def test_numbering_and_text_dialogs_apply_through_main_window(self):
        def number_accept(dialog):
            dialog.ranges.setText("2-3")
            dialog.start.setValue(5)
            dialog.affix.setCurrentIndex(1)
            dialog.zero_pad.setChecked(True)
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(NumberingDialog, "exec", number_accept):
            self.window.number()
        self.assertIn("p.5", self.window.document.doc[1].get_text())
        self.assertIn("p.6", self.window.document.doc[2].get_text())
        self.assertNotIn("p.", self.window.document.doc[0].get_text())
        span = self.window.document.doc[0].get_text("dict")["blocks"][0]["lines"][0]["spans"][0]

        self.window.edit_text(0, span)
        self.assertIsNotNone(self.window.view.text_placement)
        self.window.text_value.setPlainText("Revised content")
        self.window.view.text_placement.setPos(QPointF(90, 75))
        self.window.text_geometry_changed()
        self.assertTrue(self.window.apply_text())
        text = self.window.document.doc[0].get_text()
        self.assertIn("Revised content", ' '.join(text.split()))
        self.assertNotIn("needle page 1", text)
        self.assertGreater(self.window.document.doc[0].search_for("Revised")[0].x0, 80)
        self.assertTrue(self.window.document.dirty)
        self.assert_source_unchanged()

    def test_page_picker_replaces_with_multiple_pages_and_closes_source(self):
        opened_dialogs = []

        def pick_pages(dialog):
            opened_dialogs.append(dialog)
            dialog.thumbnails.clearSelection()
            dialog.thumbnails.item(2).setSelected(True)
            dialog.thumbnails.item(4).setSelected(True)
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(QFileDialog, "getOpenFileName", return_value=(str(self.source), "")), \
             patch.object(PagePickerDialog, "exec", pick_pages):
            self.window.replace_pages()
        self.assertEqual(self.window.document.page_count, 7)
        self.assertIn("needle page 3", self.window.document.doc[0].get_text())
        self.assertIn("needle page 5", self.window.document.doc[1].get_text())
        self.assertTrue(opened_dialogs[0].doc.is_closed)
        self.window.undo()
        self.assertEqual(self.window.document.page_count, 6)
        self.assert_source_unchanged()

    def test_save_as_cancellation_overwrite_refusal_and_new_copy(self):
        self.window.edit(lambda: self.window.document.rotate([1], 90))
        with patch.object(self.window, "output_path", return_value=""):
            self.assertFalse(self.window.save_as())
        self.assertTrue(self.window.document.dirty)
        with patch.object(self.window, "output_path", return_value=str(self.source)), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.assertFalse(self.window.save_as())
        self.assert_source_unchanged()
        output = self.root / "saved.pdf"
        with patch.object(self.window, "output_path", return_value=str(output)):
            self.assertTrue(self.window.save_as())
        self.assertFalse(self.window.document.dirty)
        with pymupdf.open(output) as saved:
            self.assertEqual(saved[1].rotation, 90)
        self.assert_source_unchanged()

    def test_split_dialog_exports_unsaved_state_via_worker(self):
        self.window.edit(lambda: self.window.document.rotate([1], 90))

        def split_accept(dialog):
            dialog.mode.setCurrentIndex(1)
            dialog.range_input.setText("2, 4-5")
            dialog.remaining.setChecked(True)
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(SplitDialog, "exec", split_accept), \
             patch.object(QFileDialog, "getExistingDirectory", return_value=str(self.root)), \
             patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
            self.window.split()
            self.wait_until(lambda: self.window.worker is None)
        output = self.root / "source_p002.pdf"
        self.assertTrue(output.exists())
        with pymupdf.open(output) as extracted:
            self.assertEqual(extracted.page_count, 1)
            self.assertEqual(extracted[0].rotation, 90)
        self.assertEqual(len(list(self.root.glob("source_*.pdf"))), 3)
        self.assertTrue(self.window.document.dirty)
        self.assert_source_unchanged()

    def test_compression_dialog_exports_searchable_pdf_via_worker(self):
        output = self.root / "compressed.pdf"

        def compression_accept(dialog):
            self.assertFalse(dialog.target.isEnabled())
            self.assertFalse(dialog.target_label.isEnabled())
            self.assertTrue(all(widget.isEnabled() for widget in dialog.manual_controls))
            dialog.target_enabled.setChecked(True)
            self.assertTrue(dialog.target.isEnabled())
            self.assertTrue(dialog.target_label.isEnabled())
            self.assertTrue(all(not widget.isEnabled() for widget in dialog.manual_controls))
            dialog.target_enabled.setChecked(False)
            self.assertFalse(dialog.target.isEnabled())
            self.assertTrue(all(widget.isEnabled() for widget in dialog.manual_controls))
            dialog.mode.setCurrentIndex(0)
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(CompressionDialog, "exec", compression_accept), \
             patch.object(self.window, "output_path", return_value=str(output)), \
             patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Save), \
             patch.object(QMessageBox, "information", return_value=QMessageBox.StandardButton.Ok):
            self.window.compress()
            self.wait_until(lambda: self.window.worker is None)
        with pymupdf.open(output) as compressed:
            self.assertEqual(compressed.page_count, 6)
            self.assertIn("needle page 1", compressed[0].get_text())
        self.assert_source_unchanged()

    def test_merge_dialog_order_survives_worker_and_opens_result(self):
        second = self.root / "second.pdf"
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((40, 60), "Second file first")
            doc.save(second)
        output = self.root / "merged.pdf"

        def merge_accept(dialog):
            dialog.file_list.item(1).setSelected(True)
            dialog.move_selected(-1)
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(MergeDialog, "exec", merge_accept), \
             patch.object(QFileDialog, "getSaveFileName", return_value=(str(output), "")):
            self.window.merge(paths=[str(self.source), str(second)])
            self.wait_until(lambda: self.window.worker is None)
        self.assertEqual(self.window.document.page_count, 7)
        self.assertIn("Second file first", self.window.document.doc[0].get_text())
        self.assertEqual(Path(self.window.document.path), output)
        self.assert_source_unchanged()

    def test_pdf_add_inserts_into_open_document_and_waits_for_final_save(self):
        added = self.root / "added.pdf"
        with pymupdf.open() as doc:
            doc.new_page().insert_text((40, 60), "Added first")
            doc.new_page().insert_text((40, 60), "Added second")
            doc.save(added)

        def add_accept(dialog):
            self.assertTrue(dialog.is_insert)
            self.assertEqual(dialog.insert_index, 1)
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(MergeDialog, "exec", add_accept):
            self.window.add_pdf(paths=[str(added)])
        self.assertEqual(self.window.document.page_count, 8)
        self.assertIn("Added first", self.window.document.doc[1].get_text())
        self.assertTrue(self.window.document.dirty)
        self.assert_source_unchanged()
        self.window.undo()
        self.assertEqual(self.window.document.page_count, 6)

    def test_unified_toolbar_reflows_without_brand_label(self):
        self.window.resize(1020, 700)
        self.app.processEvents()
        self.window.toolbar.reflow(1020)
        self.assertTrue(self.window.toolbar._two_rows)
        self.assertEqual(self.window.toolbar.height(), 84)
        # Leave room for wider offscreen/platform fallback fonts as well.
        self.window.resize(2200, 700)
        self.app.processEvents()
        self.window.toolbar.reflow(self.window.toolbar.width())
        self.assertFalse(self.window.toolbar._two_rows)
        self.assertEqual(self.window.toolbar.height(), 46)
        # Both new tools remain reachable without overlapping their neighbours.
        buttons = self.window.toolbar.file_items + self.window.toolbar.edit_items + self.window.toolbar.tail_items
        for button in buttons:
            self.assertTrue(self.window.toolbar.rect().contains(button.geometry()))
        for first, second in zip(buttons, buttons[1:]):
            self.assertFalse(first.geometry().intersects(second.geometry()))

    def test_empty_launch_and_close_have_no_document_content(self):
        fresh = MainWindow(smoke=True)
        try:
            self.assertEqual(fresh.document.page_count, 0)
            self.assertEqual(fresh.view.pages, [])
            self.assertEqual(fresh.thumbnails.count(), 0)
            self.assertIs(fresh.stack.currentWidget(), fresh.empty_workspace)
            self.assertFalse(fresh.actions['save'].isEnabled())
            self.assertFalse(fresh.actions['text'].isEnabled())
            self.assertTrue(fresh.actions['open'].isEnabled())
            self.assertTrue(fresh.actions['combine'].isEnabled())
        finally:
            fresh.close()
            fresh.deleteLater()
        self.window.actions['text'].trigger()
        self.window.close_document()
        self.assertIs(self.window.stack.currentWidget(), self.window.empty_workspace)
        self.assertEqual(self.window.view.pages, [])
        self.assertEqual(self.window.thumbnails.count(), 0)
        self.assertFalse(self.window.actions['text'].isChecked())
        self.assertTrue(self.window.textbar.isHidden())

    def open_paragraph_fixture(self, rotation=0):
        path = self.root / 'paragraph.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=500, height=620)
            page.insert_text((40, 70), 'First wrapped line\nSecond wrapped line\nFinal wrapped line',
                             fontsize=14, lineheight=1.4)
            page.insert_text((40, 230), 'Separate paragraph', fontsize=14)
            page.set_rotation(rotation)
            doc.save(path)
        self.assertTrue(self.window.open_path(path))
        self.window.view.set_mode('single')
        self.window.actions['text'].trigger()
        self.app.processEvents()
        self.window.view.fit('page')
        QTest.qWait(30)
        page = self.window.document.doc[0]
        rect = page.search_for('Second wrapped line')[0] * page.rotation_matrix
        item = self.window.view.pages[0]
        click = self.window.view.mapFromScene(item.mapToScene(QPointF(rect.x0 + rect.width / 2, rect.y0 + rect.height / 2)))
        QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=click)
        return path

    def test_click_wrapped_line_selects_paragraph_and_enter_adds_newline(self):
        source = self.open_paragraph_fixture()
        original_bytes = source.read_bytes()
        self.assertEqual(self.window.text_value.toPlainText(),
                         'First wrapped line\nSecond wrapped line\nFinal wrapped line')
        self.assertFalse(self.window.document.dirty)
        self.window.text_value.setPlainText('Changed first')
        cursor = self.window.text_value.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.window.text_value.setTextCursor(cursor)
        QTest.keyClick(self.window.text_value, Qt.Key.Key_Return)
        QTest.keyClicks(self.window.text_value, 'Changed second')
        self.assertEqual(self.window.text_value.toPlainText(), 'Changed first\nChanged second')
        self.assertFalse(self.window.document.dirty)
        QTest.keyClick(self.window.text_value, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        text = self.window.document.doc[0].get_text()
        self.assertIn('Changed first', ' '.join(text.split()))
        self.assertIn('Changed second', ' '.join(text.split()))
        self.assertIn('Separate paragraph', text)
        self.assertNotIn('wrapped line', text)
        self.assertEqual(source.read_bytes(), original_bytes)
        self.window.undo()
        self.assertIn('Second wrapped line', self.window.document.doc[0].get_text())
        self.assertFalse(self.window.document.dirty)

    def test_inline_paragraph_drag_snaps_and_saves_through_main_window(self):
        source = self.open_paragraph_fixture()
        original_bytes = source.read_bytes()
        view = self.window.view
        view.set_snap_enabled(True)
        item = view.text_placement
        original_rect = view.text_rect()
        start = view.mapFromScene(item.mapToScene(QPointF(15, -2)))
        finish = view.mapFromScene(view.pages[0].mapToScene(QPointF(58, 328)))
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), finish)
        self.assertAlmostEqual(item.x(), 40, delta=.01)
        self.assertIn((0, 40), view.snap_guides)
        self.assertIs(item.proxy.widget(), self.window.text_value)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=finish)
        self.assertTrue(self.window.text_selection_dirty)
        self.assertFalse(view.snap_guides)
        target = self.root / 'paragraph-snapped.pdf'
        with patch.object(self.window, 'output_path', return_value=str(target)):
            self.assertTrue(self.window.save_as())
        with pymupdf.open(target) as saved:
            rect = saved[0].search_for('First wrapped line')[0]
            self.assertAlmostEqual(rect.x0, 40, delta=.01)
            self.assertGreater(rect.y0, 300)
            self.assertIn('Separate paragraph', saved[0].get_text())
        self.assertEqual(source.read_bytes(), original_bytes)
        self.window.undo()
        restored = self.window.document.doc[0].search_for('First wrapped line')[0]
        self.assertAlmostEqual(restored.y0, original_rect.y0, delta=.01)

    def test_rotated_paragraph_selection_and_pending_save(self):
        source = self.open_paragraph_fixture(rotation=90)
        original_bytes = source.read_bytes()
        self.assertIn('First wrapped line\nSecond wrapped line', self.window.text_value.toPlainText())
        self.assertEqual(self.window.view.text_placement.rotation, 90)
        self.window.text_value.setPlainText('Saved first\nSaved second')
        target = self.root / 'paragraph-saved.pdf'
        with patch.object(self.window, 'output_path', return_value=str(target)):
            self.assertTrue(self.window.save_as())
        with pymupdf.open(target) as saved:
            self.assertEqual(saved[0].rotation, 90)
            self.assertIn('Saved first', ' '.join(saved[0].get_text().split()))
            self.assertIn('Saved second', ' '.join(saved[0].get_text().split()))
            self.assertNotIn('wrapped line', saved[0].get_text())
        self.assertEqual(source.read_bytes(), original_bytes)

    def test_moved_paragraph_preview_matches_apply_and_escape_preserves_edit(self):
        source = self.open_paragraph_fixture()
        original_bytes = source.read_bytes()
        self.window.text_value.setPlainText('Moved first\nMoved second')
        self.window.view.text_placement.setPos(80, 280)
        self.window.text_geometry_changed()
        self.window.view.render_text_preview()
        preview = self.window.view.text_preview_document
        self.assertIsNotNone(preview)
        preview_text = preview[0].get_text()
        # The PDF preview erases original glyphs; the embedded editor draws the
        # new content directly on the page until the selection is finished.
        self.assertIn('Moved second', self.window.view.text_placement.editor.toPlainText())
        self.assertNotIn('wrapped line', preview_text)
        self.assertIn('wrapped line', self.window.document.doc[0].get_text())
        self.assertFalse(self.window.document.dirty)
        self.window.escape()
        self.assertIsNone(self.window.view.text_placement)
        self.assertIsNone(self.window.view.text_preview_document)
        self.assertTrue(self.window.document.dirty)
        applied = self.window.document.doc[0]
        self.assertIn('Moved second', ' '.join(applied.get_text().split()))
        self.assertAlmostEqual(applied.search_for('Moved')[0].x0, 80, delta=.1)
        self.assertAlmostEqual(applied.search_for('Moved')[0].y0, 280, delta=1)
        self.assertEqual(source.read_bytes(), original_bytes)

    def test_korean_pdf_font_name_matches_family_for_multiline_edits(self):
        from adf.dialogs import resolve_font_file
        from adf.text_groups import text_group_at
        fontfile = resolve_font_file('Malgun Gothic')
        if not fontfile:
            self.skipTest('Malgun Gothic is not installed on this platform')
        source = self.root / 'korean.pdf'
        with pymupdf.open() as doc:
            page = doc.new_page(width=500, height=620)
            page.insert_text((40, 70), '첫 번째 줄입니다.\n두 번째 줄입니다.', fontsize=14,
                             fontname='Korean', fontfile=fontfile, lineheight=1.4)
            doc.save(source)
        self.assertTrue(self.window.open_path(source))
        self.window.actions['text'].trigger()
        self.window.edit_text(0, text_group_at(self.window.document.doc[0], (45, 65)))
        self.assertEqual(self.window.edit_font.source, 'PDF 내장')
        self.assertIn('MalgunGothic', self.window.text_font.currentText().replace(' ', ''))
        self.assertEqual(self.window.text_value.toPlainText(), '첫 번째 줄입니다.\n두 번째 줄입니다.')
        self.window.text_value.setPlainText('문단을 수정했습니다.\n줄바꿈도 유지합니다.')
        self.assertTrue(self.window.apply_text())
        result = self.window.document.doc[0].get_text()
        self.assertIn('문단을 수정했습니다.', ' '.join(result.split()))
        self.assertIn('줄바꿈도 유지합니다.', ' '.join(result.split()))

    def test_merge_table_metadata_duplicate_ranges_and_reordering(self):
        dialog = MergeDialog(parent=self.window, paths=[self.source, self.source])
        self.assertEqual([dialog.file_list.headerItem().text(i) for i in range(5)],
                         ["이름", "페이지 범위", "크기", "수정 날짜", "상태"])
        first = dialog.file_list.item(0)
        self.assertEqual(first.text(0), self.source.name)
        self.assertEqual(first.toolTip(0), str(self.source.resolve()))
        self.assertIn("6페이지", first.text(1))
        self.assertIn("KB", first.text(2))
        self.assertRegex(first.text(3), r"\d{4}-\d{2}-\d{2}")
        self.assertEqual(first.text(4), "준비됨")

        def select_range(picker):
            picker.range_input.setText("2, 4-5")
            picker.accept()
            return QDialog.DialogCode.Accepted

        first.setSelected(True)
        with patch.object(MergePageRangeDialog, "exec", select_range):
            dialog.choose_pages()
        self.assertEqual(dialog.selected_ranges, [[1, 3, 4], None])
        self.assertIn("9페이지", dialog.summary.text())
        dialog.file_list.move_rows([0], 2)
        self.assertEqual(dialog.selected_ranges, [None, [1, 3, 4]])
        self.assertEqual(dialog.paths, [str(self.source), str(self.source)])
        dialog.reject()
        self.assert_source_unchanged()

    def test_inline_click_type_ime_font_and_final_save_on_lightweight_pdf(self):
        from adf.text_groups import text_group_at
        from PySide6.QtGui import QColor, QFont
        self.window.set_view_mode('single')
        self.window.view.fit('page')
        self.window.actions['text'].trigger()
        self.assertTrue(self.window.textbar.isHidden())
        preview_errors = []
        self.window.view.textPreviewError.connect(preview_errors.append)
        for index in range(4):
            self.window.view.goto(index)
            QTest.qWait(20)
            page = self.window.document.doc[index]
            group = text_group_at(page, (45, 55))
            rect = pymupdf.Rect(group['bbox'])
            point = self.window.view.mapFromScene(self.window.view.pages[index].mapToScene(QPointF(rect.x0+8, rect.y0+8)))
            QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
            self.assertFalse(self.window.textbar.isHidden())
            self.assertIs(self.window.view.text_placement.proxy.widget(), self.window.text_value)
            self.assertEqual(self.window.textbar.layout().indexOf(self.window.text_value), -1)
            self.window.text_font.setCurrentFont(QFont('Malgun Gothic'))
            self.window.text_size.setValue(16)
            self.window.text_color.color = QColor('#245ccc')
            self.window.text_color.changed.emit()
            QTest.keyClick(self.window.text_value, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClicks(self.window.text_value, f'Edited {index+1}')
            QTest.keyClick(self.window.text_value, Qt.Key.Key_Return)
            ime = QInputMethodEvent()
            ime.setCommitString('한글 수정')
            QApplication.sendEvent(self.window.text_value, ime)
            self.assertEqual(self.window.text_value.toPlainText(), f'Edited {index+1}\n한글 수정')
            QTest.qWait(90)
            self.assertEqual(self.window.edit_font.label, 'Malgun Gothic')
            self.assertEqual(self.window.text_value.font().family(), self.window.edit_font_family)
            blank = self.window.view.mapFromScene(self.window.view.pages[index].mapToScene(QPointF(290, 400)))
            QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=blank)
            self.assertTrue(self.window.textbar.isHidden())
            self.assertIsNone(self.window.view.text_placement)
            words = ' '.join(self.window.document.doc[index].get_text().split())
            self.assertIn(f'Edited {index+1}', words)
            self.assertIn('한글 수정', words)
        self.assertEqual(preview_errors, [])
        output = self.root / 'typed-saved.pdf'
        with patch.object(self.window, 'output_path', return_value=str(output)):
            self.assertTrue(self.window.save_as())
        with pymupdf.open(output) as saved:
            for index in range(4):
                self.assertIn(f'Edited {index+1}', ' '.join(saved[index].get_text().split()))
                self.assertIn('한글 수정', ' '.join(saved[index].get_text().split()))
        self.assert_source_unchanged()

    def test_inline_delete_and_undo_edit_text_without_deleting_pages(self):
        self.open_paragraph_fixture()
        editor = self.window.text_value
        QTest.keyClick(editor, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(editor, 'Typing')
        QTest.keyClick(editor, Qt.Key.Key_Home)
        QTest.keyClick(editor, Qt.Key.Key_Delete)
        self.assertEqual(editor.toPlainText(), 'yping')
        self.assertEqual(self.window.document.page_count, 1)
        QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.toPlainText(), 'Typing')
        self.window.finish_text_selection()

    def test_view_icons_direction_footer_and_single_page_wheel(self):
        for mode in ('single', 'continuous', 'grid'):
            self.window.set_view_mode(mode)
            original_start = self.window.view.start_right
            for button in self.window.direction_buttons.values():
                self.assertFalse(button.isEnabled())
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            self.assertEqual(self.window.view.mode, mode)
            self.assertEqual(self.window.view.start_right, original_start)
        self.window.set_view_mode('spread')
        self.assertTrue(all(button.isEnabled() for button in self.window.direction_buttons.values()))
        self.window.set_view_mode('spread_continuous')
        self.assertTrue(all(button.isEnabled() for button in self.window.direction_buttons.values()))
        QTest.mouseClick(self.window.direction_buttons[False], Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.view.mode, 'spread_continuous')
        self.assertLess(self.window.view.pages[0].x(), self.window.view.pages[1].x())
        QTest.mouseClick(self.window.direction_buttons[True], Qt.MouseButton.LeftButton)
        self.assertGreater(self.window.view.pages[0].x(), self.window.view.pages[1].x())
        self.assertLess(self.window.view.pages[0].y(), self.window.view.pages[1].y())
        self.assertLess(self.window.view.pages[1].x(), self.window.view.pages[2].x())
        self.assertTrue(self.window.direction_buttons[True].isChecked())
        QTest.mouseClick(self.window.view_buttons['single'], Qt.MouseButton.LeftButton)
        self.window.view.goto(0)
        self.window.view.fit('page')
        self.app.processEvents()
        view = self.window.view
        def wheel(delta):
            event = QWheelEvent(QPointF(50,50), QPointF(view.mapToGlobal(QPoint(50,50))), QPoint(), QPoint(0,delta),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(view.viewport(), event)
        wheel(-120)
        self.assertEqual(self.window.current, 1)
        wheel(120)
        self.assertEqual(self.window.current, 0)
        self.assertGreater(self.window.page_spin.mapTo(self.window, QPoint()).y(), view.mapTo(self.window, view.rect().bottomLeft()).y())
        self.assertEqual(list(self.window.side_buttons), ['rotate_left','rotate','blank','replace','delete'])
        self.window.actions['rotate_left'].trigger()
        self.assertEqual(self.window.document.doc[0].rotation, 270)
        self.window.actions['rotate'].trigger()
        self.assertEqual(self.window.document.doc[0].rotation, 0)
        self.assert_source_unchanged()

    def test_two_page_slots_and_wheel_navigation_keep_pdf_order(self):
        view = self.window.view
        for start_right, expected in [(False, [[0, 1], [2, 3], [4, 5]]),
                                      (True, [[0], [1, 2], [3, 4], [5]])]:
            with self.subTest(start_right=start_right):
                view.start_right = start_right
                view.set_mode('spread')
                view.goto(0)
                view.fit('page')
                def wheel(delta):
                    event = QWheelEvent(QPointF(50,50), QPointF(view.mapToGlobal(QPoint(50,50))), QPoint(), QPoint(0,delta),
                                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                    QApplication.sendEvent(view.viewport(), event)
                for row in expected:
                    self.assertEqual([p.index for p in view.pages if p.isVisible()], row)
                    if len(row) == 2:
                        self.assertLess(view.pages[row[0]].x(), view.pages[row[1]].x())
                    elif row == [0]:
                        self.assertGreater(view.pages[0].x(), view.sceneRect().center().x())
                        self.assertIsNone(view.page_at(QPointF(100, 100)))
                    else:
                        self.assertLess(view.pages[5].sceneBoundingRect().right(), view.sceneRect().center().x())
                    wheel(-120)
                for row in reversed(expected[:-1]):
                    wheel(120)
                    self.assertEqual([p.index for p in view.pages if p.isVisible()], row)
                self.assertEqual(view.current, 0)
                view.set_mode('spread_continuous')
                self.assertEqual([p.index for p in view.pages if p.isVisible()], list(range(6)))
                self.assertGreater(view.verticalScrollBar().maximum(), 0)
                for first, second in zip(expected, expected[1:]):
                    self.assertLess(view.pages[first[0]].y(), view.pages[second[0]].y())
                # Ordinary single-page modes never inherit the spread blanks.
                view.set_mode('single')
                self.assertEqual([p.index for p in view.pages if p.isVisible()], [0])
                self.assertEqual(self.window.document.page_count, 6)
        self.assert_source_unchanged()

    def test_zoomed_spread_scrolls_before_turning_and_stops_at_bounds(self):
        view = self.window.view
        view.start_right = True
        view.set_mode('spread')
        view.goto(1)
        view.set_zoom(2)
        scroll = view.verticalScrollBar()
        scroll.setValue(scroll.minimum())
        event = QWheelEvent(QPointF(50,50), QPointF(view.mapToGlobal(QPoint(50,50))), QPoint(), QPoint(0,-120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(view.viewport(), event)
        self.assertEqual(view.current_group(), [1, 2])
        self.assertGreater(scroll.value(), scroll.minimum())
        scroll.setValue(scroll.maximum())
        QApplication.sendEvent(view.viewport(), event)
        self.assertEqual(view.current_group(), [3, 4])
        view.goto(5)
        view.navigate(1)
        self.assertEqual(view.current, 5)
        view.goto(0)
        view.navigate(-1)
        self.assertEqual(view.current, 0)

    def test_page_hover_gap_hides_during_external_drop_and_inserts_at_slot(self):
        image = self.root / 'drop.png'
        Image.new('RGB', (80, 100), 'red').save(image)
        thumbs = self.window.thumbnails
        normal_height = thumbs.item(0).sizeHint().height()
        thumbs.set_hover_slot(1)
        self.assertTrue(thumbs.insert_button.isVisible())
        self.assertEqual(thumbs.item(0).sizeHint().height(), normal_height + 40)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(image))])
        point = QPoint(50, normal_height - 20)
        enter = QDragEnterEvent(point, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        thumbs.dragEnterEvent(enter)
        self.assertTrue(enter.isAccepted())
        self.assertFalse(thumbs.insert_button.isVisible())
        self.assertEqual(thumbs.item(0).sizeHint().height(), normal_height)
        move = QDragMoveEvent(point, Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        thumbs.dragMoveEvent(move)
        self.assertIsNotNone(thumbs.drop_y)
        drop = QDropEvent(QPointF(point), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        thumbs.dropEvent(drop)
        self.assertEqual(self.window.document.page_count, 7)
        self.assertEqual(self.window.document.doc[1].get_text(), '')
        self.assertEqual(len(self.window.document.doc[1].get_images()), 1)
        self.assertIn('needle page 2', self.window.document.doc[2].get_text())
        self.assert_source_unchanged()

    def test_mixed_file_order_and_clipboard_page_are_one_edit_each(self):
        png, jpg = self.root / 'one.png', self.root / 'two.jpg'
        Image.new('RGBA', (80,100), (255,0,0,128)).save(png)
        Image.new('RGB', (120,80), 'blue').save(jpg)
        def order(dialog):
            self.assertTrue(dialog.allow_images)
            self.assertEqual(dialog.insert_index, 2)
            dialog.file_list.move_rows([2], 0)
            self.assertEqual(dialog.paths, [str(self.source), str(png), str(jpg)])
            dialog.accept()
            return QDialog.DialogCode.Accepted
        with patch.object(MergeDialog, 'exec', order):
            self.window.add_pdf(paths=[str(png),str(jpg),str(self.source)], insert_index=2)
        self.assertEqual(self.window.document.page_count, 14)
        self.assertIn('needle page 1', self.window.document.doc[2].get_text())
        self.assertEqual(len(self.window.document.doc[8].get_images()), 1)
        self.window.undo()
        self.assertEqual(self.window.document.page_count, 6)
        previous_clipboard = QMimeData()
        clipboard_mime = QApplication.clipboard().mimeData()
        if clipboard_mime:
            for mime_format in clipboard_mime.formats():
                previous_clipboard.setData(mime_format, clipboard_mime.data(mime_format))
        QApplication.clipboard().setImage(QImage(str(png)))
        try:
            self.window.insert_clipboard_page(0)
            self.assertEqual(self.window.document.page_count, 7)
            self.assertEqual(len(self.window.document.doc[0].get_images()), 1)
            self.assertIn('needle page 1', self.window.document.doc[1].get_text())
            self.window.undo()
            self.assertEqual(self.window.document.page_count, 6)
        finally:
            # Qt's headless clipboard can crash at interpreter shutdown when
            # owning a Python QMimeData. Only the real desktop needs restoring.
            if self.app.platformName() != 'offscreen' and previous_clipboard.formats():
                QApplication.clipboard().setMimeData(previous_clipboard)
            else:
                QApplication.clipboard().clear()
        self.assert_source_unchanged()

    def test_middle_clipboard_page_after_image_and_pending_text_edit_round_trips(self):
        from adf.text_groups import text_group_at

        png = self.root / 'clipboard-middle.png'
        Image.new('RGBA', (80, 100), (255, 0, 0, 128)).save(png)
        self.assertTrue(self.window.edit(lambda: self.window.document.add_image(
            0, (180, 180, 240, 255), png.read_bytes())))
        self.window.view.goto(1)
        self.window.actions['text'].trigger()
        self.window.edit_text(1, text_group_at(self.window.document.doc[1], (45, 55)))
        self.window.text_value.setPlainText('Changed middle page')
        self.assertTrue(self.window.text_selection_dirty)

        clipboard = Mock()
        mime = QMimeData()
        mime.setImageData(QImage(str(png)))
        clipboard.mimeData.return_value = mime
        clipboard.image.return_value = QImage(str(png))

        def choose_clipboard(menu, *args):
            action = next(action for action in menu.actions() if '클립보드' in action.text())
            self.assertTrue(action.isEnabled())
            action.trigger()

        class ClipboardMenu(QMenu):
            def exec(menu, *args):
                choose_clipboard(menu, *args)

        # Follow the plus button and its menu, including committing a pending
        # paragraph edit before inserting between the second and third pages.
        self.window.thumbnails.set_hover_slot(2)
        with patch.object(QApplication, 'clipboard', return_value=clipboard), \
             patch('adf.app.QMenu', ClipboardMenu):
            self.window.thumbnails.request_insertion()
        self.assertEqual(self.window.document.page_count, 7)
        self.assertEqual(self.window.current, 2)
        self.assertIsNone(self.window.thumbnails.hover_slot)
        self.assertIsNone(self.window.view.text_placement)
        self.assertIn('Changed middle page', ' '.join(self.window.document.doc[1].get_text().split()))
        self.assertEqual(len(self.window.document.doc[0].get_images()), 1)
        self.assertEqual(len(self.window.document.doc[2].get_images()), 1)
        self.assertIn('needle page 3', self.window.document.doc[3].get_text())
        self.wait_until(lambda: self.window.view.pages[2].pixmap is not None)

        self.window.undo()
        self.assertEqual(self.window.document.page_count, 6)
        self.assertIn('Changed middle page', ' '.join(self.window.document.doc[1].get_text().split()))
        self.assertEqual(len(self.window.document.doc[0].get_images()), 1)
        self.assertIn('needle page 3', self.window.document.doc[2].get_text())
        self.window.redo()
        self.assertEqual(self.window.document.page_count, 7)
        output = self.root / 'clipboard-middle-saved.pdf'
        with patch.object(self.window, 'output_path', return_value=str(output)):
            self.assertTrue(self.window.save_as())
        self.window.close_document()
        self.assertTrue(self.window.open_path(output))
        self.assertEqual(self.window.document.page_count, 7)
        self.assertFalse(self.window.document.dirty)
        self.assertIn('Changed middle page', ' '.join(self.window.document.doc[1].get_text().split()))
        self.assertEqual(len(self.window.document.doc[0].get_images()), 1)
        pixel = self.window.document.doc[2].get_pixmap().pixel(20, 20)
        self.assertGreater(pixel[0], 245)
        self.assertTrue(110 <= pixel[1] <= 140)
        self.assertTrue(110 <= pixel[2] <= 140)
        self.assert_source_unchanged()

    def test_clipboard_file_urls_insert_selected_pdf_and_image_in_middle(self):
        added = self.root / 'clipboard-pages.pdf'
        with pymupdf.open() as doc:
            doc.new_page().insert_text((40, 60), 'Clipboard first')
            doc.new_page().insert_text((40, 60), 'Clipboard second')
            doc.save(added)
        png = self.root / 'clipboard-file.png'
        Image.new('RGB', (100, 80), 'blue').save(png)
        original_pdf, original_image = added.read_bytes(), png.read_bytes()
        self.assertTrue(self.window.edit(lambda: self.window.document.rotate([3], 90)))
        clipboard = Mock()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(added)), QUrl.fromLocalFile(str(png))])
        clipboard.mimeData.return_value = mime

        def select_pages(dialog):
            self.assertEqual(dialog.insert_index, 3)
            self.assertEqual(dialog.paths, [str(added), str(png)])
            dialog.file_list.item(0).setData(0, Qt.ItemDataRole.UserRole + 3, [1])
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(QApplication, 'clipboard', return_value=clipboard), \
             patch.object(MergeDialog, 'exec', select_pages):
            self.window.insert_clipboard_page(3)
        clipboard.image.assert_not_called()
        self.assertEqual(self.window.document.page_count, 8)
        self.assertIn('Clipboard second', self.window.document.doc[3].get_text())
        self.assertEqual(len(self.window.document.doc[4].get_images()), 1)
        self.assertIn('needle page 4', self.window.document.doc[5].get_text())
        self.assertEqual(self.window.document.doc[5].rotation, 90)
        self.window.undo()
        self.assertEqual(self.window.document.page_count, 6)
        self.assertEqual(self.window.document.doc[3].rotation, 90)
        self.window.redo()
        output = self.root / 'clipboard-files-saved.pdf'
        with patch.object(self.window, 'output_path', return_value=str(output)):
            self.assertTrue(self.window.save_as())
        with pymupdf.open(output) as saved:
            self.assertEqual(saved.page_count, 8)
            self.assertIn('Clipboard second', saved[3].get_text())
            self.assertEqual(saved[4].get_pixmap().pixel(20, 20), (0, 0, 255))
            self.assertEqual(saved[5].rotation, 90)
        self.assertEqual(added.read_bytes(), original_pdf)
        self.assertEqual(png.read_bytes(), original_image)
        self.assert_source_unchanged()

    def test_merge_invalid_files_and_page_ranges_disable_submission(self):
        invalid_file = self.root / "not-a-pdf.txt"
        invalid_file.write_text("not a PDF", encoding="utf-8")
        dialog = MergeDialog(parent=self.window, paths=[self.source, self.source, invalid_file])
        submit = dialog.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.assertFalse(submit.isEnabled())
        self.assertEqual(dialog.file_list.item(2).text(4), "읽기 실패")
        dialog.file_list.item(2).setSelected(True)
        dialog.remove_selected()
        self.assertTrue(submit.isEnabled())
        picker = MergePageRangeDialog(self.source, parent=dialog)
        picker.range_input.setText("0-99")
        self.assertFalse(picker.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        self.assertEqual(picker.thumbnails.selected_pages, [])
        picker.accept()
        self.assertEqual(picker.result(), QDialog.DialogCode.Rejected)
        picker.reject()
        self.assertTrue(picker.doc.is_closed)
        self.assertEqual(dialog.selected_ranges, [None, None])
        dialog.reject()

    def test_standalone_merge_and_split_submit_hooks_keep_dialog_open(self):
        merge = MergeDialog(parent=self.window, paths=[self.source, self.source])
        split = SplitDialog(self.window.document, parent=self.window)
        submitted = []
        for name, dialog in (("merge", merge), ("split", split)):
            dialog.submit_handler = lambda label=name: submitted.append(label)
            dialog.show()
            dialog.accept()
            self.assertTrue(dialog.isVisible())
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            dialog.reject()
        self.assertEqual(submitted, ["merge", "split"])

    def test_merge_selected_ranges_of_duplicate_files_reach_worker(self):
        output = self.root / "selected-merged.pdf"

        def select_range(picker):
            picker.range_input.setText("2, 4")
            picker.accept()
            return QDialog.DialogCode.Accepted

        def merge_accept(dialog):
            dialog.file_list.item(0).setSelected(True)
            dialog.choose_pages()
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(MergeDialog, "exec", merge_accept), \
             patch.object(MergePageRangeDialog, "exec", select_range), \
             patch.object(QFileDialog, "getSaveFileName", return_value=(str(output), "")):
            self.window.merge(paths=[str(self.source), str(self.source)])
            self.wait_until(lambda: self.window.worker is None)
        self.assertEqual(self.window.document.page_count, 8)
        self.assertIn("needle page 2", self.window.document.doc[0].get_text())
        self.assertIn("needle page 4", self.window.document.doc[1].get_text())
        self.assertIn("needle page 1", self.window.document.doc[2].get_text())
        self.assert_source_unchanged()

    def test_canceled_dialog_timers_do_not_touch_closed_document(self):
        dialogs = [NumberingDialog(self.window.document, parent=self.window),
                   SplitDialog(self.window.document, parent=self.window)]
        for dialog in dialogs:
            dialog.show()
            self.app.processEvents()
            dialog.reject()
        self.window.close_document()
        QTest.qWait(120)
        self.assertFalse(dialogs[0]._preview_timer.isActive())
        self.assertFalse(dialogs[1].thumbnails._timer.isActive())

    def test_print_page_range_to_pdf_preserves_selected_page_artwork(self):
        source = self.root / "print-source.pdf"
        output = self.root / "printed.pdf"
        colors = [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0)]
        with pymupdf.open() as doc:
            for index, color in enumerate(colors):
                page = doc.new_page(width=360, height=480)
                page.insert_text((40, 60), f"Print page {index + 1}")
                page.draw_rect((100, 150, 260, 330), color=color, fill=color)
            doc.save(source)
        self.assertTrue(self.window.open_path(source))

        def accept_pdf_printer(dialog):
            printer = dialog.printer()
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(output))
            return QDialog.DialogCode.Accepted

        def select_pages(dialog):
            dialog.target.setCurrentIndex(dialog.target.findData('range'))
            dialog.ranges.setText('2-3')
            dialog.accept()
            return QDialog.DialogCode.Accepted

        with patch.object(PrintOptionsDialog, 'exec', select_pages), \
             patch("adf.app.QPrintDialog.exec", accept_pdf_printer):
            self.window.print_document()
        with pymupdf.open(output) as printed:
            self.assertEqual(printed.page_count, 2)
            for index, expected in enumerate(((0, 255, 0), (0, 0, 255))):
                pixmap = printed[index].get_pixmap(colorspace=pymupdf.csRGB)
                observed = pixmap.pixel(pixmap.width // 2, pixmap.height // 2)
                for actual, wanted in zip(observed, expected):
                    self.assertLess(abs(actual - wanted), 10)


if __name__ == "__main__":
    unittest.main()
