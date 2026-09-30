"""Reuse document fonts without installing them or silently substituting glyphs."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import re

import pymupdf
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable


def normal_name(name):
    name = re.sub(r'^[A-Z]{6}\+', '', name)
    return re.sub(r'[\s_,-]', '', name).casefold().removesuffix('regular')


def _save(font):
    output = io.BytesIO()
    font.save(output)
    return output.getvalue()


def _unicode(value):
    try:
        text = bytes.fromhex(value).decode('utf-16-be')
        return ord(text) if len(text) == 1 else None
    except (ValueError, UnicodeError):
        return None


def _cmap_pairs(data):
    """Read bounded bfchar/bfrange mappings; multi-character ligatures stay out."""
    source = re.sub(r'%[^\r\n]*', '', data.decode('latin1'))
    for kind, body in re.findall(r'begin(bfchar|bfrange)\b(.*?)end\1', source, re.S):
        if kind == 'bfchar':
            for key, value in re.findall(r'<([\da-fA-F]+)>\s*<([\da-fA-F]+)>', body):
                yield int(key, 16), _unicode(value)
        else:
            for start, end, scalar, array in re.findall(
                    r'<([\da-fA-F]+)>\s*<([\da-fA-F]+)>\s*(?:<([\da-fA-F]+)>|\[([^\]]*)\])', body):
                first, last = int(start, 16), int(end, 16)
                if last < first or last-first > 65535:
                    continue
                if scalar:
                    value = int(scalar, 16)
                    for offset in range(last-first+1):
                        yield first+offset, _unicode(f'{value+offset:0{len(scalar)}x}')
                else:
                    for offset, value in enumerate(re.findall(r'<([\da-fA-F]+)>', array)[:last-first+1]):
                        yield first+offset, _unicode(value)


def _pdf_mapping(page, resource):
    doc, xref = page.parent, resource[0]
    result = {}
    # Identity-H's character code is a CID, not necessarily a glyph index.
    if resource[5] == 'Identity-H':
        _, descendants = doc.xref_get_key(xref, 'DescendantFonts')
        refs = re.findall(r'(\d+)\s+0\s+R', descendants)
        kind, value = doc.xref_get_key(int(refs[0]), 'CIDToGIDMap') if refs else ('null', '')
        gids = doc.xref_stream(int(value.split()[0])) if kind == 'xref' else None
        identity = value == '/Identity' or (kind == 'null' and resource[2] == 'Type0')
        kind, value = doc.xref_get_key(xref, 'ToUnicode')
        if kind == 'xref' and (identity or gids is not None):
            for cid, codepoint in _cmap_pairs(doc.xref_stream(int(value.split()[0]))):
                if codepoint is None:
                    continue
                if gids is None:
                    gid = cid
                elif cid*2+2 <= len(gids):
                    gid = int.from_bytes(gids[cid*2:cid*2+2], 'big')
                else:
                    continue
                result[codepoint] = gid
    # Covers simple encodings and PDFs without ToUnicode. MuPDF supplies the
    # actual Unicode/glyph-index pairs it used to render the selected page.
    matching = {r[0] for r in page.get_fonts(full=True) if normal_name(r[3]) == normal_name(resource[3])}
    for span in page.get_texttrace() if len(matching) == 1 else ():
        if normal_name(span['font']) == normal_name(resource[3]):
            for codepoint, gid, *_ in span['chars']:
                if codepoint != 0xfffd and gid > 0:
                    result.setdefault(codepoint, gid)
    return result


def _sfnt(data, mapping=None):
    """Wrap raw CFF, or repair a subset's missing Unicode cmap, keeping outlines."""
    mapping = mapping or {}
    metrics = pymupdf.Font(fontbuffer=data)
    if data[:4] not in (b'\x00\x01\x00\x00', b'OTTO', b'true'):
        if data[:1] != b'\x01':
            raise ValueError('이 내장 글꼴 형식은 재사용을 지원하지 않습니다.')
        from fontTools.cffLib import CFFFontSet
        from fontTools.fontBuilder import FontBuilder
        cff = CFFFontSet()
        cff.decompile(io.BytesIO(data), TTFont())
        top = cff.topDictIndex[0]
        # CID-keyed CFF requires the PDF's CID charset mapping as well. Avoid
        # interpreting those IDs as glyph IDs when a safe conversion is absent.
        if hasattr(top, 'ROS'):
            raise ValueError('이 CID CFF 글꼴은 재사용을 지원하지 않습니다.')
        matrix = top.FontMatrix
        if matrix[0] <= 0 or matrix[0] != matrix[3] or any(matrix[i] for i in (1, 2, 4, 5)):
            raise ValueError('이 CFF 글꼴의 변환 행렬은 재사용을 지원하지 않습니다.')
        units = round(1 / matrix[0])
        if not 16 <= units <= 16384:
            raise ValueError('이 CFF 글꼴의 단위 크기는 지원하지 않습니다.')
        order = top.charset
        builder = FontBuilder(units, isTTF=False)
        builder.setupGlyphOrder(order)
        builder.setupCharacterMap({cp: order[metrics.has_glyph(cp)] for cp in metrics.valid_codepoints()
                                   if metrics.has_glyph(cp) < len(order)})
        from fontTools.pens.basePen import NullPen
        widths = {}
        for name in order:
            charstring = top.CharStrings[name]
            charstring.draw(NullPen())
            widths[name] = (round(charstring.width), 0)
        builder.setupHorizontalMetrics(widths)
        builder.setupHorizontalHeader(ascent=round(metrics.ascender*units), descent=round(metrics.descender*units))
        builder.setupNameTable({'familyName': metrics.name, 'styleName': 'Regular',
                               'fullName': metrics.name, 'psName': re.sub(r'\s+', '', metrics.name)})
        builder.setupOS2(sTypoAscender=round(metrics.ascender*units), sTypoDescender=round(metrics.descender*units),
                         usWinAscent=max(0, round(metrics.ascender*units)), usWinDescent=max(0, round(-metrics.descender*units)))
        builder.setupPost()
        builder.setupMaxp()
        font = builder.font
        font.sfntVersion = 'OTTO'
        font['CFF '] = newTable('CFF ')
        font['CFF '].cff = cff
    else:
        font = TTFont(io.BytesIO(data), recalcTimestamp=False)
    order = font.getGlyphOrder()
    cmap = dict(font.getBestCmap() or {}) if 'cmap' in font else {}
    for cp, gid in mapping.items():
        if 0 < gid < len(order) and 0 <= cp <= 0x10ffff and not 0xd800 <= cp <= 0xdfff:
            cmap[cp] = order[gid]
    # Some subsetters preserve original GIDs / full ToUnicode but clear unused
    # outlines. Such entries must never advertise support for a new character.
    if 'glyf' in font:
        # Reading the contour count from the glyph header avoids expanding and
        # recompiling tens of thousands of untouched Korean glyph outlines.
        offsets = font['loca'].locations
        glyph_data = font.reader['glyf']
        gids = font.getReverseGlyphMap()
        def has_outline(name):
            gid = gids[name]
            start, end = offsets[gid], offsets[gid+1]
            return end-start >= 2 and int.from_bytes(glyph_data[start:start+2], 'big', signed=True) != 0
        cmap = {cp: name for cp, name in cmap.items() if name != order[0] and
                (chr(cp).isspace() or has_outline(name))}
    elif 'CFF ' in font:
        if hasattr(font['CFF '].cff.topDictIndex[0], 'ROS'):
            raise ValueError('이 CID CFF 글꼴은 재사용을 지원하지 않습니다.')
        from fontTools.pens.boundsPen import BoundsPen
        glyphset = font.getGlyphSet()
        visible = set()
        for name in set(cmap.values()):
            pen = BoundsPen(glyphset)
            glyphset[name].draw(pen)
            if pen.bounds is not None and name != order[0]:
                visible.add(name)
        cmap = {cp: name for cp, name in cmap.items() if name in visible or chr(cp).isspace()}
    table = newTable('cmap')
    table.tableVersion = 0
    table.tables = []
    for format_, encoding, entries in ((4, 1, {cp: name for cp, name in cmap.items() if cp <= 0xffff}), (12, 10, cmap)):
        sub = CmapSubtable.newSubtable(format_)
        sub.platformID, sub.platEncID, sub.language, sub.cmap = 3, encoding, 0, entries
        table.tables.append(sub)
    font['cmap'] = table
    return font


