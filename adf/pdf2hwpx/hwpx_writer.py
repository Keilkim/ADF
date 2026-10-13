"""Write measured page blocks into an HWPX document that Hancom lays out like the PDF."""
from copy import deepcopy
import json

import pymupdf

from .pdf_reader import clean_text, fit_line
from .schema import sanitize_cells


HP = '{http://www.hancom.co.kr/hwpml/2011/paragraph}'
FULL_WIDTH_SPACE = '󰀀'  # stands in for U+3000 while python-hwpx writes a run


HH = '{http://www.hancom.co.kr/hwpml/2011/head}'


LINE_WIDTHS = (.1, .12, .15, .2, .25, .3, .4, .5, .6, .7, 1., 1.5, 2., 3., 4., 5.)


def add_runs(doc, paragraph, block, spacing_before_pt=0):
    paragraph.clear_text()
    runs = block.get('runs') or [block]
    for run in runs:
        style = doc.styles.ensure_run(font=run.get('font', block.get('font', '맑은 고딕')),
            size=run.get('font_size_pt', block['font_size_pt']), bold=run.get('bold', block.get('bold', False)),
            italic=run.get('italic', block.get('italic', False)), color=run.get('color', block.get('color', '#000000')),
            underline=bool(run.get('underline')), underline_color=run.get('underline') or None,
            # Spacing belongs to each run; None would let python-hwpx reuse
            # a style with any spacing.
            letter_spacing=run.get('letter_spacing') or 0)
        if run.get('baseline_shift'):
            def lower(shape, value=str(run['baseline_shift'])):
                offset = shape.find(HH + 'offset')
                if offset is not None:
                    for key in list(offset.attrib):
                        offset.set(key, value)
            style = style_variant(doc, 'charPr', style, lower)
        if run.get('highlight'):
            # Shading behind these letters only (a highlighter).
            style = style_variant(doc, 'charPr', style, lambda shape, colour=run['highlight']: shape.set('shadeColor', colour))
        if run.get('font_space'):
            # Use the font's own space width instead of Hancom's half em.
            style = style_variant(doc, 'charPr', style, lambda shape: shape.set('useFontSpace', '1'))
        # python-hwpx drops tab characters; they go in as hp:tab elements.
        # It also writes U+3000 as hp:fwSpace, which Hancom draws a quarter
        # em wide (measured); the character itself is a full em, as the
        # PDF's room for it.
        text = clean_text(run['text']).replace('　', FULL_WIDTH_SPACE)
        first, *rest = text.split('	')
        added = paragraph.add_run(first, char_pr_id_ref=style, expand_special_characters=True)
        if rest:
            _append_tabs(added.element, rest)
        if FULL_WIDTH_SPACE in text:
            for element in added.element.iter():
                for attribute in ('text', 'tail'):
                    value = getattr(element, attribute)
                    if value and FULL_WIDTH_SPACE in value:
                        setattr(element, attribute, value.replace(FULL_WIDTH_SPACE, '　'))
    paragraph_format(doc, paragraph, block, spacing_before_pt)
    if block.get('tab_stop_pt'):
        _right_tab(doc, paragraph, block)


def _append_tabs(run, parts):
    """A tab and then each text part, appended to the run's last hp:t."""
    texts = list(run.iter(HP + 't'))
    text = texts[-1] if texts else run.makeelement(HP + 't', {})
    if not texts:
        run.append(text)
    for part in parts:
        tab = text.makeelement(HP + 'tab', {})
        tab.tail = part
        text.append(tab)


def _right_tab(doc, paragraph, block):
    """A right tab stop with a dot leader at the line's end (a contents entry)."""
    header = doc.oxml.headers[0]
    # Hancom measures tab stops from the column (or cell) edge, not from
    # the paragraph's indent.
    start = block.get('indent_left_pt', 0) + max(0, block.get('first_line_indent_pt', 0))
    position = round((start + block['tab_stop_pt']) * 100)
    stops = [dict(pos=position, type='RIGHT', leader=block.get('tab_leader', 'CIRCLE'))]
    tab_id = header.ensure_tab_definition(tab_stops=stops)
    base = paragraph.element.get('paraPrIDRef')
    paragraph.element.set('paraPrIDRef', style_variant(doc, 'paraPr', base, lambda shape: shape.set('tabPrIDRef', str(tab_id))))


def _structure(element, skip_id=False):
    attributes = sorted((k, v) for k, v in element.attrib.items() if not (skip_id and k == 'id'))
    return element.tag, attributes, (element.text or '').strip(), [_structure(child) for child in element]


