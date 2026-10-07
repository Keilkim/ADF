"""Measure PDF styling, then inspect the saved HWPX character and cell styles."""
from copy import deepcopy
import zipfile

from lxml import etree
import pymupdf
import pytest
from hwpx import HwpxDocument

from adf.hwpx_api import ConversionError, validate_page
from adf.hwpx_export import convert_document, native_page, write_page, verify_package
from adf.hwpx_formatting import PdfFormatting, reuse_native_formatting

HH = '{http://www.hancom.co.kr/hwpml/2011/head}'


def styled_pdf(path):
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 60), 'Large bold ', fontsize=20, fontname='hebo', color=(1, 0, 0))
        x = 50 + pymupdf.get_text_length('Large bold ', fontname='hebo', fontsize=20)
        page.insert_text((x, 60), 'small italic', fontsize=12, fontname='heit', color=(0, 0, 1))
        page.draw_rect((50, 100, 130, 140), color=None, fill=(.95, .9, .85))
        page.draw_rect((50, 100, 350, 210), width=2, color=(1, 0, 0))
        page.draw_line((130, 100), (130, 210), width=.5, color=(0, 1, 0))
        page.draw_line((50, 140), (350, 140), width=1, color=(0, 0, 1), dashes='[4 2] 0')
        page.insert_text((56, 128), 'Big', fontsize=18, fontname='hebo', color=(1, 0, 0))
        page.insert_text((140, 125), 'Small', fontsize=8, fontname='heit', color=(0, 0, 1))
        page.insert_text((56, 173), 'Body', fontsize=14)
        page.insert_text((140, 175), 'Other', fontsize=10)
        doc.save(path)


def header_styles(path):
    with zipfile.ZipFile(path) as archive:
        header = etree.fromstring(archive.read('Contents/header.xml'))
    return {element.get('id'): element for element in header.iter(HH + 'charPr')}, {
        element.get('id'): element for element in header.iter(HH + 'borderFill')}


def test_saved_table_retains_distinct_borders_fill_sizes_and_cell_text(tmp_path):
    source, target = tmp_path / 'styles.pdf', tmp_path / 'styles.hwpx'
    styled_pdf(source)
    original = source.read_bytes()
    result = convert_document(source, target)
    assert result['tables'] == 1
    assert source.read_bytes() == original
    char_styles, border_styles = header_styles(target)
    with HwpxDocument.open(target) as doc:
        assert doc.validate().ok
        table = list(doc.tables)[0]
        assert (table.cell(0, 0).width, table.cell(0, 1).width) == (8000, 22000)
        assert (table.cell(0, 0).height, table.cell(1, 0).height) == (4000, 7000)
        assert table.cell(0, 0).text == 'Big'
        assert table.cell(0, 1).text == 'Small'
        large = char_styles[table.cell(0, 0).paragraphs[0].runs[0].char_pr_id_ref]
        small = char_styles[table.cell(0, 1).paragraphs[0].runs[0].char_pr_id_ref]
        assert large.get('height') == '1800'
        assert small.get('height') == '800'
        assert large.find(HH + 'bold') is not None
        assert small.find(HH + 'italic') is not None
        assert large.get('textColor') == '#FF0000'
        assert small.get('textColor') == '#0000FF'
        fill = border_styles[table.cell(0, 0).element.get('borderFillIDRef')]
        top, right, bottom = [fill.find(HH + side + 'Border') for side in ('top', 'right', 'bottom')]
        assert (top.get('width'), top.get('color'), top.get('type')) == ('0.7 mm', '#FF0000', 'SOLID')
        assert (right.get('width'), right.get('color')) == ('0.2 mm', '#00FF00')
        assert (bottom.get('type'), bottom.get('color')) == ('DASH', '#0000FF')
        assert next(fill.iter('{http://www.hancom.co.kr/hwpml/2011/core}winBrush')).get('faceColor') == '#F2E5D9'