@dataclass
class EditFont:
    label: str
    data: bytes
    source: str

    def __post_init__(self):
        self.metrics = pymupdf.Font(fontbuffer=self.data)

    def missing(self, text):
        return ''.join(dict.fromkeys(c for c in text if not c.isspace() and not self.metrics.has_glyph(ord(c))))

    def preview_data(self):
        font = TTFont(io.BytesIO(self.data), recalcTimestamp=False)
        alias = 'ADFDocument' + hashlib.sha256(self.data).hexdigest()[:20]
        # Unique family names stop Qt confusing two PDF subsets or selecting an
        # installed font of the same name. This copy is used only for preview.
        for record in list(font['name'].names):
            if record.nameID in (1, 3, 4, 6, 16, 21):
                font['name'].setName(alias, record.nameID, record.platformID, record.platEncID, record.langID)
            elif record.nameID in (2, 17, 22):
                font['name'].setName('Regular', record.nameID, record.platformID, record.platEncID, record.langID)
        for platform, encoding, language in ((3, 1, 0x409), (1, 0, 0)):
            for name_id in (1, 3, 4, 6, 16):
                font['name'].setName(alias, name_id, platform, encoding, language)
            for name_id in (2, 17):
                font['name'].setName('Regular', name_id, platform, encoding, language)
        return _save(font)


