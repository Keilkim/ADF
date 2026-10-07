"""Copy real PDF glyphs with encoded spaces, positioned words and bad mappings."""
import os
import re

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
import pytest
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf.app import MainWindow
from adf.selection_widgets import PageText, RegionSelection
from test_fonts import fixture_font


@pytest.fixture(scope='module')
def application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    yield app
    app.clipboard().clear()


def positioned(page, words, gap, tracking=0, fontname='korea'):
    x, size = 40., 16
    font = pymupdf.Font(fontname)
    for word in words:
        for char in word:
            page.insert_text((x, 80), char, fontname=fontname, fontsize=size)
            x += font.text_length(char, fontsize=size) + tracking
        x += gap - tracking


@pytest.mark.parametrize('gap', [2, 3, 4, 6])
@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_positioned_korean_words_recover_spaces_without_changing_pdf(gap, rotation):
    with pymupdf.open() as doc:
        page = doc.new_page()
        positioned(page, ['계약', '해지', '조건', '안내'], gap)
        page.set_rotation(rotation)
        before = doc.tobytes(no_new_id=True)
        selected = PageText(page)
        assert selected.text(0, len(selected.chars)) == '계약 해지 조건 안내'
        assert doc.tobytes(no_new_id=True) == before


@pytest.mark.parametrize('tracking', [0, .75, 2, 4])
def test_uniform_letter_tracking_does_not_create_word_spaces(tracking):
    with pymupdf.open() as doc:
        page = doc.new_page()
        positioned(page, ['계약해지조건안내'], 0, tracking=tracking)
        selected = PageText(page)
        assert selected.text(0, len(selected.chars)) == '계약해지조건안내'


def test_word_gaps_are_distinguished_from_letter_tracking():
    with pymupdf.open() as doc:
        page = doc.new_page()
        positioned(page, ['계약', '해지', '조건', '안내'], 5, tracking=1.5)
        selected = PageText(page)
        assert selected.text(0, len(selected.chars)) == '계약 해지 조건 안내'


def test_japanese_gaps_do_not_invent_korean_word_boundaries():
    with pymupdf.open() as doc:
        page = doc.new_page()
        positioned(page, ['契約', '解除', '条件', '案内'], 6, fontname='japan')
        selected = PageText(page)
        assert selected.text(0, len(selected.chars)) == '契約解除条件案内'


@pytest.mark.parametrize('text', ['한글  두 칸   세 칸', 'ABC  123 $100', '한글 ABC 123', '계약해지조건안내'])
def test_existing_spaces_and_currency_are_preserved(text):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 80), text, fontname='korea', fontsize=16)
        selected = PageText(page)
        assert selected.text(0, len(selected.chars)) == text


@pytest.mark.parametrize('unmapped', [False, True])
def test_blank_dollar_glyph_recovers_space_but_real_currency_remains(unmapped):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_font(fontname='Fixture', fontbuffer=fixture_font(
            space_width=300, extra_codepoints=(36, 49, 48), spacer_gid=36 if unmapped else 1))
        page.insert_text((40, 80), 'AB C $100', fontname='Fixture', fontsize=20)
        xref = int(doc.xref_get_key(page.get_fonts()[0][0], 'ToUnicode')[1].split()[0])
        cmap = doc.xref_stream(xref)
        cmap = re.sub(rb'<0024>\s*<0020>', b'', cmap) if unmapped else re.sub(
            rb'(<0001>\s*)<0020>', rb'\g<1><0024>', cmap)
        doc.update_stream(xref, cmap)
        doc.subset_fonts()
        with pymupdf.open(stream=doc.tobytes(), filetype='pdf') as reopened:
            selected = PageText(reopened[0])
            assert selected.text(0, len(selected.chars)) == 'AB C $100'


def test_drag_copy_and_region_copy_preserve_positioned_spaces(application, tmp_path):
    source = tmp_path / 'positioned.pdf'
    with pymupdf.open() as doc:
        positioned(doc.new_page(), ['계약', '해지', '조건', '안내'], 4)
        doc.save(source)
    window = MainWindow(smoke=True)
    try:
        window.show()
        assert window.open_path(source)
        view = window.view
        view.set_mode('single')
        view.fit('page')
        application.processEvents()
        page = window.document.doc[0]
        text = PageText(page)
        first, last = text.chars[0][0].rect, text.chars[-1][0].rect
        def point(x, y):
            return view.mapFromScene(view.pages[0].mapToScene(QPointF(x, y)))
        viewport = view.viewport()
        start = point(first.x0 - 1, (first.y0 + first.y1) / 2)
        end = point(last.x1 + 1, (last.y0 + last.y1) / 2)
        QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(viewport, end)
        QTest.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=end)
        window.actions['copy'].trigger()
        assert application.clipboard().text() == '계약 해지 조건 안내'
        region = RegionSelection(view, view.pages[0])
        try:
            region.set_scene_rect(view.pages[0].mapRectToScene(QRectF(35, 55, 250, 35)))
            assert region.finish(page) == '계약 해지 조건 안내'
        finally:
            region.dispose()
    finally:
        window.close()
        application.clipboard().clear()