def style_variant(doc, kind, base_id, change):
    """Id of a copy of a header style (charPr/paraPr) changed by change().

    python-hwpx cannot set every OWPML attribute; this copies the style it
    made, applies the change and reuses an identical existing style.
    """
    header = doc.oxml.headers[0]
    container = header.element.find('.//' + HH + ('charProperties' if kind == 'charPr' else 'paraProperties'))
    base = next((style for style in container if style.get('id') == str(base_id)), None)
    if base is None:
        return base_id
    style = deepcopy(base)
    change(style)
    style.attrib.pop('id')
    wanted = _structure(style)
    for existing in container:
        if _structure(existing, skip_id=True) == wanted:
            return existing.get('id')
    identity = str(max(int(item.get('id', '0')) for item in container) + 1)
    style.set('id', identity)
    container.append(style)
    container.set('itemCnt', str(len(container)))
    header.mark_dirty()
    return identity


def line_breaking_shape(doc, base_id, inside_words, condense=5):
    """A copy of a paragraph shape that breaks lines the way the source did.

    The PDF shows whether a paragraph's Korean text wrapped between the
    syllables of a word (per-character breaking) or only between words.
    OWPML names per-character breaking KEEP_WORD and word breaking
    BREAK_WORD. A small space condensing lets a justified line keep its
    last word, as the PDF lines do. Formatting derived from this shape
    keeps both settings.
    """
    def change(shape):
        shape.set('condense', str(condense))
        breaking = shape.find(HH + 'breakSetting')
        if breaking is not None:
            breaking.set('breakNonLatinWord', 'KEEP_WORD' if inside_words else 'BREAK_WORD')
    return style_variant(doc, 'paraPr', base_id, change)


def paragraph_format(doc, paragraph, block, spacing_before_pt=0):
    paragraph.para_pr_id_ref = line_breaking_shape(doc, paragraph.para_pr_id_ref, block.get('wraps_inside_words', False))
    # Rounded values keep the number of distinct paragraph shapes small.
    millimeters = lambda points: round(points * 25.4 / 72, 1)
    doc.styles.apply_paragraph_format(paragraphs=[paragraph], alignment=block.get('alignment', 'LEFT'),
        line_spacing_percent=block.get('line_spacing_percent', 160),
        indent_left_mm=millimeters(block.get('indent_left_pt', 0)),
        indent_right_mm=millimeters(block.get('indent_right_pt', 0)),
        first_line_indent_mm=millimeters(block.get('first_line_indent_pt', 0)),
        spacing_before_pt=round(spacing_before_pt * 2) / 2, spacing_after_pt=0)


def vertical_spacing(items, start):
    """Space before each item so it starts where the PDF placed it.

    Hancom adds a line's spacing below every line, so a paragraph ends
    line_count pitches below its top. Items are (top_pt, height_pt).
    """
    cursor, result = start, []
    for top, height in items:
        result.append(max(0., top - cursor))
        cursor = max(cursor, top) + height
    return result


def paragraph_height(block):
    size = block.get('line_size_pt', block['font_size_pt'])
    pitch = size * block.get('line_spacing_percent', 160) / 100
    return block.get('line_count', 1) * pitch


def cell_border_fill(doc, cell, cache):
    """Create one independent borderFill for a cell's four distinct edges."""
    key = json.dumps([cell.get('borders'), cell.get('fill_color')], sort_keys=True)
    if key in cache:
        return cache[key]
    template_id = doc.styles.ensure_border_fill(active_borders=[], fill_color=cell.get('fill_color'))
    header = doc.oxml.headers[0]
    container = header.element.find('.//' + HH + 'borderFills')
    template = next(element for element in container if element.get('id') == template_id)
    element = deepcopy(template)
    identity = str(max(int(item.get('id', '0')) for item in container) + 1)
    element.set('id', identity)
    borders = {border['side']: border for border in cell.get('borders', [])}
    for side in ('left', 'right', 'top', 'bottom'):
        border = borders.get(side, dict(type='NONE', width_pt=0, color='#000000'))
        if border.get('hairline'):
            border = dict(border, type='SOLID', color=border['hairline'])
        edge = element.find(HH + side + 'Border')
        mm = float(border['width_pt']) * 25.4 / 72
        nearest = min(LINE_WIDTHS, key=lambda value: abs(value - mm))
        width = f'{nearest:.1f}' if nearest in (1., 2., 3., 4., 5.) else str(nearest)
        edge.set('type', border['type'])
        edge.set('width', width + ' mm')
        edge.set('color', border['color'])
    container.append(element)
    container.set('itemCnt', str(len(container)))
    header.mark_dirty()
    cache[key] = identity
    return identity


