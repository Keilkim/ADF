"""Selected PDF shapes, images and clipping groups: move, resize, delete and edit nodes."""
import math
import pymupdf
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QGraphicsObject
from .theme import ACCENT

# Handles by name: which edges of the frame they move.
HANDLES = {'tl': (-1, -1), 't': (0, -1), 'tr': (1, -1), 'r': (1, 0),
           'br': (1, 1), 'b': (0, 1), 'bl': (-1, 1), 'l': (-1, 0)}
CURSORS = {'tl': Qt.CursorShape.SizeFDiagCursor, 'br': Qt.CursorShape.SizeFDiagCursor,
           'tr': Qt.CursorShape.SizeBDiagCursor, 'bl': Qt.CursorShape.SizeBDiagCursor,
           't': Qt.CursorShape.SizeVerCursor, 'b': Qt.CursorShape.SizeVerCursor,
           'l': Qt.CursorShape.SizeHorCursor, 'r': Qt.CursorShape.SizeHorCursor}


def shown_transform(page, before, after):
    """The unrotated page Matrix that maps the shown rect `before` onto `after`."""
    sx, sy = after.width()/before.width(), after.height()/before.height()
    if abs(sx-1) < 1e-9 and abs(sy-1) < 1e-9:
        start = pymupdf.Point(before.x(), before.y())*page.derotation_matrix
        end = pymupdf.Point(after.x(), after.y())*page.derotation_matrix
        return pymupdf.Matrix(1, 0, 0, 1, end.x-start.x, end.y-start.y)
    shown = (pymupdf.Matrix(1, 0, 0, 1, -before.x(), -before.y())*pymupdf.Matrix(sx, 0, 0, sy, 0, 0)
             * pymupdf.Matrix(1, 0, 0, 1, after.x(), after.y()))
    return page.rotation_matrix*shown*page.derotation_matrix


