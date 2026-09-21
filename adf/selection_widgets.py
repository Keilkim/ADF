"""Transient text highlighting and an animated boundary, separate from PDF content."""
from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsObject

from .fullscreen import motion_enabled

HIGHLIGHT = QColor(40, 120, 245, 64)
HIGHLIGHT_EDGE = QColor(40, 120, 245, 28)


@dataclass
class _Line:
    rect: pymupdf.Rect
    start: int
    end: int
    direction: tuple
    block: int


class PageText:
    """A page's characters in reading order, selected between two carets like a word processor.

    Coordinates are unrotated PDF page coordinates. Caret k sits before character k.
    """

    def __init__(self, page):
        self.chars = []  # (quad, character, line index)
        self.lines = []
        if page is None:
            return
        flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
        for block_index, block in enumerate(page.get_text('rawdict', flags=flags)['blocks']):
            for line in block.get('lines', []):
                start, rect = len(self.chars), None
                for span in line['spans']:
                    for char in span['chars']:
                        quad = pymupdf.recover_char_quad(line['dir'], span, char)
                        self.chars.append((quad, char['c'], len(self.lines)))
                        rect = quad.rect if rect is None else rect | quad.rect
                if rect is not None:
                    self.lines.append(_Line(rect, start, len(self.chars), tuple(line['dir']), block_index))

    def near(self, point):
        """Whether a point is on a line of text or within one line height of it."""
        for line in self.lines:
            margin = max(line.rect.height, 4)
            if pymupdf.Rect(line.rect.x0-margin, line.rect.y0-margin, line.rect.x1+margin, line.rect.y1+margin).contains(point):
                return True
        return False

    def _line_at(self, point):
        def distance(line):
            rect = line.rect
            dx = max(rect.x0-point.x, 0, point.x-rect.x1)
            dy = max(rect.y0-point.y, 0, point.y-rect.y1)
            return dx + 2*dy  # Stay on the row being dragged across.
        return min(self.lines, key=distance)

    def caret(self, point):
        if not self.lines:
            return 0
        # Well above or below all text: the start or the end of the page.
        top = min(self.lines, key=lambda line: line.rect.y0)
        if point.y < top.rect.y0 - top.rect.height/2:
            return 0
        bottom = max(self.lines, key=lambda line: line.rect.y1)
        if point.y > bottom.rect.y1 + bottom.rect.height/2:
            return len(self.chars)
        line = self._line_at(point)
        dx, dy = line.direction
        along = point.x*dx + point.y*dy
        caret = line.start
        for quad, _, _ in self.chars[line.start:line.end]:
            center = (quad.ul + quad.lr) / 2
            if center.x*dx + center.y*dy >= along:
                break
            caret += 1
        return caret

    def word(self, point):
        """The characters between spaces under a double-click, if it is on text."""
        if not self.near(point):
            return None
        line = self._line_at(point)
        index = min(max(self.caret(point), line.start), line.end-1)
        for candidate, (quad, _, _) in enumerate(self.chars[line.start:line.end], line.start):
            if quad.rect.contains(point):
                index = candidate
                break
        if self.chars[index][1].isspace():
            return index, index+1
        start, end = index, index+1
        while start > line.start and not self.chars[start-1][1].isspace():
            start -= 1
        while end < line.end and not self.chars[end][1].isspace():
            end += 1
        return start, end

    def paragraph(self, page, point):
        """The paragraph under a triple-click, grouped like text editing groups it."""
        if not self.near(point):
            return None
        from .text_groups import text_group_at
        group = text_group_at(page, point)
        if group:
            box = pymupdf.Rect(group['bbox'])
            inside = [index for index, (quad, _, _) in enumerate(self.chars)
                      if box.contains((quad.ul + quad.lr) / 2)]
            if inside:
                return inside[0], inside[-1]+1
        block = self._line_at(point).block
        lines = [line for line in self.lines if line.block == block]
        return lines[0].start, lines[-1].end

    def text(self, start, end):
        parts, current = [], None
        for _, character, line in self.chars[start:end]:
            if current is not None and line != current:
                parts.append('\n')
            parts.append(character)
            current = line
        return ''.join(parts)

    def shapes(self, start, end, matrix):
        """One band per line from the first to the last selected character."""
        polygons = []
        for line in self.lines:
            first, last = max(start, line.start), min(end, line.end)
            if first >= last:
                continue
            quads = [quad for quad, _, _ in self.chars[first:last]]
            if abs(line.direction[1]) < 1e-3 and line.direction[0] > 0:
                x0, x1 = min(quad.rect.x0 for quad in quads), max(quad.rect.x1 for quad in quads)
                corners = [pymupdf.Point(x0, line.rect.y0), pymupdf.Point(x1, line.rect.y0),
                           pymupdf.Point(x1, line.rect.y1), pymupdf.Point(x0, line.rect.y1)]
            else:
                corners = [quads[0].ul, quads[-1].ur, quads[-1].lr, quads[0].ll]
            polygons.append(QPolygonF([QPointF((p*matrix).x, (p*matrix).y) for p in corners]))
        return polygons