def apply_cell_format(doc, table, cell, block, cache, page=None):
    r, c = cell['row'], cell['col']
    target = table.cell(r, c)
    style = {**block, **cell}
    target.set_text('')
    paragraphs = cell.get('paragraphs') or [style]
    spacing = vertical_spacing([(paragraph.get('top_pt', 0), paragraph_height({**style, **paragraph}))
                                for paragraph in paragraphs], paragraphs[0].get('top_pt', 0))
    for index, (paragraph, before) in enumerate(zip(paragraphs, spacing)):
        add_runs(doc, target.paragraphs[0] if index == 0 else target.add_paragraph(), {**style, **paragraph}, before)
    # A picture inside a cell (a photo grid with captions) stays in it.
    for image in cell.get('images', []) if page is not None else []:
        clip = pymupdf.Rect(image['box_pt']) & page.rect
        if clip.is_empty:
            continue
        host = target.paragraphs[0] if not cell.get('text', '').strip() else target.add_paragraph()
        paragraph_format(doc, host, dict(alignment='CENTER', line_spacing_percent=100))
        _add_picture(doc, page, host, image, clip, max(100, round(clip.width * 100)), max(100, round(clip.height * 100)))
    if 'width_pt' in cell and 'height_pt' in cell:
        target.set_size(round(cell['width_pt'] * 100), round(cell['height_pt'] * 100))
    margins = cell.get('margins_pt')
    widths = block.get('column_widths_pt') or []
    width = sum(widths[c:c + cell.get('col_span', 1)]) if len(widths) > c else None
    if not margins and width is not None and width < 12:
        # Hancom's default padding (5.1pt a side) would push a narrow
        # spacer cell wider and move the whole table down.
        margins = dict(left=0, right=0, top=0, bottom=0)
    if margins:
        target.set_margins(**{key: round(value * 100) for key, value in margins.items()})
    if 'borders' in cell or 'fill_color' in cell:
        table.set_cell_border_fill(r, c, cell_border_fill(doc, cell, cache))
    sublist = target.element.find(HP + 'subList')
    if sublist is not None and cell.get('vertical_alignment'):
        sublist.set('vertAlign', cell['vertical_alignment'])
    if sublist is not None and cell.get('text_direction') == 'VERTICAL':
        sublist.set('textDirection', 'VERTICAL')


def _sync_manifest(document):
    """Keep every embedded image listed after sections were added.

    python-hwpx records images in the package manifest but writes its own
    copy when a section was added, which loses the images added since
    then. Hancom shows a missing-picture placeholder for such an image.
    """
    namespace = '{http://www.idpf.org/2007/opf/}manifest'
    target = document.oxml.manifest.find(namespace)
    source = document.package.manifest_tree().find(namespace)
    if target is None or source is None or target is source:
        return
    known = {item.get('id') for item in target}
    for item in source:
        if item.get('id') not in known:
            target.append(deepcopy(item))


def _picture_source_size(element, width_px, height_px):
    """Describe the embedded bitmap by its pixels, as Hancom does.

    python-hwpx records the display size as the image's own size. Hancom
    reads that as a crop of a bitmap measured at 96 dpi (75 HWPUNIT per
    pixel), so a higher-resolution picture showed only its top-left part.
    """
    width, height = width_px * 75, height_px * 75
    for child in element.iter():
        name = child.tag.rsplit('}', 1)[-1]
        if name == 'imgClip':
            child.set('left', '0'); child.set('top', '0')
            child.set('right', str(width)); child.set('bottom', str(height))
        elif name == 'imgDim':
            child.set('dimwidth', str(width)); child.set('dimheight', str(height))


def _bitmap(page, block, clip):
    """The embedded bitmap of an upright image, else a rendering of clip.

    Rendering the page region would also bake any text drawn over the
    image into the picture.
    """
    if block.get('xref'):
        try:
            pixmap = pymupdf.Pixmap(page.parent, block['xref'])
            if pixmap.colorspace is None or pixmap.colorspace.n != 3:
                pixmap = pymupdf.Pixmap(pymupdf.csRGB, pixmap)
            if pixmap.alpha:
                pixmap = pymupdf.Pixmap(pixmap, 0)
            limit = max(1, round(clip.width / 72 * 300))
            if pixmap.width > limit * 1.5:
                pixmap = pymupdf.Pixmap(pixmap, limit, max(1, round(pixmap.height * limit / pixmap.width)), None)
            return pixmap, pixmap.tobytes('jpg', jpg_quality=90), 'jpg'
        except Exception:
            pass
    for dpi in (180, 96, 48):
        try:
            pixmap = page.get_pixmap(clip=clip, dpi=dpi, alpha=False)
            return pixmap, pixmap.tobytes('png'), 'png'
        except Exception:  # an image too large for MuPDF to decode at this size
            continue
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1, 1), 0)
    pixmap.clear_with(255)
    return pixmap, pixmap.tobytes('png'), 'png'


