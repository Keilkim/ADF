"""Page structures found in real documents: columns, footers, open and merged
tables, pictures in cells, backgrounds, vertical text and designed pages."""
import io
import zipfile

import pymupdf
from PIL import Image

from adf.pdf2hwpx import convert_document, native_page
from adf.pdf2hwpx.pdf_reader import font_name

TEXT = ('The committee reviewed the proposal in detail and agreed that the pilot should start in two districts. '
        'Budget figures were revised after the second hearing, and the plan keeps the schedule. ')


def png(width=60, height=40, colour=(200, 80, 40)):
    data = io.BytesIO()
    Image.new('RGB', (width, height), colour).save(data, format='PNG')
    return data.getvalue()


def section_xml(path, index=0):
    with zipfile.ZipFile(path) as archive:
        return archive.read(f'Contents/section{index}.xml').decode()


def test_newspaper_columns_become_one_borderless_table():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 60), 'Report title across both columns', fontsize=16)
        for column in (0, 1):
            page.insert_textbox(pymupdf.Rect(72 + column * 235, 80, 287 + column * 235, 400), TEXT * 3,
                                fontsize=10, fontname='tiro')
        blocks = native_page(page)['blocks']
    tables = [block for block in blocks if block['kind'] == 'table']
    assert len(tables) == 1 and tables[0]['cols'] == 2
    left, right = tables[0]['cells']
    assert left['text'].startswith('The committee') and right['text'].startswith('The committee')
    assert {border['type'] for cell in tables[0]['cells'] for border in cell['borders']} == {'NONE'}
    assert any(block['kind'] == 'paragraph' and 'Report title' in block['text'] for block in blocks)


def test_page_number_goes_to_the_footer(tmp_path):
    source, target = tmp_path / 'footer.pdf', tmp_path / 'footer.hwpx'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 523, 300), TEXT * 3, fontsize=11)
        page.insert_text((290, 800), '- 3 -', fontsize=10)
        pdf.save(source)
    convert_document(source, target)
    xml = section_xml(target)
    footer = xml[xml.index('<hp:footer'):xml.index('</hp:footer>')]
    assert '- 3 -' in footer


def test_open_sided_ruled_table_and_unruled_merged_cell():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        # Rows ruled across, no outer side borders; one inner column line
        # that stops below the first row, whose cell spans both columns.
        for y in (100, 130, 160, 190):
            page.draw_line((60, y), (400, y), width=.6)
        page.draw_line((150, 130), (150, 190), width=.6)
        page.insert_text((70, 120), 'A heading cell that spans the row', fontsize=10)
        page.insert_text((70, 150), 'Name', fontsize=10)
        page.insert_text((160, 150), 'Value one', fontsize=10)
        page.insert_text((70, 180), 'Other', fontsize=10)
        page.insert_text((160, 180), 'Value two', fontsize=10)
        table = next(block for block in native_page(page)['blocks'] if block['kind'] == 'table')
    assert (table['rows'], table['cols']) == (3, 2)
    first = next(cell for cell in table['cells'] if cell['row'] == 0)
    assert first['col_span'] == 2 and first['text'].startswith('A heading')
    sides = {border['side']: border['type'] for border in first['borders']}
    assert sides['left'] == 'NONE' and sides['top'] == 'SOLID'


def test_photo_grid_pictures_stay_in_their_cells_and_backgrounds_go_behind(tmp_path):
    source, target = tmp_path / 'photos.pdf', tmp_path / 'photos.hwpx'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_image(page.rect, stream=png(120, 170, (235, 240, 250)))  # page template
        for x in (60, 220):
            page.draw_rect((x, 100, x + 160, 220), color=(0, 0, 0), width=.5)
            page.draw_rect((x, 220, x + 160, 240), color=(0, 0, 0), fill=(.3, .3, .3), width=.5)
            page.insert_image(pymupdf.Rect(x + 5, 105, x + 155, 215), stream=png())
            page.insert_text((x + 6, 234), 'Caption', fontsize=9, color=(1, 1, 1))
        page.insert_text((60, 80), 'Photos of the district', fontsize=12)
        pdf.save(source)
    with pymupdf.open(source) as pdf:
        blocks = native_page(pdf[0])['blocks']
    table = next(block for block in blocks if block['kind'] == 'table')
    assert sum(len(cell.get('images', [])) for cell in table['cells']) == 2
    images = [block for block in blocks if block['kind'] == 'image']
    assert [block.get('placement') for block in images] == ['BEHIND_TEXT']
    convert_document(source, target)
    xml = section_xml(target)
    assert 'textWrap="BEHIND_TEXT"' in xml and xml.count('<hp:pic') == 3