def embedded_font(page, name):
    resources = {r[0]: r for r in page.get_fonts(full=True) if normal_name(r[3]) == normal_name(name)}
    candidates = {}
    for resource in resources.values():
        try:
            _, _, _, data = page.parent.extract_font(resource[0])
            source = 'PDF 내장'
            if not data and resource[3] in pymupdf.Base14_fontnames:
                data = pymupdf.Font(resource[3]).buffer
                source = 'PDF 표준'
            if not data:
                continue
            font = _sfnt(data, _pdf_mapping(page, resource))
            result = EditFont(name, _save(font), source)
            candidates[hashlib.sha256(result.data).digest()] = result
        except Exception:
            continue
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return equivalent_font(list(candidates.values()))


def equivalent_font(candidates):
    """Recognize an edited font alongside its original subset by actual glyphs.

    A repeated family name alone is insufficient: require a superset whose
    shared outlines and advances all match before selecting it without a prompt.
    """
    if not candidates:
        return None
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    fonts = []
    try:
        for candidate in candidates:
            font = TTFont(io.BytesIO(candidate.data))
            fonts.append((candidate, font, font.getBestCmap() or {}, font.getGlyphSet()))
        fonts.sort(key=lambda item: len(item[2]), reverse=True)
        chosen, main, main_cmap, main_set = fonts[0]
        main_shapes = {}
        for _, font, cmap, glyphset in fonts[1:]:
            if not cmap or not cmap.keys() <= main_cmap.keys() or font['head'].unitsPerEm != main['head'].unitsPerEm:
                return None
            if ('glyf' in main and 'glyf' in font
                    and all(main.reader[tag] == font.reader[tag] for tag in ('glyf', 'loca', 'hmtx'))
                    and all(main.getGlyphID(main_cmap[cp]) == font.getGlyphID(name) for cp, name in cmap.items())):
                continue
            for cp, name in cmap.items():
                if font['hmtx'][name][0] != main['hmtx'][main_cmap[cp]][0]:
                    return None
                if ('glyf' in main and 'glyf' in font
                        and main.getGlyphID(main_cmap[cp]) == font.getGlyphID(name)
                        and font['glyf'][name].compile(font['glyf']) == main['glyf'][main_cmap[cp]].compile(main['glyf'])):
                    continue
                if cp not in main_shapes:
                    pen = DecomposingRecordingPen(main_set)
                    main_set[main_cmap[cp]].draw(pen)
                    main_shapes[cp] = pen.value
                pen = DecomposingRecordingPen(glyphset)
                glyphset[name].draw(pen)
                if pen.value != main_shapes[cp]:
                    return None
        return chosen
    except Exception:
        return None
    finally:
        for _, font, _, _ in fonts:
            font.close()