_MEDIA = {}


def _media(doc, data, kind):
    """The document's id for an image, storing identical bytes once: a page
    of repeated marks (thousands of small icons) would otherwise exceed the
    number of parts an HWPX package may hold."""
    import hashlib
    key = (id(doc), hashlib.sha1(data).hexdigest())
    if key not in _MEDIA:
        if len(_MEDIA) > 20000:
            _MEDIA.clear()
        _MEDIA[key] = str(doc.media.add_image(data, kind))
    return _MEDIA[key]


def _add_picture(doc, page, host, block, clip, width, height, floating=False):
    pixmap, data, kind = _bitmap(page, block, clip)
    image_id = _media(doc, data, kind)
    if floating:
        picture = host.add_picture(image_id, width=width, height=height, treat_as_char=False, text_wrap='BEHIND_TEXT',
                                   pos_overrides=dict(horzRelTo='PAPER', vertRelTo='PAPER', horzAlign='LEFT',
                                                      vertAlign='TOP', horzOffset=round(clip.x0 * 100),
                                                      vertOffset=round(clip.y0 * 100)))
    else:
        picture = host.add_picture(image_id, width=width, height=height)
    _picture_source_size(picture.element, pixmap.width, pixmap.height)
    return picture


def _float(element, rect, wrap='IN_FRONT_OF_TEXT'):
    """Pin a drawing object (picture, text box, table) to a place on the paper."""
    element.set('textWrap', wrap)
    position = next((child for child in element if child.tag.endswith('}pos')), None)
    if position is not None:
        for key, value in dict(treatAsChar='0', flowWithText='0', allowOverlap='1', vertRelTo='PAPER',
                               horzRelTo='PAPER', vertAlign='TOP', horzAlign='LEFT',
                               vertOffset=str(round(rect.y0 * 100)), horzOffset=str(round(rect.x0 * 100))).items():
            position.set(key, value)


def _artwork(page):
    """The page with its text removed: drawings, pictures and fills only."""
    with pymupdf.open() as copy:
        copy.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
        sheet = copy[0]
        sheet.add_redact_annot(sheet.rect)
        sheet.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                               text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        pixmap = sheet.get_pixmap(dpi=150, alpha=False)
    return pixmap, pixmap.tobytes('jpg', jpg_quality=88), 'jpg'


def _text_box(doc, anchor, rect, paragraphs, style, vertical='TOP', margins=None):
    shape = anchor.add_rectangle(max(100, round(rect.width * 100)), max(100, round(rect.height * 100)),
                                 treat_as_char=False)
    line = next((child for child in shape.element.iter() if child.tag.endswith('}lineShape')), None)
    if line is not None:
        line.set('style', 'NONE')
    margins = {key: round(value * 100) for key, value in (margins or dict(left=0, right=0, top=0, bottom=0)).items()}
    text = shape.set_draw_text('', margin=margins, vert_align=vertical)
    # Paragraphs of one box (a contents page in a cell) keep their gaps.
    spacing = vertical_spacing([(paragraph.get('top_pt', 0), paragraph_height({**style, **paragraph}))
                                for paragraph in paragraphs], paragraphs[0].get('top_pt', 0))
    for index, (paragraph, before) in enumerate(zip(paragraphs, spacing)):
        add_runs(doc, text.paragraphs[0] if index == 0 else text.add_paragraph(), {**style, **paragraph}, before)
    _float(shape.element, rect)


