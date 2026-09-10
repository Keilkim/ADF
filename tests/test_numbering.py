"""Page numbering: facing order, physical margins and preview/export parity."""

import os
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from adf.app import MainWindow
from adf.dialogs import NumberingDialog, pdf_pixmap
from adf.document import PdfDocument, MM_TO_PT, page_number_record


class NumberingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='adf-number-test-')
        self.root = Path(self.directory.name)
        self.source = self.root / 'source.pdf'
        with pymupdf.open() as doc:
            for index in range(6):
                page = doc.new_page(width=400, height=600)
                page.insert_text((40, 90), f'Original page {index + 1}')
                page.set_rotation((index % 4) * 90)
            doc.save(self.source)
        self.original = self.source.read_bytes()
        self.model = PdfDocument()
        self.model.open(self.source)
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs:
            dialog.reject()
            dialog.deleteLater()
        self.app.processEvents()
        self.model.close()
        self.assertEqual(self.source.read_bytes(), self.original)
        self.directory.cleanup()

    def dialog(self, **kwargs):
        dialog = NumberingDialog(self.model, **kwargs)
        self.dialogs.append(dialog)
        return dialog

    def test_automatic_padding_uses_last_number_including_start_and_range(self):
        dialog = self.dialog()
        self.assertEqual(dialog.ranges.text(), '')
        self.assertEqual(dialog._options()['indices'], list(range(6)))
        dialog.start.setValue(98)
        dialog.zero_pad.setChecked(True)
        dialog.affix.setCurrentIndex(1)
        self.model.number_pages(**dialog._options())
        for index, page in enumerate(self.model.doc):
            self.assertIn(f'p.{98 + index:03d}', page.get_text())
        self.model.undo()
        dialog.ranges.setText('2, 4')
        dialog.affix.setCurrentIndex(2)
        self.model.number_pages(**dialog._options())
        self.assertIn('98p', self.model.doc[1].get_text())
        self.assertIn('99p', self.model.doc[3].get_text())
        self.assertNotIn('98p', self.model.doc[0].get_text())
        self.model.undo()
        dialog.start.setValue(999)
        self.model.number_pages(**dialog._options())
        self.assertIn('0999p', self.model.doc[1].get_text())
        self.assertIn('1000p', self.model.doc[3].get_text())
        self.model.undo()
        dialog.zero_pad.setChecked(False)
        self.model.number_pages(**dialog._options())
        self.assertIn('999p', self.model.doc[1].get_text())
        self.assertNotIn('0999p', self.model.doc[1].get_text())

    def test_mirrored_numbers_follow_actual_page_sides_and_mm_margins(self):
        for start_right, sides in [
            (False, ['left', 'right', 'left', 'right']),
            (True, ['right', 'left', 'right', 'left']),
        ]:
            with self.subTest(start_right=start_right):
                self.model.number_pages([0, 1, 2, 3], prefix='p.', position='bottom-' + sides[0],
                                        mirror=True, anchor_page=0, start_right=start_right, margin_x=15, margin_y=18)
                for index, side in enumerate(sides):
                    page = self.model.doc[index]
                    rect = page.search_for(f'p.{index + 1}')[0] * page.rotation_matrix
                    margin = rect.x0 if side == 'left' else page.rect.width - rect.x1
                    self.assertAlmostEqual(margin, 15 * MM_TO_PT, delta=.1)
                    self.assertAlmostEqual(page.rect.height - rect.y1, 18 * MM_TO_PT, delta=.1)
                self.model.undo()

    def test_subset_does_not_reset_facing_sides_to_number_parity(self):
        self.model.number_pages([1, 3, 4], start=99, prefix='p.', mirror=True,
                                position='top-left', anchor_page=1, start_right=True, margin_x=10)
        for index, number, side in [(1, 99, 'left'), (3, 100, 'left'), (4, 101, 'right')]:
            page = self.model.doc[index]
            rect = page.search_for(f'p.{number}')[0] * page.rotation_matrix
            self.assertAlmostEqual(rect.x0 if side == 'left' else page.rect.width - rect.x1, 10 * MM_TO_PT, delta=.1)

    def test_recorded_numbers_are_removed_on_rotated_pages(self):
        self.model.number_pages(list(range(6)), prefix='p.', position='bottom-right')
        self.assertEqual(self.model.numbered_pages(), list(range(6)))
        self.assertEqual(self.model.remove_page_numbers(range(6)), (list(range(6)), []))
        for index, page in enumerate(self.model.doc):
            text = page.get_text()
            self.assertNotIn(f'p.{index + 1}', text.split())
            self.assertIn(f'Original page {index + 1}', text)
        self.assertEqual(self.model.numbered_pages(), [])
        self.model.undo()
        self.assertEqual(self.model.numbered_pages(), list(range(6)))
        self.assertIn('p.3', self.model.doc[2].get_text().split())

    def test_numbering_again_edits_only_numbers_that_adf_recorded(self):
        path = self.root / 'foreign.pdf'
        with pymupdf.open() as doc:
            for _ in range(2):
                page = doc.new_page(width=400, height=600)
                page.insert_text((40, 90), 'See p.1 and p.2 in the body')
                page.insert_text((40, 580), 'p.7')
            doc.save(path)
        model = PdfDocument()
        model.open(path)
        try:
            # Numbers from other programs have no record and are never removed.
            with self.assertRaises(ValueError):
                model.remove_page_numbers([0, 1])
            model.number_pages([0, 1], prefix='p.', position='bottom-right')
            model.number_pages([0, 1], start=10, prefix='p.', position='top-left')
            for index, page in enumerate(model.doc):
                words = page.get_text().split()
                self.assertIn(f'p.{index + 10}', words)
                self.assertEqual(words.count(f'p.{index + 1}'), 1)
                self.assertIn('p.7', words)
            self.assertEqual(model.remove_page_numbers(model.numbered_pages()), ([0, 1], []))
            for page in model.doc:
                self.assertEqual(' '.join(page.get_text().split()), 'See p.1 and p.2 in the body p.7')
        finally:
            model.close()

    def test_changed_numbers_are_left_alone(self):
        self.model.number_pages([0, 1, 2], prefix='p.', position='bottom-center')
        # Another label now overlaps page 1's number, and page 2's number was edited.
        _, rect = page_number_record(self.model.doc[0])
        self.model.doc[0].insert_text((rect.x0, rect.y1 - 3), 'X', fontsize=11)
        _, rect = page_number_record(self.model.doc[1])
        self.model.replace_text(1, rect, 'two', fit=True)
        self.assertEqual(self.model.remove_page_numbers([0, 1, 2]), ([2], [0, 1]))
        self.assertIn('p.1', self.model.doc[0].get_text().split())
        self.assertNotIn('p.3', self.model.doc[2].get_text().split())
        self.assertEqual(self.model.numbered_pages(), [0])

    def test_recorded_numbers_survive_saving(self):
        self.model.number_pages([0, 1, 2], prefix='p.')
        saved = self.root / 'numbered.pdf'
        self.model.save(saved)
        reopened = PdfDocument()
        reopened.open(saved)
        try:
            self.assertEqual(reopened.numbered_pages(), [0, 1, 2])
            self.assertEqual(reopened.remove_page_numbers([0, 1, 2]), ([0, 1, 2], []))
        finally:
            reopened.close()

    def test_preview_matches_replaced_numbers(self):
        self.model.number_pages(list(range(6)), prefix='p.', position='bottom-center')
        dialog = self.dialog(current_page=1, start_right=True)
        dialog.show()
        self.app.processEvents()
        dialog.start.setValue(40)
        dialog._update_preview()
        expected = {page['index']: page['image'].toImage() for page in dialog.preview.pages}
        self.model.number_pages(**dialog._options())
        for index, image in expected.items():
            self.assertEqual(image, pdf_pixmap(self.model.doc[index], 950, 1200).toImage())
        for page in self.model.doc:
            self.assertFalse(any(word.startswith('p.') for word in page.get_text().split()))

    def test_preview_is_same_as_saved_pdf_and_position_icons_mirror(self):
        dialog = self.dialog(current_page=1, start_right=True)
        dialog.mirror.setChecked(True)
        dialog.show()
        self.app.processEvents()
        dialog._update_preview()
        self.assertEqual([page['index'] for page in dialog.preview.pages], [1, 2])
        self.assertEqual([page['position'] for page in dialog.preview.pages], ['bottom-left', 'bottom-right'])
        button = dialog.preview.position_buttons[0]['top-left']
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        dialog.color_button.value.setText('#2878A9')
        dialog.font_size.setCurrentText('12.5')
        dialog.ranges.setText('2-5')
        dialog._update_preview()
        self.assertEqual([page['position'] for page in dialog.preview.pages], ['top-left', 'top-right'])
        expected = {page['index']: page['image'].toImage() for page in dialog.preview.pages}
        self.assertFalse(self.model.dirty)
        self.model.number_pages(**dialog._options())
        for index, image in expected.items():
            self.assertEqual(image, pdf_pixmap(self.model.doc[index], 950, 1200).toImage())
        self.model.save(self.root / 'numbered.pdf')
        with pymupdf.open(self.root / 'numbered.pdf') as saved:
            for index, image in expected.items():
                self.assertEqual(image, pdf_pixmap(saved[index], 950, 1200).toImage())

    def test_preview_inherits_reader_start_side_and_spread(self):
        window = MainWindow(smoke=True)
        try:
            window.open_path(self.source)
            for start_right, current, expected in [
                (False, 0, [0, 1]), (True, 1, [1, 2]),
                (True, 2, [1, 2]), (True, 0, [0]),
                (False, 3, [2, 3]), (True, 5, [5]),
            ]:
                with self.subTest(start_right=start_right, current=current):
                    window.view.start_right = start_right
                    window.view.set_mode('spread')
                    window.view.goto(current)
                    dialog = NumberingDialog(window.document, current_page=current, parent=window)
                    self.dialogs.append(dialog)
                    dialog._update_preview()
                    self.assertEqual([page['index'] for page in dialog.preview.pages], expected)
                    reader_order = sorted(expected, key=lambda index: window.view.pages[index].x())
                    self.assertEqual(reader_order, expected)
                    dialog.reject()
        finally:
            for dialog in self.dialogs:
                dialog.reject()
            window.close()
            window.deleteLater()

    def test_invalid_live_settings_disable_apply_and_recover(self):
        dialog = self.dialog()
        for widget, value in [(dialog.ranges, '0'), (dialog.color_button.value, '#XYZ'), (dialog.font_size, '999')]:
            with self.subTest(value=value):
                if hasattr(widget, 'setCurrentText'):
                    widget.setCurrentText(value)
                else:
                    widget.setText(value)
                dialog._update_preview()
                self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
                dialog.ranges.clear()
                dialog.color_button.value.setText('#222222')
                dialog.font_size.setCurrentText('11')
                dialog._update_preview()
                self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled())
        self.assertFalse(self.model.dirty)

    def test_right_start_blank_slots_match_both_reader_modes(self):
        for current, slots in [(0, [None, 0]), (5, [5, None])]:
            dialog = self.dialog(current_page=current, start_right=True, spread=True)
            dialog.show()
            self.app.processEvents()
            dialog._update_preview()
            self.assertEqual(dialog.preview.slots, slots)
            rect = dialog.preview.page_rects[current]
            midpoint = dialog.preview.width()/2
            if current == 0:
                self.assertGreater(rect.left(), midpoint)
                self.assertTrue(dialog.preview.position_buttons[0]['top-left'].isHidden())
                self.assertFalse(dialog.preview.position_buttons[1]['top-left'].isHidden())
            else:
                self.assertLess(rect.right(), midpoint)
                self.assertTrue(dialog.preview.position_buttons[1]['top-left'].isHidden())
            self.assertEqual(self.model.page_count, 6)