class ObjectSelection(QGraphicsObject):
    """A frame with eight resize handles. The page keeps its paint order until a drag begins."""
    committed = Signal(int, object, object)
    deleteRequested = Signal()
    doubleClicked = Signal(object)

    def __init__(self, view, page_item, target, pixmap, shown):
        super().__init__(page_item)
        self.view = view
        self.index, self.target = page_item.index, target
        self.object_id = ('group', target['id']) if target['kind'] == 'group' else target['id']
        self.pixmap = pixmap
        self.pixmap_rect = QRectF(shown)
        frame = pymupdf.Rect(target.get('bounds', target['rect']))*view.document.doc[self.index].rotation_matrix
        self.origin = QRectF(frame.x0, frame.y0, max(frame.width, .5), max(frame.height, .5))
        self.current = QRectF(self.origin)
        self.press = None
        self.dragging = False
        self.setZValue(35)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        hint = ('더블클릭하면 안쪽 객체 선택 · ' if target['kind'] == 'group' else
                '더블클릭하면 노드 편집 · ' if target['kind'] == 'path' else '')
        self.setToolTip('끌어서 이동 · 모서리로 크기 조절(Shift 비율 유지) · '+hint+'Delete 삭제 · Esc 해제')

    @property
    def rect(self):
        return self.current

    def factor(self):
        return max(.01, self.view.transform().m11())

    def delete_rect(self):
        size = 22/self.factor()
        return QRectF(self.current.right()-size, self.current.top()-size-6/self.factor(), size, size)

    def handle_rect(self, name):
        dx, dy = HANDLES[name]
        size = 8/self.factor()
        x = (self.current.left(), self.current.center().x(), self.current.right())[dx+1]
        y = (self.current.top(), self.current.center().y(), self.current.bottom())[dy+1]
        return QRectF(x-size/2, y-size/2, size, size)

    def handle_at(self, pos):
        grow = 3/self.factor()
        for name in HANDLES:
            if self.handle_rect(name).adjusted(-grow, -grow, grow, grow).contains(pos):
                return name
        return None

    def boundingRect(self):
        pad = 8/self.factor()
        return self.current.united(self.delete_rect()).united(self.pixmap_frame()).adjusted(-pad, -pad, pad, pad)

    def shape(self):
        path = QPainterPath()
        grow = 6/self.factor()
        path.addRect(self.current.adjusted(-grow, -grow, grow, grow))
        path.addEllipse(self.delete_rect())
        return path

    def pixmap_frame(self):
        """Where the object's pixels go for the current frame."""
        sx = self.current.width()/self.origin.width()
        sy = self.current.height()/self.origin.height()
        return QRectF(self.current.x()+(self.pixmap_rect.x()-self.origin.x())*sx,
                      self.current.y()+(self.pixmap_rect.y()-self.origin.y())*sy,
                      self.pixmap_rect.width()*sx, self.pixmap_rect.height()*sy)

    def paint(self, painter, option, widget=None):
        if self.dragging:
            painter.drawPixmap(self.pixmap_frame(), self.pixmap, QRectF(self.pixmap.rect()))
        factor = self.factor()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ACCENT), 1.2/factor, Qt.PenStyle.DashLine))
        painter.drawRect(self.current)
        painter.setPen(QPen(QColor(ACCENT), 1/factor))
        painter.setBrush(QColor('#ffffff'))
        for name in HANDLES:
            painter.drawRect(self.handle_rect(name))
        button = self.delete_rect()
        painter.drawEllipse(button)
        inset = 7/factor
        x = button.adjusted(inset, inset, -inset, -inset)
        painter.drawLine(x.topLeft(), x.bottomRight())
        painter.drawLine(x.topRight(), x.bottomLeft())

    def hoverMoveEvent(self, event):
        pos = event.pos()
        handle = self.handle_at(pos)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.delete_rect().contains(pos) else
                       CURSORS[handle] if handle else Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event):
        if self.delete_rect().contains(event.pos()):
            self.deleteRequested.emit()
        else:
            self.press = (event.scenePos(), QRectF(self.current), self.handle_at(event.pos()))
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.press = None
        self.doubleClicked.emit(event.scenePos())
        event.accept()

    def set_current(self, rect):
        self.prepareGeometryChange()
        self.current = rect
        self.update()

    def mouseMoveEvent(self, event):
        if self.press is None:
            return
        start, frame, handle = self.press
        page = self.parentItem()
        delta = page.mapFromScene(event.scenePos())-page.mapFromScene(start)
        if not self.dragging:
            if QLineF(event.screenPos(), event.buttonDownScreenPos(Qt.MouseButton.LeftButton)).length() < QApplication.startDragDistance():
                return
            self.dragging = True
            self.view.begin_object_drag()
        if handle is None:
            moved = self.view.snap_geometry(page, frame.translated(delta), event.modifiers())
            moved.moveLeft(max(-moved.width()+4, min(moved.left(), page.rect.width()-4)))
            moved.moveTop(max(-moved.height()+4, min(moved.top(), page.rect.height()-4)))
            self.set_current(moved)
        else:
            self.set_current(self.resized(frame, handle, delta, event.modifiers()))
        event.accept()

    def resized(self, frame, handle, delta, modifiers):
        dx, dy = HANDLES[handle]
        minimum = 2/self.factor()
        left, top, right, bottom = frame.left(), frame.top(), frame.right(), frame.bottom()
        if dx < 0:
            left = min(left+delta.x(), right-minimum)
        elif dx > 0:
            right = max(right+delta.x(), left+minimum)
        if dy < 0:
            top = min(top+delta.y(), bottom-minimum)
        elif dy > 0:
            bottom = max(bottom+delta.y(), top+minimum)
        rect = QRectF(QPointF(left, top), QPointF(right, bottom))
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            # Keep the proportions, anchored at the opposite handle.
            scale = max(rect.width()/frame.width() if dx else 0, rect.height()/frame.height() if dy else 0)
            width, height = frame.width()*scale, frame.height()*scale
            x = frame.center().x()-width/2 if not dx else rect.left() if dx > 0 else rect.right()-width
            y = frame.center().y()-height/2 if not dy else rect.top() if dy > 0 else rect.bottom()-height
            rect = QRectF(x, y, width, height)
        return rect

    def mouseReleaseEvent(self, event):
        dragged, self.press = self.dragging, None
        self.view.clear_snap_guides()
        if dragged:
            self.dragging = False
            page = self.view.document.doc[self.index]
            if self.current != self.origin:
                self.committed.emit(self.index, self.object_id, shown_transform(page, self.origin, self.current))
            else:
                self.view.end_object_drag()
        event.accept()