def _free_page(doc, page, section, blocks, boxes, area):
    """A designed page: its artwork behind, every text where it was.

    Text that a flow cannot hold (beside pictures, over shapes) goes into
    borderless text boxes at its PDF position, still editable; drawings,
    pictures and table rules come from one picture of the page without
    its text. Boxes are a little wider than the PDF text so a slightly
    wider font does not wrap a line.
    """
    counts = dict(paragraphs=0, tables=0, figures=1)
    anchor = section.paragraphs[0]
    pixmap, data, kind = _artwork(page)
    picture = anchor.add_picture(_media(doc, data, kind), width=round(page.rect.width * 100),
                                 height=round(page.rect.height * 100), treat_as_char=False, text_wrap='BEHIND_TEXT',
                                 pos_overrides=dict(horzRelTo='PAPER', vertRelTo='PAPER', horzAlign='LEFT',
                                                    vertAlign='TOP', horzOffset=0, vertOffset=0))
    _picture_source_size(picture.element, pixmap.width, pixmap.height)
    for block, box in zip(blocks, boxes):
        if block['kind'] == 'paragraph' and block.get('parts'):
            # Pieces of one visual line (names in a grid, a label and its
            # value) each keep their place; spaces would round their gaps.
            for part in block['parts']:
                if not part['runs']:
                    continue
                piece = pymupdf.Rect(part['box_pt'])
                rect = pymupdf.Rect(piece.x0 - 2, box.y0, piece.x0 - 2 + piece.width * 1.04 + 4,
                                    box.y0 + max(box.height, paragraph_height(block)))
                layout = {key: value for key, value in block.items()
                          if key not in ('parts', 'left_pt', 'right_pt', 'tab_stop_pt', 'tab_leader', 'first_line_indent_pt')}
                layout.update(runs=[dict(run) for run in part['runs']], alignment='LEFT', indent_left_pt=2,
                              text=''.join(run['text'] for run in part['runs']))
                fit_line(layout, rect.width - 2)
                _text_box(doc, anchor, rect, [layout], {})
            counts['paragraphs'] += 1
            continue
        if block['kind'] == 'paragraph':
            # Justified lines stretch to the box edge: their box keeps the
            # PDF width (the break matching keeps their words on the line).
            justified = block.get('alignment') == 'JUSTIFY' and block.get('line_count', 1) > 1
            width = box.width + .5 if justified else box.width * 1.04 + 4
            rect = pymupdf.Rect(box.x0 - 2, box.y0, box.x0 - 2 + width, box.y0 + max(box.height, paragraph_height(block)))
            if block.get('alignment') == 'RIGHT':
                rect = pymupdf.Rect(box.x1 + 2 - width, rect.y0, box.x1 + 2, rect.y1)
            elif block.get('alignment') == 'CENTER':
                rect = pymupdf.Rect(box.x0 + (box.width - width) / 2, rect.y0, box.x1 - (box.width - width) / 2, rect.y1)
            layout = dict(block, runs=[dict(run) for run in block.get('runs', [])])
            if layout.get('alignment', 'LEFT') in ('LEFT', 'JUSTIFY'):
                layout['indent_left_pt'] = max(0, layout['left_pt'] - rect.x0 if 'left_pt' in layout else
                                               layout.get('indent_left_pt', 0) - (box.x0 - area.x0))
            if layout.get('line_count', 1) == 1 and layout['runs']:
                # A placed line must not wrap inside its box.
                fit_line(layout, rect.width - layout.get('indent_left_pt', 0))
            _text_box(doc, anchor, rect, [layout], {})
            counts['paragraphs'] += 1
        elif block['kind'] == 'table':
            widths, heights = block.get('column_widths_pt'), block.get('row_heights_pt')
            if not widths or not heights:
                continue
            xs = [box.x0]
            for value in widths:
                xs.append(xs[-1] + value)
            ys = [box.y0]
            for value in heights:
                ys.append(ys[-1] + value)
            for cell in block['cells']:
                if not cell.get('text', '').strip():
                    continue
                r, c = cell['row'], cell['col']
                rect = pymupdf.Rect(xs[c], ys[r], xs[min(c + cell['col_span'], len(xs) - 1)],
                                    ys[min(r + cell['row_span'], len(ys) - 1)])
                style = {**block, **cell}
                _text_box(doc, anchor, rect, cell.get('paragraphs') or [style], style,
                          cell.get('vertical_alignment', 'CENTER'), cell.get('margins_pt'))
            counts['tables'] += 1
    return counts


def _add_frame(anchor, rect, style):
    """A drawn page frame: a rectangle behind the text in the PDF's line style."""
    shape = anchor.add_rectangle(max(100, round(rect.width * 100)), max(100, round(rect.height * 100)),
                                 treat_as_char=False)
    line = next((child for child in shape.element.iter() if child.tag.endswith('}lineShape')), None)
    if style.get('hairline'):
        style = dict(style, type='SOLID', color=style['hairline'])
    if line is not None:
        line.set('style', {'NONE': 'SOLID'}.get(style.get('type'), style.get('type', 'SOLID')))
        line.set('color', style.get('color', '#000000'))
        line.set('width', str(max(10, round(style.get('width_pt', .3) * 100))))
    _float(shape.element, rect, 'BEHIND_TEXT')


