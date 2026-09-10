"""Font reuse fixtures deliberately have no corresponding installed font."""
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from PySide6.QtGui import QFontDatabase, QRawFont
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from adf.fonts import embedded_font, installed_font, original_font, _cmap_pairs
from adf.document import PdfDocument
from adf.text_groups import text_group_at
from adf.app import MainWindow


def fixture_font(name='ADF Fixture Uninstalled', width=520):
    builder = FontBuilder(1000, isTTF=True)
    cmap = {cp: f'u{cp:04X}' for cp in [32, 65, 66, 67, 88, 44032, 45208, 45796]}
    order = ['.notdef'] + list(cmap.values())
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    glyphs = {}
    for index, glyph in enumerate(order):
        pen = TTGlyphPen(None)
        if glyph != 'u0020':
            pen.moveTo((40, 0)); pen.lineTo((width-40, 0))
            pen.lineTo((width-80, 650-index*12)); pen.lineTo((40, 650)); pen.closePath()
        glyphs[glyph] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({glyph: (width, 40) for glyph in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({'familyName': name, 'styleName': 'Regular', 'fullName': name,
                           'psName': name.replace(' ', '')})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost(); builder.setupMaxp()
    output = io.BytesIO(); builder.save(output)
    return output.getvalue()


def fixture_pdf(path, subset=True, width=520):
    # Both pages must embed the exact same font bytes. Rebuilding per page can
    # cross a timestamp tick and make MuPDF subset two separate font resources.
    data = fixture_font(width=width)
    with pymupdf.open() as doc:
        for text in ('AB 가나', 'C 다'):
            page = doc.new_page(width=400, height=500)
            page.insert_font(fontname='Fixture', fontbuffer=data)
            page.insert_text((40, 80), text, fontname='Fixture', fontsize=20)
        if subset:
            doc.subset_fonts()
        doc.save(path)


class DocumentFontTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='adf-font-test-')
        self.path = Path(self.temp.name)/'uninstalled.pdf'
        fixture_pdf(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def font(self, doc):
        return embedded_font(doc[0], text_group_at(doc[0], (45, 75))['font'])

    def test_subset_restores_mapping_and_only_retained_outlines(self):
        self.assertIsNone(installed_font('ADF Fixture Uninstalled'))
        with pymupdf.open(self.path) as doc:
            original_data = doc.extract_font(doc[0].get_fonts()[0][0])[3]
            self.assertNotIn('cmap', TTFont(io.BytesIO(original_data)))
            font = self.font(doc)
            self.assertIsNotNone(font)
            self.assertEqual(font.missing('ABC 가나다'), '')  # Includes page 2's glyphs.
            self.assertEqual(font.missing('X똠'), 'X똠')       # Removed / never present.
            self.assertEqual(font.source, 'PDF 내장')
            preview_id = QFontDatabase.addApplicationFontFromData(font.preview_data())
            try:
                families = QFontDatabase.applicationFontFamilies(preview_id)
                self.assertEqual(len(families), 1)
                from PySide6.QtGui import QFont
                qfont = QFont(families[0]); qfont.setPixelSize(20)
                qfont.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
                raw = QRawFont.fromFont(qfont)
                self.assertEqual(raw.familyName(), families[0])
                self.assertTrue(raw.supportsCharacter(ord('가')))
                self.assertFalse(raw.supportsCharacter(ord('X')))
                self.assertAlmostEqual(raw.advancesForGlyphIndexes(raw.glyphIndexesForString('A'))[0].x(),
                                       font.metrics.text_length('A', fontsize=20), delta=.02)
            finally:
                QFontDatabase.removeApplicationFont(preview_id)

    def test_full_font_retains_unused_characters(self):
        fixture_pdf(self.path, subset=False)
        with pymupdf.open(self.path) as doc:
            self.assertEqual(self.font(doc).missing('X 가나다'), '')

    def test_same_named_document_fonts_get_different_preview_families(self):
        other = self.path.with_name('other.pdf'); fixture_pdf(other, width=740)
        with pymupdf.open(self.path) as first, pymupdf.open(other) as second:
            font1, font2 = self.font(first), self.font(second)
            ids = [QFontDatabase.addApplicationFontFromData(font.preview_data()) for font in (font1, font2)]
            try:
                self.assertNotEqual(QFontDatabase.applicationFontFamilies(ids[0]), QFontDatabase.applicationFontFamilies(ids[1]))
                self.assertNotAlmostEqual(font1.metrics.text_length('A'), font2.metrics.text_length('A'))
            finally:
                for font_id in ids:
                    QFontDatabase.removeApplicationFont(font_id)

    def test_nonidentity_cid_to_glyph_mapping(self):
        from adf.fonts import _pdf_mapping
        with pymupdf.open(self.path) as doc:
            resource = doc[0].get_fonts(full=True)[0]
            import re
            descendant = int(re.findall(r'(\d+) 0 R', doc.xref_get_key(resource[0], 'DescendantFonts')[1])[0])
            mapping = doc.get_new_xref(); doc.update_object(mapping, '<<>>')
            doc.update_stream(mapping, b'\x00\x00\x00\x06')
            doc.xref_set_key(descendant, 'CIDToGIDMap', f'{mapping} 0 R')
            unicode_xref = int(doc.xref_get_key(resource[0], 'ToUnicode')[1].split()[0])
            doc.update_stream(unicode_xref, b'1 beginbfchar <0001> <AC00> endbfchar')
            with patch.object(pymupdf.Page, 'get_texttrace', return_value=[]):
                self.assertEqual(_pdf_mapping(doc[0], resource), {44032: 6})

    def test_edit_save_reopen_keeps_original_glyph_shapes_and_source(self):
        before = self.path.read_bytes()
        model = PdfDocument(); model.open(self.path)
        try:
            font = self.font(model.doc)
            span = text_group_at(model.doc[0], (45, 75))
            model.replace_text(0, span['bbox'], 'C 다\nAB 나가', font_size=20, fontbuffer=font.data,
                               target_rect=(40, 60, 300, 150), source_rects=span['source_rects'])
            target = self.path.with_name('saved.pdf'); model.save(target)
            with pymupdf.open(target) as saved:
                self.assertIn('C 다\nAB 나가', saved[0].get_text())
                resulting = text_group_at(saved[0], (45, 72))
                after = embedded_font(saved[0], resulting['font'])
                self.assertIsNotNone(after)
                for char in 'ABC가나다':
                    # Compare rendered individual glyphs and advances, not only
                    # the family name: a lookalike substitution must fail.
                    images = []
                    for data in (font.data, after.data):
                        with pymupdf.open() as single:
                            page = single.new_page(width=100, height=100)
                            page.insert_font(fontname='Probe', fontbuffer=data)
                            page.insert_text((10, 50), char, fontname='Probe', fontsize=32)
                            images.append(page.get_pixmap().samples)
                    self.assertEqual(*images)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertTrue(model.undo())
            self.assertIn('AB 가나', model.doc[0].get_text())
        finally:
            model.close()

    def test_missing_glyph_is_rejected_before_document_changes(self):
        model = PdfDocument(); model.open(self.path)
        try:
            span = text_group_at(model.doc[0], (45, 75)); before = model.doc[0].get_pixmap().samples
            with self.assertRaises(ValueError):
                model.replace_text(0, span['bbox'], 'X', fontbuffer=self.font(model.doc).data)
            self.assertEqual(model.doc[0].get_pixmap().samples, before)
            self.assertFalse(model.dirty)
        finally:
            model.close()

    def test_standard_pdf_fonts_reuse_engine_outlines(self):
        with pymupdf.open() as doc:
            for name in ('Helvetica', 'Times-Roman', 'Courier-Bold'):
                page = doc.new_page(); page.insert_text((40, 70), 'ABC', fontname=name)
                font = original_font(page, name, 'ABC')
                self.assertEqual(font.source, 'PDF 표준')
                self.assertEqual(font.missing('ABC'), '')
                self.assertAlmostEqual(font.metrics.text_length('ABC'), pymupdf.Font(name).text_length('ABC'), places=4)

    def test_unicode_ranges_arrays_and_ligatures(self):
        data = b'1 beginbfrange <0001> <0002> <AC00> <0003> <0004> [<0041> <D83DDE00>] endbfrange 1 beginbfchar <0005> <00660069> endbfchar'
        self.assertEqual(dict(_cmap_pairs(data)), {1: 44032, 2: 44033, 3: 65, 4: 0x1f600, 5: None})

    def with_window(self, callback):
        window = MainWindow(smoke=True)
        errors = []
        window.error = lambda e: errors.append(str(e))
        try:
            window.open_path(self.path)
            window.actions['text'].trigger()
            span = text_group_at(window.document.doc[0], (45, 75))
            with patch.object(window, 'choose_replacement_font') as choose:
                window.edit_text(0, span)
                choose.assert_not_called()
            callback(window)
            self.assertEqual(errors, [])
        finally:
            for timer in window.findChildren(QTimer):
                timer.stop()
            with patch.object(window, 'maybe_save', return_value=True):
                window.close()
            window.deleteLater()
            self.app.processEvents()

    def test_missing_character_cancel_discards_only_current_paragraph(self):
        def run(window):
            window.document.rotate([1], 90)
            before = window.document.doc[0].get_pixmap().samples
            alias = window.edit_font_family
            with patch.object(window, 'choose_replacement_font', return_value=None) as choose:
                window.text_value.setPlainText('X')
                window.check_text_font()
                choose.assert_called_once()
            self.assertIsNone(window.text_selection)
            self.assertEqual(window.document.doc[0].get_pixmap().samples, before)
            self.assertEqual(window.document.doc[1].rotation, 90)
            self.assertTrue(window.document.dirty)
            self.assertNotIn(alias, QFontDatabase.families())
        self.with_window(run)

    def test_missing_character_accept_then_single_final_save(self):
        def run(window):
            fallback = installed_font('Malgun Gothic') or installed_font('DejaVu Sans')
            if fallback is None:
                self.skipTest('No fixture fallback installed')
            with patch.object(window, 'choose_replacement_font', return_value=fallback) as choose:
                window.text_value.setPlainText('X')
                window.check_text_font()
                choose.assert_called_once()
                self.assertEqual(window.edit_font.data, fallback.data)
                self.assertTrue(window.finish_text_selection())
            self.assertIn('X', window.document.doc[0].get_text())
            target = self.path.with_name('fallback.pdf'); window.document.save(target)
            with pymupdf.open(target) as saved:
                self.assertIn('X', saved[0].get_text())
        self.with_window(run)

    def test_exact_installed_font_used_for_new_glyph_without_prompt(self):
        from adf.fonts import EditFont
        complete = EditFont('ADF Fixture Uninstalled', fixture_font(), 'PC 글꼴')
        def run(window):
            with patch('adf.app.installed_font', return_value=complete), patch.object(window, 'choose_replacement_font') as choose:
                window.text_value.setPlainText('X')
                window.check_text_font()
                choose.assert_not_called()
                self.assertEqual(window.edit_font.source, 'PC 글꼴')
                self.assertTrue(window.finish_text_selection())
        self.with_window(run)

    def test_replacement_dialog_labels_and_cancel_default(self):
        window = MainWindow(smoke=True)
        def inspect(dialog):
            self.assertEqual({button.text() for button in dialog.buttons()}, {'대체 글꼴로 편집', '편집 취소'})
            self.assertEqual(dialog.defaultButton().text(), '편집 취소')
            self.assertFalse(dialog.checkBox().isChecked())
            return 0
        try:
            with patch.object(QMessageBox, 'exec', inspect):
                self.assertIsNone(window.choose_replacement_font('Missing', 'ABC', 'C'))
        finally:
            window.close()

    def test_edited_font_can_be_selected_again_without_another_prompt(self):
        def run(window):
            window.text_value.setPlainText('C 다')
            self.assertTrue(window.finish_text_selection())
            page = window.document.doc[0]
            rect = page.search_for('C')[0]
            with patch.object(window, 'choose_replacement_font') as choose:
                window.edit_text(0, text_group_at(page, rect.tl + (1, 1)))
                choose.assert_not_called()
                self.assertIsNotNone(window.edit_font)
                window.text_value.setPlainText('AB 가나')
                self.assertTrue(window.finish_text_selection())
            target = self.path.with_name('edited-again.pdf')
            window.document.save(target)
            self.assertTrue(window.open_path(target))
            page = window.document.doc[0]
            rect = page.search_for('AB')[0]
            with patch.object(window, 'choose_replacement_font') as choose:
                window.edit_text(0, text_group_at(page, rect.tl + (1, 1)))
                choose.assert_not_called()
                self.assertIsNotNone(window.edit_font)
        self.with_window(run)

    def test_same_name_with_different_glyphs_is_not_automatically_equivalent(self):
        from adf.fonts import EditFont, equivalent_font
        one = EditFont('Same', fixture_font(width=520), 'PDF 내장')
        two = EditFont('Same', fixture_font(width=740), 'PDF 내장')
        self.assertIsNone(equivalent_font([one, two]))

    def test_checked_choice_is_reused_only_for_same_font_and_document(self):
        window = MainWindow(smoke=True)
        prompts = []
        def accept(dialog):
            prompts.append(dialog.text())
            dialog.checkBox().setChecked(True)
            button = next(b for b in dialog.buttons() if b.text() == '대체 글꼴로 편집')
            button.click()
            return 0
        try:
            with patch.object(QMessageBox, 'exec', accept):
                first = window.choose_replacement_font('AAAAAA+MissingOne', 'ABC')
                second = window.choose_replacement_font('MissingOne', 'ABC')
                self.assertIs(first, second)
                self.assertEqual(len(prompts), 1)
                window.choose_replacement_font('MissingTwo', 'ABC')
                self.assertEqual(len(prompts), 2)
                self.assertTrue(window.open_path(self.path))
                window.choose_replacement_font('MissingOne', 'ABC')
                self.assertEqual(len(prompts), 3)
                window.close_document()
                self.assertEqual(window.font_choices, {})
        finally:
            window.close()

    def test_unchecked_choice_is_not_remembered_and_cancel_does_not_store_it(self):
        window = MainWindow(smoke=True)
        def accept(dialog):
            self.assertFalse(dialog.checkBox().isChecked())
            next(b for b in dialog.buttons() if b.text() == '대체 글꼴로 편집').click()
            return 0
        try:
            with patch.object(QMessageBox, 'exec', accept) as call:
                self.assertIsNotNone(window.choose_replacement_font('Missing', 'ABC'))
                self.assertIsNotNone(window.choose_replacement_font('Missing', 'ABC'))
                self.assertEqual(window.font_choices, {})
            def cancel(dialog):
                dialog.checkBox().setChecked(True)
                next(b for b in dialog.buttons() if b.text() == '편집 취소').click()
                return 0
            with patch.object(QMessageBox, 'exec', cancel):
                self.assertIsNone(window.choose_replacement_font('Missing', 'ABC'))
                self.assertEqual(window.font_choices, {})
        finally:
            window.close()