def installed_font(name):
    from .system_fonts import resolve_font_face
    name = re.sub(r'^[A-Z]{6}\+', '', name)
    try:
        face = resolve_font_face(name)
        if face:
            return EditFont(name, face.data(), 'PC 글꼴')
    except Exception:
        pass
    return None


def original_font(page, name, text):
    embedded = embedded_font(page, name)
    if embedded and not embedded.missing(text):
        return embedded
    installed = installed_font(re.sub(r'^[A-Z]{6}\+', '', name))
    if installed and not installed.missing(text):
        return installed
    return None


# Weight words in PDF and PostScript font names, longest first so 'ExtraBold' wins over 'Bold'.
_WEIGHTS = [('extralight', 200), ('ultralight', 200), ('hairline', 100), ('semilight', 300), ('semibold', 600),
            ('demibold', 600), ('extrabold', 800), ('ultrabold', 800), ('regular', 400), ('medium', 500),
            ('normal', 400), ('light', 300), ('heavy', 900), ('black', 900), ('thin', 100), ('bold', 700),
            ('book', 400), ('roman', 400)]
# Korean families mark the weight with a trailing letter: NanumSquareB, HY헤드라인M, NanumSquareEB.
_WEIGHT_LETTERS = [('ul', 200), ('eb', 800), ('xb', 800), ('sb', 600), ('l', 300), ('r', 400), ('m', 500), ('b', 700)]
_SERIF_WORDS = ('myeongjo', 'myungjo', 'batang', 'mincho', 'serif', 'times', 'garamond', 'georgia', 'song',
                '명조', '바탕', '궁서', 'gungsuh')
_MONO_WORDS = ('mono', 'courier', 'consol', 'code', 'd2coding', 'fixed')
_SANS_DEFAULTS = ['Malgun Gothic', 'Apple SD Gothic Neo', 'Noto Sans KR', 'NanumGothic', 'Arial', 'DejaVu Sans']
_SERIF_DEFAULTS = ['Batang', 'AppleMyungjo', 'Noto Serif KR', 'NanumMyeongjo', 'Times New Roman', 'DejaVu Serif']
_MONO_DEFAULTS = ['Consolas', 'D2Coding', 'Courier New', 'Menlo', 'DejaVu Sans Mono']


def font_style(name):
    """Split a PDF font name into a normalized family, a weight class and italic.

    'AAAAAA+NanumGothic-Bold' gives ('nanumgothic', 700, False); 'HY헤드라인M' gives ('hy헤드라인', 500, False).
    """
    name = re.sub(r'^[A-Z]{6}\+', '', name)
    family = re.sub(r'[\s_,.\-]', '', name)
    italic = bool(re.search(r'(italic|oblique)$', family, re.I))
    family = re.sub(r'(italic|oblique)$', '', family, flags=re.I)
    family = re.sub(r'(PSMT|MT|PS)$', '', family)
    key = family.casefold()
    for word, value in _WEIGHTS:
        if key.endswith(word) and len(key) > len(word) + 2:
            return key[:-len(word)], value, italic
    for letters, value in _WEIGHT_LETTERS:
        cut = len(family) - len(letters)
        # Only an uppercase suffix after a lowercase or Hangul letter is a weight: NanumSquareB, not Arial.
        if (cut > 2 and family[cut:].isupper() and family[cut:].casefold() == letters
                and (family[cut-1].islower() or '가' <= family[cut-1] <= '힣')):
            return key[:cut], value, italic
    return key, 400, italic