def _page_footer(blocks, boxes, height):
    """Index of a page number or footer line at the bottom of the page."""
    if not blocks:
        return None
    index = max(range(len(blocks)), key=lambda i: boxes[i].y1)
    block, box = blocks[index], boxes[index]
    others = [other for i, other in enumerate(boxes) if i != index]
    if (block['kind'] == 'paragraph' and block.get('line_count', 1) == 1 and len(block['text'].strip()) <= 40
            and box.y0 > .88 * height and all(other.y1 < box.y0 - 2 for other in others)):
        return index
    return None


def _plan_flow(members, margins):
    """Items of one flow (a page or a column) with the space before each."""
    # Pictures side by side in the PDF (a row of logos) share one line.
    items = []
    for block, box in members:
        band = pymupdf.Rect(items[-1][0][1]) if items else None
        for _, other in items[-1] if items else []:
            band |= other
        if (block['kind'] == 'image' and items and all(member['kind'] == 'image' for member, _ in items[-1])
                and min(box.y1, band.y1) - max(box.y0, band.y0) > .5 * min(box.height, band.height)):
            items[-1].append((block, box))
        else:
            items.append([(block, box)])
    bands = []
    for item in items:
        band = pymupdf.Rect(item[0][1])
        for _, box in item[1:]:
            band |= box
        bands.append(band)
    heights = [paragraph_height(item[0][0]) if item[0][0]['kind'] == 'paragraph' else band.height
               for item, band in zip(items, bands)]
    for index, item in enumerate(items[:-1]):
        # A one-line paragraph has no measured spacing; the default would
        # push the next line (a two-line title) below its place.
        block = item[0][0]
        room = bands[index + 1].y0 - bands[index].y0
        if block['kind'] == 'paragraph' and block.get('line_count', 1) == 1 and 0 < room < heights[index]:
            size = block.get('line_size_pt', block['font_size_pt'])
            percent = max(80, int(room / size * 100))
            item[0] = (dict(block, line_spacing_percent=percent), item[0][1])
            heights[index] = paragraph_height(item[0][0])
    spacing = vertical_spacing([(band.y0, h) for band, h in zip(bands, heights)], margins['top'])
    end = margins['top']
    for band, h in zip(bands, heights):
        end = max(end, band.y0) + h
    return dict(items=items, bands=bands, spacing=spacing, end=end)


