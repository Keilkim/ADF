"""PDF editing and export, independent of the Qt user interface.

All page numbers are zero based. Rectangles use unrotated PyMuPDF page
coordinates; numbering margins alone are in millimetres. Source files are never
modified by an edit. Saving to an existing path is an explicit caller decision.
"""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable, Iterator

import pymupdf


MM_TO_PT = 72 / 25.4
HISTORY_STEPS = 20
HISTORY_BYTES = 512 * 1024 * 1024
PAGE_FILE_SUFFIXES = {'.pdf', '.png', '.jpg', '.jpeg'}


def image_page_document(data):
    """Turn an image into a PDF page, respecting EXIF orientation and alpha."""
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as original:
        oriented = ImageOps.exif_transpose(original)
        if oriented.mode not in {'1', 'L', 'LA', 'P', 'RGB', 'RGBA', 'I', 'I;16'}:
            oriented = oriented.convert('RGB')
        with io.BytesIO() as buffer:
            oriented.save(buffer, format='PNG')
            pixels = buffer.getvalue()
        width, height = oriented.size
    # 96 px/in gives predictable page dimensions for screenshots and clipboard
    # images. Large images remain within PDF's supported page dimensions.
    scale = min(.75, 14400 / max(width, height))
    result = pymupdf.open()
    try:
        page = result.new_page(width=max(1, width * scale), height=max(1, height * scale))
        page.insert_image(page.rect, stream=pixels)
        return result
    except Exception:
        result.close()
        raise


def open_page_file(path, password=None):
    suffix = Path(path).suffix.lower()
    if suffix not in PAGE_FILE_SUFFIXES:
        raise ValueError('PDF, PNG, JPG 파일을 선택해 주세요.')
    if suffix == '.pdf':
        doc = _open_pdf(path, password)
        try:
            _require_permission(doc, pymupdf.PDF_PERM_COPY)
            return doc
        except Exception:
            doc.close()
            raise
    return image_page_document(Path(path).read_bytes())


class PasswordRequired(ValueError):
    """The PDF needs a valid opening password."""

    def __init__(self, path: str = "", incorrect: bool = False):
        self.path = path
        self.incorrect = incorrect
        super().__init__("암호가 맞지 않습니다." if incorrect else "PDF 암호를 입력해 주세요.")


def _open_pdf(source: str | Path | bytes, password: str | None = None) -> pymupdf.Document:
    # Reading into memory releases the source file handle, allowing an atomic
    # replace on Windows while the document remains open in the application.
    data = source if isinstance(source, (bytes, bytearray)) else Path(source).read_bytes()
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        if not doc.is_pdf:
            raise ValueError("표준 PDF 파일을 선택해 주세요.")
        auth = 0
        if doc.needs_pass or password is not None:
            auth = doc.authenticate(password or "")
            if not auth:
                raise PasswordRequired("" if isinstance(source, (bytes, bytearray)) else str(source), password is not None)
        if doc.page_count < 1:
            raise ValueError("페이지가 없는 PDF입니다.")
        doc._adf_password = password or ""
        doc._adf_owner_authenticated = bool(auth & 4)
        return doc
    except BaseException:
        doc.close()
        raise


def _allowed(doc: pymupdf.Document, permission: int) -> bool:
    return bool(getattr(doc, "_adf_owner_authenticated", False) or doc.permissions & permission)


def _require_permission(doc: pymupdf.Document, permission: int = pymupdf.PDF_PERM_MODIFY) -> None:
    if not _allowed(doc, permission):
        raise PermissionError("이 PDF는 소유자가 편집 또는 추출을 제한했습니다. 소유자 암호로 다시 열어 주세요.")


def _indices(values: Iterable[int], count: int) -> list[int]:
    result = list(values)
    if not result:
        raise ValueError("페이지를 한 개 이상 선택해 주세요.")
    if any(isinstance(i, bool) or not isinstance(i, int) or i < 0 or i >= count for i in result):
        raise ValueError(f"페이지는 1~{count} 범위 안에서 선택해 주세요.")
    if len(result) != len(set(result)):
        raise ValueError("같은 페이지가 중복 선택되었습니다.")
    return result


def _output_path(path: str | Path) -> Path:
    result = Path(path).expanduser().resolve()
    if result.suffix.lower() != ".pdf":
        raise ValueError("저장 파일의 확장자는 .pdf여야 합니다.")
    if not result.parent.is_dir():
        raise FileNotFoundError("저장할 폴더가 없습니다.")
    return result


def _atomic_save(doc: pymupdf.Document, path: str | Path, *, overwrite: bool) -> str:
    destination = _output_path(path)
    if not overwrite and destination.exists():
        raise FileExistsError(f"같은 이름의 파일이 이미 있습니다: {destination.name}")
    fd, temporary = tempfile.mkstemp(prefix=".adf-", suffix=".pdf", dir=destination.parent)
    os.close(fd)
    try:
        doc.save(temporary, garbage=4, deflate=True, use_objstms=1, encryption=pymupdf.PDF_ENCRYPT_KEEP)
        with open(temporary, "r+b") as stream:
            os.fsync(stream.fileno())
        if overwrite:
            os.replace(temporary, destination)
        else:
            # Hard linking a fully written sibling prevents a check/write race
            # from overwriting a concurrently created output on either OS.
            os.link(temporary, destination)
        return str(destination)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _copy_source(source, *, permission: int | None = None) -> Iterator[pymupdf.Document]:
    if isinstance(source, PdfDocument):
        original = source._require_doc()
    elif isinstance(source, pymupdf.Document):
        original = source
    else:
        original = None
    if original is not None:
        if permission is not None:
            _require_permission(original, permission)
        doc = _open_pdf(original.tobytes(garbage=1, encryption=pymupdf.PDF_ENCRYPT_KEEP), getattr(original, "_adf_password", ""))
        doc._adf_owner_authenticated = getattr(original, "_adf_owner_authenticated", False)
    else:
        doc = _open_pdf(source)
    try:
        if permission is not None:
            _require_permission(doc, permission)
        yield doc
    finally:
        doc.close()


