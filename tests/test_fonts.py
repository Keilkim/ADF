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
from PySide6.QtGui import QFont, QFontDatabase, QRawFont
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from adf.fonts import EditFont, embedded_font, installed_font, original_font, _cmap_pairs
from adf.document import PdfDocument
from adf.text_groups import text_group_at
from adf.app import MainWindow


def fixture_font(name='ADF Fixture Uninstalled', width=520, space_width=None, extra_codepoints=(), spacer_gid=1):
    builder = FontBuilder(1000, isTTF=True)
    cmap = {cp: f'u{cp:04X}' for cp in [32, 65, 66, 67, 88, 44032, 45208, 45796, *extra_codepoints]}
    order = ['.notdef'] + [f'pad{i}' for i in range(spacer_gid-1)] + list(cmap.values())
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
    builder.setupHorizontalMetrics({glyph: (space_width if glyph == 'u0020' and space_width is not None else width, 40)
                                    for glyph in order})
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

    def test_positioned_word_spaces_keep_width_in_editor_and_saved_pdf(self):
        # The PDF positions words without painting a space glyph. Subsetting
        # clears its advance in hmtx while retaining the width in the PDF /W.
        data = fixture_font(space_width=300)
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=500)
            page.insert_font(fontname='Fixture', fontbuffer=data)
            page.insert_text((40, 80), 'AB', fontname='Fixture', fontsize=20)
            page.insert_text((66.8, 80), '가나', fontname='Fixture', fontsize=20)
            doc.subset_fonts()
            doc.save(self.path)
        before = self.path.read_bytes()
        with pymupdf.open(self.path) as doc:
            raw = TTFont(io.BytesIO(doc.extract_font(doc[0].get_fonts()[0][0])[3]))
            self.assertEqual(raw['hmtx'][raw.getGlyphOrder()[1]][0], 0)

        def run(window):
            self.assertEqual(window.text_value.toPlainText(), 'AB 가나')
            self.assertAlmostEqual(window.edit_font.metrics.glyph_advance(32), .3, places=4)
            # Measure the actual QTextEdit layout: text contents alone would
            # miss a space that is present but rendered with zero advance.
            layout = window.text_value.document().firstBlock().layout()
            window.text_value.document().documentLayout().documentSize()
            line = layout.lineAt(0)
            self.assertAlmostEqual(line.cursorToX(3)[0] - line.cursorToX(2)[0], 6, delta=.05)
            window.text_value.setPlainText('가나 AB')
            self.assertTrue(window.finish_text_selection())
            self.assertIn('가나 AB', window.document.doc[0].get_text())
            target = self.path.with_name('spaces-saved.pdf')
            window.document.save(target)
            with pymupdf.open(target) as saved:
                self.assertIn('가나 AB', saved[0].get_text())
                chars = saved[0].get_text('rawdict')['blocks'][0]['lines'][0]['spans'][0]['chars']
                for char in chars:
                    if char['c'] == ' ':
                        self.assertAlmostEqual(char['bbox'][2] - char['bbox'][0], 6, delta=.05)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertTrue(window.document.undo())
            self.assertIn('AB 가나', window.document.doc[0].get_text())

        self.with_window(run)

    def test_changing_font_preserves_repeated_spaces_and_line_breaks(self):
        replacement = EditFont('ADF Fixture Replacement',
                               fixture_font('ADF Fixture Replacement', width=360, space_width=240), 'PC 글꼴')
        text = 'AB  가나\nC 다'

        def run(window):
            window.text_value.setPlainText(text)
            with patch('adf.app.installed_font', return_value=replacement):
                window.text_font.setCurrentFont(QFont(replacement.label))
            self.assertIs(window.edit_font, replacement)
            self.assertEqual(window.text_value.toPlainText(), text)
            window.text_value.document().documentLayout().documentSize()
            line = window.text_value.document().firstBlock().layout().lineAt(0)
            for start in (2, 3):
                self.assertAlmostEqual(line.cursorToX(start+1)[0] - line.cursorToX(start)[0], 4.8, delta=.05)
            self.assertTrue(window.finish_text_selection())
            target = self.path.with_name('font-changed.pdf')
            window.document.save(target)
            with pymupdf.open(target) as saved:
                self.assertIn(text, saved[0].get_text())

        self.with_window(run)

    def test_zero_space_advance_is_restored_from_pdf_widths_or_visible_gap(self):
        import re
        data = fixture_font(space_width=300)
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname='Fixture', fontbuffer=data)
            page.insert_text((40, 80), 'AB', fontname='Fixture', fontsize=20)
            page.insert_text((66.8, 80), '가나', fontname='Fixture', fontsize=20)
            doc.subset_fonts()
            buffer = doc.tobytes()
        for widths, default in (('[1 [300] 2 8 520]', None), ('[1 1 300 2 8 520]', None),
                                ('[2 8 520]', '300'), ('[2 8 520]', None)):
            with self.subTest(widths=widths, default=default), pymupdf.open(stream=buffer) as doc:
                page = doc[0]
                resource = page.get_fonts(full=True)[0]
                descendant = int(re.findall(r'(\d+)\s+0\s+R', doc.xref_get_key(resource[0], 'DescendantFonts')[1])[0])
                doc.xref_set_key(descendant, 'W', widths)
                doc.xref_set_key(descendant, 'DW', default or 'null')
                font = embedded_font(page, resource[3])
                self.assertIsNotNone(font)
                self.assertAlmostEqual(font.metrics.glyph_advance(32), .3, places=3)
                # Restoring a blank space must leave all painted glyphs alone.
                original = pymupdf.Font(fontbuffer=data)
                self.assertEqual(font.metrics.text_length('AB가나'), original.text_length('AB가나'))

    def test_subset_without_any_space_mapping_renders_empty_space_in_editor(self):
        import re
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=500)
            page.insert_font(fontname='Fixture', fontbuffer=fixture_font(space_width=300))
            page.insert_text((40, 80), 'AB', fontname='Fixture', fontsize=20)
            page.insert_text((66.8, 80), 'C', fontname='Fixture', fontsize=20)
            resource = page.get_fonts()[0]
            unicode_ref = int(doc.xref_get_key(resource[0], 'ToUnicode')[1].split()[0])
            doc.update_stream(unicode_ref, re.sub(rb'<0001>\s*<0020>', b'', doc.xref_stream(unicode_ref)))
            doc.subset_fonts()
            doc.save(self.path)

        def run(window):
            self.assertEqual(window.text_value.toPlainText(), 'AB C')
            raw = QRawFont.fromFont(window.text_font.currentFont())
            gid = raw.glyphIndexesForString(' ')[0]
            self.assertGreater(gid, 0)
            self.assertTrue(raw.boundingRect(gid).isEmpty())
            window.text_value.document().documentLayout().documentSize()
            line = window.text_value.document().firstBlock().layout().lineAt(0)
            self.assertAlmostEqual(line.cursorToX(3)[0]-line.cursorToX(2)[0], 6, delta=.05)
            window.text_value.setPlainText('C AB')
            self.assertTrue(window.finish_text_selection())
            self.assertIn('C AB', window.document.doc[0].get_text())

        self.with_window(run)

    def test_unmapped_blank_cid_36_recovers_a_space_and_keeps_currency(self):
        import re
        data = fixture_font(space_width=300, extra_codepoints=(36, 49, 48), spacer_gid=36)
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname='Fixture', fontbuffer=data)
            page.insert_text((40, 80), 'AB C $100', fontname='Fixture', fontsize=20)
            resource = page.get_fonts()[0]
            unicode_ref = int(doc.xref_get_key(resource[0], 'ToUnicode')[1].split()[0])
            cmap = doc.xref_stream(unicode_ref)
            cmap = re.sub(rb'<0024>\s*<0020>', b'', cmap)
            doc.update_stream(unicode_ref, cmap)
            doc.subset_fonts()
            buffer = doc.tobytes()
        with pymupdf.open(stream=buffer) as doc:
            self.assertIn('AB$C$$100', doc[0].get_text())
            group = text_group_at(doc[0], (45, 75))
            self.assertEqual(group['text'], 'AB C $100')
            source = embedded_font(doc[0], group['font'])
            self.assertIsNotNone(source)
            self.assertEqual(source.missing(group['text']), '')
            self.assertAlmostEqual(source.metrics.glyph_advance(32), .3, places=4)
            # Actual currency remains an outlined dollar in the recovered font.
            font = TTFont(io.BytesIO(source.data))
            self.assertNotEqual(font.getBestCmap()[32], font.getBestCmap()[36])

    def test_blank_dollar_mapping_is_a_space_after_font_change_and_save(self):
        import re
        data = fixture_font(space_width=300, extra_codepoints=(36, 49, 48))
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=500)
            page.insert_font(fontname='Fixture', fontbuffer=data)
            page.insert_text((40, 80), 'AB C $100', fontname='Fixture', fontsize=20)
            resource = page.get_fonts()[0]
            unicode_ref = int(doc.xref_get_key(resource[0], 'ToUnicode')[1].split()[0])
            cmap = doc.xref_stream(unicode_ref)
            cmap = re.sub(rb'(<0001>\s*)<0020>', rb'\g<1><0024>', cmap)
            doc.update_stream(unicode_ref, cmap)
            doc.subset_fonts()
            doc.save(self.path)
        original = self.path.read_bytes()
        replacement = EditFont('ADF Dollar Replacement',
                               fixture_font('ADF Dollar Replacement', space_width=240,
                                            extra_codepoints=(36, 49, 48)), 'PC 글꼴')

        def run(window):
            self.assertEqual(window.text_value.toPlainText(), 'AB C $100')
            self.assertGreater(window.edit_font.metrics.glyph_advance(32), 0)
            with patch('adf.app.installed_font', return_value=replacement):
                window.text_font.setCurrentFont(QFont(replacement.label))
            self.assertIs(window.edit_font, replacement)
            self.assertEqual(window.text_value.toPlainText(), 'AB C $100')
            self.assertTrue(window.finish_text_selection())
            target = self.path.with_name('blank-dollar-saved.pdf')
            window.document.save(target)
            with pymupdf.open(target) as saved:
                self.assertIn('AB C $100', saved[0].get_text())
                self.assertEqual(saved[0].get_text().count('$'), 1)
            self.assertEqual(self.path.read_bytes(), original)
            self.assertTrue(window.document.undo())
            self.assertIn('AB$C', window.document.doc[0].get_text())

        self.with_window(run)

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
        from adf.font_widgets import FontSubstituteDialog, OTHER_FONT
        window = MainWindow(smoke=True)
        def inspect(dialog):
            self.assertEqual({dialog.replace.text(), dialog.cancel.text()}, {'대체 글꼴로 편집', '편집 취소'})
            self.assertTrue(dialog.cancel.isDefault())
            self.assertFalse(dialog.remember.isChecked())
            self.assertFalse(dialog.save_exception.isChecked())
            self.assertIn('Missing', dialog.headline.text())
            # Ranked suggestions with their reasons, then a free choice.
            self.assertGreaterEqual(dialog.fonts.count(), 2)
            self.assertIn('  ·  ', dialog.fonts.itemText(0))
            self.assertEqual(dialog.fonts.itemText(dialog.fonts.count()-1), OTHER_FONT)
            self.assertIsNotNone(dialog.choice)
            return 0
        try:
            with patch.object(FontSubstituteDialog, 'exec', inspect):
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
        from adf.font_widgets import FontSubstituteDialog
        window = MainWindow(smoke=True)
        prompts = []
        def accept(dialog):
            prompts.append(dialog.headline.text())
            dialog.remember.setChecked(True)
            return 1
        try:
            with patch.object(FontSubstituteDialog, 'exec', accept):
                first = window.choose_replacement_font('AAAAAA+MissingOne', 'ABC')
                second = window.choose_replacement_font('MissingOne', 'ABC')
                self.assertIs(first, second)
                self.assertTrue(first.source.startswith('대체 글꼴'))
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
        from adf.font_widgets import FontSubstituteDialog
        window = MainWindow(smoke=True)
        try:
            with patch.object(FontSubstituteDialog, 'exec', lambda dialog: 1):
                self.assertIsNotNone(window.choose_replacement_font('Missing', 'ABC'))
                self.assertIsNotNone(window.choose_replacement_font('Missing', 'ABC'))
                self.assertEqual(window.font_choices, {})
            def cancel(dialog):
                dialog.remember.setChecked(True)
                return 0
            with patch.object(FontSubstituteDialog, 'exec', cancel):
                self.assertIsNone(window.choose_replacement_font('Missing', 'ABC'))
                self.assertEqual(window.font_choices, {})
        finally:
            window.close()

    def test_saved_substitute_is_suggested_first_in_later_documents(self):
        from adf.font_widgets import FontSubstituteDialog
        window = MainWindow(smoke=True)
        window.settings.remove('fonts/substitutes')
        self.addCleanup(window.settings.remove, 'fonts/substitutes')
        seen = []
        def keep_second(dialog):
            seen.append([dialog.fonts.itemText(i) for i in range(dialog.fonts.count())])
            if len(seen) == 1:
                dialog.fonts.setCurrentIndex(1)
                dialog.save_exception.setChecked(True)
            return 1
        try:
            with patch.object(FontSubstituteDialog, 'exec', keep_second):
                chosen = window.choose_replacement_font('SavedMissing', 'ABC')
                again = window.choose_replacement_font('SavedMissing', 'ABC')
            self.assertEqual(again.label, chosen.label)
            self.assertTrue(seen[1][0].startswith(chosen.label + '  ·  저장한 대체 글꼴'))
        finally:
            window.close()