def test_stacked_characters_become_vertical_cell_text():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.draw_rect((60, 100, 90, 260), width=.6)
        page.draw_rect((90, 100, 300, 260), width=.6)
        for index, char in enumerate('VERTICAL'):
            page.insert_text((70, 120 + index * 18), char, fontsize=10)
        page.insert_text((100, 180), 'Body cell', fontsize=10)
        table = next(block for block in native_page(page)['blocks'] if block['kind'] == 'table')
    cell = next(cell for cell in table['cells'] if cell['col'] == 0)
    assert cell['text_direction'] == 'VERTICAL' and cell['text'] == 'VERTICAL'


def test_designed_page_keeps_artwork_behind_editable_text_boxes(tmp_path):
    source, target = tmp_path / 'design.pdf', tmp_path / 'design.hwpx'
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=842, height=595)
        page.draw_circle((600, 300), 150, color=None, fill=(.3, .8, .7))
        for y in range(40, 560, 40):
            # Text beside a large picture cannot flow: it must stay in place.
            page.insert_text((40, y), f'Item {y} of the left column', fontsize=12)
        page.insert_image(pymupdf.Rect(300, 40, 800, 540), stream=png(200, 200))
        pdf.save(source)
    result = convert_document(source, target)
    assert result['pages'] == 1
    xml = section_xml(target)
    assert xml.count('<hp:drawText') >= 13 and 'Item 520 of the left column' in xml
    assert 'textWrap="BEHIND_TEXT"' in xml


def test_hancom_font_names_and_shadow_text():
    assert font_name('ÈÞ¸Õ¸íÁ¶', serif=True) in ('휴먼명조', '바탕')
    assert font_name('H2hdrM') == 'HY헤드라인M'
    assert font_name('ABCDEF+MalgunGothicBold') == '맑은 고딕'
    assert font_name('T5', serif=True) == '바탕'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((101, 101), 'Shadowed title', fontsize=20, color=(.7, .7, .7))
        page.insert_text((100, 100), 'Shadowed title', fontsize=20)
        texts = [block['text'] for block in native_page(page)['blocks'] if block['kind'] == 'paragraph']
    assert texts == ['Shadowed title']


def test_cells_of_a_misread_grid_are_kept_inside_the_table():
    from adf.pdf2hwpx.schema import sanitize_cells
    cells = [dict(row=0, col=0, row_span=3, col_span=2), dict(row=0, col=2, row_span=1, col_span=1),
             dict(row=0, col=3, row_span=3, col_span=2), dict(row=1, col=1, row_span=1, col_span=1),
             dict(row=5, col=0, row_span=1, col_span=1)]
    kept = sanitize_cells(cells, 3, 4, fill=True)
    slots = [(r, c) for cell in kept for r in range(cell['row'], cell['row'] + cell['row_span'])
             for c in range(cell['col'], cell['col'] + cell['col_span'])]
    assert len(slots) == len(set(slots)) and all(0 <= r < 3 and 0 <= c < 4 for r, c in slots)
    assert (5, 0) not in {(cell['row'], cell['col']) for cell in kept}


def test_an_unreadable_page_becomes_a_picture_and_the_document_still_converts(tmp_path, monkeypatch):
    import pymupdf
    import adf.pdf2hwpx.convert as convert
    source = tmp_path / 'two.pdf'
    with pymupdf.open() as pdf:
        for text in ('First page', 'Second page'):
            pdf.new_page().insert_text((72, 100), text, fontsize=12)
        pdf.save(source)
    real = convert.native_page
    monkeypatch.setattr(convert, 'native_page', lambda page: real(page) if page.number == 0 else 1 / 0)
    result = convert.convert_document(str(source), str(tmp_path / 'two.hwpx'))
    assert result['pages'] == 2 and any('쪽 그림' in warning['message'] for warning in result['warnings'])
