"""Combine an AI transcription with what the PDF itself measures."""
from collections import Counter

import pymupdf


def reuse_native_formatting(native, extracted):
    """AI transcription keeps measured PDF styles when text/grid matches."""
    for block in extracted['blocks']:
        candidates = [source for source in native['blocks'] if source['kind'] == block['kind']
                      and (pymupdf.Rect(source['bbox']) & pymupdf.Rect(block['bbox'])).get_area()
                      > .5 * min(pymupdf.Rect(source['bbox']).get_area(), pymupdf.Rect(block['bbox']).get_area())]
        if len(candidates) != 1:
            continue
        source = candidates[0]
        if block['kind'] == 'paragraph' and source['text'].strip() == block['text'].strip():
            block.update({key: value for key, value in source.items() if key not in ('kind', 'bbox')})
        elif block['kind'] == 'table' and (source['rows'], source['cols']) == (block['rows'], block['cols']):
            measured = {(cell['row'], cell['col']): cell for cell in source['cells']}
            for cell in block['cells']:
                original = measured.get((cell['row'], cell['col']))
                if original and (original['row_span'], original['col_span']) == (cell['row_span'], cell['col_span']):
                    cell.update({key: value for key, value in original.items() if key not in
                                 ('row', 'col', 'row_span', 'col_span', 'text', 'runs', 'paragraphs')})
                    if original['text'].strip() == cell['text'].strip():
                        cell['runs'] = original['runs']
                        cell['text'] = original['text']
                        if 'paragraphs' in original:
                            cell['paragraphs'] = original['paragraphs']
            for key in ('column_widths_pt', 'row_heights_pt'):
                if key in source:
                    block[key] = source[key]
    return extracted


def _coverage(native, extracted):
    def content(page):
        return ''.join(b['text'] if b['kind'] == 'paragraph' else
                       ''.join(c['text'] for c in b.get('cells', [])) for b in page['blocks'])
    original = Counter(c for c in content(native) if c.isalnum())
    actual = Counter(c for c in content(extracted) if c.isalnum())
    count = sum(original.values())
    if count and sum((original - actual).values()) / count > .02:
        extracted['warnings'].append('PDF 텍스트와 비교해 일부 글자의 누락 또는 변경 가능성이 있습니다. 원본과 대조하세요.')