class SimilarFontTests(unittest.TestCase):
    def test_font_names_split_into_family_weight_and_italic(self):
        from adf.fonts import font_kind, font_style
        self.assertEqual(font_style('AAAAAA+NanumGothic-Bold'), ('nanumgothic', 700, False))
        self.assertEqual(font_style('MalgunGothicBold'), ('malgungothic', 700, False))
        self.assertEqual(font_style('Arial,BoldItalic'), ('arial', 700, True))
        self.assertEqual(font_style('Arial-BoldMT'), ('arial', 700, False))
        self.assertEqual(font_style('Pretendard-SemiBold'), ('pretendard', 600, False))
        self.assertEqual(font_style('NanumSquareEB'), ('nanumsquare', 800, False))
        self.assertEqual(font_style('HY헤드라인M'), ('hy헤드라인', 500, False))
        # A capital at the end of an ordinary name is not a weight.
        self.assertEqual(font_style('Arial'), ('arial', 400, False))
        self.assertEqual(font_style('GulimChe'), ('gulimche', 400, False))
        self.assertEqual(font_kind('NanumMyeongjo'), 'serif')
        self.assertEqual(font_kind('CourierNewPSMT'), 'mono')
        self.assertEqual(font_kind('Unknown', flags=2), 'serif')
        self.assertEqual(font_kind('Unknown'), 'sans')

    def test_similar_names_come_first_in_the_nearest_weight(self):
        from adf.fonts import similar_fonts
        from adf.system_fonts import font_families
        if 'arial' not in font_families():
            self.skipTest('Arial is not installed')
        found = similar_fonts('Arial-BoldMT', 'ABC')
        self.assertEqual(found[0][1], '이름이 비슷한 글꼴')
        self.assertIn('Arial', found[0][0].label)
        self.assertIn('Bold', found[0][0].label)
        self.assertTrue(all(not font.missing('ABC') for font, _ in found))

    def test_unknown_names_get_the_default_of_their_kind(self):
        from adf.fonts import similar_fonts
        found = similar_fonts('QqxzUnrelated', 'ABC')
        if not found:
            self.skipTest('No default font is installed')
        self.assertTrue(found[0][1].endswith('기본 글꼴'))