def font_kind(name, flags=0):
    """'serif', 'mono' or 'sans', from the PDF descriptor flags or else from the name."""
    key = name.casefold()
    if flags & 1 or any(word in key for word in _MONO_WORDS):
        return 'mono'
    if flags & 2 or any(word in key for word in _SERIF_WORDS):
        return 'serif'
    return 'sans'


def font_flags(page, name):
    """The FontDescriptor flags and weight of the page's font of this name, when the PDF records them."""
    doc = page.parent
    for resource in page.get_fonts(full=True):
        if normal_name(resource[3]) != normal_name(name):
            continue
        xref = resource[0]
        kind, value = doc.xref_get_key(xref, 'DescendantFonts')
        if kind == 'array':
            match = re.search(r'(\d+) 0 R', value)
            if match:
                xref = int(match.group(1))
        kind, value = doc.xref_get_key(xref, 'FontDescriptor')
        if kind != 'xref':
            continue
        descriptor = int(value.split()[0])
        flags, weight = doc.xref_get_key(descriptor, 'Flags'), doc.xref_get_key(descriptor, 'FontWeight')
        try:
            return (int(flags[1]) if flags[0] == 'int' else 0,
                    int(float(weight[1])) if weight[0] in ('int', 'real') else None)
        except ValueError:
            return 0, None
    return 0, None


def similar_fonts(name, text, *, flags=0, weight=None, preferred=None, limit=8):
    """Installed fonts that can stand in for a missing one, best first, each with a reason.

    Like CorelDRAW's font matching: a saved substitute first, then installed families
    whose names are close to the missing name, then the default font of the same kind
    (명조, 고딕 or 고정폭). Each covers every character of the text, in the closest weight.
    """
    from difflib import SequenceMatcher
    from .system_fonts import face_details, font_families, resolve_font_face
    family, named_weight, italic = font_style(name)
    if weight is None:
        weight = 700 if flags & (1 << 18) else named_weight
    families = font_families()
    results, seen = [], set()

    def best_face(faces):
        return min(faces, key=lambda face: (abs(face_details(face)[1] - weight), face_details(face)[3] != italic,
                                             not face.regular, face.signature[0], face.index))

    def add(face, reason):
        key = (face.signature, face.index)
        if key in seen or len(results) >= limit:
            return
        seen.add(key)
        try:
            font = EditFont(face_details(face)[0], face.data(), 'PC 글꼴')
        except Exception:
            return
        if not font.missing(text):
            results.append((font, reason))

    if preferred:
        # A saved substitute is the face the user chose, by its full name.
        face = resolve_font_face(preferred, allow_family=True)
        if face is not None:
            add(face, '저장한 대체 글꼴')
    if len(family) >= 3:
        scored = []
        for key, faces in families.items():
            ratio = SequenceMatcher(None, family, key).ratio()
            if min(len(family), len(key)) >= 4 and (family.startswith(key) or key.startswith(family)):
                ratio = max(ratio, .85)
            if ratio >= .6:
                scored.append((ratio, key, faces))
        for _, _, faces in sorted(scored, key=lambda item: (-item[0], item[1])):
            add(best_face(faces), '이름이 비슷한 글꼴')
    kind = font_kind(name, flags)
    defaults = {'serif': _SERIF_DEFAULTS, 'mono': _MONO_DEFAULTS}.get(kind, _SANS_DEFAULTS)
    label = {'serif': '명조(세리프)', 'mono': '고정폭'}.get(kind, '고딕(산세리프)')
    for default in defaults + (_SANS_DEFAULTS if kind != 'sans' else []):
        faces = families.get(normal_name(default))
        if faces:
            add(best_face(faces), f'{label} 기본 글꼴' if default in defaults else '기본 글꼴')
    return results
