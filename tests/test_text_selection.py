"""Text selection follows reading order like Acrobat instead of a dragged box."""
from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from adf.app import MainWindow
from adf.selection_widgets import PageText, RegionSelection, TextSelection

LINES = [(60, 'First line of the paragraph'), (76, 'second line continues here'), (92, 'third line ends it.'),
         (150, 'Another paragraph below')]


def sample(path, picture=False, text=True, lines=LINES):
    with pymupdf.open() as doc:
        page = doc.new_page(width=360, height=480)
        if picture:
            image = io.BytesIO()
            Image.new('RGB', (60, 60), (200, 40, 40)).save(image, format='PNG')
            page.insert_image((200, 250, 320, 370), stream=image.getvalue())
        if text:
            for y, line in lines:
                page.insert_text((40, y), line, fontsize=12)
        doc.save(path)


class PageTextTests(unittest.TestCase):
    def setUp(self):
        self.doc = pymupdf.open()
        self.page = self.doc.new_page(width=360, height=480)
        for y, line in LINES:
            self.page.insert_text((40, y), line, fontsize=12)
        self.page.insert_text((40, 200), '계약 해지 조건 안내', fontsize=12, fontname='korea')
        self.text = PageText(self.page)
        self.words = self.page.get_text('words')

    def tearDown(self):
        self.doc.close()

    def word(self, text, occurrence=0):
        return pymupdf.Rect([word for word in self.words if word[4] == text][occurrence][:4])

    def test_range_between_two_points_follows_lines(self):
        start, end = self.word('line').tl + (1, 5), self.word('ends').br - (1, 5)
        selected = self.text.text(*sorted((self.text.caret(start), self.text.caret(end))))
        self.assertEqual(selected, 'line of the paragraph\nsecond line continues here\nthird line ends')

    def test_points_beside_or_beyond_text_snap_to_line_ends_and_page_ends(self):
        line = self.word('second')
        self.assertEqual(self.text.caret(pymupdf.Point(5, line.y0+5)), self.text.lines[1].start)
        self.assertEqual(self.text.caret(pymupdf.Point(350, line.y0+5)), self.text.lines[1].end)
        self.assertEqual(self.text.caret(pymupdf.Point(200, 5)), 0)
        self.assertEqual(self.text.caret(pymupdf.Point(20, 470)), len(self.text.chars))

    def test_double_and_triple_click_units(self):
        self.assertEqual(self.text.text(*self.text.word(self.word('continues').tl + (3, 5))), 'continues')
        self.assertEqual(self.text.text(*self.text.word(self.word('조건').tl + (3, 5))), '조건')
        paragraph = self.text.paragraph(self.page, self.word('second').tl + (3, 5))
        self.assertEqual(self.text.text(*paragraph), '\n'.join(line for _, line in LINES[:3]))
        self.assertIsNone(self.text.word(pymupdf.Point(300, 400)))

    def test_highlight_is_one_band_per_line(self):
        start, end = self.text.caret(self.word('line').tl + (1, 5)), self.text.caret(self.word('ends').br - (1, 5))
        bands = self.text.shapes(start, end, self.page.rotation_matrix)
        self.assertEqual(len(bands), 3)
        second = bands[1].boundingRect()
        self.assertAlmostEqual(second.left(), self.word('second').x0, delta=.5)
        self.assertAlmostEqual(second.right(), self.word('here').x1, delta=.5)


class SelectionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='adf-selection-test-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.window = MainWindow(smoke=True)
        self.addCleanup(self.window.close)
        self.window.show()
        self.open(picture=True)

    def open(self, **options):
        source = self.root/f'selection-{len(list(self.root.iterdir()))}.pdf'
        sample(source, **options)
        self.assertTrue(self.window.open_path(source))
        self.view = self.window.view
        self.view.set_mode('single')
        self.view.fit('page')
        self.app.processEvents()
        self.words = self.window.document.doc[0].get_text('words')

    def point(self, x, y):
        return self.view.mapFromScene(self.view.pages[0].mapToScene(QPointF(x, y)))

    def word(self, text):
        return pymupdf.Rect([word for word in self.words if word[4] == text][0][:4])

    def drag(self, start, end, modifiers=Qt.KeyboardModifier.NoModifier):
        viewport = self.view.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, modifiers, self.point(*start))
        QTest.mouseMove(viewport, self.point(*end))
        selection = self.view.selection_item
        live = (selection.finished, selection.text_path.isEmpty()) if isinstance(selection, TextSelection) else None
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, modifiers, self.point(*end))
        return live

    def test_drag_selects_the_text_between_the_two_points_while_dragging(self):
        start, end = self.word('line'), self.word('ends')
        live = self.drag((start.x0+1, start.y0+5), (end.x1-1, end.y0+5))
        self.assertEqual(live, (False, False))  # highlighted before the button is released
        self.assertIsInstance(self.view.selection_item, TextSelection)
        self.assertEqual(self.window.clip_text, 'line of the paragraph\nsecond line continues here\nthird line ends')
        self.window.copy_text()
        self.assertEqual(QApplication.clipboard().text(), self.window.clip_text)
        # The selected text can still be copied as a picture of its area.
        self.window.actions['copy_region'].trigger()
        self.assertFalse(QApplication.clipboard().image().isNull())
        QApplication.clipboard().clear()

    def test_double_click_selects_a_word_and_triple_click_the_paragraph(self):
        target = self.point(*(self.word('continues').tl + (3, 5)))
        viewport = self.view.viewport()
        QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=target)
        self.assertEqual(self.window.clip_text, 'continues')
        QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=target)
        self.assertEqual(self.window.clip_text, '\n'.join(line for _, line in LINES[:3]))
        self.assertIsNone(self.window.selected_image)

    def test_shift_click_extends_the_selection(self):
        first, last = self.word('second'), self.word('ends')
        self.drag((first.x0+1, first.y0+5), (first.x1-1, first.y0+5))
        self.assertEqual(self.window.clip_text, 'second')
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                         self.point(last.x1-1, last.y0+5))
        self.assertEqual(self.window.clip_text, 'second line continues here\nthird line ends')

    def test_shift_click_back_on_the_anchor_leaves_nothing_to_copy(self):
        first = self.word('second')
        anchor = (first.x0+1, first.y0+5)
        self.drag(anchor, (first.x1-1, first.y0+5))
        self.assertEqual(self.window.clip_text, 'second')
        QTest.mouseClick(self.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                         self.point(*anchor))
        self.assertIsNone(self.view.selection_item)
        self.assertEqual(self.window.clip_text, '')
        QApplication.clipboard().setText('earlier')
        self.window.copy_text()
        self.assertEqual(QApplication.clipboard().text(), 'earlier')  # nothing highlighted, nothing copied
        QApplication.clipboard().clear()

    def test_highlight_is_rebuilt_only_when_a_caret_moves(self):
        first = self.word('second')
        self.drag((first.x0+1, first.y0+5), (first.x1-1, first.y0+5))
        selection = self.view.selection_item
        path = selection.text_path
        selection.select(selection.anchor, selection.focus)
        self.assertIs(selection.text_path, path)
        selection.select(selection.anchor, selection.focus+1)
        self.assertIsNot(selection.text_path, path)
        self.assertEqual(selection.text(), 'second ')

    def test_alt_drag_and_drags_from_pictures_select_an_area(self):
        start, end = self.word('line'), self.word('ends')
        self.drag((start.x0+1, start.y0+5), (end.x1-1, end.y0+5), Qt.KeyboardModifier.AltModifier)
        self.assertIsInstance(self.view.selection_item, RegionSelection)
        self.assertNotIn('First', self.window.clip_text)  # only the text inside the box
        self.drag((220, 270), (300, 350))
        self.assertIsInstance(self.view.selection_item, RegionSelection)
        self.assertIsNotNone(self.view.selected_region())
        self.assertEqual(self.window.clip_text, '')

    def test_page_without_text_keeps_area_selection(self):
        self.open(picture=True, text=False)
        self.drag((20, 20), (340, 400))
        self.assertIsInstance(self.view.selection_item, RegionSelection)
        self.assertIsNotNone(self.view.selected_region())

    def test_drag_across_empty_space_selects_nothing(self):
        self.drag((200, 420), (330, 460))
        self.assertIsNone(self.view.selection_item)
        self.assertEqual(self.window.clip_text, '')

    def test_dragging_past_the_window_edge_scrolls(self):
        self.open(lines=[(40+14*row, f'Row {row} of a long page') for row in range(30)])
        self.view.set_zoom(3)
        self.app.processEvents()
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.minimum())
        start = self.word('Row')
        viewport = self.view.viewport()
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=self.point(start.x0+1, start.y0+5))
        bottom = QPoint(viewport.width()//2, viewport.height()-2)
        QTest.mouseMove(viewport, bottom)
        before, text = bar.value(), self.view.selection_item.text()
        self.assertTrue(self.view.selection_scroll.isActive())
        for _ in range(5):
            self.view.scroll_selection()
        self.assertGreater(bar.value(), before)
        self.assertGreater(len(self.view.selection_item.text()), len(text))
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=bottom)
        self.assertFalse(self.view.selection_scroll.isActive())

    def test_edge_scrolling_stops_when_the_drag_is_lost(self):
        self.open(lines=[(40+14*row, f'Row {row} of a long page') for row in range(30)])
        self.view.set_zoom(3)
        self.app.processEvents()
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.minimum())
        viewport = self.view.viewport()
        bottom = QPoint(viewport.width()//2, viewport.height()-2)
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=self.point(self.word('Row').x0+1, self.word('Row').y0+5))
        QTest.mouseMove(viewport, bottom)
        self.assertTrue(self.view.selection_scroll.isActive())
        # The button comes up over another window, so the view never sees the release.
        other = QWidget()
        other.resize(60, 60)
        other.show()
        self.addCleanup(other.close)
        QTest.mouseRelease(other, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
        self.assertIsNotNone(self.view.selection_start)
        before = bar.value()
        self.view.scroll_selection()
        self.assertFalse(self.view.selection_scroll.isActive())
        self.assertEqual(bar.value(), before)
        # Losing focus mid-drag stops it as well; moving on starts it again. The next row,
        # so the press cannot pair with the first one as a double-click.
        bar.setValue(bar.minimum())
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=self.point(self.word('1').x0+1, self.word('1').y0+5))
        QTest.mouseMove(viewport, bottom)
        self.assertTrue(self.view.selection_scroll.isActive())
        QApplication.sendEvent(self.view, QFocusEvent(QEvent.Type.FocusOut, Qt.FocusReason.ActiveWindowFocusReason))
        self.assertFalse(self.view.selection_scroll.isActive())
        QTest.mouseMove(viewport, bottom - QPoint(0, 1))
        self.assertTrue(self.view.selection_scroll.isActive())
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=bottom)
        self.assertFalse(self.view.selection_scroll.isActive())


if __name__ == '__main__':
    unittest.main()
