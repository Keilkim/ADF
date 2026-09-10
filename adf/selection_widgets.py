"""Transient text highlighting and an animated boundary, separate from PDF content."""
import pymupdf
from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsObject

from .fullscreen import motion_enabled


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
            glow = QPen(QColor(40, 120, 245, 28), 3)
            glow.setCosmetic(True)
            painter.setPen(glow)
            painter.setBrush(QColor(40, 120, 245, 64))
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
