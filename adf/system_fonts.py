"""Resolve installed font faces by their OpenType names, including TTC faces."""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import io
import os
from pathlib import Path
import re
import struct
import sys
import tempfile
import time
import unicodedata

from fontTools.ttLib import TTFont


def normal_font_name(value):
    value = unicodedata.normalize('NFKC', value)
    value = re.sub(r'^[A-Z]{6}\+', '', value)
    value = re.sub(r'\s*\((?:TrueType|OpenType)\)\s*$', '', value, flags=re.I)
    return re.sub(r'[\s_,.\-]', '', value).casefold().removesuffix('regular')


def font_paths():
    paths = set()
    if sys.platform == 'win32':
        import winreg
        windows = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'
        user = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Microsoft/Windows/Fonts'
        roots = [windows, user]
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts') as key:
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        _, value, _ = winreg.EnumValue(key, i)
                        if not isinstance(value, str):
                            continue
                        path = Path(os.path.expandvars(value))
                        if path.is_absolute():
                            paths.add(path)
                        else:
                            # Per-user registrations can also name a system file.
                            paths.update(root / path for root in roots)
            except OSError:
                pass
    else:
        roots = ([Path('/System/Library/Fonts'), Path('/Library/Fonts'), Path.home() / 'Library/Fonts']
                 if sys.platform == 'darwin' else
                 [Path('/usr/share/fonts'), Path('/usr/local/share/fonts'), Path.home() / '.local/share/fonts'])
    for root in roots:
        if root.is_dir():
            paths.update(path for path in root.rglob('*') if path.suffix.lower() in ('.ttf', '.otf', '.ttc', '.otc'))
    return sorted((path for path in paths if path.is_file()), key=lambda path: str(path).casefold())


def _signature(path):
    stat = Path(path).stat()
    return str(path), stat.st_size, stat.st_mtime_ns


_inventory_cache = None
_inventory_checked = 0.0


def refresh_fonts():
    global _inventory_cache
    _inventory_cache = None


def _inventory():
    global _inventory_cache, _inventory_checked
    now = time.monotonic()
    if _inventory_cache is None or now - _inventory_checked >= 1:
        signatures = []
        for path in font_paths():
            try:
                signatures.append(_signature(path))
            except OSError:
                continue
        _inventory_cache, _inventory_checked = tuple(signatures), now
    return _inventory_cache


@dataclass(frozen=True)
class FontFace:
    signature: tuple
    index: int
    collection: bool
    names: frozenset
    families: frozenset
    regular: bool

    def data(self):
        if self.collection:
            return _face_data(self.signature, self.index)
        return Path(self.signature[0]).read_bytes()

    def file(self):
        if not self.collection:
            return self.signature[0]
        # MuPDF's fontfile API opens the first TTC face. Supply the selected
        # face as a standalone font to numbering and other file-based tools.
        data = self.data()
        path = _face_directory() / (hashlib.sha256(data).hexdigest() + '.ttf')
        if not path.is_file():
            path.write_bytes(data)
        return str(path)


_temporary_faces = None


def _face_directory():
    global _temporary_faces
    if _temporary_faces is None:
        _temporary_faces = tempfile.TemporaryDirectory(prefix='adf-font-faces-')
    return Path(_temporary_faces.name)


@lru_cache(maxsize=32)
def _face_data(signature, index):
    with TTFont(signature[0], fontNumber=index, lazy=True, recalcTimestamp=False) as font:
        output = io.BytesIO()
        font.save(output)
        return output.getvalue()


@lru_cache(maxsize=4096)
def _file_faces(signature):
    path = signature[0]
    faces = []
    try:
        with open(path, 'rb') as stream:
            header = stream.read(12)
        collection = header[:4] == b'ttcf'
        count = struct.unpack_from('>I', header, 8)[0] if collection else 1
        if not 1 <= count <= 256:
            return ()
        for index in range(count):
            with TTFont(path, fontNumber=index if collection else -1, lazy=True, recalcTimestamp=False) as font:
                by_id = {}
                for record in font['name'].names:
                    try:
                        by_id.setdefault(record.nameID, set()).add(record.toUnicode())
                    except (UnicodeError, LookupError):
                        continue
                families = set().union(*(by_id.get(i, set()) for i in (1, 16, 21)))
                def is_regular(styles):
                    return any(normal_font_name(style) in ('', 'normal', 'roman', 'book', '보통', '일반') for style in styles)
                regular = is_regular(by_id.get(17) or by_id.get(2) or by_id.get(22, set()))
                names = by_id.get(4, set()) | by_id.get(6, set())
                # A legacy "Family Semibold / Regular" pair can coexist with
                # typographic "Family / Semibold". Only the matching family's
                # own subfamily may qualify it as the regular face.
                for family_id, style_id in ((1, 2), (16, 17), (21, 22)):
                    pair_families = by_id.get(family_id, set())
                    pair_styles = by_id.get(style_id, set())
                    names.update(family + ' ' + style for family in pair_families for style in pair_styles)
                    if is_regular(pair_styles):
                        names.update(pair_families)
                faces.append(FontFace(signature, index, collection,
                    frozenset(map(normal_font_name, names)), frozenset(map(normal_font_name, families)), regular))
    except Exception:
        # A malformed / unsupported file must not prevent other fonts loading.
        pass
    return tuple(faces)


@lru_cache(maxsize=4)
def _font_index(signatures):
    exact, families = {}, {}
    for signature in signatures:
        for face in _file_faces(signature):
            for name in face.names:
                exact.setdefault(name, []).append(face)
            for name in face.families:
                families.setdefault(name, []).append(face)
    return exact, families


def resolve_font_face(name, *, allow_family=False):
    # Refresh the inventory, including previously missing names, when fonts
    # are installed while ADF is open. Table parsing is cached by file stat.
    exact, families = _font_index(_inventory())
    target = normal_font_name(name)
    matches = exact.get(target) or (families.get(target) if allow_family else None)
    return min(matches, key=lambda face: (not face.regular, face.signature[0], face.index)) if matches else None


def resolve_font_file(name):
    face = resolve_font_face(name, allow_family=True)
    return face.file() if face else None
