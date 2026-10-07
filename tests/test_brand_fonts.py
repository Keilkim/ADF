"""Validate the eight bilingual font deliverables and real shaping/rendering.

Optional build dependencies: scripts/requirements-brand-fonts.txt.
The tests register fonts only in this test process, never in the OS.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
import unicodedata
import zipfile

import pytest
from fontTools.ttLib import TTFont

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo, QImage, QRawFont
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / 'assets/fonts'
MANIFEST = json.loads((FONTS / 'manifest.json').read_text(encoding='utf-8'))
FACES = MANIFEST['fonts']
IDS = [f"{face['family']}-{face['style']}" for face in FACES]


@pytest.fixture(scope='module')
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def hb():
    return pytest.importorskip('uharfbuzz', reason='Install the optional font build dependencies')


def shape(hb, data, text, features=None):
    font = hb.Font(hb.Face(data))
    buffer = hb.Buffer()
    buffer.add_str(text)
    buffer.guess_segment_properties()
    hb.shape(font, buffer, features)
    return [(info.codepoint, pos.x_advance, pos.y_advance, pos.x_offset, pos.y_offset)
            for info, pos in zip(buffer.glyph_infos, buffer.glyph_positions)]


def test_requested_family_and_weight_matrix():
    assert {(f['family'], f['style'], f['weight']) for f in FACES} == {
        (family, style, weight) for family in ('Xebatang', 'Xedotum')
        for style, weight in (('Light', 300), ('Medium', 500), ('Bold', 700), ('ExtraBold', 800))
    }
    assert len(list((FONTS / 'ttf').glob('*.ttf'))) == 8
    assert len(list((FONTS / 'woff2').glob('*.woff2'))) == 8


@pytest.mark.parametrize('face', FACES, ids=IDS)
def test_every_face_has_full_modern_hangul_latin_and_valid_metadata(face):
    path = FONTS / face['ttf']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == face['ttf_sha256']
    with TTFont(path, checkChecksums=2) as font:
        cmap = font.getBestCmap()
        assert set(range(0xAC00, 0xD7A4)) <= cmap.keys()
        assert set(range(0x20, 0x7F)) <= cmap.keys()
        assert {ord(c) for c in '₩“”‘’—·…'} <= cmap.keys()
        assert all(font.getGlyphID(cmap[cp]) > 0 for cp in range(0xAC00, 0xD7A4))
        assert font['name'].getDebugName(16) == face['family']
        assert font['name'].getDebugName(17) == face['style']
        assert font['name'].getDebugName(6) == f"{face['family']}-{face['style']}"
        assert font['OS/2'].usWeightClass == face['weight']
        assert font['OS/2'].fsType == 0
        assert font['OS/2'].version >= 4
        assert 'Open Font License' in font['name'].getDebugName(13)
        assert 'XDF contributors' in font['name'].getDebugName(0)
        assert 'Original Latin and Hangul outlines' in font['name'].getDebugName(0)
        assert 'fvar' not in font and 'gvar' not in font
        tags = {record.FeatureTag for record in font['GSUB'].table.FeatureList.FeatureRecord}
        assert {'ccmp', 'dlig'} <= tags
        assert len(font['GPOS'].table.LookupList.Lookup) > 0


@pytest.mark.parametrize('face', FACES, ids=IDS)
def test_web_and_desktop_versions_have_identical_characters_and_metrics(face):
    pytest.importorskip('brotli', reason='WOFF2 decoder is an optional font build dependency')
    web = FONTS / face['woff2']
    assert hashlib.sha256(web.read_bytes()).hexdigest() == face['woff2_sha256']
    with TTFont(FONTS / face['ttf']) as ttf, TTFont(web) as woff:
        assert ttf.getBestCmap() == woff.getBestCmap()
        assert ttf['hmtx'].metrics == woff['hmtx'].metrics
        assert ttf['name'].getDebugName(4) == woff['name'].getDebugName(4)


@pytest.mark.parametrize('face', FACES, ids=IDS)
def test_korean_shaping_and_opt_in_logo(face, hb):
    data = (FONTS / face['ttf']).read_bytes()
    text = '가 고 과 간 곤 관 · 값 읽다 꽃 · 새로운 문서의 시작.'
    composed = shape(hb, data, text)
    decomposed = shape(hb, data, unicodedata.normalize('NFD', text))
    assert composed == decomposed
    assert all(item[0] for item in composed)
    latin = shape(hb, data, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz 0123456789')
    assert all(item[0] for item in latin)
    normal = shape(hb, data, 'XDF', {'dlig': False})
    logo = shape(hb, data, 'XDF', {'dlig': True})
    assert len(normal) == 3
    assert len(logo) == 1
    with TTFont(FONTS / face['ttf']) as font:
        assert logo[0][0] == face['logo_glyph_id']
    assert all(item[0] for item in logo)


def test_native_font_loading_selects_all_four_real_weights(qt_app):
    ids = []
    try:
        for face in FACES:
            identity = QFontDatabase.addApplicationFont(str(FONTS / face['ttf']))
            assert identity >= 0, face['ttf']
            ids.append(identity)
        for family in ('Xebatang', 'Xedotum'):
            for style, weight in (('Light', 300), ('Medium', 500), ('Bold', 700), ('ExtraBold', 800)):
                requested = QFont(family)
                requested.setWeight(QFont.Weight(weight))
                requested.setStyleName(style)
                info = QFontInfo(requested)
                assert info.family() == family
                assert info.styleName() == style
                # CoreText reports this real ExtraBold face as Black on macOS.
                # Verify the selected file's OpenType weight as well as its name.
                allowed = {weight, 900} if sys.platform == 'darwin' and weight == 800 else {weight}
                assert int(info.weight()) in allowed
                if sys.platform == 'darwin' and qt_app.platformName() == 'cocoa':
                    raw = QRawFont.fromFont(requested)
                    assert raw.familyName() == family
                    assert raw.styleName() == style
                    assert int.from_bytes(bytes(raw.fontTable('OS/2'))[4:6], 'big') == weight
    finally:
        for identity in ids:
            QFontDatabase.removeApplicationFont(identity)


def test_real_rendered_stroke_coverage_increases_at_each_weight(qt_app):
    for family in ('Xebatang', 'Xedotum'):
        areas = []
        for face in [f for f in FACES if f['family'] == family]:
            raw = QRawFont(str(FONTS / face['ttf']), 128)
            assert raw.isValid()
            assert all(raw.supportsCharacter(ord(char)) for char in '값읽꽃괜AZaz09')
            glyph = raw.glyphIndexesForString('문')[0]
            alpha = raw.alphaMapForGlyph(glyph, QRawFont.AntialiasingType.PixelAntialiasing)
            assert alpha.format() == QImage.Format.Format_Alpha8
            assert not alpha.isNull()
            buf = bytes(alpha.constBits())
            area = sum(sum(buf[row*alpha.bytesPerLine():row*alpha.bytesPerLine()+alpha.width()])
                       for row in range(alpha.height()))
            areas.append(area)
        assert all(a < b for a, b in zip(areas, areas[1:])), (family, areas)


@pytest.mark.parametrize('face', FACES, ids=IDS)
def test_font_logo_preserves_the_transparent_d_counter(face, qt_app):
    raw = QRawFont(str(FONTS / face['ttf']), 256)
    alpha = raw.alphaMapForGlyph(face['logo_glyph_id'], QRawFont.AntialiasingType.PixelAntialiasing)
    assert alpha.format() == QImage.Format.Format_Alpha8
    # Positions are proportional to the original SVG's 1871 x 478 ink bounds.
    row = round(alpha.height()*247.5/478)
    counter = round(alpha.width()*1020/1871)
    stem = round(alpha.width()*800/1871)
    assert alpha.pixelColor(counter, row).alpha() == 0
    assert alpha.pixelColor(stem, row).alpha() > 240


def test_download_archive_contains_all_fonts_and_license():
    with zipfile.ZipFile(FONTS / 'xdf-fonts-0.200.zip') as archive:
        names = set(archive.namelist())
        assert {'OFL.txt', 'manifest.json', 'build-xdf-type.py', 'xdf_type_geometry.py', 'README.txt',
                'preview-cover.png', 'preview.png', 'original-glyphs.svg', 'letter-anatomy.svg', 'index.html'} <= names
        license_text = archive.read('OFL.txt').decode('utf-8')
        assert 'SIL OPEN FONT LICENSE Version 1.1' in license_text
        assert 'XDF contributors' in license_text
        assert 'Adobe' not in license_text and 'Noto' not in license_text
        for face in FACES:
            for kind in ('ttf', 'woff2'):
                assert archive.read(face[kind]) == (FONTS / face[kind]).read_bytes()
        for name in ('preview-cover.png', 'preview.png'):
            assert archive.read(name) == (FONTS / name).read_bytes()


def test_original_sources_and_contextual_hangul_are_delivered():
    assert MANIFEST['upstream_typefaces'] == []
    source = ROOT / 'scripts/xdf_type_geometry.py'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == MANIFEST['geometry_sha256']
    assert (FONTS / 'xdf_type_geometry.py').read_bytes() == source.read_bytes()
    for face in FACES:
        assert face['latin_original'] and face['hangul_original']
        with TTFont(FONTS / face['ttf']) as font:
            cmap = font.getBestCmap()
            for cp in (ord(c) for c in '가고과간곤관값읽꽃'):
                glyph = font['glyf'][cmap[cp]]
                assert glyph.isComposite()
                assert all(component.glyphName.startswith(('cho.', 'jung.', 'jong.'))
                           for component in glyph.components)
            assert font['glyf'][cmap[ord('a')]].coordinates != font['glyf'][cmap[ord('o')]].coordinates


@pytest.mark.parametrize('face', FACES, ids=IDS)
def test_small_hangul_counters_survive_even_the_heaviest_weight(face):
    with TTFont(FONTS / face['ttf']) as font:
        for form in ('v0','v1','h0','h1','m0','m1'):
            for jamo,minimum in ((6,2),(7,3),(11,2),(18,2)):
                assert font['glyf'][f'cho.{form}.{jamo}'].numberOfContours >= minimum
        for jamo,minimum in ((16,2),(17,3),(21,2),(27,2)):
            assert font['glyf'][f'jong.{jamo}'].numberOfContours >= minimum
        # Rieul is an open folded stroke. Its two spaces must stay open.
        assert font['glyf']['jong.8'].numberOfContours == 1


@pytest.mark.parametrize('face', FACES, ids=IDS)
def test_text_letter_heights_stay_aligned_across_real_weights(face):
    with TTFont(FONTS / face['ttf']) as font:
        cmap = font.getBestCmap()
        for character in 'aenorsu':
            glyph = font['glyf'][cmap[ord(character)]]
            assert glyph.yMin == 0 and glyph.yMax == font['OS/2'].sxHeight
        for character in 'bdfhkli':
            glyph = font['glyf'][cmap[ord(character)]]
            assert glyph.yMin == 0 and glyph.yMax == font['OS/2'].sCapHeight
