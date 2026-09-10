"""Ink survives PDF save/undo and uses page coordinates for mouse and tablet input."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pymupdf
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QInputDevice, QPointingDevice, QTabletEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from adf.app import MainWindow
from adf.document import PdfDocument
from adf.ink import read_ink


class PenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='adf-pen-')
        self.root = Path(self.temp.name)
        self.source = self.root/'source.pdf'
        with pymupdf.open() as doc:
            for rotation in (0, 90, 180, 270):
                page = doc.new_page(width=420, height=540)
                page.set_cropbox(pymupdf.Rect(20, 30, 400, 510))
                page.set_rotation(rotation)
            doc.save(self.source)
        self.original = self.source.read_bytes()
        self.window = MainWindow(smoke=True)
        self.errors = []
        self.window.error = lambda error: self.errors.append(str(error))
        self.window.show()
        self.window.open_path(self.source)
        self.window.set_view_mode('single')
        self.app.processEvents()
        self.window.show_pen_options('pen')
        self.window.pen_menu.hide()
        self.app.processEvents()

    def tearDown(self):
        with patch.object(self.window, 'maybe_save', return_value=True):
            self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def points(self, index=0):
        view = self.window.view
        view.goto(index)
        self.app.processEvents()
        page = view.pages[index]
        return [view.mapFromScene(page.mapToScene(QPointF(x, y)))
                for x, y in [(65, 75), (100, 100), (145, 85)]]

    def draw(self, index=0):
        points = self.points(index)
        viewport = self.window.view.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=points[0])
        QTest.mouseMove(viewport, points[1])
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=points[2])

    def test_mouse_ink_roundtrip_crop_rotations_and_undo(self):
        for index in range(4):
            self.draw(index)
            page = self.window.document.doc[index]
            annotations = list(page.annots())
            self.assertEqual(len(annotations), 1)
            annotation = annotations[0]
            self.assertEqual(annotation.type[0], pymupdf.PDF_ANNOT_INK)
            self.assertTrue(annotation.flags & pymupdf.PDF_ANNOT_IS_PRINT)
            # Rotate saved PDF coordinates back to displayed page coordinates.
            start = pymupdf.Point(annotation.vertices[0][0])*page.rotation_matrix
            self.assertAlmostEqual(start.x, 65, delta=2)
            self.assertAlmostEqual(start.y, 75, delta=2)
            self.assertAlmostEqual(annotation.border['width'], 1.2, places=4)
        self.assertTrue(self.window.document.dirty)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.window.undo()
        self.assertEqual(len(list(self.window.document.doc[3].annots())), 0)
        self.window.redo()
        output = self.root/'ink.pdf'
        with patch.object(self.window, 'output_path', return_value=str(output)):
            self.assertTrue(self.window.save_as())
        with pymupdf.open(output) as saved:
            for page in saved:
                self.assertEqual(len(list(page.annots())), 1)
                self.assertNotEqual(page.get_pixmap(annots=True).samples,
                                    page.get_pixmap(annots=False).samples)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_separate_tool_toggles_and_corner_options(self):
        from PySide6.QtCore import QPoint
        for tool in ('pen', 'eraser'):
            button = getattr(self.window, tool+'_button')
            self.window.change_pointer('select_tool')
            QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
            self.assertEqual(self.window.pointer_mode, tool)
            self.assertEqual(self.window.view.pen.tool, tool)
            self.assertTrue(self.window.view.pen.enabled)
            self.assertFalse(self.window.pen_menu.isVisible())
            QTest.mouseClick(button, Qt.MouseButton.LeftButton, pos=QPoint(12, 12))
            self.assertEqual(self.window.pointer_mode, 'select_tool')
            self.assertFalse(self.window.view.pen.enabled)
            QTest.mouseClick(button.options, Qt.MouseButton.LeftButton)
            self.assertTrue(self.window.pen_menu.isVisible())
            self.assertEqual(self.window.pointer_mode, tool)
            self.assertTrue(self.window.actions[tool].isChecked())
            QTest.keyClick(self.window.pen_menu, Qt.Key.Key_Escape)
            self.assertFalse(self.window.pen_menu.isVisible())
            self.assertTrue(self.window.view.pen.enabled)

    def test_hold_straightens_and_extends_original_direction_on_rotated_pages(self):
        import math
        view = self.window.view
        for index in range(4):
            with self.subTest(rotation=index*90):
                view.goto(index)
                self.app.processEvents()
                item = view.pages[index]
                def position(x, y):
                    return QPointF(view.mapFromScene(item.mapToScene(QPointF(x, y))))
                pen = view.pen
                revision = self.window.document.revision
                pen.begin(position(50, 100))
                pen.append(position(100, 111))
                pen.append(position(200, 115))
                QTest.qWait(pen.HOLD_MS+100)
                self.assertIsNotNone(pen.line_origin)
                self.assertEqual(len(pen.paths[0]), 2)
                self.assertEqual(self.window.document.revision, revision)
                origin, direction = pen.line_origin, pen.line_direction
                pen.append(position(240, 150))
                endpoint = pen.paths[0][-1]
                self.assertAlmostEqual((endpoint[0]-origin[0])*direction[1]-(endpoint[1]-origin[1])*direction[0], 0)
                self.assertGreater(math.dist(origin, endpoint), 160)
                pen.finish(position(240, 150))
                page = self.window.document.doc[index]
                stroke = read_ink(page.first_annot)['paths'][0]
                self.assertEqual(len(stroke), 2)
                shown = [tuple(pymupdf.Point(p)*page.rotation_matrix) for p in stroke]
                self.assertAlmostEqual(shown[1][0], endpoint[0], delta=.02)
                self.assertFalse(pen.hold_timer.isActive())
                self.window.undo()
                self.assertIsNone(self.window.document.doc[index].first_annot)
                self.window.redo()
                page = self.window.document.doc[index]
                self.assertEqual(len(read_ink(page.first_annot)['paths'][0]), 2)
        output = self.root/'straight.pdf'
        self.window.document.save(output)
        with pymupdf.open(output) as saved:
            for page in saved:
                self.assertEqual(len(page.first_annot.vertices[0]), 2)

    def test_hold_preserves_curves_and_release_or_escape_cancels_timer(self):
        view = self.window.view
        points = self.points()
        pen = view.pen
        pen.begin(QPointF(points[0]))
        pen.append(QPointF(points[1]))
        pen.append(QPointF(points[2]))
        QTest.qWait(pen.HOLD_MS+100)
        self.assertIsNone(pen.line_origin)
        self.assertEqual(len(pen.paths[0]), 3)
        pen.finish(QPointF(points[2]))
        self.assertFalse(pen.hold_timer.isActive())
        pen.begin(QPointF(points[0]))
        pen.append(QPointF(points[2]))
        self.window.escape()
        self.assertFalse(pen.hold_timer.isActive())
        self.assertIsNone(pen.page)
        self.assertEqual(len(list(self.window.document.doc[0].annots())), 1)

    def test_width_and_color_preview_updates_for_each_tool_in_compact_popover(self):
        from PySide6.QtGui import QColor
        self.window.show_pen_options('pen')
        menu = self.window.pen_menu
        for index in range(menu.kind.count()):
            menu.kind.setCurrentIndex(index)
            menu.set_color(QColor('#d53939'))
            menu.slider.setValue(1)
            small = menu.preview.grab().toImage()
            menu.slider.setValue(60)
            large = menu.preview.grab().toImage()
            self.assertNotEqual(small, large)
            menu.set_color(QColor('#2869cf'))
            self.assertNotEqual(large, menu.preview.grab().toImage())
        self.assertLessEqual(menu.width(), 304)
        self.assertLessEqual(menu.height(), 470)
        self.window.show_pen_options('eraser')
        menu.slider.setValue(4)
        small = menu.preview.grab().toImage()
        menu.slider.setValue(60)
        self.assertNotEqual(small, menu.preview.grab().toImage())
        self.assertLessEqual(menu.width(), 248)
        self.assertLessEqual(menu.height(), 270)
        menu.hide()

    def test_tablet_events_create_one_stroke_and_save_current_file(self):
        device = QPointingDevice('test stylus', 19, QInputDevice.DeviceType.Stylus,
            QPointingDevice.PointerType.Pen, QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 1)
        viewport = self.window.view.viewport()
        points = self.points()
        for kind, point in zip((QEvent.Type.TabletPress, QEvent.Type.TabletMove, QEvent.Type.TabletRelease), points):
            release = kind == QEvent.Type.TabletRelease
            event = QTabletEvent(kind, device, QPointF(point), QPointF(viewport.mapToGlobal(point)),
                0 if release else .7, 0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier,
                Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton if release else Qt.MouseButton.LeftButton)
            QApplication.sendEvent(viewport, event)
            self.assertTrue(event.isAccepted())
        self.assertEqual(len(list(self.window.document.doc[0].annots())), 1)
        with patch.object(self.window, 'output_path') as picker:
            self.window.actions['save'].trigger()
            picker.assert_not_called()
        self.assertFalse(self.window.document.dirty)
        with pymupdf.open(self.source) as saved:
            self.assertEqual(len(list(saved[0].annots())), 1)

    def test_color_slider_dot_and_escape_cancel(self):
        menu = self.window.pen_menu
        menu.colors.buttons()[1].click()
        menu.slider.setValue(10)
        QTest.keyClick(menu.slider, Qt.Key.Key_Right)
        self.assertEqual(self.window.view.pen.width, 2.2)
        viewport = self.window.view.viewport()
        point = self.points()[0]
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=point)
        page = self.window.document.doc[0]
        annotation = page.first_annot
        self.assertAlmostEqual(annotation.border['width'], 2.2, places=4)
        self.assertGreater(annotation.colors['stroke'][0], .8)
        self.assertNotEqual(page.get_pixmap(annots=True).samples, page.get_pixmap(annots=False).samples)
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=point)
        self.window.escape()
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(len(list(self.window.document.doc[0].annots())), 1)
        self.window.escape()
        self.assertFalse(self.window.view.pen.enabled)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_leaving_page_does_not_join_unrelated_segments(self):
        view = self.window.view
        points = self.points()
        view.pen.begin(QPointF(points[0]))
        view.pen.append(QPointF(view.mapFromScene(view.pages[0].mapToScene(QPointF(-40, 70)))))
        view.pen.append(QPointF(points[1]))
        view.pen.finish(QPointF(points[2]))
        page = self.window.document.doc[0]
        self.assertEqual(len(page.first_annot.vertices), 2)

    def test_fullscreen_and_switching_tools(self):
        self.window.fullscreen.enter()
        self.draw()
        self.assertTrue(self.window.fullscreen.active)
        self.window.fullscreen.exit()
        self.window.toggle_text(True)
        self.assertFalse(self.window.view.pen.enabled)
        self.window.change_pointer('pen')
        self.assertTrue(self.window.view.pen.enabled)
        self.window.change_pointer('hand_tool')
        self.assertFalse(self.window.view.pen.enabled)
        self.assertEqual(len(list(self.window.document.doc[0].annots())), 1)

    def test_save_failure_keeps_original_and_unsaved_state(self):
        self.draw()
        with patch.object(self.window.document, 'save', side_effect=OSError('write failed')):
            self.assertFalse(self.window.save())
        self.assertEqual(self.errors.pop(), 'write failed')
        self.assertTrue(self.window.document.dirty)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_invalid_ink_does_not_change_history(self):
        for paths, color, width in [([[(float('nan'), 5)]], '#252525', 1.2),
                                     ([[(4, 5)]], '#252525', float('inf')),
                                     ([[(4, 5)]], 'wrong', 1.2)]:
            with self.assertRaises(ValueError):
                self.window.document.add_ink(0, paths, color, width)
        self.assertFalse(self.window.document.dirty)
        self.assertFalse(self.window.document.can_undo)

    def test_read_only_pdf_blocks_pen_and_preserves_file(self):
        restricted = self.root/'read-only.pdf'
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(restricted, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                     owner_pw='owner', user_pw='reader', permissions=pymupdf.PDF_PERM_PRINT)
        original = restricted.read_bytes()
        self.window.document.open(restricted, 'reader')
        self.window.refresh_document()
        self.assertFalse(self.window.actions['pen'].isEnabled())
        self.assertFalse(self.window.view.pen.enabled)
        with self.assertRaises(PermissionError):
            self.window.document.add_ink(0, [[(20, 30), (40, 50)]], '#252525', 1.2)
        self.assertEqual(restricted.read_bytes(), original)

    def test_save_new_document_requests_destination(self):
        self.draw()
        self.window.document.path = ''
        output = self.root/'new.pdf'
        with patch.object(self.window, 'output_path', return_value=str(output)) as picker:
            self.assertTrue(self.window.save())
            picker.assert_called_once()
        self.assertTrue(output.is_file())
        self.assertFalse(self.window.document.dirty)

    def select_drawn(self, index=0):
        self.window.change_pointer('select_tool')
        point = self.points(index)[1]
        QTest.mouseClick(self.window.view.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.app.processEvents()
        self.assertIsNotNone(self.window.view.ink_selection)
        return self.window.view.ink_selection

    def drag_object(self, start, end):
        viewport = self.window.view.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(viewport, end)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        self.app.processEvents()
        self.assertEqual(self.errors, [])
        return self.window.view.ink_selection

    def test_select_move_resize_rotate_and_reopen_editable(self):
        self.draw()
        selection = self.select_drawn()
        view = self.window.view
        self.assertEqual(len(selection.handles()), 9)
        start = view.mapFromScene(selection.mapToScene(QPointF(0, 0)))
        from PySide6.QtCore import QPoint
        original = selection.data['frame'][:]
        selection = self.drag_object(start, start+QPoint(30, 25))
        self.assertGreater(selection.data['frame'][0], original[0])
        start = view.mapFromScene(selection.mapToScene(selection.handles()[4]))
        original_width = selection.size[0]
        selection = self.drag_object(start, start+QPoint(30, 20))
        self.assertGreater(selection.size[0], original_width)
        start = view.mapFromScene(selection.mapToScene(selection.handles()[8]))
        center = view.mapFromScene(selection.mapToScene(QPointF()))
        selection = self.drag_object(start, center+QPoint(45, 0))
        self.assertNotAlmostEqual(selection.data['frame'][4], 0)
        expected = selection.data['frame'][:]
        output = self.root/'objects.pdf'
        with patch.object(self.window, 'output_path', return_value=str(output)):
            self.assertTrue(self.window.save_as())
        self.window.undo()
        self.assertTrue(self.window.document.dirty)
        self.window.redo()
        self.assertFalse(self.window.document.dirty)
        self.assertTrue(self.window.open_path(output))
        page = self.window.document.doc[0]
        annotation = page.first_annot
        actual = read_ink(annotation)
        for a, b in zip(actual['frame'], expected):
            self.assertAlmostEqual(a, b, delta=.01)
        self.window.view.select_ink(0, annotation.xref)
        self.assertEqual(len(self.window.view.ink_selection.handles()), 9)
        self.assertEqual(len(list(page.annots())), 1)
        self.window.delete_selection()
        self.assertFalse(list(self.window.document.doc[0].annots()))
        self.window.undo()
        self.assertEqual(len(list(self.window.document.doc[0].annots())), 1)

    def test_all_resize_handles_commit_and_cancel_does_not_edit(self):
        from PySide6.QtCore import QPoint
        self.draw()
        self.window.change_pointer('select_tool')
        deltas = [(-10,-10),(0,-10),(10,-10),(10,0),(10,10),(0,10),(-10,10),(-10,0)]
        for index, (dx, dy) in enumerate(deltas):
            page = self.window.document.doc[0]
            self.window.view.select_ink(0, page.first_annot.xref)
            selection = self.window.view.ink_selection
            start = self.window.view.mapFromScene(selection.mapToScene(selection.handles()[index]))
            self.drag_object(start, start+QPoint(dx,dy))
        revision = self.window.document.revision
        self.window.escape()
        self.assertIsNone(self.window.view.ink_selection)
        self.assertEqual(self.window.document.revision, revision)

    def test_three_pen_kinds_and_brush_appearance_on_rotated_crop(self):
        from adf.ink import KINDS
        for index, kind in enumerate(KINDS):
            self.window.pen_menu.kind.setCurrentIndex(index)
            self.assertEqual(self.window.view.pen.kind, kind)
            self.assertEqual(self.window.view.pen.width, KINDS[kind][1])
            self.draw(index)
            page = self.window.document.doc[index]
            annotation = page.first_annot
            data = read_ink(annotation)
            self.assertEqual(data['kind'], kind)
            self.assertAlmostEqual(1 if annotation.opacity < 0 else annotation.opacity, KINDS[kind][2], places=3)
            pixmap = page.get_pixmap()
            self.assertNotEqual(pixmap.pixel(100,100), (255,255,255))
        self.window.document.save(self.root/'styles.pdf')
        with pymupdf.open(self.root/'styles.pdf') as saved:
            page = saved[2]
            self.assertEqual(read_ink(page.first_annot)['kind'], 'brush')
            self.assertNotEqual(page.get_pixmap().samples, page.get_pixmap(annots=False).samples)

    def test_rotated_object_drag_and_escape_during_resize(self):
        from PySide6.QtCore import QPoint
        for index in range(4):
            self.window.change_pointer('pen')
            self.draw(index)
            selection = self.select_drawn(index)
            start = self.window.view.mapFromScene(selection.mapToScene(QPointF()))
            selection = self.drag_object(start, start+QPoint(25,15))
            page = self.window.document.doc[index]
            point = pymupdf.Point(page.first_annot.vertices[0][1])*page.rotation_matrix
            pixmap = page.get_pixmap()
            self.assertNotEqual(pixmap.pixel(round(point.x),round(point.y)), (255,255,255))
        revision = self.window.document.revision
        start = self.window.view.mapFromScene(selection.mapToScene(selection.handles()[4]))
        viewport = self.window.view.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(viewport, start+QPoint(15,15))
        self.window.escape()
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=start+QPoint(15,15))
        self.assertIsNone(self.window.view.ink_selection)
        self.assertEqual(self.window.document.revision, revision)

    def test_popover_dismiss_click_and_drag_never_draw_or_erase(self):
        viewport = self.window.view.viewport()
        for tool in ('pen', 'eraser'):
            menu = self.window.pen_menu
            self.window.show_pen_options(tool)
            points = self.points()
            revision = self.window.document.revision
            # The dismissal gesture must be swallowed through mouse release.
            QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=points[1])
            QTest.mouseMove(viewport, points[2])
            QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=points[2])
            self.assertFalse(menu.isVisible())
            self.assertIsNone(self.window.view.pen.page)
            self.assertEqual(self.window.document.revision, revision)
            self.assertTrue(self.window.view.pen.enabled)
        self.window.pen_menu.change_tool('pen')
        self.draw()
        self.assertEqual(len(list(self.window.document.doc[0].annots())), 1)

    def test_color_field_hex_and_tool_widths_stay_independent(self):
        from PySide6.QtGui import QColor
        menu = self.window.pen_menu
        menu.hex.setText('#A26FC4')
        menu.apply_hex()
        self.assertEqual(self.window.view.pen.color, '#a26fc4')
        menu.field.pick(QPointF(menu.field.width()*.5, menu.field.height()*.5))
        self.assertEqual(self.window.view.pen.color, menu.field.color().name())
        previous = self.window.view.pen.color
        menu.hex.setText('invalid')
        menu.apply_hex()
        self.assertEqual(self.window.view.pen.color, previous)
        menu.slider.setValue(11)
        menu.change_tool('eraser')
        menu.slider.setValue(38)
        menu.change_tool('pen')
        self.assertEqual(menu.slider.value(), 11)
        self.assertEqual(self.window.view.pen.width, 2.2)
        menu.change_tool('eraser')
        self.assertEqual(menu.slider.value(), 38)
        menu.change_tool('pen')
        menu.set_color(QColor('#d53939'))
        self.draw()
        page = self.window.document.doc[0]
        self.assertEqual(read_ink(page.first_annot)['color'], '#d53939')

    def test_partial_eraser_live_preview_rotations_save_undo_and_redo(self):
        from PySide6.QtCore import QPointF
        view = self.window.view
        for index in range(4):
            view.goto(index)
            self.app.processEvents()
            page = self.window.document.doc[index]
            paths = [[tuple(pymupdf.Point(p)*page.derotation_matrix) for p in [(40, 120), (200, 120)]]]
            self.window.document.add_ink(index, paths, '#252525', 2)
            view.invalidate_page(index)
            self.window.pen_menu.change_tool('eraser')
            points = [view.mapFromScene(view.pages[index].mapToScene(QPointF(x, y))) for x, y in [(110, 80), (110, 160)]]
            viewport = view.viewport()
            revision = self.window.document.revision
            QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=points[0])
            QTest.mouseMove(viewport, points[1])
            self.assertEqual(self.window.document.revision, revision)
            self.assertEqual(len(next(iter(view.pen.preview.ink.values()))['paths']), 2)
            QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=points[1])
            self.assertEqual(self.window.document.revision, revision+1)
            page = self.window.document.doc[index]
            self.assertEqual(len(page.first_annot.vertices), 2)
            raster = page.get_pixmap()
            self.assertEqual(raster.pixel(110, 120), (255, 255, 255))
            self.assertNotEqual(raster.pixel(50, 120), (255, 255, 255))
            self.window.undo()
            page = self.window.document.doc[index]
            self.assertEqual(len(page.first_annot.vertices), 1)
            self.window.redo()
        output = self.root/'erased.pdf'
        self.window.document.save(output)
        with pymupdf.open(output) as saved:
            for page in saved:
                self.assertEqual(len(read_ink(page.first_annot)['paths']), 2)
                self.assertEqual(page.get_pixmap().pixel(110, 120), (255, 255, 255))
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_eraser_cancel_miss_and_full_delete(self):
        self.draw()
        view = self.window.view
        self.window.pen_menu.change_tool('eraser')
        points = self.points()
        revision = self.window.document.revision
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=points[0])
        QTest.mouseMove(view.viewport(), points[1])
        self.window.escape()
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=points[1])
        self.assertEqual(self.window.document.revision, revision)
        self.assertEqual(len(list(self.window.document.doc[0].annots())), 1)
        self.assertFalse(self.window.document.erase_ink(0, [((300, 300), (320, 320))], 10))
        self.assertEqual(self.window.document.revision, revision)
        self.draw()
        self.assertEqual(len(list(self.window.document.doc[0].annots())), 0)
        self.window.undo()
        self.assertEqual(len(list(self.window.document.doc[0].annots())), 1)

    def test_eraser_preserves_brush_shape_and_ignores_locked_ink(self):
        doc = self.window.document
        doc.add_ink(0, [[(30, 130), (70, 130), (120, 130), (170, 130), (230, 130)]],
                    '#252525', 8, 'brush', [[.3, .5, 1., .7, .2]])
        page = doc.doc[0]
        before = page.get_pixmap(matrix=pymupdf.Matrix(3, 3))
        doc.erase_ink(0, [((115, 110), (115, 150))], 10)
        page = doc.doc[0]
        after = page.get_pixmap(matrix=pymupdf.Matrix(3, 3))
        for x in (40, 55, 80, 90, 150, 180, 210):
            self.assertEqual([before.pixel(x*3, y) for y in range(370, 410)],
                             [after.pixel(x*3, y) for y in range(370, 410)])
        self.assertIsNotNone(read_ink(page.first_annot)['weights'])
        output = self.root/'erased-brush.pdf'
        doc.save(output)
        with pymupdf.open(output) as saved:
            saved_page = saved[0]
            self.assertIsNotNone(read_ink(saved_page.first_annot)['weights'])
            self.assertEqual(saved_page.get_pixmap(matrix=pymupdf.Matrix(3, 3)).samples, after.samples)
        annotation = page.first_annot
        annotation.set_flags(annotation.flags | pymupdf.PDF_ANNOT_IS_LOCKED)
        revision = doc.revision
        self.assertFalse(doc.erase_ink(0, [((30, 130), (230, 130))], 36))
        self.assertEqual(doc.revision, revision)

    def test_eraser_failure_rolls_back_whole_gesture(self):
        self.draw()
        doc = self.window.document
        before = doc.doc[0].get_pixmap().samples
        revision = doc.revision
        with patch('adf.ink.write_ink', side_effect=OSError('failed')):
            with self.assertRaises(OSError):
                doc.erase_ink(0, [((100, 80), (100, 120))], 4)
        self.assertEqual(doc.revision, revision)
        self.assertEqual(doc.doc[0].get_pixmap().samples, before)

    def test_popover_tablet_dismissal_and_eraser_tablet_input(self):
        device = QPointingDevice('test stylus', 20, QInputDevice.DeviceType.Stylus,
            QPointingDevice.PointerType.Pen, QInputDevice.Capability.Position | QInputDevice.Capability.Pressure, 1, 1)
        viewport = self.window.view.viewport()
        self.draw()
        self.window.pen_menu.change_tool('eraser')
        points = self.points()
        self.window.show_pen_options('eraser')
        revision = self.window.document.revision
        for dismissal in (True, False):
            for kind, point in zip((QEvent.Type.TabletPress, QEvent.Type.TabletMove, QEvent.Type.TabletRelease), points):
                release = kind == QEvent.Type.TabletRelease
                event = QTabletEvent(kind, device, QPointF(point), QPointF(viewport.mapToGlobal(point)),
                    0 if release else .7, 0, 0, 0, 0, 0, Qt.KeyboardModifier.NoModifier,
                    Qt.MouseButton.NoButton if kind == QEvent.Type.TabletMove else Qt.MouseButton.LeftButton,
                    Qt.MouseButton.NoButton if release else Qt.MouseButton.LeftButton)
                QApplication.sendEvent(viewport, event)
                self.assertTrue(event.isAccepted())
            if dismissal:
                self.assertEqual(self.window.document.revision, revision)
                self.assertFalse(self.window.pen_menu.isVisible())
            else:
                self.assertEqual(len(list(self.window.document.doc[0].annots())), 0)

    def test_eraser_never_changes_pdf_content_and_rejects_readonly_document(self):
        doc = self.window.document
        page = doc.doc[0]
        page.insert_text((40, 100), 'Original document')
        page.draw_rect((40, 120, 180, 170), color=(0, 0, 0))
        content = page.get_pixmap(annots=False).samples
        self.draw()
        doc.erase_ink(0, [((30, 80), (190, 160))], 36)
        self.assertEqual(doc.doc[0].get_pixmap(annots=False).samples, content)
        restricted = self.root/'erase-readonly.pdf'
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            from adf.ink import write_ink
            write_ink(page, [[(30, 40), (100, 40)]], '#252525', 3)
            pdf.save(restricted, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                     owner_pw='owner', user_pw='reader', permissions=pymupdf.PDF_PERM_PRINT)
        doc.open(restricted, 'reader')
        self.window.refresh_document()
        revision = doc.revision
        with self.assertRaises(PermissionError):
            doc.erase_ink(0, [((30, 40), (100, 40))], 10)
        self.assertEqual(doc.revision, revision)
        self.assertFalse(doc.can_undo)