def _write_flow(doc, page, section, flow, area, available_height, border_cache, counts, state,
                column_break=False):
    """Write one flow's paragraphs, tables and pictures in order."""
    items, bands, spacing = flow['items'], flow['bands'], flow['spacing']
    available_width = max(100, round(area.width * 100))
    for item, band, before in zip(items, bands, spacing):
        block, box = item[0]
        if block['kind'] == 'paragraph':
            paragraph = section.paragraphs[0] if state['first'] else doc.add_paragraph(section=section, include_run=False, inherit_style=False)
            layout = dict(block)
            if layout.get('alignment', 'LEFT') in ('LEFT', 'JUSTIFY'):
                # Indents count from the page margin, which other blocks
                # (a wider table) may have moved left of this paragraph.
                if 'left_pt' in layout:
                    layout['indent_left_pt'] = max(0, layout['left_pt'] - area.x0)
                elif 'indent_left_pt' not in layout:
                    layout['indent_left_pt'] = max(0, box.x0 - area.x0)
                if 'right_pt' in layout:
                    layout['indent_right_pt'] = max(0, area.x1 - layout['right_pt'])
            if layout.get('line_count', 1) == 1 and layout.get('runs') and '	' not in layout.get('text', ''):
                # A line that fit in the PDF must not wrap here: one extra
                # line moves everything below it on the page.
                layout['runs'] = [dict(run) for run in layout['runs']]
                indent = layout.get('indent_left_pt', 0) + max(0, layout.get('first_line_indent_pt', 0))
                fit_line(layout, area.width - indent - layout.get('indent_right_pt', 0)
                         if layout.get('alignment', 'LEFT') in ('LEFT', 'JUSTIFY') else area.width,
                         margin=.995, limit=1)
            add_runs(doc, paragraph, layout, before)
            if column_break:
                paragraph.element.set('columnBreak', '1')
                column_break = False
            state['first'] = False
            counts['paragraphs'] += 1
            continue
        # Tables and pictures sit in their own paragraph; place it with the
        # source offset instead of the default left edge.
        placement = dict(alignment='LEFT', line_spacing_percent=100, indent_left_pt=max(0, band.x0 - area.x0))
        if len(item) == 1 and abs((box.x0 - area.x0) - (area.x1 - box.x1)) < 3:
            placement = dict(alignment='CENTER', line_spacing_percent=100)
        if block['kind'] == 'table':
            table_width = min(available_width, max(100, round(box.width * 100)))
            table_height = max(100, round(box.height * 100))
            if block.get('row_heights_pt') and len(block['row_heights_pt']) != block['rows']:
                block = dict(block, row_heights_pt=None)  # a grid read wrong; Hancom sizes the rows
            if block.get('row_heights_pt'):
                table_height = max(100, round(sum(block['row_heights_pt']) * 100))
            host = section.paragraphs[0] if state['first'] else doc.add_paragraph(section=section, include_run=False, inherit_style=False)
            state['first'] = False
            try:
                _write_table(doc, host, block, table_width, table_height, border_cache, page)
                counts['tables'] += 1
            except (ValueError, IndexError, KeyError):
                # A grid Hancom cannot hold goes in as a picture of the table
                # rather than stopping the document.
                for run in list(host.element):
                    if any(child.tag.endswith('}tbl') for child in run.iter()):
                        host.element.remove(run)
                clip = box & page.rect
                if not clip.is_empty:
                    _add_picture(doc, page, host, dict(block, kind='image', xref=None), clip, table_width,
                                 max(100, round(table_width * clip.height / clip.width)))
                    counts['figures'] += 1
        else:
            host = section.paragraphs[0] if state['first'] else doc.add_paragraph(section=section, inherit_style=False)
            state['first'] = False
            right = band.x0
            for member, box in sorted(item, key=lambda member: member[1].x0):
                clip = box & page.rect
                image_width = min(available_width, max(100, round(clip.width * 100)))
                image_height = max(100, round(image_width * clip.height / clip.width))
                if image_height > available_height:
                    image_width = max(100, round(image_width * available_height / image_height))
                    image_height = available_height
                picture = _add_picture(doc, page, host, member, clip, image_width, image_height)
                # The gap to the previous picture of the row is its margin.
                margin = next((child for child in picture.element if child.tag.endswith('}outMargin')), None)
                if margin is not None and clip.x0 > right:
                    margin.set('left', str(round((clip.x0 - right) * 100)))
                right = clip.x1
                counts['figures'] += 1
        paragraph_format(doc, host, placement, before)
        if column_break:
            host.element.set('columnBreak', '1')
            column_break = False


def _write_table(doc, host, block, width, height, border_cache, page):
    """A table block into host. Column widths go in before cells merge:
    python-hwpx counts columns in the first row."""
    cells = sanitize_cells(block['cells'], block['rows'], block['cols'], fill=True)
    table = host.add_table(block['rows'], block['cols'], width=width, height=height)
    if block.get('column_widths_pt') and len(block['column_widths_pt']) == block['cols']:
        table.set_column_widths(block['column_widths_pt'])
    for cell in cells:
        r, c = cell['row'], cell['col']
        if cell['row_span'] > 1 or cell['col_span'] > 1:
            table.merge_cells(r, c, r + cell['row_span'] - 1, c + cell['col_span'] - 1)
        apply_cell_format(doc, table, cell, block, border_cache, page)
        if block.get('row_heights_pt'):
            table.cell(r, c).set_size(height=round(sum(block['row_heights_pt'][r:r + cell['row_span']]) * 100))
    return table


def _set_columns(section, count, gap):
    """A newspaper-column section: count columns of one width, gap apart."""
    for element in section.element.iter():
        if element.tag.endswith('}colPr'):
            element.set('colCount', str(count))
            element.set('sameSz', '1')
            element.set('sameGap', str(max(0, round(gap * 100))))


