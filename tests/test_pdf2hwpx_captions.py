"""Captions of tables and figures become real Hancom captions."""
import os
from pathlib import Path
import zipfile

import pymupdf
import pytest

from adf.pdf2hwpx import convert_document, native_page
from adf.pdf2hwpx.captions import attach_captions, caption_kind, detach_captions, names_several

MALGUN = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'malgun.ttf'
needs_korean_font = pytest.mark.skipif(not MALGUN.is_file(), reason='needs a Korean font')


@pytest.mark.parametrize('text, kind', [
    ('표 1. 지역별 매출 현황', 'table'), ('<표 2> 사업 개요', 'table'), ('[표 3-1] 예산', 'table'),
    ('〈표 Ⅱ-1〉 추진 일정', 'table'), ('Table 4: Results by year', 'table'), ('Tab. 5 Summary', 'table'),
    ('그림 1. 시스템 구성도', 'figure'), ('<그림 2-3> 조감도', 'figure'), ('Figure 1: Taxonomy of code', 'figure'),
    ('Fig. 2. Overview', 'figure'), ('사진 3 현장 전경', 'figure'),
])
def test_caption_openings(text, kind):
    assert caption_kind(text) == kind


@pytest.mark.parametrize('text', [
    '표 1에서 보듯이 매출이 늘었다.', '그림은 아래와 같다.', '표준 규격에 따라 작성한다.', 'Figures show the trend.',
    'Tables are listed below.', '도시 계획을 수립한다.', '', '1. 개요',
])
def test_sentences_are_not_captions(text):
    assert caption_kind(text) is None


def _page_with_table_and_figure(pdf):
    page = pdf.new_page()
    page.insert_font(fontname='malgun', fontfile=str(MALGUN))
    page.insert_text((72, 80), '표 1. 지역별 매출 현황', fontsize=11, fontname='malgun')
    for row in range(3):
        for col in range(3):
            page.draw_rect((72 + col * 100, 90 + row * 24, 172 + col * 100, 114 + row * 24), width=.6)
            page.insert_text((78 + col * 100, 107 + row * 24), f'칸 {row}{col}', fontsize=10, fontname='malgun')
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), 0)
    pixmap.set_rect(pixmap.irect, (90, 140, 200))
    page.insert_image(pymupdf.Rect(150, 250, 350, 350), pixmap=pixmap)
    page.insert_text((190, 372), 'Figure 2: System overview', fontsize=10)
    page.insert_text((72, 430), '표 1에서 보듯이 매출은 해마다 늘었다. 이 문장은 본문이다.', fontsize=11, fontname='malgun')
    return page


@needs_korean_font
def test_captions_attach_to_the_table_above_and_the_picture_below():
    with pymupdf.open() as pdf:
        blocks = native_page(_page_with_table_and_figure(pdf))['blocks']
    table = next(block for block in blocks if block['kind'] == 'table')
    image = next(block for block in blocks if block['kind'] == 'image')
    assert table['caption']['side'] == 'TOP' and '지역별 매출' in table['caption']['paragraph']['text']
    assert image['caption']['side'] == 'BOTTOM' and 'System overview' in image['caption']['paragraph']['text']
    # The caption leaves the paragraph list; the sentence about the table stays.
    texts = [block['text'] for block in blocks if block['kind'] == 'paragraph']
    assert not any('지역별 매출' in text or 'System overview' in text for text in texts)
    assert any('표 1에서 보듯이' in text for text in texts)
    # The object's own box is kept, its bbox covers both.
    assert table['bbox'][1] < table['caption']['object']['bbox'][1]


@needs_korean_font
def test_written_hwpx_has_real_captions(tmp_path):
    source, target = tmp_path / 'c.pdf', tmp_path / 'c.hwpx'
    with pymupdf.open() as pdf:
        _page_with_table_and_figure(pdf)
        pdf.save(source)
    convert_document(str(source), str(target))
    with zipfile.ZipFile(target) as package:
        section = package.read('Contents/section0.xml').decode()
    assert section.count('<hp:caption') == 2
    table_at, picture_at = section.index('<hp:tbl'), section.index('<hp:pic')
    top = section.index('<hp:caption side="TOP"')
    bottom = section.index('<hp:caption side="BOTTOM"')
    assert table_at < top < section.index('<hp:tr', table_at)       # inside the table, before its rows
    assert bottom > picture_at and '지역별 매출' in section[top:top + 3000]
    assert 'System overview' in section[bottom:bottom + 3000]
    # The gap keeps the table where it was: the caption's line top is the
    # baseline minus 0.85 em (80 - 9.35 pt), its height one 11 pt line (Hancom
    # adds no line spacing under a caption's last line), the table starts at 90 pt.
    import re
    gap = int(re.search(r'<hp:caption side="TOP"[^>]* gap="(\d+)"', section).group(1))
    assert gap == pytest.approx((90 - (80 - .85 * 11) - 11) * 100, abs=60)


@needs_korean_font
def test_free_layout_turns_captions_back_into_paragraphs():
    with pymupdf.open() as pdf:
        blocks = native_page(_page_with_table_and_figure(pdf))['blocks']
    loose = detach_captions(blocks)
    assert len(loose) == len(blocks) + 2 and not any(block.get('caption') for block in loose)
    assert attach_captions([dict(block) for block in loose], 612, 792) == 2


def test_a_wide_heading_that_names_no_object_is_left_alone():
    blocks = [dict(kind='paragraph', bbox=[100, 100, 900, 120], text='표 1. 제목이 아주 넓은 안내문입니다.', font_size_pt=11),
              dict(kind='table', bbox=[300, 125, 600, 300], text='', rows=1, cols=1, cells=[], font_size_pt=10)]
    assert attach_captions(blocks, 612, 792) == 0 and len(blocks) == 2


def test_two_captions_on_one_line_belong_to_two_objects():
    assert names_several('[ 표 1-2-2 표고분석 ]                     [ 그림 1-2-3 지형도 ]')
    assert not names_several('표 1. 지역별 매출 현황 (단위: 백만 원)')
    blocks = [dict(kind='paragraph', bbox=[100, 100, 500, 120], text='[ 표 1 가 ]          [ 그림 2 나 ]', font_size_pt=11),
              dict(kind='table', bbox=[100, 125, 300, 300], text='', rows=1, cols=1, cells=[], font_size_pt=10)]
    assert attach_captions(blocks, 612, 792) == 0
