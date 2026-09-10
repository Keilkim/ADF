import io
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, QTimer, Qt, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QFileDialog

from adf.app import MainWindow
from adf.compare_widgets import CompareDialog
from adf.stamps import StampLibrary
from adf.stamp_widgets import StampDialog
from adf.document import PdfDocument


def stamp_data():
    image = Image.new('RGBA', (100, 50), (0,0,0,0))
    image.paste((220,25,45,255), (10,10,90,40))
    stream = io.BytesIO()
    image.save(stream, 'PNG')
    return stream.getvalue()


class StampLibraryTests(unittest.TestCase):
    def test_persistence_transparency_update_delete_and_restore(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'library.sqlite3'
            library = StampLibrary(path)
            stamp = library.add('회사 직인', stamp_data(), 20)
            reopened = StampLibrary(path)
            self.assertEqual(reopened.get(stamp.id), stamp)
            with Image.open(io.BytesIO(stamp.png)) as image:
                self.assertEqual(image.getpixel((0,0))[3], 0)
                self.assertEqual(image.getpixel((50,25))[3], 255)
            reopened.update(stamp.id, '대표 서명', 37.5)
            updated = library.get(stamp.id)
            self.assertEqual((updated.name, updated.width_mm), ('대표 서명', 37.5))
            removed = library.remove(stamp.id)
            self.assertEqual(library.list(), [])
            library.restore(removed)
            self.assertEqual(reopened.get(stamp.id), updated)

    def test_invalid_image_or_details_do_not_change_library(self):
        with tempfile.TemporaryDirectory() as folder:
            library = StampLibrary(Path(folder)/'library.sqlite3')
            for name, data, width in [('bad', b'no image', 20), (' ', stamp_data(), 20), ('bad', stamp_data(), float('nan'))]:
                with self.assertRaises(ValueError):
                    library.add(name, data, width)
            self.assertEqual(library.list(), [])

    def test_repeated_stamp_undo_save_with_invalid_unreferenced_pdf_dictionary(self):
        with pymupdf.open() as doc:
            doc.new_page(width=300, height=400).insert_text((20,40), 'Keep this text')
            unused = doc.get_new_xref()
            doc.update_object(unused, '<< /Unused 123 >>')
            broken = doc.tobytes().replace(b'/Unused', b'(BADXX)')
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder)/'source.pdf', Path(folder)/'result.pdf'
            source.write_bytes(broken)
            with pymupdf.open(source) as raw:
                with self.assertRaisesRegex(Exception, 'invalid key in dict'):
                    raw.tobytes()
            with PdfDocument() as model:
                model.open(source)
                for index in range(3):
                    model.add_image(0, (20,60+index*60,100,100+index*60), stamp_data())
                    model.doc[0].get_pixmap()
                self.assertEqual(len(model.doc[0].get_image_info()), 3)
                model.undo()
                self.assertEqual(len(model.doc[0].get_image_info()), 2)
                model.redo()
                model.save(output)
                snapshot = model.tobytes()
            with pymupdf.open(output) as saved, pymupdf.open(stream=snapshot, filetype='pdf') as copy:
                self.assertEqual(len(saved[0].get_image_info()), 3)
                self.assertEqual(len(copy[0].get_image_info()), 3)
                self.assertIn('Keep this text', saved[0].get_text())
                self.assertEqual(saved[0].get_images()[0][0], saved[0].get_image_info(xrefs=True)[0]['xref'])
            self.assertEqual(source.read_bytes(), broken)


class FeatureWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        from adf.theme import STYLE
        cls.app.setStyleSheet(STYLE)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='adf-features-')
        self.root = Path(self.folder.name)
        self.left, self.right = self.root/'old.pdf', self.root/'new.pdf'
        for path, value in ((self.left, '1000'), (self.right, '2000')):
            with pymupdf.open() as doc:
                for index in range(3):
                    page = doc.new_page(width=300, height=400)
                    page.insert_text((30,60), f'Page {index+1} amount {value}')
                doc.save(path)
        self.original = self.left.read_bytes(), self.right.read_bytes()
        self.window = MainWindow(smoke=True)
        self.errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, error, tb: self.errors.append(str(error))
        self.window.error = lambda error: self.errors.append(str(error))
        self.window.show()
        self.window.open_path(self.left)
        self.dialog = None
        self.app.processEvents()

    def tearDown(self):
        if self.dialog:
            self.dialog.reject()
            self.wait_until(lambda: self.dialog.worker is None)
            self.dialog.deleteLater()
        with patch.object(self.window, 'maybe_save', return_value=True):
            self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        sys.excepthook = self.old_hook
        self.assertEqual((self.left.read_bytes(), self.right.read_bytes()), self.original)
        self.folder.cleanup()
        self.assertEqual(self.errors, [])

    def wait_until(self, predicate):
        deadline = time.monotonic()+20
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def library(self):
        self.window.show_stamps()
        dock = self.window.stamp_dock
        self.stamp = self.window.stamp_library.add('회사 직인', stamp_data())
        dock.refresh()
        self.app.processEvents()
        return dock

    def click_page(self, index, x, y, button=Qt.MouseButton.LeftButton):
        view = self.window.view
        point = view.mapFromScene(view.pages[index].mapToScene(QPointF(x, y)))
        QTest.mouseMove(view.viewport(), point)
        QTest.mouseClick(view.viewport(), button, pos=point)
        self.app.processEvents()

    def test_registered_card_enters_mode_and_repeated_clicks_stamp_without_apply(self):
        dock = self.library()
        self.window.stamp_library.update(self.stamp.id, '대표 서명', 37.5)
        dock.refresh()
        QTest.mouseClick(dock.cards[self.stamp.id].preview, Qt.MouseButton.LeftButton)
        self.assertEqual(self.window.active_stamp_id, self.stamp.id)
        self.assertTrue(dock.cards[self.stamp.id].property('selected'))
        self.assertIsNone(self.window.view.placement)
        self.assertFalse(self.window.imagebar.isVisible())
        self.assertFalse(self.window.document.dirty)
        view = self.window.view
        view.set_mode('single')
        view.goto(0)
        self.app.processEvents()
        for x, y in ((100,120), (150,190), (180,270)):
            self.click_page(0, x, y)
        self.assertEqual(len(self.window.document.doc[0].get_image_info()), 3)
        self.assertEqual(self.window.active_stamp_id, self.stamp.id)
        self.assertTrue(self.window.document.dirty)
        output = self.root/'stamped.pdf'
        self.window.document.save(output)
        with pymupdf.open(output) as doc:
            images = doc[0].get_image_info()
            self.assertEqual(len(images), 3)
            for info in images:
                self.assertAlmostEqual(pymupdf.Rect(info['bbox']).width*25.4/72, 37.5, places=3)
            bbox = pymupdf.Rect(images[0]['bbox'])
            self.assertEqual(doc[0].get_pixmap().pixel(round(bbox.x0+1), round(bbox.y0+1)), (255,255,255))
        self.window.undo()
        self.assertEqual(len(self.window.document.doc[0].get_image_info()), 2)
        self.window.redo()
        self.assertEqual(len(self.window.document.doc[0].get_image_info()), 3)
        dock.remove(self.stamp.id)
        self.assertIsNone(self.window.active_stamp_id)
        self.assertFalse(self.window.stamp_library.list())
        self.assertEqual(len(self.window.document.doc[0].get_image_info()), 3)
        dock.undo_remove()
        self.assertEqual(self.window.stamp_library.list()[0].name, '대표 서명')

    def test_rotated_stamp_click_center_width_and_esc(self):
        self.window.document.rotate([1], 90)
        self.window.refresh_document()
        dock = self.library()
        dock.cards[self.stamp.id].preview.click()
        self.window.view.set_mode('single')
        self.window.view.goto(1)
        self.app.processEvents()
        self.click_page(1, 120, 100)
        page = self.window.document.doc[1]
        bbox = pymupdf.Rect(page.get_image_info()[-1]['bbox']) * page.rotation_matrix
        self.assertAlmostEqual(bbox.width*25.4/72, 20, places=4)
        self.assertAlmostEqual((bbox.x0+bbox.x1)/2, 120, delta=1)
        self.assertAlmostEqual((bbox.y0+bbox.y1)/2, 100, delta=1)
        self.window.escape()
        self.assertIsNone(self.window.view.stamp_pixmap)
        self.assertIsNone(self.window.active_stamp_id)
        self.assertFalse(dock.cards[self.stamp.id].property('selected'))

    def test_refitting_after_navigation_ignores_stale_mouse_anchor(self):
        view = self.window.view
        point = view.mapFromScene(view.pages[0].mapToScene(QPointF(100, 100)))
        QTest.mouseMove(view.viewport(), point)
        view.goto(1)
        view.set_zoom(view.transform().m11()*.9, manual=False)
        self.window.show_stamps()
        self.wait_until(lambda: view.pages[1].pixmap is not None)
        self.assertEqual(self.window.current, 1)

    def test_library_management_works_without_document_and_place_is_disabled(self):
        self.window.close_document()
        dock = self.library()
        self.assertTrue(self.window.actions['stamps'].isEnabled())
        self.assertTrue(self.window.actions['compare'].isEnabled())
        self.assertFalse(dock.cards[self.stamp.id].preview.isEnabled())
        self.assertTrue(dock.register.isEnabled())

    def test_registration_cancel_and_edit_use_same_popup_and_hover_actions(self):
        self.window.show_stamps()
        dock = self.window.stamp_dock
        png = self.root/'signature.png'
        png.write_bytes(stamp_data())
        def register(dialog):
            self.assertEqual(dialog.width.value(), 20)
            self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled())
            self.assertTrue(dialog.load_file(png))
            dialog.name.setText('등록한 도장')
            dialog.accept()
            return dialog.result()
        with patch.object(StampDialog, 'exec', register):
            dock.register.click()
        self.assertEqual(len(dock.cards), 1)
        self.assertIsNone(self.window.active_stamp_id)
        stamp = self.window.stamp_library.list()[0]
        card = dock.cards[stamp.id]
        card.preview.click()
        self.assertTrue(card.property('selected'))
        QTest.mouseMove(card, QPoint(15,15))
        self.app.processEvents()
        self.assertTrue(card.edit.isVisible())
        self.assertTrue(card.delete.isVisible())
        self.assertLess(card.height(), 160)
        self.window.view.setFocus()
        QTest.mouseMove(self.window.view.viewport(), QPoint(10, 10))
        self.app.processEvents()
        self.assertFalse(card.edit.isVisible())
        self.assertFalse(card.delete.isVisible())
        QTest.mouseMove(card, QPoint(15, 15))
        self.app.processEvents()
        def edit(dialog):
            self.assertEqual(dialog.windowTitle(), '도장 관리')
            self.assertEqual(dialog.name.text(), '등록한 도장')
            dialog.name.setText('수정한 도장')
            dialog.width.setValue(35)
            dialog.accept()
            return dialog.result()
        with patch.object(StampDialog, 'exec', edit):
            card.edit.click()
        updated = self.window.stamp_library.get(stamp.id)
        self.assertEqual((updated.name, updated.width_mm), ('수정한 도장', 35))
        with patch.object(StampDialog, 'exec', return_value=QDialog.DialogCode.Rejected):
            dock.register.click()
        self.assertEqual(len(self.window.stamp_library.list()), 1)

    def test_stamp_mode_excludes_outside_clicks_and_exits_on_right_click_or_tools(self):
        dock = self.library()
        dock.cards[self.stamp.id].preview.click()
        QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(1,1))
        self.assertFalse(self.window.document.dirty)
        QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.RightButton, pos=QPoint(1,1))
        self.assertIsNone(self.window.active_stamp_id)
        dock.cards[self.stamp.id].preview.click()
        self.window.actions['text'].trigger()
        self.assertIsNone(self.window.active_stamp_id)
        self.assertIsNone(self.window.view.stamp_pixmap)
        dock.cards[self.stamp.id].preview.click()
        dock.hide()
        self.assertIsNone(self.window.active_stamp_id)

    def compare(self):
        self.dialog = CompareDialog(self.window, str(self.left))
        self.dialog.paths[1].setText(str(self.right))
        self.dialog.show()
        self.app.processEvents()
        self.dialog.start_comparison()
        self.wait_until(lambda: self.dialog.worker is None)
        self.assertIsNotNone(self.dialog.result, self.dialog.status.text())
        return self.dialog

    def test_compare_real_worker_navigation_zoom_scroll_and_input_reset(self):
        dialog = self.compare()
        self.assertEqual(dialog.result['changed_pages'], 3)
        self.assertIsNone(dialog.job)
        dialog.navigate(1)
        self.assertEqual(dialog.changes.currentRow(), 1)
        dialog.zoom.setCurrentIndex(dialog.zoom.findData(2.))
        self.app.processEvents()
        self.assertEqual(dialog.views[0].transform().m11(), dialog.views[1].transform().m11())
        left, right = (view.verticalScrollBar() for view in dialog.views)
        left.setValue(left.maximum()//2)
        self.assertLessEqual(abs(left.value()/max(1,left.maximum())-right.value()/max(1,right.maximum())), .02)
        dialog.pages.setCurrentIndex(2)
        self.assertEqual(dialog.current_pair, 2)
        dialog.highlights.setChecked(False)
        self.assertFalse(dialog.views[0].overlays)
        dialog.highlights.setChecked(True)
        self.assertTrue(dialog.views[0].overlays)
        dialog.paths[1].setText(str(self.left))
        self.assertIsNone(dialog.result)
        self.assertFalse(dialog.documents)
        self.assertEqual(dialog.changes.count(), 0)

    def test_comparison_failure_and_cancel_leave_no_job_or_results(self):
        self.dialog = CompareDialog(self.window, str(self.left))
        self.dialog.paths[1].setText(str(self.root/'missing.pdf'))
        self.dialog.start_comparison()
        self.assertIsNone(self.dialog.worker)
        self.assertIsNone(self.dialog.job)
        self.assertFalse(self.dialog.documents)
        self.dialog.paths[1].setText(str(self.right))
        self.dialog.start_comparison()
        self.dialog.cancel_comparison()
        self.wait_until(lambda: self.dialog.worker is None)
        self.assertIsNone(self.dialog.job)
        self.assertIsNone(self.dialog.result)
        self.assertIn('취소', self.dialog.status.text())
