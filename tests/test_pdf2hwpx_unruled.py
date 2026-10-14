"""Tables drawn without cell borders are found from how the text is lined up."""
import pymupdf

from adf.pdf2hwpx import native_page

ROWS = [('Method', 'Mechanism', 'Paradigm'), ('PoT', 'Delegated', 'Hybrid comments'), ('PAL', 'Delegated', 'Program-aided'),
        ('SATLM', 'Formal', 'SAT solving'), ('ReProver', 'Formal', 'Lean search'), ('NExT', 'Iterative', 'Traces')]
COLUMNS = (72, 170, 270)


def _table_text(page, top=140, step=14, rows=ROWS, columns=COLUMNS, size=10):
    for index, row in enumerate(rows):
        for x, text in zip(columns, row):
            page.insert_text((x, top + index * step), text, fontsize=size)


def _tables(page):
    return [block for block in native_page(page)['blocks'] if block['kind'] == 'table']


def _found(page):
    """Tables the text-alignment reader made (not the column bands of other readers)."""
    return [block for block in _tables(page) if block.get('detected')]


def test_table_with_only_horizontal_rules_is_found_with_its_columns_and_rules():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        _table_text(page)
        for y in (128, 143, 212):                  # top, under the header, bottom
            page.draw_line((60, y), (380, y), width=.8)
        tables = _tables(page)
    assert len(tables) == 1 and (tables[0]['rows'], tables[0]['cols']) == (6, 3)
    assert tables[0]['detected'] == 'rules'
    first = {cell['col']: cell for cell in tables[0]['cells'] if cell['row'] == 0}
    assert [first[c]['text'] for c in range(3)] == ['Method', 'Mechanism', 'Paradigm']
    top = {border['side']: border['type'] for border in first[0]['borders']}
    bottom = {border['side']: border['type'] for border in first[0]['borders']}
    assert top['top'] == 'SOLID' and bottom['bottom'] == 'SOLID' and top['left'] == 'NONE'


def test_aligned_text_without_any_rule_is_a_table_when_rows_and_columns_are_enough():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        _table_text(page)
        tables = _tables(page)
    assert len(tables) == 1 and tables[0]['detected'] == 'aligned' and (tables[0]['rows'], tables[0]['cols']) == (6, 3)


def test_a_two_column_table_needs_its_caption():
    rows = [('Year', 'Sales'), ('2021', '10'), ('2022', '12'), ('2023', '15')]
    with pymupdf.open() as pdf:
        plain = pdf.new_page()
        _table_text(plain, rows=rows, columns=(72, 200))
        assert _found(plain) == []
    with pymupdf.open() as pdf:
        captioned = pdf.new_page()
        captioned.insert_text((72, 120), 'Table 1: Sales by year', fontsize=10)
        _table_text(captioned, rows=rows, columns=(72, 200))
        found = _found(captioned)
    assert len(found) == 1 and found[0]['detected'] == 'caption' and found[0]['caption']['side'] == 'TOP'


def test_running_text_in_two_columns_is_not_a_table():
    sentence = 'The harness must map abstract language outputs into executable behaviors for the agent'
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for row in range(12):
            page.insert_text((60, 100 + row * 13), sentence[:44] + f' {row}', fontsize=9)
            page.insert_text((320, 100 + row * 13), sentence[44:] + ' and the rest of it', fontsize=9)
        assert _found(page) == []


def test_contents_lines_with_dot_leaders_are_not_a_table():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for row in range(6):
            page.insert_text((72, 100 + row * 16), f'{row + 1}. Chapter title {"." * 50}', fontsize=10)
            page.insert_text((480, 100 + row * 16), str(row + 3), fontsize=10)
        assert _found(page) == []


def test_a_bulleted_list_is_not_a_table():
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for row in range(6):
            page.insert_text((72, 100 + row * 16), '-', fontsize=10)
            page.insert_text((90, 100 + row * 16), f'An item of the list number {row}', fontsize=10)
        assert _found(page) == []


def test_a_wrapped_hyphenated_cell_stays_in_its_row():
    rows = [('Method', 'Kind', 'Idea'), ('Goedel-Code-', 'Formal', 'Searches proofs'), ('', '', ''), ('Lean4Agent', 'Formal', 'Models agents'),
            ('Chain of Code', 'Hybrid', 'Simulates code'), ('SATLM', 'Hybrid', 'Uses solvers')]
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        for index, row in enumerate(rows):
            for x, text in zip(COLUMNS, row):
                if text:
                    page.insert_text((x, 140 + index * 14), text, fontsize=10)
        page.insert_text((72, 140 + 2 * 14), 'Prover [94]', fontsize=10)    # the cell's second line
        tables = _tables(page)
    assert len(tables) == 1 and tables[0]['rows'] == 5
    assert any('Goedel-Code-' in cell['text'] and 'Prover' in cell['text'] for cell in tables[0]['cells'])
