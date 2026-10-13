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
    return _key(name) in families or (name in ALIASES and _key(ALIASES[name]) in families)


@lru_cache(maxsize=64)
def _font(name):
    path = _installed().get(_key(name)) or (_installed().get(_key(ALIASES[name])) if name in ALIASES else None)
    if not path:
        return None
    try:
        return pymupdf.Font(fontfile=path)
    except Exception:
        return None


def glyph_width(font_name, char, size):
    """Advance width of char in the installed font, or None if unknown."""
    font = _font(font_name)
    if font is None or not font.has_glyph(ord(char)):
        return None
    return font.text_length(char, size)
