"""A per-user, transactional image library. No source-image paths are retained."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import io
import math
from pathlib import Path
import sqlite3
import uuid

from PIL import Image, ImageOps


@dataclass(frozen=True)
class Stamp:
    id: str
    name: str
    width_mm: float
    png: bytes


def _details(name, width_mm):
    name = str(name).strip()
    if not name or len(name) > 80:
        raise ValueError('이름을 1~80자로 입력해 주세요.')
    width = float(width_mm)
    if not math.isfinite(width) or not 1 <= width <= 500:
        raise ValueError('너비는 1~500mm로 입력해 주세요.')
    return name, width


def normalize_image(data):
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 25_000_000:
                raise ValueError('이미지가 너무 큽니다. 2,500만 화소 이하로 줄여 주세요.')
            image = ImageOps.exif_transpose(source).convert('RGBA')
            image.thumbnail((2400, 2400), Image.Resampling.LANCZOS)
            stream = io.BytesIO()
            image.save(stream, 'PNG')
            return stream.getvalue()
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError('읽을 수 있는 PNG 또는 JPG 이미지를 선택해 주세요.') from error


class StampLibrary:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('CREATE TABLE IF NOT EXISTS stamps '
                       '(id TEXT PRIMARY KEY, name TEXT NOT NULL, width_mm REAL NOT NULL, png BLOB NOT NULL)')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connection() as db:
            return [Stamp(*row) for row in db.execute('SELECT id, name, width_mm, png FROM stamps ORDER BY rowid')]

    def get(self, identifier):
        with self.connection() as db:
            row = db.execute('SELECT id, name, width_mm, png FROM stamps WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('보관함에서 이 이미지를 찾을 수 없습니다.')
        return Stamp(*row)

    def add(self, name, data, width_mm=20):
        name, width = _details(name, width_mm)
        stamp = Stamp(uuid.uuid4().hex, name, width, normalize_image(data))
        self.restore(stamp)
        return stamp

    def restore(self, stamp):
        with self.connection() as db:
            db.execute('INSERT INTO stamps VALUES (?, ?, ?, ?)',
                       (stamp.id, stamp.name, stamp.width_mm, stamp.png))

    def update(self, identifier, name, width_mm, data=None):
        name, width = _details(name, width_mm)
        png = normalize_image(data) if data is not None else None
        with self.connection() as db:
            if not db.execute('UPDATE stamps SET name=?, width_mm=?, png=COALESCE(?, png) WHERE id=?', (name, width, png, identifier)).rowcount:
                raise ValueError('보관함에서 이 이미지를 찾을 수 없습니다.')

    def remove(self, identifier):
        with self.connection() as db:
            row = db.execute('SELECT id, name, width_mm, png FROM stamps WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise ValueError('보관함에서 이 이미지를 찾을 수 없습니다.')
            db.execute('DELETE FROM stamps WHERE id=?', (identifier,))
        return Stamp(*row)