def test_mixed_paragraph_preserves_inline_sizes_fonts_and_colors(tmp_path):
    source, target = tmp_path / 'styles.pdf', tmp_path / 'styles.hwpx'
    styled_pdf(source)
    convert_document(source, target)
    styles, _ = header_styles(target)
    with HwpxDocument.open(target) as doc:
        paragraph = doc.sections[0].paragraphs[0]
        assert paragraph.text == 'Large bold small italic'
        # The first paragraph also holds the non-text section properties run.
        runs = [run for run in paragraph.runs if run.text]
        assert [styles[run.char_pr_id_ref].get('height') for run in runs] == ['2000', '1200']
        assert [doc.styles.font_face(run.char_pr_id_ref) for run in runs] == ['Arial', 'Arial']


def test_ai_keeps_measured_native_styles_and_custom_transcription(tmp_path):
    source = tmp_path / 'styles.pdf'
    styled_pdf(source)
    with pymupdf.open(source) as pdf:
        native = native_page(pdf[0])
    extracted = deepcopy(native)
    table = next(block for block in extracted['blocks'] if block['kind'] == 'table')
    for cell in table['cells']:
        for key in ('font_size_pt', 'borders', 'width_pt', 'height_pt', 'fill_color', 'runs'):
            cell.pop(key, None)
    table['cells'][0]['text'] = 'AI corrected text'
    reuse_native_formatting(native, extracted)
    assert table['cells'][0]['text'] == 'AI corrected text'
    assert 'runs' not in table['cells'][0]
    assert table['cells'][0]['font_size_pt'] == 18
    assert len(table['cells'][0]['borders']) == 4
    validate_page(extracted)


def test_invisible_cell_edges_remain_invisible():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 100), 'Text')
        cell = PdfFormatting(page).cell((40, 80, 150, 130), 'Text')
        assert {border['type'] for border in cell['borders']} == {'NONE'}


def test_ai_only_cell_styles_write_valid_double_and_dotted_borders(tmp_path):
    target = tmp_path / 'ai-styles.hwpx'
    cell = dict(row=0, col=0, row_span=1, col_span=1, text='Scanned text',
                font='Arial', font_size_pt=22, bold=True, color='#223344',
                fill_color='#FFEEDD', borders=[
                    dict(side='top', type='DOUBLE_SLIM', width_pt=1.5, color='#112233'),
                    dict(side='bottom', type='DOT', width_pt=.5, color='#445566')])
    block = dict(kind='table', bbox=[100,100,500,200], text='', font_size_pt=10,
                 rows=1, cols=1, cells=[cell], column_widths_pt=[238], row_heights_pt=[84])
    with pymupdf.open() as pdf, HwpxDocument.new() as doc:
        write_page(doc, pdf.new_page(), validate_page(dict(blocks=[block], warnings=[])), 0)
        doc.save_to_path(target)
    verify_package(target)
    styles, borders = header_styles(target)
    with HwpxDocument.open(target) as doc:
        saved = list(doc.tables)[0].cell(0, 0)
        assert saved.text == 'Scanned text'
        assert styles[saved.paragraphs[0].runs[0].char_pr_id_ref].get('height') == '2200'
        fill = borders[saved.element.get('borderFillIDRef')]
        assert fill.find(HH + 'topBorder').get('type') == 'DOUBLE_SLIM'
        assert fill.find(HH + 'bottomBorder').get('type') == 'DOT'
        assert fill.find(HH + 'leftBorder').get('type') == 'NONE'


@pytest.mark.parametrize('field,value', [('font_size_pt', float('nan')), ('color', 'red'),
    ('color', None), ('font', {}), ('bold', 1), ('borders', [{'side':'top','type':'DASH','width_pt':float('inf'),'color':'#000000'}])])
def test_rejects_invalid_cell_styles(field, value):
    cell = dict(row=0, col=0, row_span=1, col_span=1, text='Text')
    cell[field] = value
    block = dict(kind='table', bbox=[0,0,100,100], text='', font_size_pt=10, rows=1, cols=1, cells=[cell])
    with pytest.raises(ConversionError):
        validate_page(dict(blocks=[block], warnings=[]))
