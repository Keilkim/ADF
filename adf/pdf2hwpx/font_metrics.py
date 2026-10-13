"""Glyph widths of installed fonts, as Hancom will draw them.

A PDF records where each glyph went, and the widths of the font it embedded.
Hancom draws with the installed font instead, whose widths can differ (an
embedded Malgun Gothic subset declared Hangul 0.95 em wide; the installed
font and Hancom use 1 em). Letter spacing measured against the installed
font reproduces the PDF's line widths. Without the font, callers fall back
to the PDF's own glyph widths.
"""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
import sys

import pymupdf


# Korean family names and the English names Windows registers them under.
ALIASES = {
    '맑은 고딕': 'Malgun Gothic', '바탕': 'Batang', '바탕체': 'BatangChe', '돋움': 'Dotum', '돋움체': 'DotumChe',
    '굴림': 'Gulim', '굴림체': 'GulimChe', '궁서': 'Gungsuh', '궁서체': 'GungsuhChe', '함초롬바탕': 'HCR Batang',
    '함초롬돋움': 'HCR Dotum', '한컴바탕': 'Haansoft Batang', '한컴돋움': 'Haansoft Dotum', '나눔고딕': 'NanumGothic',
    '나눔명조': 'NanumMyeongjo', '나눔바른고딕': 'NanumBarunGothic',
}


def _key(name):
    name = re.sub(r'^[A-Z]{6}\+', '', name or '')
    name = re.sub(r'\s*\((TrueType|OpenType)\)$', '', name)
    name = re.sub(r'[-,]?(Regular|BoldItalic|Bold|Italic|Medium|Light)$', '', name)
    return re.sub(r'[\s_\-]', '', name).lower()


@lru_cache(maxsize=1)
def _installed():
    """Map of normalized family name -> font file, from the Windows font registry."""
    return _registry()[0]


@lru_cache(maxsize=1)
def _registry():
    files, families = {}, set()
    if sys.platform != 'win32':
        return files, families
    import winreg
    folders = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts',
               Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'Windows' / 'Fonts']
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                path = Path(value)
                if not path.is_absolute():
                    path = next((folder / value for folder in folders if (folder / value).is_file()), None)
                if path is None or not path.is_file():
                    continue
                families.update(_key(family) for family in name.split('&'))
                # 'Batang & BatangChe & Gungsuh & GungsuhChe' share one file;
                # only its first face is read, so only that name is listed.
                files.setdefault(_key(name.split('&')[0]), str(path))
    for folder in _hancom_font_folders():
        _add_hancom_fonts(folder, files, families)
    return files, families


def _hancom_font_folders():
    """Font folders of installed Hancom Office versions."""
    roots = [Path(os.environ.get(name, '')) for name in ('ProgramFiles(x86)', 'ProgramFiles')]
    found = []
    for root in roots:
        for folder in (root / 'HNC').glob('*/HOffice*/Bin/Shared/Fonts') if root.parts else []:
            found.append(folder)
        for folder in (root / 'HNC').glob('*/HOffice*/Shared/Fonts') if root.parts else []:
            found.append(folder)
    return found


def _add_hancom_fonts(folder, files, families):
    """Fonts only Hancom Office draws: its own HFT fonts and bundled TTFs.

    HFTInfo.dat lists 'Korean name=FILE.HFT,...,English name'. HFT widths
    cannot be read, so these fonts count as available without widths and
    the PDF's own glyph widths stand in (Hancom made those PDFs with them).
    """
    info = folder / 'HFTInfo.dat'
    if info.is_file():
        try:
            text = info.read_bytes().decode('utf-16')
        except UnicodeDecodeError:
            text = ''
        for line in text.splitlines():
            name, _, value = line.partition('=')
            if '.HFT' in value.upper():
                families.add(_key(name))
                english = value.split(',')[-1].strip()
                if english:
                    families.add(_key(english))
    for path in list(folder.glob('*.ttf')) + list(folder.glob('*.TTF')):
        try:
            family = pymupdf.Font(fontfile=str(path)).name
        except Exception:
            continue
        families.add(_key(family))
        files.setdefault(_key(family), str(path))


def registered_family(name):
    """The registered family for a PostScript name of one weight, if any.

    Fonts installed per weight register 'Pretendard Medium'; Hancom finds
    that family name, not 'Pretendard' or 'Pretendard-Medium'.
    """
    _registry()
    wanted = re.sub(r'[\s_\-]', '', name).lower()
    return _names().get(wanted)


@lru_cache(maxsize=1)
def _names():
    names = {}
    if sys.platform != 'win32':
        return names
    import winreg
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            key = winreg.OpenKey(root, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts')
        except OSError:
            continue
        with key:
            index = 0
            while True:
                try:
                    name = winreg.EnumValue(key, index)[0]
                except OSError:
                    break
                index += 1
                for family in name.split('&'):
                    family = re.sub(r'\s*\((TrueType|OpenType)\)$', '', family).strip()
                    names.setdefault(re.sub(r'[\s_\-]', '', family).lower(), family)
    return names


def installed(name):
    """True or False on Windows; None where installed fonts are not known."""
    files, families = _registry()
    if not families:
        return None
    return (_key(name) in families or (name in ALIASES and _key(ALIASES[name]) in families)
            or _exact(name) in _faces())


def _exact(name):
    """A face name for lookup: case, spaces, hyphens and underscores do not count."""
    return re.sub(r'[\s_\-]', '', name or '').lower()


def _font_files():
    """Every font file Windows and Hancom Office install, all weights."""
    folders = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts',
               Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft' / 'Windows' / 'Fonts',
               *_hancom_font_folders()]
    files = {str(path) for folder in folders if folder.parts and folder.is_dir() for path in folder.iterdir()
             if path.suffix.lower() in ('.ttf', '.otf', '.ttc')}
    return sorted(files | {path for path in _installed().values() if path.lower().endswith(('.ttf', '.otf', '.ttc'))})