@dataclass
class _Snapshot:
    path: Path
    state: int
    size: int


class PdfDocument:
    """Editable document with transactional changes and bounded disk history."""

    def __init__(self):
        self.doc: pymupdf.Document | None = None
        self.path: str | None = None
        self.revision = 0
        self._state = 0
        self._saved_state = 0
        self._serial = 0
        self._undo: list[_Snapshot] = []
        self._redo: list[_Snapshot] = []
        self._history_dir: tempfile.TemporaryDirectory | None = None
        self._page_sizes = {}
        self._page_sizes_revision = -1
        self.opening_preview = None

    @property
    def page_count(self) -> int:
        return self.doc.page_count if self.doc is not None else 0

    @property
    def dirty(self) -> bool:
        return self.doc is not None and self._state != self._saved_state

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def editable(self) -> bool:
        return self.doc is not None and _allowed(self.doc, pymupdf.PDF_PERM_MODIFY)

    @property
    def permissions(self) -> int:
        return self.doc.permissions if self.doc is not None else 0

    def _require_doc(self) -> pymupdf.Document:
        if self.doc is None or self.doc.is_closed:
            raise ValueError("먼저 PDF 파일을 열어 주세요.")
        return self.doc

    def open(self, path: str | Path, password: str | None = None) -> None:
        new_doc = _open_pdf(path, password)
        self.close()
        self.doc = new_doc
        self.path = str(Path(path).expanduser().resolve())
        self._state = self._saved_state = self._serial = 0
        self.revision += 1

    def open_prepared(self, data, path, password, sizes):
        """Adopt the bytes checked by the loader; retain original permissions."""
        new_doc = _open_pdf(data, password)
        if len(sizes) != new_doc.page_count:
            new_doc.close()
            raise ValueError('준비한 페이지 정보와 PDF가 일치하지 않습니다.')
        self.close()
        self.doc = new_doc
        self.path = str(Path(path).resolve())
        self._state = self._saved_state = self._serial = 0
        self.revision += 1
        self._page_sizes = {i: tuple(size) for i, size in enumerate(sizes)}
        self._page_sizes_revision = self.revision

    def page_size(self, index):
        if self._page_sizes_revision != self.revision:
            self._page_sizes.clear()
            self._page_sizes_revision = self.revision
        if index not in self._page_sizes:
            rect = self._require_doc()[index].rect
            self._page_sizes[index] = (rect.width, rect.height)
        return self._page_sizes[index]

    def close(self) -> None:
        if self.doc is not None:
            self.doc.close()
            self.doc = None
        self.path = None
        self._undo.clear()
        self._redo.clear()
        if self._history_dir is not None:
            self._history_dir.cleanup()
            self._history_dir = None
        self._state = self._saved_state = 0
        self.revision += 1
        self._page_sizes.clear()
        self.opening_preview = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _snapshot(self) -> _Snapshot:
        doc = self._require_doc()
        if self._history_dir is None:
            self._history_dir = tempfile.TemporaryDirectory(prefix="adf-history-")
        fd, path = tempfile.mkstemp(suffix=".pdf", dir=self._history_dir.name)
        os.close(fd)
        try:
            # Discard unreachable objects without renumbering live xrefs. Some
            # PDFs retain malformed, unused dictionaries that the default save
            # tries to serialize, failing before the next edit can begin.
            doc.save(path, garbage=1, deflate=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
            return _Snapshot(Path(path), self._state, Path(path).stat().st_size)
        except BaseException:
            Path(path).unlink(missing_ok=True)
            raise

    def _restore(self, snapshot: _Snapshot) -> None:
        original = self._require_doc()
        replacement = _open_pdf(snapshot.path, getattr(original, "_adf_password", ""))
        replacement._adf_owner_authenticated = getattr(original, "_adf_owner_authenticated", False)
        self.doc = replacement
        original.close()
        self._state = snapshot.state

    def _trim_history(self) -> None:
        while len(self._undo) > HISTORY_STEPS or (
            len(self._undo) > 1 and sum(s.size for s in self._undo + self._redo) > HISTORY_BYTES
        ):
            self._undo.pop(0).path.unlink(missing_ok=True)

    @contextmanager
    def _edit(self):
        _require_permission(self._require_doc())
        before = self._snapshot()
        try:
            yield self.doc
        except BaseException:
            self._restore(before)
            before.path.unlink(missing_ok=True)
            raise
        else:
            self._undo.append(before)
            for snapshot in self._redo:
                snapshot.path.unlink(missing_ok=True)
            self._redo.clear()
            self._serial += 1
            self._state = self._serial
            self.revision += 1
            self._trim_history()

    def undo(self) -> bool:
        if not self._undo:
            return False
        current = self._snapshot()
        try:
            self._restore(self._undo[-1])
        except BaseException:
            current.path.unlink(missing_ok=True)
            raise
        self._undo.pop().path.unlink(missing_ok=True)
        self._redo.append(current)
        self.revision += 1
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        current = self._snapshot()
        try:
            self._restore(self._redo[-1])
        except BaseException:
            current.path.unlink(missing_ok=True)
            raise
        self._redo.pop().path.unlink(missing_ok=True)
        self._undo.append(current)
        self.revision += 1
        self._trim_history()
        return True

    def save(self, path: str | Path) -> str:
        """Atomically save to the destination explicitly selected by the user."""
        with _copy_source(self) as output:
            output.subset_fonts()
            saved = _atomic_save(output, path, overwrite=True)
        self.path = saved
        self._saved_state = self._state
        return saved

    def add_ink(self, page_index, paths, color, width, kind='pencil', pressures=None):
        _indices([page_index], self.page_count)
        if not math.isfinite(width) or not .2 <= width <= 12:
            raise ValueError('펜 굵기는 0.2~12.0 pt 범위여야 합니다.')
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('펜 색이 올바르지 않습니다.')
        strokes = []
        bounds = self.doc[page_index].rect * self.doc[page_index].derotation_matrix
        for path in paths:
            stroke = [pymupdf.Point(point) for point in path]
            if not stroke:
                continue
            if any(not math.isfinite(p.x) or not math.isfinite(p.y) or
                   not (bounds.x0 <= p.x <= bounds.x1 and bounds.y0 <= p.y <= bounds.y1) for p in stroke):
                raise ValueError('펜 좌표가 페이지 범위를 벗어났습니다.')
            if len(stroke) == 1:
                p = stroke[0]
                stroke.append(pymupdf.Point(p.x+.01 if p.x+.01 <= bounds.x1 else p.x-.01, p.y))
            strokes.append([tuple(point) for point in stroke])
        if not strokes:
            return
        if pressures is not None:
            pressures = [list(values) for values, path in zip(pressures, paths) if path]
            for values, path in zip(pressures, strokes):
                if len(values) == 1 and len(path) == 2:
                    values.append(values[0])
        with self._edit() as doc:
            from .ink import write_ink
            return write_ink(doc[page_index], strokes, color, width, kind, pressures)

    def transform_ink(self, page_index, xref, paths, frame):
        from .ink import read_ink, write_ink
        _indices([page_index], self.page_count)
        page = self.doc[page_index]
        original = read_ink(page.load_annot(xref))
        if original is None or len(paths) != len(original['paths']) or any(len(a) != len(b) for a, b in zip(paths, original['paths'])):
            raise ValueError('선택한 필기를 찾을 수 없습니다.')
        bounds = page.rect * page.derotation_matrix
        if not (len(frame) == 5 and all(math.isfinite(v) for v in frame) and frame[2] > 0 and frame[3] > 0):
            raise ValueError('필기 크기가 올바르지 않습니다.')
        if any(not math.isfinite(x) or not math.isfinite(y) or not
               (bounds.x0 <= x <= bounds.x1 and bounds.y0 <= y <= bounds.y1) for path in paths for x, y in path):
            raise ValueError('필기가 페이지 범위를 벗어났습니다.')
        with self._edit() as doc:
            page = doc[page_index]
            page.delete_annot(page.load_annot(xref))
            return write_ink(page, paths, original['color'], original['width'], original['kind'],
                             original['pressures'], frame, original['uid'], original.get('weights'))

    def erase_ink(self, page_index, sweeps, radius):
        from .eraser import erasable, erase_sweep
        from .ink import read_ink, write_ink
        _require_permission(self._require_doc())
        _indices([page_index], self.page_count)
        if not math.isfinite(radius) or not 2 <= radius <= 36:
            raise ValueError('지우개 크기가 올바르지 않습니다.')
        sweeps = [(tuple(a), tuple(b)) for a, b in sweeps]
        if any(len(p) != 2 or any(not math.isfinite(v) for v in p) for sweep in sweeps for p in sweep):
            raise ValueError('지우개 좌표가 올바르지 않습니다.')
        page = self.doc[page_index]
        replacements = {}
        for annotation in page.annots() or []:
            if not erasable(annotation):
                continue
            data = read_ink(annotation)
            if data is None:
                continue
            for start, end in sweeps:
                updated = erase_sweep(data, start, end, radius)
                if updated is not None:
                    data = replacements[annotation.xref] = updated
        if not replacements:
            return False
        with self._edit() as doc:
            page = doc[page_index]
            for xref, data in replacements.items():
                original = page.load_annot(xref)
                info, flags, opacity = original.info, original.flags, original.opacity
                page.delete_annot(original)
                if data['paths']:
                    # Keep author/comment and visibility/printing properties.
                    write_ink(page, **data, info=info, flags=flags, opacity=opacity)
        return True

    def delete_ink(self, page_index, xref):
        _indices([page_index], self.page_count)
        with self._edit() as doc:
            page = doc[page_index]
            annotation = page.load_annot(xref)
            if annotation.type[0] != pymupdf.PDF_ANNOT_INK:
                raise ValueError('선택한 필기가 아닙니다.')
            page.delete_annot(annotation)

    def tobytes(self) -> bytes:
        return self._require_doc().tobytes(garbage=1, encryption=pymupdf.PDF_ENCRYPT_KEEP)

    def rotate(self, indices: Iterable[int], degrees: int) -> None:
        pages = _indices(indices, self.page_count)
        if isinstance(degrees, bool) or not isinstance(degrees, int) or degrees % 90:
            raise ValueError("회전 각도는 90도의 배수여야 합니다.")
        if degrees % 360 == 0:
            return
        with self._edit() as doc:
            for index in pages:
                page = doc[index]
                page.set_rotation((page.rotation + degrees) % 360)

    def delete(self, indices: Iterable[int]) -> None:
        pages = _indices(indices, self.page_count)
        if len(pages) >= self.page_count:
            raise ValueError("PDF에는 한 페이지 이상 남아 있어야 합니다.")
        with self._edit() as doc:
            doc.delete_pages(pages)

    def reorder(self, order: Iterable[int]) -> None:
        pages = _indices(order, self.page_count)
        if sorted(pages) != list(range(self.page_count)):
            raise ValueError("모든 페이지가 정확히 한 번씩 포함되어야 합니다.")
        if pages == list(range(self.page_count)):
            return
        with self._edit() as doc:
            doc.select(pages)

    def insert_blank(self, index: int, width: float | None = None, height: float | None = None) -> None:
        doc = self._require_doc()
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= doc.page_count:
            raise ValueError("빈 페이지를 넣을 위치가 잘못되었습니다.")
        adjacent = doc[min(index, doc.page_count - 1)].rect
        width = adjacent.width if width is None else float(width)
        height = adjacent.height if height is None else float(height)
        if not all(math.isfinite(v) and 1 <= v <= 14400 for v in (width, height)):
            raise ValueError("페이지 크기는 1~14,400pt 범위여야 합니다.")
        with self._edit() as doc:
            doc.new_page(pno=index, width=width, height=height)

    def replace(self, index: int, path: str | Path, indices: Iterable[int], password: str | None = None) -> None:
        _indices([index], self.page_count)
        with _open_pdf(path, password) as source:
            _require_permission(source, pymupdf.PDF_PERM_COPY)
            selected = _indices(indices, source.page_count)
            source.select(selected)
            with self._edit() as doc:
                # Insert first, so replacing a one-page PDF never creates an
                # invalid transient zero-page document.
                doc.insert_pdf(source, start_at=index)
                doc.delete_page(index + source.page_count)

    def insert_pdf(self, path: str | Path, index: int | None = None, password: str | None = None) -> None:
        index = self.page_count if index is None else index
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= self.page_count:
            raise ValueError("PDF를 넣을 위치가 잘못되었습니다.")
        with _open_pdf(path, password) as source:
            _require_permission(source, pymupdf.PDF_PERM_COPY)
            with self._edit() as doc:
                doc.insert_pdf(source, start_at=index)

    def insert_pdfs(self, paths: Iterable[str | Path], index: int | None = None,
                    passwords=None, selected_ranges=None) -> None:
        """Insert several PDFs in list order as one undoable document edit."""
        sources = [str(Path(path).expanduser().resolve()) for path in paths]
        if not sources:
            raise ValueError("추가할 PDF를 한 개 이상 선택해 주세요.")
        index = self.page_count if index is None else index
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= self.page_count:
            raise ValueError("PDF를 넣을 위치가 잘못되었습니다.")
        if selected_ranges is None:
            selected_ranges = [None] * len(sources)
        if len(selected_ranges) != len(sources):
            raise ValueError("각 PDF의 페이지 선택 정보가 맞지 않습니다.")
        passwords = passwords or {}
        with ExitStack() as stack:
            opened = []
            for path, pages in zip(sources, selected_ranges):
                source = stack.enter_context(_open_pdf(path, passwords.get(path)))
                _require_permission(source, pymupdf.PDF_PERM_COPY)
                if pages is not None:
                    source.select(_indices(pages, source.page_count))
                opened.append(source)
            with self._edit() as doc:
                cursor = index
                for source in opened:
                    doc.insert_pdf(source, start_at=cursor)
                    cursor += source.page_count

    def insert_files(self, paths, index, passwords=None, selected_ranges=None):
        """Insert all sources atomically in the chosen order, with one undo step."""
        paths = [str(Path(path).resolve()) for path in paths]
        if not paths:
            raise ValueError('추가할 파일을 선택해 주세요.')
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= self.page_count:
            raise ValueError('페이지를 넣을 위치가 잘못되었습니다.')
        ranges = [None] * len(paths) if selected_ranges is None else selected_ranges
        if len(ranges) != len(paths):
            raise ValueError('각 파일의 페이지 선택 정보가 맞지 않습니다.')
        with ExitStack() as stack:
            opened = []
            for path, selected in zip(paths, ranges):
                source = stack.enter_context(open_page_file(path, (passwords or {}).get(path)))
                if selected is not None:
                    source.select(_indices(selected, source.page_count))
                opened.append(source)
            with self._edit() as doc:
                cursor = index
                for source in opened:
                    doc.insert_pdf(source, start_at=cursor)
                    cursor += source.page_count

    def insert_image_page(self, data, index):
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index <= self.page_count:
            raise ValueError('페이지를 넣을 위치가 잘못되었습니다.')
        with image_page_document(data) as source:
            with self._edit() as doc:
                doc.insert_pdf(source, start_at=index)

    def number_pages(self, indices: Iterable[int], start: int = 1, prefix: str = "", suffix: str = "",
                     digits: int = 1, position: str = "bottom-center", margin_x: float = 12,
                     margin_y: float = 12, font_size: float = 11, color=(0, 0, 0),
                     fontfile: str | None = None, zero_pad: bool = False, mirror: bool = False,
                     anchor_page: int = 0, start_right: bool = False) -> None:
        from .page_layout import numbering_label, numbering_position
        selected = _indices(indices, self.page_count)
        _indices([anchor_page], self.page_count)
        if isinstance(start, bool) or not isinstance(start, int) or start < 0:
            raise ValueError("시작 번호는 0 이상의 정수여야 합니다.")
        if not isinstance(digits, int) or not 1 <= digits <= 12:
            raise ValueError("자릿수는 1~12로 지정해 주세요.")
        with self._edit() as doc:
            for offset, index in enumerate(selected):
                text = numbering_label(start, offset, len(selected), prefix=prefix, suffix=suffix,
                                       digits=digits, zero_pad=zero_pad)
                actual_position = numbering_position(position, index, mirror=mirror, anchor_page=anchor_page,
                                                     start_right=start_right)
                number_page(doc[index], text, position=actual_position, margin_x=margin_x, margin_y=margin_y,
                            font_size=font_size, color=color, fontfile=fontfile)

    def numbered_pages(self) -> list[int]:
        """Pages that still carry a number recorded by number_page."""
        doc = self._require_doc()
        return [index for index in range(doc.page_count)
                if doc.xref_get_key(doc.page_xref(index), NUMBER_RECORD)[0] == "string"]

    def remove_page_numbers(self, indices: Iterable[int]) -> tuple[list[int], list[int]]:
        """Remove recorded numbers as one undoable edit; returns removed and kept pages."""
        selected = _indices(indices, self.page_count)
        removed, kept = [], []
        with self._edit() as doc:
            for index in selected:
                try:
                    (removed if remove_page_number(doc[index]) else kept).append(index)
                except ValueError:
                    kept.append(index)
            if not removed:
                raise ValueError("지울 수 있는 페이지 번호를 찾지 못했습니다. 번호를 직접 고쳤거나 주변 글자가 바뀌었을 수 있습니다.")
        return removed, kept

    def add_image(self, page_index: int, rect, data: bytes, rotate: int = 0) -> int:
        _indices([page_index], self.page_count)
        target = _valid_rect(rect)
        if not data:
            raise ValueError("삽입할 이미지가 비어 있습니다.")
        if isinstance(rotate, bool) or not isinstance(rotate, int) or rotate % 90:
            raise ValueError("이미지 회전 각도는 90도의 배수여야 합니다.")
        with self._edit() as doc:
            return doc[page_index].insert_image(target, stream=data, rotate=rotate % 360,
                                                keep_proportion=True, overlay=True)

    def remove_image(self, page_index: int, xref: int) -> None:
        """Remove all uses of an image on this page, leaving other pages intact."""
        _indices([page_index], self.page_count)
        doc = self._require_doc()
        source_images = doc[page_index].get_images(full=True)
        if xref not in {image[0] for image in source_images}:
            raise ValueError("선택한 페이지에 해당 이미지가 없습니다.")
        with self._edit() as doc:
            with pymupdf.open() as isolated:
                isolated.insert_pdf(doc, from_page=page_index, to_page=page_index)
                copied_images = isolated[0].get_images(full=True)
                if len(copied_images) != len(source_images):
                    raise ValueError("이미지를 안전하게 분리하지 못했습니다.")
                matches = []
                # insert_pdf preserves resource names and dictionary order.
                # Match those resources, verifying the underlying bytes too;
                # comparing pixel bytes alone could remove a second stamp with
                # the same colour pixels but a different transparency mask.
                for original, copied in zip(source_images, copied_images):
                    if (original[2:9] != copied[2:9]
                            or doc.xref_stream_raw(original[0]) != isolated.xref_stream_raw(copied[0])):
                        raise ValueError("이미지를 안전하게 분리하지 못했습니다.")
                    if original[0] == xref:
                        matches.append(copied[0])
                if not matches:
                    raise ValueError("이미지를 안전하게 분리하지 못했습니다.")
                for item_xref in set(matches):
                    isolated[0].delete_image(item_xref)
                original_page_xref = doc.page_xref(page_index)
                doc.insert_pdf(isolated, links=False, annots=False, widgets=False)
                imported_xref = doc.page_xref(doc.page_count - 1)
                # Retain the original page object (and incoming links, geometry,
                # annotations and widgets), replacing only its visual resources.
                for key in ("Resources", "Contents"):
                    _, value = doc.xref_get_key(imported_xref, key)
                    doc.xref_set_key(original_page_xref, key, value)
                doc.delete_page(doc.page_count - 1)

    def replace_text(self, page_index: int, rect, text: str, font_size: float = 11,
                     color=(0, 0, 0), fontfile: str | None = None, fit: bool = True,
                     target_rect=None, source_rects=None, lineheight: float | None = None,
                     fontbuffer: bytes | None = None) -> None:
        """Replace a selected paragraph in memory as a single undoable edit.

        Explicit newlines are retained; longer lines wrap to the target width.
        Fit reduces the requested size only as needed. A layout that cannot fit
        at 1pt raises before removing text, and failed insertions roll back.
        source_rects limits removal to the selected glyphs rather than all text
        inside the paragraph's enclosing rectangle.
        """
        _indices([page_index], self.page_count)
        with self._edit() as doc:
            replace_page_text(doc[page_index], rect, text, font_size=font_size, color=color,
                              fontfile=fontfile, fit=fit, target_rect=target_rect,
                              source_rects=source_rects, lineheight=lineheight, fontbuffer=fontbuffer)

    def search(self, query: str) -> list[tuple[int, pymupdf.Rect]]:
        if not query.strip():
            return []
        return [(page.number, rect) for page in self._require_doc() for rect in page.search_for(query)]


def replace_page_text(page: pymupdf.Page, rect, text: str, font_size: float = 11,
                      color=(0, 0, 0), fontfile: str | None = None, fit: bool = True,
                      target_rect=None, source_rects=None, lineheight: float | None = None,
                      fontbuffer: bytes | None = None) -> None:
    """Shared PDF rendering for the live preview and the committed edit.

    Call on a disposable preview page, or inside PdfDocument._edit: an insertion
    failure after redaction requires discarding the preview or rolling back.
    No file is saved here. Validation and fitting precede any page changes.
    """
    source = _valid_rect(rect)
    target = _valid_rect(target_rect if target_rect is not None else rect)
    sources = [source] if source_rects is None else [_valid_rect(item) for item in source_rects]
    if not sources:
        raise ValueError("수정할 원본 텍스트 영역이 없습니다.")
    if any(not (source + (-.5, -.5, .5, .5)).contains(item) for item in sources):
        raise ValueError("선택한 텍스트 밖의 영역은 수정할 수 없습니다.")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    _validate_text_style(font_size, color)
    font, name, file = _font(text, fontfile, fontbuffer)
    # The PDF's built-in Korean CJK font uses fixed full-em advances, including
    # Latin and space glyphs. Font('korea') reports the fallback font's varying
    # advances instead, which would underestimate width and permit clipping.
    metrics = _BuiltinCjkMetrics() if name == "korea" and file is None else font
    if lineheight is not None and (not math.isfinite(lineheight) or not .5 <= lineheight <= 5):
        raise ValueError("줄 간격은 글자 크기의 0.5~5배로 지정해 주세요.")
    page_bounds = page.rect * page.derotation_matrix
    if not (page_bounds + (-.01, -.01, .01, .01)).contains(target):
        raise ValueError("텍스트 상자가 페이지 밖으로 나갑니다. 페이지 안으로 옮겨 주세요.")
    if any(annot.type[0] == pymupdf.PDF_ANNOT_REDACT for annot in page.annots() or ()):
        raise ValueError("이 페이지에 기존 교정 표시가 있습니다. 다른 PDF 편집기에서 먼저 처리해 주세요.")
    if text:
        font_size, lines, line_step = _fit_text(text, metrics, font_size, target, fit, lineheight)
    for area in sources:
        page.add_redact_annot(area, fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0, text=0)
    if text:
        if fontbuffer:
            page.insert_font(fontname=name, fontbuffer=fontbuffer)
        for index, line in enumerate(lines):
            if line:
                written = page.insert_text(
                    (target.x0, target.y0 + metrics.ascender * font_size + index * line_step), line,
                    fontsize=font_size, fontname=name, fontfile=file, color=color, overlay=True)
                if written != 1:
                    raise ValueError("텍스트를 모두 넣지 못했습니다. 상자를 넓혀 주세요.")


class _BuiltinCjkMetrics:
    ascender = 1.0
    descender = -.2

    @staticmethod
    def char_lengths(text, fontsize):
        return [fontsize] * len(text)


def _valid_rect(rect) -> pymupdf.Rect:
    result = pymupdf.Rect(rect)
    if result.is_empty or result.is_infinite or not all(math.isfinite(v) for v in result):
        raise ValueError("유효한 페이지 영역을 선택해 주세요.")
    return result


def _validate_text_style(font_size, color) -> None:
    if not math.isfinite(font_size) or not 1 <= font_size <= 288:
        raise ValueError("글자 크기는 1~288pt로 지정해 주세요.")
    if len(color) != 3 or any(not math.isfinite(v) or not 0 <= v <= 1 for v in color):
        raise ValueError("색은 0~1 범위의 RGB 값으로 지정해 주세요.")


def _wrapped_text(text, font, size, width):
    """Word-wrap with a character fallback for long words and CJK text."""
    result = []
    for explicit_line in text.expandtabs(4).split("\n"):
        if not explicit_line:
            result.append("")
            continue
        advances = font.char_lengths(explicit_line, fontsize=size)
        start = 0
        while start < len(explicit_line):
            used, end, space = 0.0, start, None
            while end < len(explicit_line) and used + advances[end] <= width + .00001:
                used += advances[end]
                if explicit_line[end].isspace():
                    space = end
                end += 1
            if end == start:
                return None
            if end < len(explicit_line) and space is not None and space > start:
                end = space
            result.append(explicit_line[start:end].rstrip())
            start = end
            while start < len(explicit_line) and explicit_line[start].isspace():
                start += 1
    return result


def _fit_text(text, font, size, target, fit, lineheight):
    factor = lineheight if lineheight is not None else max(font.ascender - font.descender, 1.2)

    def layout(candidate):
        lines = _wrapped_text(text, font, candidate, target.width)
        if lines is None:
            return None
        height = (font.ascender - font.descender + (len(lines) - 1) * factor) * candidate
        return lines if height <= target.height + .00001 else None

    lines = layout(size)
    if lines is not None:
        return size, lines, size * factor
    if not fit or layout(1) is None:
        raise ValueError("선택한 영역에 글자가 들어가지 않습니다. 상자를 넓히거나 글자 크기를 줄여 주세요.")
    low, high = 1.0, size
    for _ in range(22):
        middle = (low + high) / 2
        if layout(middle) is None:
            high = middle
        else:
            low = middle
    return low, layout(low), low * factor


def _font(text: str, fontfile: str | None = None, fontbuffer: bytes | None = None):
    if fontbuffer:
        font = pymupdf.Font(fontbuffer=fontbuffer)
        name, file = 'ADF' + hashlib.sha256(fontbuffer).hexdigest()[:12], None
    elif fontfile:
        file = str(Path(fontfile).resolve())
        if not Path(file).is_file():
            raise FileNotFoundError("선택한 글꼴 파일을 찾을 수 없습니다.")
        font = pymupdf.Font(fontfile=file)
        name = "ADF" + hashlib.sha256(file.encode()).hexdigest()[:12]
    else:
        file = None
        name = "helv" if text.isascii() else "korea"
        font = pymupdf.Font(name)
    missing = [char for char in text if not char.isspace() and not font.has_glyph(ord(char))]
    if missing:
        raise ValueError(f"선택한 글꼴에 '{missing[0]}' 글자가 없습니다. 다른 글꼴을 선택해 주세요.")
    return font, name, file


NUMBER_RECORD = "ADFPageNumber"


def _same_rect(first, second) -> bool:
    return all(abs(a - b) < .5 for a, b in zip(first, second))


def page_number_record(page: pymupdf.Page):
    """The label and position that number_page last recorded on this page, if any."""
    kind, value = page.parent.xref_get_key(page.xref, NUMBER_RECORD)
    if kind != "string":
        return None
    try:
        record = json.loads(value)
        return str(record["text"]), pymupdf.Rect(record["rect"])
    except (ValueError, KeyError, TypeError):
        return None


def _record_page_number(page: pymupdf.Page, text: str, rect) -> None:
    record = json.dumps({"text": text, "rect": list(rect)})
    page.parent.xref_set_key(page.xref, NUMBER_RECORD, "<" + record.encode("ascii").hex() + ">")


def copy_page_number_record(source: pymupdf.Page, target: pymupdf.Page) -> None:
    """insert_pdf leaves the record out; keep it with a copied page."""
    record = page_number_record(source)
    if record:
        _record_page_number(target, *record)


def remove_page_number(page: pymupdf.Page) -> bool:
    """Remove the label number_page recorded, only while it is still intact.

    Page numbers are ordinary page text. The recorded text must still be the
    only text at the recorded position. Numbers added by other programs or by
    earlier ADF versions carry no record and are never touched.
    """
    record = page_number_record(page)
    if record is None:
        return False
    text, rect = record
    hits = [hit for hit in page.search_for(text) if _same_rect(hit, rect)]
    if not hits:
        # The label was edited or flattened; nothing of it remains to remove.
        page.parent.xref_set_key(page.xref, NUMBER_RECORD, "null")
        return False
    if len(hits) != 1 or page.get_text("text", clip=hits[0]).split() != text.split():
        raise ValueError(f"{page.number + 1}쪽의 기존 페이지 번호 주변 글자가 바뀌어 안전하게 고치지 못했습니다.")
    if any(annot.type[0] == pymupdf.PDF_ANNOT_REDACT for annot in page.annots() or ()):
        raise ValueError("이 페이지에 기존 교정 표시가 있습니다. 다른 PDF 편집기에서 먼저 처리해 주세요.")
    page.add_redact_annot(hits[0], fill=False, cross_out=False)
    page.apply_redactions(images=0, graphics=0, text=0)
    page.parent.xref_set_key(page.xref, NUMBER_RECORD, "null")
    return True


def number_page(page: pymupdf.Page, text: str, *, position: str = "bottom-center", margin_x: float = 12,
                margin_y: float = 12, font_size: float = 11, color=(0, 0, 0), fontfile: str | None = None) -> None:
    """Draw one label; shared by the live preview and final document editing."""
    _validate_text_style(font_size, color)
    if any(not math.isfinite(v) or v < 0 for v in (margin_x, margin_y)):
        raise ValueError("여백은 0 이상의 숫자로 지정해 주세요.")
    if position not in {f"{v}-{h}" for v in ("top", "bottom") for h in ("left", "center", "right")}:
        raise ValueError("번호 위치가 잘못되었습니다.")
    if "\n" in text or "\r" in text:
        raise ValueError("페이지 번호는 한 줄로 입력해 주세요.")
    font, name, file = _font(text, fontfile)
    top, horizontal = position.split("-")
    width, height = page.rect.width, page.rect.height
    mx, my = margin_x * MM_TO_PT, margin_y * MM_TO_PT
    text_width = font.text_length(text, fontsize=font_size)
    if mx * 2 + text_width > width or my * 2 + (font.ascender - font.descender) * font_size > height:
        raise ValueError("페이지 밖으로 번호가 나갑니다. 여백 또는 글자 크기를 줄여 주세요.")
    x = {"left": mx, "center": (width - text_width) / 2, "right": width - mx - text_width}[horizontal]
    y = my + font.ascender * font_size if top == "top" else height - my + font.descender * font_size
    point = pymupdf.Point(x, y) * page.derotation_matrix
    # Adding a number again edits it: the label recorded here earlier is replaced.
    remove_page_number(page)
    before = page.search_for(text)
    page.insert_text(point, text, fontsize=font_size, fontname=name, fontfile=file,
                     color=color, rotate=page.rotation, overlay=True)
    # Record the new label so that it can later be edited or removed on its own.
    added = [hit for hit in page.search_for(text) if not any(_same_rect(hit, old) for old in before)]
    if len(added) == 1:
        _record_page_number(page, text, added[0])


def parse_ranges(text: str, page_count: int) -> list[list[int]]:
    """Parse e.g. '1-3, 7, 12-끝' into independently exportable page groups."""
    if isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1:
        raise ValueError("페이지 수가 올바르지 않습니다.")
    value = text.strip().lower()
    if value in {"전체", "all"}:
        return [list(range(page_count))]
    if not value:
        raise ValueError("페이지 범위를 입력해 주세요. 예: 1-3, 7, 12-끝")
    value = re.sub(r"[~–—]", "-", value)
    groups = []
    for token in re.split(r"[,;]", value):
        match = re.fullmatch(r"\s*(\d+|끝|end|last)\s*(?:-\s*(\d+|끝|end|last)\s*)?", token)
        if not match:
            raise ValueError(f"페이지 범위를 확인해 주세요: {token.strip()}")
        def number(part):
            return page_count if part in {"끝", "end", "last"} else int(part)
        first = number(match[1])
        last = number(match[2]) if match[2] is not None else first
        if not 1 <= first <= last <= page_count:
            raise ValueError(f"페이지 범위는 1~{page_count} 사이여야 합니다: {token.strip()}")
        groups.append(list(range(first - 1, last)))
    return groups


def merge_pdfs(paths: Iterable[str | Path], output: str | Path, passwords=None, selected_ranges=None) -> str:
    sources = list(paths)
    if not sources:
        raise ValueError("병합할 PDF를 추가해 주세요.")
    target = _output_path(output)
    if target in {Path(path).resolve() for path in sources}:
        raise ValueError("원본과 다른 이름으로 병합 결과를 저장해 주세요.")
    if passwords is not None and not isinstance(passwords, dict) and len(passwords) != len(sources):
        raise ValueError("파일 수와 암호 수가 맞지 않습니다.")
    if selected_ranges is not None and len(selected_ranges) != len(sources):
        raise ValueError("파일 수와 페이지 범위 수가 맞지 않습니다.")
    with pymupdf.open() as result:
        toc = []
        for index, path in enumerate(sources):
            password = None
            if isinstance(passwords, dict):
                password = passwords.get(str(path), passwords.get(path))
            elif passwords is not None:
                password = passwords[index]
            with _open_pdf(path, password) as source:
                _require_permission(source, pymupdf.PDF_PERM_COPY)
                selection = selected_ranges[index] if selected_ranges is not None else None
                if selection is not None:
                    source.select(_indices(selection, source.page_count))
                offset = result.page_count
                result.insert_pdf(source)
                toc.append([1, Path(path).stem, offset + 1])
                last_level = 1
                for level, title, page in source.get_toc():
                    if page > 0:
                        # A selected range may remove an outline parent while
                        # retaining a child; keep the merged hierarchy valid.
                        merged_level = min(level + 1, last_level + 1)
                        toc.append([merged_level, title, offset + page])
                        last_level = merged_level
        result.set_toc(toc)
        result.set_metadata({"producer": "ADF · PyMuPDF", "title": target.stem})
        return _atomic_save(result, target, overwrite=False)


def extract_pdf(doc_or_bytes, pages: Iterable[int], output: str | Path) -> str:
    target = _output_path(output)
    with _copy_source(doc_or_bytes, permission=pymupdf.PDF_PERM_COPY) as source:
        source.select(_indices(pages, source.page_count))
        return _atomic_save(source, target, overwrite=False)


def split_pdf(doc_or_bytes, groups: Iterable[Iterable[int]], output_dir: str | Path, stem: str,
              include_remaining: bool = False) -> list[str]:
    directory = Path(output_dir).expanduser().resolve()
    if not directory.is_dir():
        raise FileNotFoundError("저장할 폴더를 선택해 주세요.")
    if not stem.strip() or re.search(r'[<>:"/\\|?*\x00-\x1f]', stem) or stem.endswith((".", " ")):
        raise ValueError("파일 이름에 사용할 수 없는 문자가 있습니다.")
    with _copy_source(doc_or_bytes, permission=pymupdf.PDF_PERM_COPY) as source:
        selections = [_indices(group, source.page_count) for group in groups]
        if not selections:
            raise ValueError("추출할 페이지를 선택해 주세요.")
        labels = []
        for group in selections:
            if len(group) == 1:
                labels.append(f"p{group[0] + 1:03d}")
            elif group == list(range(group[0], group[-1] + 1)):
                labels.append(f"p{group[0] + 1:03d}-{group[-1] + 1:03d}")
            else:
                labels.append(f"선택{len(labels) + 1:02d}")
        if include_remaining:
            used = {i for group in selections for i in group}
            remaining = [i for i in range(source.page_count) if i not in used]
            if remaining:
                selections.append(remaining)
                labels.append("나머지")
        targets = []
        for label in labels:
            target = directory / f"{stem}_{label}.pdf"
            duplicate = 2
            while target in targets:
                target = directory / f"{stem}_{label}_{duplicate}.pdf"
                duplicate += 1
            if target.exists():
                raise FileExistsError(f"같은 이름의 파일이 이미 있습니다: {target.name}")
            targets.append(target)
        written = []
        try:
            for group, target in zip(selections, targets):
                with _copy_source(source) as part:
                    part.select(group)
                    written.append(_atomic_save(part, target, overwrite=False))
        except BaseException:
            for path in written:
                Path(path).unlink(missing_ok=True)
            raise
        return written


def compress_pdf(doc_or_bytes, output: str | Path, mode: str = "images", dpi: int = 144,
                 quality: int = 75, target_mb: float | None = None) -> dict:
    if mode not in {"images", "raster"}:
        raise ValueError("압축 방식은 images 또는 raster여야 합니다.")
    if not isinstance(dpi, int) or not 72 <= dpi <= 300 or not isinstance(quality, int) or not 10 <= quality <= 100:
        raise ValueError("DPI는 72~300, JPEG 품질은 10~100 범위로 지정해 주세요.")
    if target_mb is not None and (not math.isfinite(target_mb) or target_mb <= 0):
        raise ValueError("목표 용량은 0보다 커야 합니다.")
    target = _output_path(output)
    original_path = doc_or_bytes.path if isinstance(doc_or_bytes, PdfDocument) else (
        str(doc_or_bytes) if isinstance(doc_or_bytes, (str, Path)) else None)
    if original_path and target == Path(original_path).resolve():
        raise ValueError("원본과 다른 이름으로 압축 결과를 저장해 주세요.")
    if target.exists():
        raise FileExistsError(f"같은 이름의 파일이 이미 있습니다: {target.name}")
    with _copy_source(doc_or_bytes, permission=pymupdf.PDF_PERM_MODIFY) as source:
        if isinstance(doc_or_bytes, bytes):
            original_size = len(doc_or_bytes)
        elif original_path and Path(original_path).is_file():
            original_size = Path(original_path).stat().st_size
        else:
            original_size = len(source.tobytes(garbage=1, encryption=pymupdf.PDF_ENCRYPT_KEEP))
        target_bytes = target_mb * 1024 * 1024 if target_mb is not None else None
        attempts = [(dpi, quality)]
        if target_bytes is not None:
            attempts += [(max(72, round(dpi * factor)), max(10, round(quality * factor))) for factor in (.8, .6, .4)]
        best_bytes = None
        best_settings = attempts[0]
        for candidate_dpi, candidate_quality in dict.fromkeys(attempts):
            with _copy_source(source) as candidate:
                if mode == "images":
                    candidate.rewrite_images(dpi_threshold=candidate_dpi + 1, dpi_target=candidate_dpi,
                                             quality=candidate_quality)
                else:
                    # Retain the encrypted document container so passwords and
                    # permissions survive. Replace only the page artwork.
                    candidate.bake()
                    for index in range(candidate.page_count):
                        page = candidate[index]
                        pixmap = page.get_pixmap(dpi=candidate_dpi, colorspace=pymupdf.csRGB, alpha=False)
                        jpeg = pixmap.tobytes("jpeg", jpg_quality=candidate_quality)
                        original_xref = page.xref
                        with pymupdf.open() as raster:
                            raster_page = raster.new_page(width=page.rect.width, height=page.rect.height)
                            raster_page.insert_image(raster_page.rect, stream=jpeg)
                            candidate.insert_pdf(raster, links=False, annots=False, widgets=False)
                        imported_xref = candidate.page_xref(candidate.page_count - 1)
                        for key in ("Resources", "Contents", "MediaBox"):
                            _, value = candidate.xref_get_key(imported_xref, key)
                            candidate.xref_set_key(original_xref, key, value)
                        for key in ("Rotate", "CropBox", "Annots"):
                            candidate.xref_set_key(original_xref, key, "null")
                        candidate.delete_page(candidate.page_count - 1)
                encoded = candidate.tobytes(garbage=4, deflate=True, use_objstms=1,
                                            encryption=pymupdf.PDF_ENCRYPT_KEEP)
            if best_bytes is None or len(encoded) < len(best_bytes):
                best_bytes, best_settings = encoded, (candidate_dpi, candidate_quality)
            if target_bytes is None or len(best_bytes) <= target_bytes:
                break
        with _open_pdf(best_bytes, getattr(source, "_adf_password", "")) as result:
            saved = _atomic_save(result, target, overwrite=False)
        output_size = target.stat().st_size
        return {"original_size": original_size, "output_size": output_size,
                "target_met": target_bytes is None or output_size <= target_bytes,
                "dpi": best_settings[0], "quality": best_settings[1], "path": saved}
