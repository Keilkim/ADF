"""Measurements that keep Hancom's line breaks equal to the PDF's."""
import os
from pathlib import Path

import pymupdf
import pytest

from adf.pdf2hwpx import native_page
from adf.pdf2hwpx.font_metrics import glyph_width
from adf.pdf2hwpx.pdf_reader import PdfFormatting, fit_line, line_width

MALGUN = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'malgun.ttf'
KOREAN = ('원도심의 빈 상가를 새로운 활동 공간으로 바꾸고 청년과 문화예술인의 활동을 지원한다. '
          '사업 기간 동안 임대료를 지원하고 종료 후에도 효과가 이어지도록 운영 계획을 마련한다. ')


def test_dotted_border_drawn_as_tiny_strokes_is_one_border():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        x0, y0, x1, y1 = 60, 100, 400, 160
        for x in range(x0, x1, 2):
            page.draw_line((x, y0), (x + .5, y0), width=.4)
            page.draw_line((x, y1), (x + .5, y1), width=.4)
        for y in range(y0, y1, 2):
            page.draw_line((x0, y), (x0, y + .5), width=.4)
            page.draw_line((x1, y), (x1, y + .5), width=.4)
        page.insert_text((70, 135), 'Text inside a dotted box', fontsize=11)
        table = next(block for block in native_page(page)['blocks'] if block['kind'] == 'table')
    assert {border['type'] for border in table['cells'][0]['borders']} == {'DOT'}


def test_latin_text_uses_the_fonts_own_space():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 100), 'A short line of plain text with spaces.', fontsize=11)
        reader = PdfFormatting(page)
    if glyph_width('Arial', ' ', 1) is None:
        pytest.skip('Arial is not installed')
    assert reader.font_space == {'Helvetica': True}


def test_one_line_cell_text_is_condensed_to_fit_its_room():
    if glyph_width('Arial', 'a', 10) is None:
        pytest.skip('Arial is not installed')
    paragraph = dict(runs=[dict(text='Contact person', font='Arial', font_size_pt=10)], line_count=1)
    width = line_width(paragraph['runs'])
    fit_line(paragraph, width * .9)
    assert paragraph['runs'][0]['letter_spacing'] <= -10
    assert line_width(paragraph['runs']) <= width * .9


def test_chart_bars_outside_cells_make_a_designed_page():
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=842, height=595)
        # A Gantt chart: labelled bars over a grid that ties them together.
        # A chart with little text stays one picture; this one is mostly
        # text, so its bars must be kept behind editable labels.
        for x in range(100, 701, 40):
            page.draw_line((x, 70), (x, 530), width=.3, color=(.8, .8, .8))
        for row in range(20):
            page.draw_rect((100 + row * 20, 80 + row * 22, 260 + row * 20, 92 + row * 22), color=None, fill=(.3, .3, .5))
            page.insert_text((270 + row * 20, 90 + row * 22), f'Task {row}: survey, design and review', fontsize=11)
        assert native_page(page).get('free_layout') is True
        plain = pdf.new_page()
        plain.insert_textbox(pymupdf.Rect(72, 72, 523, 400), KOREAN, fontsize=11)
        assert not native_page(plain).get('free_layout')


@pytest.mark.skipif(not MALGUN.is_file(), reason='needs a Korean font')
def test_line_ends_without_spaces_keep_word_spacing_and_ragged_alignment():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_font(fontname='malgun', fontfile=str(MALGUN))
        page.insert_textbox(pymupdf.Rect(72, 72, 400, 400), KOREAN * 2, fontsize=11, fontname='malgun')
        paragraph = next(block for block in native_page(page)['blocks'] if block['kind'] == 'paragraph')
    words = ' '.join(KOREAN.split())
    assert ' '.join(paragraph['text'].split()).startswith(words[:60])
    assert '사업 기간' in paragraph['text'] and paragraph['alignment'] == 'LEFT'


def test_page_frame_is_drawn_and_shaded_title_bar_stays_editable():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        # A dotted frame around the page body, a shaded title bar of two
        # boxes drawn as closed paths, and a table below.
        page.draw_rect((50, 50, 545, 790), width=.4, dashes='[1 1] 0')
        for x0, x1 in ((60, 120), (128, 535)):
            shape = page.new_shape()
            shape.draw_polyline([(x0, 60), (x1, 60), (x1, 88), (x0, 88), (x0, 60)])
            shape.finish(color=None, fill=(.94, .94, .94), closePath=True)
            shape.commit()
        page.insert_text((70, 79), 'Note 1', fontsize=14)
        page.insert_text((140, 79), 'Housing fund support', fontsize=14)
        for row in range(5):
            for col in range(3):
                page.draw_rect((60 + col * 150, 120 + row * 30, 210 + col * 150, 150 + row * 30), width=.5)
                page.insert_text((65 + col * 150, 140 + row * 30), f'cell {row} {col}', fontsize=10)
        blocks = native_page(page)['blocks']
    frames = [block for block in blocks if block.get('frame')]
    assert len(frames) == 1 and frames[0]['frame']['type'] == 'DOT'
    title = next(block for block in blocks if block['kind'] == 'table' and block['rows'] == 1)
    assert title['cols'] == 3 and title['cells'][1]['margins_pt']['left'] == 0
    assert 'Housing fund support' in title['cells'][2]['text']


def test_highlight_behind_some_words_becomes_letter_shading():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 99), '[Criteria] funds are limited and checked first.', fontsize=12)
        word = page.search_for('Criteria')[0]
        page.draw_rect(word + (-1, 1, 1, -1), color=None, fill=(.89, .97, 1), overlay=False)
        paragraph = next(block for block in native_page(page)['blocks'] if block['kind'] == 'paragraph')
    shaded = [run for run in paragraph['runs'] if run.get('highlight')]
    assert shaded and 'Criteria' in ''.join(run['text'] for run in shaded)
    assert 'limited' not in ''.join(run['text'] for run in shaded)


@pytest.mark.skipif(not MALGUN.is_file(), reason='needs a Korean font')
def test_title_line_starts_where_hancom_puts_its_baseline():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_font(fontname='malgun', fontfile=str(MALGUN))
        page.insert_text((72, 300), '인천광역시', fontsize=30, fontname='malgun')
        page.insert_text((72, 342), '도시디자인 진흥계획', fontsize=34, fontname='malgun')
        height = page.rect.height
        title = next(block for block in native_page(page)['blocks'] if block['kind'] == 'paragraph')
    # Malgun Gothic's glyph box reaches far above; Hancom draws the
    # baseline 0.85 em below the line top.
    assert abs(title['bbox'][1] * height / 1000 - (300 - .85 * 30)) < 1