def _faces():
    return _index()[0]


def hancom_family(name):
    """The family name Hancom lists for an installed face, or None.

    name may be a PDF's PostScript name ('KoPubWorldDotumMedium', 'H2HDRM')
    or a full name; the face's own records give the Korean family name
    Hancom shows ('KoPubWorld돋움체 Medium', 'HY헤드라인M'), or the English one.
    """
    face = _index()[0].get(_exact(name))
    return _index()[1].get(face) if face else None


@lru_cache(maxsize=1)
def _index():
    """Every installed face by its names, as (font file, face index).

    Names come from the fonts themselves, Korean and English, so a face of
    one weight ('Pretendard Medium') and a face inside a collection
    ('DotumChe' in gulim.ttc) are found as Hancom finds them. A full name
    wins over a family and style, which wins over a family alone (Arial
    Narrow also calls its family 'Arial').
    """
    from fontTools.ttLib import TTCollection, TTFont
    regular = ('regular', 'normal', 'standard', 'book', '보통', '표준')
    ranked, families = {}, {}
    for path in _font_files():
        try:
            count = len(TTCollection(path, lazy=True).fonts) if path.lower().endswith('.ttc') else 1
        except Exception:
            continue
        for index in range(count):
            try:
                names = TTFont(path, fontNumber=index, lazy=True)['name']
            except Exception:
                continue
            records, korean = {}, None
            for record in names.names:
                try:
                    text = record.toUnicode()
                except Exception:
                    continue
                records.setdefault(record.nameID, []).append(text)
                if record.nameID == 1 and (record.platformID, record.langID) in ((3, 0x412), (1, 23)):
                    korean = text
            english = next((name for name in records.get(1, []) if name.isascii()), None)
            if korean or english:
                families[(path, index)] = korean or english
            keys = [(0, name) for name in records.get(4, []) + records.get(6, [])]
            keys += [(1, family + ' ' + style) for family_id, style_id in ((16, 17), (1, 2))
                     for family in records.get(family_id, []) for style in records.get(style_id, [])]
            for family_id, style_id in ((1, 2), (16, 17)):
                if any(_exact(style) in regular for style in records.get(style_id, [])):
                    keys += [(2, family) for family in records.get(family_id, [])]
            for rank, key in keys:
                key = _exact(key)
                if key not in ranked or rank < ranked[key][0]:
                    ranked[key] = (rank, (path, index))
    return {key: face for key, (_, face) in ranked.items()}, families


def _face(name):
    faces = _faces()
    for candidate in (name, ALIASES.get(name)):
        if candidate and _exact(candidate) in faces:
            return faces[_exact(candidate)]
    # A weight-less name may still match the old way (the family's file).
    path = _installed().get(_key(name)) or (_installed().get(_key(ALIASES[name])) if name in ALIASES else None)
    return (path, 0) if path and path.lower().endswith(('.ttf', '.otf', '.ttc')) else None


@lru_cache(maxsize=128)
def _metrics(name):
    """(character map, advance widths, units per em) of a font, or None."""
    face = _face(name)
    if face is None:
        return None
    from fontTools.ttLib import TTFont
    try:
        font = TTFont(face[0], fontNumber=face[1], lazy=True)
        cmap = {}
        for table in font['cmap'].tables:
            if table.isUnicode() or table.platformID == 3:
                for code, glyph in table.cmap.items():
                    cmap.setdefault(code, glyph)
        # A symbol font maps its characters at U+F000 + code.
        for code, glyph in list(cmap.items()):
            if 0xF020 <= code <= 0xF0FF:
                cmap.setdefault(code - 0xF000, glyph)
        advances = {glyph: metric[0] for glyph, metric in font['hmtx'].metrics.items()}
        return {code: advances.get(glyph, 0) for code, glyph in cmap.items()}, font['head'].unitsPerEm
    except Exception:
        return None


@lru_cache(maxsize=1)
def _measured():
    """Widths Hancom draws for fonts whose files cannot be read (its HFT
    fonts) and for characters a font lacks, measured in Hancom itself
    (scripts/pdf2hwpx/hancom_widths.py), in em."""
    import json
    path = Path(__file__).with_name('hancom_widths.json')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _class(char):
    code = ord(char)
    if 0xAC00 <= code <= 0xD7A3:
        return 'hangul'
    if 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
        return 'hanja'
    return None


def glyph_width(font_name, char, size):
    """Advance width Hancom gives char in font_name at size, or None if unknown."""
    metrics = _metrics(font_name)
    if metrics is not None:
        widths, units = metrics
        advance = widths.get(ord(char))
        if advance:
            return advance * size / units
    table = _measured().get(font_name) or _measured().get(ALIASES.get(font_name, ''))
    if table:
        em = table.get(char, table.get(_class(char) or '', None))
        if em is not None:
            return em * size
    if metrics is not None and _class(char):
        # Hancom draws a Korean character the font lacks with its fallback.
        fallback = _measured().get('fallback', {}).get(_class(char))
        if fallback is not None:
            return fallback * size
    return None