class NodeEditor(QGraphicsObject):
    """Anchor points and Bézier handles of one path, edited as in a direct-selection tool."""
    committed = Signal(int, object, object)

    def __init__(self, view, page_item, target, parts):
        super().__init__(page_item)
        self.view = view
        self.index, self.object_id, self.target = page_item.index, target['id'], target
        page = view.document.doc[self.index]
        self.rotation, self.derotation = page.rotation_matrix, page.derotation_matrix
        # Shown coordinates, part by part: [name, [QPointF, ...]].
        self.parts = [[name, [self.to_shown(p) for p in points]] for name, points in parts]
        self.original = [[name, list(points)] for name, points in self.parts]
        self.press = None
        self.hover = None
        self.setZValue(36)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setToolTip('네모 점(앵커)·동그라미(핸들)를 끌어 모양 수정 · Alt 핸들 따로 움직이기 · Esc 끝내기')

    def to_shown(self, point):
        shown = pymupdf.Point(point)*self.rotation
        return QPointF(shown.x, shown.y)

    def to_page(self, point):
        return pymupdf.Point(point.x(), point.y())*self.derotation

    def factor(self):
        return max(.01, self.view.transform().m11())

    def anchors(self):
        """(part, point) of every anchor; curve controls are handles, not anchors."""
        return [(i, len(points)-1) for i, (name, points) in enumerate(self.parts) if name in ('m', 'l', 'c')]

    def handles(self):
        """(part, point, anchor part, anchor point) of every Bézier control."""
        found, previous = [], None
        for i, (name, points) in enumerate(self.parts):
            if name == 'c':
                if previous is not None:
                    found.append((i, 0, *previous))
                found.append((i, 1, i, 2))
            if name in ('m', 'l', 'c'):
                previous = (i, len(points)-1)
            elif name == 'h':
                previous = None
        return found

    def point(self, ref):
        return self.parts[ref[0]][1][ref[1]]

    def outline(self):
        path = QPainterPath()
        for name, points in self.parts:
            if name == 'm':
                path.moveTo(points[0])
            elif name == 'l':
                path.lineTo(points[0])
            elif name == 'c':
                path.cubicTo(*points)
            elif name == 'h':
                path.closeSubpath()
        return path

    def boundingRect(self):
        pad = 12/self.factor()
        rect = self.outline().boundingRect()
        for ref in self.anchors()+[h[:2] for h in self.handles()]:
            p = self.point(ref)
            rect = rect.united(QRectF(p, p))
        return rect.adjusted(-pad, -pad, pad, pad)

    def shape(self):
        path = QPainterPath()
        radius = 6/self.factor()
        for ref in self.anchors()+[h[:2] for h in self.handles()]:
            path.addEllipse(self.point(ref), radius, radius)
        return path

    def target_at(self, pos):
        radius = 6/self.factor()
        for kind, refs in (('handle', [h[:2] for h in self.handles()]), ('anchor', self.anchors())):
            for ref in refs:
                if QLineF(self.point(ref), pos).length() <= radius:
                    return kind, ref
        return None

    def paint(self, painter, option, widget=None):
        factor = self.factor()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ACCENT), 1.2/factor))
        painter.drawPath(self.outline())
        painter.setPen(QPen(QColor('#7b8798'), .8/factor))
        for part, point, anchor_part, anchor_point in self.handles():
            painter.drawLine(self.point((part, point)), self.point((anchor_part, anchor_point)))
        radius = 3.2/factor
        painter.setPen(QPen(QColor(ACCENT), 1/factor))
        painter.setBrush(QColor('#ffffff'))
        for part, point, _, _ in self.handles():
            painter.drawEllipse(self.point((part, point)), radius, radius)
        size = 7/factor
        for ref in self.anchors():
            p = self.point(ref)
            selected = self.press is not None and self.press[1] == ref
            painter.setBrush(QColor(ACCENT) if selected else QColor('#ffffff'))
            painter.drawRect(QRectF(p.x()-size/2, p.y()-size/2, size, size))

    def hoverMoveEvent(self, event):
        found = self.target_at(event.pos())
        self.setCursor(Qt.CursorShape.CrossCursor if found else Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, event):
        found = self.target_at(event.pos())
        if found is None:
            event.ignore()
            return
        kind, ref = found
        self.press = (kind, ref, event.pos(), [[n, list(p)] for n, p in self.parts])
        self.update()
        event.accept()

    def linked(self, ref, parts):
        """Handles that travel with an anchor, and coincident anchors such as a closed start."""
        anchor = parts[ref[0]][1][ref[1]]
        linked = {ref}
        for part, point, anchor_part, anchor_point in self.handles():
            if (anchor_part, anchor_point) == ref:
                linked.add((part, point))
        for other in self.anchors():
            if other != ref and QLineF(parts[other[0]][1][other[1]], anchor).length() < 1e-6:
                linked.add(other)
                linked |= {(p, q) for p, q, a, b in self.handles() if (a, b) == other}
        return linked

    def mouseMoveEvent(self, event):
        if self.press is None:
            return
        kind, ref, start, before = self.press
        delta = event.pos()-start
        self.prepareGeometryChange()
        self.parts = [[n, list(p)] for n, p in before]
        if kind == 'anchor':
            for part, point in self.linked(ref, before):
                self.parts[part][1][point] = before[part][1][point]+delta
        else:
            moved = before[ref[0]][1][ref[1]]+delta
            self.parts[ref[0]][1][ref[1]] = moved
            if not event.modifiers() & Qt.KeyboardModifier.AltModifier:
                self.mirror(ref, before, moved)
        self.update()
        event.accept()

    def mirror(self, ref, before, moved):
        """A smooth point keeps its two handles in line, each with its own length."""
        handles = self.handles()
        mine = next(h for h in handles if h[:2] == ref)
        anchor = before[mine[2]][1][mine[3]]
        for other in handles:
            if other[:2] == ref or other[2:] != mine[2:]:
                continue
            old_mine, old_other = before[ref[0]][1][ref[1]], before[other[0]][1][other[1]]
            a, b = old_mine-anchor, old_other-anchor
            length_a, length_b = math.hypot(a.x(), a.y()), math.hypot(b.x(), b.y())
            if length_a < 1e-6 or length_b < 1e-6 or (a.x()*b.x()+a.y()*b.y())/(length_a*length_b) > -.995:
                continue
            direction = moved-anchor
            length = math.hypot(direction.x(), direction.y())
            if length > 1e-6:
                self.parts[other[0]][1][other[1]] = anchor-direction*(length_b/length)

    def mouseReleaseEvent(self, event):
        if self.press is None:
            return
        self.press = None
        self.update()
        if self.parts != self.original:
            parts = [(name, [self.to_page(p) for p in points]) for name, points in self.parts]
            self.committed.emit(self.index, self.object_id, parts)
        event.accept()


