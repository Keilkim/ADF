"""Installed-font discovery without changing Windows' font registrations."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf
from fontTools.ttLib import TTFont, TTCollection

from adf.fonts import installed_font
from adf.system_fonts import refresh_fonts, resolve_font_face, resolve_font_file
from test_fonts import fixture_font


class SystemFontTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='adf-font-discovery-')
        self.root = Path(self.temp.name)
        self.paths = patch('adf.system_fonts.font_paths', side_effect=lambda: sorted(self.root.glob('*')))
        self.paths.start()
        refresh_fonts()

    def tearDown(self):
        self.paths.stop()
        refresh_fonts()
        self.temp.cleanup()

    def font(self, family, ps_name, *, width=520, style='Regular', localized=None):
        font = TTFont(io.BytesIO(fixture_font(family, width=width)))
        for platform, encoding, language in [(1, 0, 0), (3, 1, 0x409)]:
            for name_id, value in [(1, family), (2, style), (4, family+' '+style), (6, ps_name), (16, family), (17, style)]:
                font['name'].setName(value, name_id, platform, encoding, language)
        if localized:
            font['name'].setName(localized, 1, 3, 1, 0x412)
            font['name'].setName(localized, 4, 3, 1, 0x412)
        return font

    def test_internal_english_localized_and_pdf_names_resolve_same_file(self):
        path = self.root/'unrelated-filename.ttf'
        with self.font('ADF Alias Family', 'ADFAliasMT', localized='검증 글꼴') as font:
            font.save(path)
        for name in ['ADF Alias Family', 'ADF Alias Family Regular', 'ADFAliasMT', 'ABCDEF+ADFAliasMT', '검증 글꼴']:
            with self.subTest(name=name):
                resolved = installed_font(name)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved.data, path.read_bytes())
                self.assertEqual(resolved.missing('AB 가나'), '')
        self.assertIsNone(installed_font('ADF Alias Family Italic'))

    def test_regular_and_bold_are_distinguished_by_postscript_name(self):
        for style, width in [('Bold', 800), ('Regular', 520)]:
            with self.font('ADF Face', 'ADFFace-'+style+'MT', style=style, width=width) as font:
                font.save(self.root/(style+'.ttf'))
        regular = installed_font('ADFFace-RegularMT')
        bold = installed_font('ADFFace-BoldMT')
        self.assertAlmostEqual(regular.metrics.glyph_advance(ord('A')), .52, places=3)
        self.assertAlmostEqual(bold.metrics.glyph_advance(ord('A')), .8, places=3)
        self.assertEqual(installed_font('ADF Face').data, regular.data)

    def test_nonfirst_collection_face_is_used_for_editing_and_numbering_file(self):
        collection = TTCollection()
        collection.fonts = [self.font('ADF First Face', 'ADFFirst', width=520),
                            self.font('ADF Second Face', 'ADFSecond', width=800, localized='둘째 글꼴')]
        collection.save(self.root/'random-collection.ttc')
        collection.close()
        face = resolve_font_face('ADFSecond')
        self.assertEqual(face.index, 1)
        edited = installed_font('둘째 글꼴')
        path = resolve_font_file('ADF Second Face')
        self.assertNotEqual(Path(path).read_bytes()[:4], b'ttcf')
        self.assertEqual(Path(path).read_bytes(), edited.data)
        metrics = pymupdf.Font(fontfile=path)
        self.assertAlmostEqual(metrics.glyph_advance(ord('A')), .8, places=3)
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_font(fontname='Selected', fontfile=path)
            page.insert_text((40, 60), 'AB', fontname='Selected')
            data = doc.tobytes()
        with pymupdf.open(stream=data, filetype='pdf') as saved:
            self.assertEqual(saved[0].get_text().strip(), 'AB')
            stored = saved.extract_font(saved[0].get_fonts()[0][0])[3]
            self.assertAlmostEqual(pymupdf.Font(fontbuffer=stored).glyph_advance(ord('A')), .8, places=3)

    def test_legacy_regular_subfamily_does_not_mistake_semibold_for_regular(self):
        with self.font('ADF Weight Semibold', 'ADFWeight-Semibold', width=800) as font:
            for platform, encoding, language in [(1, 0, 0), (3, 1, 0x409)]:
                font['name'].setName('ADF Weight', 16, platform, encoding, language)
                font['name'].setName('Semibold', 17, platform, encoding, language)
            font.save(self.root/'00-semibold.ttf')
        with self.font('ADF Weight', 'ADFWeight-Regular', width=520) as font:
            font.save(self.root/'99-regular.ttf')
        self.assertAlmostEqual(installed_font('ADF Weight').metrics.glyph_advance(ord('A')), .52, places=3)
        self.assertAlmostEqual(installed_font('ADF Weight Semibold').metrics.glyph_advance(ord('A')), .8, places=3)

    def test_newly_installed_font_is_found_after_inventory_refresh(self):
        with patch('adf.system_fonts.time.monotonic', return_value=10):
            self.assertIsNone(resolve_font_face('ADFLater'))
        with self.font('ADF Later', 'ADFLater') as font:
            font.save(self.root/'later.ttf')
        with patch('adf.system_fonts.time.monotonic', return_value=12):
            self.assertIsNotNone(resolve_font_face('ADFLater'))

    def test_broken_font_does_not_hide_other_installed_fonts(self):
        (self.root/'broken.ttf').write_bytes(b'not a font')
        with self.font('ADF Healthy', 'ADFHealthy') as font:
            font.save(self.root/'healthy.ttf')
        self.assertIsNotNone(installed_font('ADFHealthy'))
