"""Paragraph reconstruction, page geometry and package details of HWPX export."""
import io
import re
import zipfile

import pymupdf
from PIL import Image
from hwpx import HwpxDocument

from adf.hwpx_export import convert_document, native_page
from adf.hwpx_formatting import font_name, letter_spacing

LINES = ['  First paragraph line that reaches the right edge of', 'the text column and wraps once here.',
         'Second paragraph after a gap.']


def layout_pdf(path):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=595, height=842)
        right = 60 + pymupdf.get_text_length(LINES[0], fontsize=12)
        page.insert_text((60, 100), LINES[0], fontsize=12)
        page.insert_text((60, 118), LINES[1], fontsize=12)
        page.insert_text((60, 160), LINES[2], fontsize=12)
        # Two pieces of one visual line, like a date field and its label.
        page.insert_text((60, 200), 'Label', fontsize=10)
        page.insert_text((140, 200), 'Value', fontsize=10)
        # A ruled box around text only, like a press release title box.
        page.draw_rect((55, 230, right + 5, 290), width=.5)
        page.insert_text((70, 252), 'Boxed title', fontsize=16)
        page.insert_text((70, 278), '- boxed item', fontsize=11)
        # A row of three pictures.
        for x in (60, 200, 340):
            image = io.BytesIO()
            Image.new('RGB', (120, 40), (x % 255, 90, 160)).save(image, format='PNG')
            page.insert_image(pymupdf.Rect(x, 320, x + 120, 360), stream=image.getvalue())
        pdf.new_page(width=595, height=842).insert_text((60, 100), 'Next page', fontsize=12)
        pdf.save(path)


def test_wrapped_lines_become_one_paragraph_with_measured_pitch(tmp_path):
    layout_pdf(tmp_path / 'layout.pdf')
    with pymupdf.open(tmp_path / 'layout.pdf') as pdf:
        blocks = native_page(pdf[0])['blocks']
    paragraphs = [block for block in blocks if block['kind'] == 'paragraph']
    first = paragraphs[0]
    assert first['text'] == LINES[0] + ' ' + LINES[1]
    assert (first['line_count'], first['alignment'], first['line_spacing_percent']) == (2, 'JUSTIFY', 150)
    assert paragraphs[1]['text'] == LINES[2]
    # Pieces on one baseline stay on one line instead of stacking.
    label = next(block for block in paragraphs if block['text'].startswith('Label'))
    assert '\n' not in label['text'] and label['text'].split() == ['Label', 'Value']


def test_text_box_stays_editable_and_picture_row_shares_a_line(tmp_path):
    source, target = tmp_path / 'layout.pdf', tmp_path / 'layout.hwpx'
    layout_pdf(source)
    result = convert_document(source, target)
    assert (result['tables'], result['figures']) == (1, 3)
    with HwpxDocument.open(target) as document:
        box = list(document.tables)[0].cell(0, 0)
        assert [paragraph.text for paragraph in box.paragraphs] == ['Boxed title', '- boxed item']
        hosts = [paragraph for paragraph in document.sections[0].paragraphs
                 if paragraph.element.find('.//{http://www.hancom.co.kr/hwpml/2011/paragraph}pic') is not None]
        assert len(hosts) == 1
    with zipfile.ZipFile(target) as archive:
        manifest = archive.read('Contents/content.hpf').decode()
        section = archive.read('Contents/section0.xml').decode()
    # Every picture is registered even though a section was added later.
    assert len(re.findall(r'href="BinData/', manifest)) == 3
    # The bitmap is described by its pixels (75 HWPUNIT each), not cropped.
    assert re.search(r'<hp:imgClip left="0" right="22500" top="0" bottom="7500"', section)
    assert '<hp:linesegarray' not in section


def test_font_names_letter_spacing_and_stroked_bold():
    assert font_name('ABCDEF+HCRBatang') == '함초롬바탕'
    assert font_name('HCRDotum-Bold') == '함초롬돋움'
    chars = [dict(c='가', bbox=(x, 0, x + 14, 14), origin=(x, 12)) for x in (0, 13.44, 26.88, 40.32)]
    assert letter_spacing([dict(chars=chars)], 14) == -4
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 100), 'Stroked', fontsize=14, render_mode=2, border_width=.1)
        page.insert_text((50, 140), 'Plain text', fontsize=14)
        blocks = native_page(page)['blocks']
    styles = {block['text']: block['runs'][0]['bold'] for block in blocks}
    assert styles == {'Stroked': True, 'Plain text': False}