class ShapeDraft(QGraphicsObject):
    """The rectangle, ellipse or line being drawn, in the new-shape style."""

    def __init__(self, view, page_item, kind, start, style):
        super().__init__(page_item)
        self.view, self.kind, self.style = view, kind, style
        self.start = self.end = start
        self.setZValue(37)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def set_end(self, end, modifiers):
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            d = end-self.start
            if self.kind == 'line':
                # Snap to 45° steps.
                angle = round(math.atan2(d.y(), d.x())/(math.pi/4))*(math.pi/4)
                length = math.hypot(d.x(), d.y())
                end = self.start+QPointF(math.cos(angle)*length, math.sin(angle)*length)
            else:
                size = max(abs(d.x()), abs(d.y()))
                end = self.start+QPointF(math.copysign(size, d.x() or 1), math.copysign(size, d.y() or 1))
        self.prepareGeometryChange()
        self.end = end
        self.update()

    def frame(self):
        return QRectF(self.start, self.end).normalized()

    def boundingRect(self):
        pad = max(2., self.style['width'])+4/max(.01, self.view.transform().m11())
        return self.frame().adjusted(-pad, -pad, pad, pad)

    def paint(self, painter, option, widget=None):
        stroke, fill, width = self.style['stroke'], self.style['fill'], self.style['width']
        pen = QPen(QColor.fromRgbF(*stroke), width) if stroke is not None else QPen(QColor(ACCENT), 0, Qt.PenStyle.DashLine)
        if stroke is not None and width == 0:
            pen.setCosmetic(True)
            pen.setWidthF(1)
        painter.setPen(pen)
        painter.setBrush(QColor.fromRgbF(*fill) if fill is not None and self.kind != 'line' else Qt.BrushStyle.NoBrush)
        if self.kind == 'line':
            painter.drawLine(self.start, self.end)
        elif self.kind == 'ellipse':
            painter.drawEllipse(self.frame())
        else:
            painter.drawRect(self.frame())