class TextSelection(QGraphicsObject):
    """Text selected in reading order, highlighted while it is dragged."""

    def __init__(self, view, page_item, text, page, anchor):
        super().__init__(page_item)
        self.view = view
        self.page_text = text
        self.matrix = page.rotation_matrix
        self.anchor = self.focus = anchor
        self.finished = False
        self.text_path = QPainterPath()
        self.text_path.setFillRule(Qt.FillRule.WindingFill)
        # Text needs no moving boundary; the attribute matches RegionSelection.
        self.timer = QTimer(self)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(10)

    def span(self):
        return min(self.anchor, self.focus), max(self.anchor, self.focus)

    def select(self, anchor, focus):
        if (anchor, focus) == (self.anchor, self.focus):
            return  # Most drag moves stay within a character; a dense page is slow to rebuild.
        self.prepareGeometryChange()
        self.anchor, self.focus = anchor, focus
        self.text_path = QPainterPath()
        self.text_path.setFillRule(Qt.FillRule.WindingFill)
        for polygon in self.page_text.shapes(*self.span(), self.matrix):
            self.text_path.addPolygon(polygon)
            self.text_path.closeSubpath()
        self.update()

    def text(self):
        return self.page_text.text(*self.span())

    def rect(self):
        return self.text_path.boundingRect()

    def boundingRect(self):
        return self.text_path.boundingRect().adjusted(-3, -3, 3, 3)

    def finish(self, page=None, highlight_text=True):
        self.finished = True
        self.update()
        return self.text()

    def paint(self, painter, option, widget=None):
        if self.text_path.isEmpty():
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        glow = QPen(HIGHLIGHT_EDGE, 3)
        glow.setCosmetic(True)
        painter.setPen(glow)
        painter.setBrush(HIGHLIGHT)
        painter.drawPath(self.text_path)

    def dispose(self):
        if self.scene() is not None:
            self.scene().removeItem(self)
        self.setParentItem(None)
        self.deleteLater()


class RegionSelection(QGraphicsObject):
    def __init__(self, view, page):
        super().__init__(page)
        self.view = view
        self._rect = QRectF()
        self.finished = False
        self.text_path = QPainterPath()
        self.text_path.setFillRule(Qt.FillRule.WindingFill)
        self.phase = 0.
        self.elapsed = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self.advance)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(10)

    def rect(self):
        return QRectF(self._rect)

    def set_scene_rect(self, rect):
        self.prepareGeometryChange()
        self._rect = self.parentItem().mapRectFromScene(rect).intersected(self.parentItem().rect)
        self.update()

    def boundingRect(self):
        return self._rect.adjusted(-10, -10, 10, 10)

    def finish(self, page, highlight_text=True):
        self.finished = True
        r = self._rect
        clip = pymupdf.Rect(r.left(), r.top(), r.right(), r.bottom()) * page.derotation_matrix
        text = ''
        if not clip.is_empty:
            flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
            textpage = page.get_textpage(clip=clip, flags=flags)
            text = textpage.extractTextbox(clip)
            if highlight_text:
                for block in textpage.extractRAWDICT()['blocks']:
                    for line in block.get('lines', []):
                        for span in line['spans']:
                            for char in span['chars']:
                                quad = pymupdf.recover_char_quad(line['dir'], span, char)
                                points = [p * page.rotation_matrix for p in (quad.ul, quad.ur, quad.lr, quad.ll)]
                                self.text_path.addPolygon(QPolygonF([QPointF(p.x, p.y) for p in points]))
                                self.text_path.closeSubpath()
        self.update()
        if motion_enabled():
            self.elapsed.start()
            self.timer.start()
        return text

    def advance(self):
        if not self.view.isVisible() or not self.isVisible():
            return
        self.phase = (self.elapsed.elapsed() / 85) % 8
        # Only invalidate the edge strips; large captures need no full repaint.
        r = self._rect
        pad = 3 / max(.1, self.view.transform().m11())
        for strip in (QRectF(r.left()-pad, r.top()-pad, r.width()+2*pad, 2*pad),
                      QRectF(r.left()-pad, r.bottom()-pad, r.width()+2*pad, 2*pad),
                      QRectF(r.left()-pad, r.top()-pad, 2*pad, r.height()+2*pad),
                      QRectF(r.right()-pad, r.top()-pad, 2*pad, r.height()+2*pad)):
            self.update(strip)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self.finished:
            painter.fillRect(self._rect, QColor(98, 101, 107, 45))
            return
        painter.save()
        painter.setClipRect(self._rect)
        if not self.text_path.isEmpty():
            glow = QPen(HIGHLIGHT_EDGE, 3)
            glow.setCosmetic(True)
            painter.setPen(glow)
            painter.setBrush(HIGHLIGHT)
            painter.drawPath(self.text_path)
        painter.restore()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = QPen(QColor(255, 255, 255, 235), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawRect(self._rect)
        pen.setColor(QColor(48, 49, 52, 225))
        pen.setDashPattern([4, 4])
        pen.setDashOffset(self.phase)
        painter.setPen(pen)
        painter.drawRect(self._rect)

    def dispose(self):
        self.timer.stop()
        if self.scene() is not None:
            self.scene().removeItem(self)
        self.setParentItem(None)
        self.deleteLater()