def write_page(doc, page, structured, index):
    section = doc.sections[0] if index == 0 else doc.add_section()
    # The first paragraph contains secPr. Keep it instead of removing section
    # settings or introducing an empty paragraph before every source page.
    if not section.paragraphs:
        doc.add_paragraph(section=section)
    width, height = page.rect.width, page.rect.height
    box_of = lambda b: pymupdf.Rect(b['bbox'][0] * width / 1000, b['bbox'][1] * height / 1000,
                                    b['bbox'][2] * width / 1000, b['bbox'][3] * height / 1000)
    behind = [block for block in structured['blocks'] if block.get('placement') == 'BEHIND_TEXT']
    figure_text = [block for block in structured['blocks'] if block.get('placement') == 'IN_FIGURE']
    blocks = [block for block in structured['blocks'] if block.get('placement') not in ('BEHIND_TEXT', 'IN_FIGURE')]
    boxes = [box_of(block) for block in blocks]
    # A two-up sheet shows two page numbers; they stay in their columns.
    footer = None if structured.get('columns') else _page_footer(blocks, boxes, height)
    if footer is not None:
        footer_block, footer_box = blocks.pop(footer), boxes.pop(footer)
    # The page margins enclose the PDF content so paragraphs wrap at the
    # same width and start at the same place as in the source page.
    area = pymupdf.Rect(boxes[0]) if boxes else pymupdf.Rect()
    for box in boxes[1:]:
        area |= box
    if area.width < 72 or area.height < 36:
        area = pymupdf.Rect(18, 18, width - 18, height - 18)
    columns = [area]
    if structured.get('columns'):
        columns = []
        for column in range(len(structured['columns'])):
            members = [box for block, box in zip(blocks, boxes) if block.get('column', 0) == column]
            column_area = pymupdf.Rect(members[0]) if members else pymupdf.Rect(structured['columns'][column])
            for box in members[1:]:
                column_area |= box
            columns.append(column_area)
        # Hancom columns share one width; the widest half sets it and each
        # half starts where it did on the sheet.
        column_width = max(column.width for column in columns)
        columns = [pymupdf.Rect(column.x0, column.y0, column.x0 + column_width, column.y1) for column in columns]
        area = pymupdf.Rect(columns[0].x0, min(column.y0 for column in columns), columns[-1].x1,
                            max(column.y1 for column in columns))
    margins = dict(left=max(0, area.x0), right=max(0, width - area.x1), top=max(0, area.y0),
                   bottom=min(max(0, height - area.y1), 10), header=0, footer=0)
    if footer is not None:
        # A page number sits in the footer: it stays at the bottom whatever
        # the body above it needs, and the body may use the room above it.
        margins['bottom'] = max(0, height - footer_box.y1 - 2)
        margins['footer'] = footer_box.height + 2
    doc.page.set_size(width=round(width * 100), height=round(height * 100), section=section)
    doc.page.set_margins(**{key: round(value * 100) for key, value in margins.items()}, gutter=0, section=section)
    # Set on every page: a new section copies the previous one's columns.
    _set_columns(section, len(columns), columns[1].x0 - columns[0].x1 if len(columns) > 1 else 0)
    if footer is not None:
        story = section.properties.set_footer_text('')
        layout = dict(footer_block)
        if layout.get('alignment', 'LEFT') in ('LEFT', 'JUSTIFY'):
            layout['indent_left_pt'] = max(0, footer_box.x0 - area.x0)
        add_runs(doc, story.paragraphs[0], layout)
    border_cache = {}
    counts = dict(paragraphs=0, tables=0, figures=0)
    available_height = max(100, round((height - margins['top'] - margins['bottom'] - margins['footer']) * 100))
    # A two-up sheet flows as two Hancom columns, each from the top.
    flows = []
    for column, column_area in enumerate(columns):
        members = [(block, box) for block, box in zip(blocks, boxes) if block.get('column', 0) == column]
        flows.append(_plan_flow(members, margins))
    bottom = height - margins['bottom'] - margins['footer'] + 2
    if structured.get('free_layout') or any(flow['end'] > bottom for flow in flows):
        # Side-by-side blocks that a flow cannot hold (a designed page):
        # every block keeps its place on the paper instead.
        counts = _free_page(doc, page, section, blocks + figure_text, boxes + [box_of(block) for block in figure_text],
                            area)
        # The artwork lost all text; text that could only be kept as a
        # picture goes back on top of it.
        flows, behind = [], [block for block in behind if block.get('text_image')]
    state = dict(first=True)
    for column, flow in enumerate(flows):
        _write_flow(doc, page, section, flow, columns[column], available_height, border_cache, counts, state,
                    column_break=column > 0)
    for block in behind:
        # Positioned on the paper behind the text, so the page flow is the
        # same as without it.
        clip = box_of(block) & page.rect
        if clip.is_empty:
            continue
        if block.get('frame'):
            _add_frame(section.paragraphs[0], clip, block['frame'])
            continue
        _add_picture(doc, page, section.paragraphs[0], block, clip, max(100, round(clip.width * 100)),
                     max(100, round(clip.height * 100)), floating=True)
        counts['figures'] += 1
    # Cached line layouts from the template describe other content; Hancom
    # then draws later lines over pictures. Let Hancom lay out every line.
    section.remove_layout_caches()
    return counts
