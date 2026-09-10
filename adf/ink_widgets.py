"""Direct manipulation of saved ink: eight handles and a lower rotation handle."""
import math
import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPainterPathStroker, QPen, QTransform
from PySide6.QtWidgets import QGraphicsObject
from .ink import read_ink
from .pen import paint_strokes


def ink_at(view, item, scene_pos):
    if not view.document.editable:
        return None
    page = view.document.doc[item.index]
    local = item.mapFromScene(scene_pos)
    radius = 7/max(.01, view.transform().m11())
    for annotation in reversed(list(page.annots() or [])):
        data = read_ink(annotation)
        if data is None:
            continue
        path = QPainterPath()
        for stroke in data['paths']:
            points = [pymupdf.Point(p)*page.rotation_matrix for p in stroke]
            path.moveTo(points[0].x, points[0].y)
            for point in points[1:]:
                path.lineTo(point.x, point.y)
        stroker = QPainterPathStroker()
        stroker.setWidth(max(data['width'], radius))
        if stroker.createStroke(path).contains(local):
            return annotation.xref
    return None


class InkSelection(QGraphicsObject):
    committed = Signal(int, int, object, object)

    def __init__(self, view, page_item, xref):
        super().__init__(page_item)
        self.view = view
        self.index, self.xref = page_item.index, xref
        page = view.document.doc[self.index]
        self.data = read_ink(page.load_annot(xref))
        cx, cy, width, height, angle = self.data['frame']
        self.original_size = (width, height)
        self.size = (width, height)
        self.angle = angle
        center = pymupdf.Point(cx, cy)*page.rotation_matrix
        self.setPos(center.x, center.y)
        self.setRotation(page.rotation+angle)
        inverse = QTransform().rotate(-angle)
        self.points = [[inverse.map(QPointF(x-cx, y-cy)) for x, y in path] for path in self.data['paths']]
        self.drag = None
        self.setZValue(35)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def scale_factor(self):
        return max(.01, self.view.transform().m11())

    def rect(self):
        width, height = self.size
        return QRectF(-width/2, -height/2, width, height)

    def handles(self):
        w, h = self.size[0]/2, self.size[1]/2
        return [QPointF(x, y) for x, y in [(-w,-h),(0,-h),(w,-h),(w,0),(w,h),(0,h),(-w,h),(-w,0),
                                         (0,h+26/self.scale_factor())]]

    def boundingRect(self):
        pad = 9/self.scale_factor()
        return self.rect().adjusted(-pad, -pad, pad, 36/self.scale_factor())

    def hit_handle(self, point):
        radius = 8/self.scale_factor()
        for index, handle in enumerate(self.handles()):
            if abs(point.x()-handle.x()) <= radius and abs(point.y()-handle.y()) <= radius:
                return index
        return None

    def shape(self):
        path = QPainterPath()
        path.addRect(self.rect())
        for point in self.handles():
            radius = 8/self.scale_factor()
            path.addEllipse(point, radius, radius)
        return path

    def local_paths(self):
        sx, sy = self.size[0]/self.original_size[0], self.size[1]/self.original_size[1]
        return [[(p.x()*sx, p.y()*sy) for p in path] for path in self.points]

    def paint(self, painter, option, widget=None):
        paint_strokes(painter, self.local_paths(), self.data['pressures'], self.data['color'], self.data['width'], self.data['kind'], self.data.get('weights'))
        factor = self.scale_factor()
        painter.setPen(QPen(QColor('#62656b'), 1/factor))
        painter.setBrush(QColor('white'))
        for point in self.handles()[:8]:
            painter.drawRect(QRectF(point.x()-3.5/factor, point.y()-3.5/factor, 7/factor, 7/factor))
        rotate = self.handles()[8]
        painter.drawEllipse(rotate, 7/factor, 7/factor)
        painter.drawArc(QRectF(rotate.x()-4/factor, rotate.y()-4/factor, 8/factor, 8/factor), 20*16, 270*16)
        # Deliberately no bounding rectangle or connecting outline.

    def hoverMoveEvent(self, event):
        handle = self.hit_handle(event.pos())
        cursors = [Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor, Qt.CursorShape.SizeBDiagCursor,
                   Qt.CursorShape.SizeHorCursor, Qt.CursorShape.SizeFDiagCursor, Qt.CursorShape.SizeVerCursor,
                   Qt.CursorShape.SizeBDiagCursor, Qt.CursorShape.SizeHorCursor, Qt.CursorShape.CrossCursor]
        self.setCursor(cursors[handle] if handle is not None else Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event):
        self.drag = self.hit_handle(event.pos())
        if self.drag is None:
            self.drag = 'move'
        self.press_scene = event.scenePos()
        self.press_pos = self.pos()
        self.press_size = self.size
        self.press_rotation = self.rotation()
        self.press_transform = self.sceneTransform()
        self.changed = False
        event.accept()

    def mouseMoveEvent(self, event):
        if self.drag is None:
            return
        before = (self.pos(), self.size, self.rotation())
        parent = self.parentItem()
        self.prepareGeometryChange()
        if self.drag == 'move':
            delta = parent.mapFromScene(event.scenePos())-parent.mapFromScene(self.press_scene)
            self.setPos(self.press_pos+delta)
        elif self.drag == 8:
            center = parent.mapToScene(self.press_pos)
            a, b = self.press_scene-center, event.scenePos()-center
            degrees = math.degrees(math.atan2(b.y(), b.x())-math.atan2(a.y(), a.x()))
            rotation = self.press_rotation+degrees
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                rotation = round(rotation/15)*15
            self.setRotation(rotation)
        else:
            point = self.press_transform.inverted()[0].map(event.scenePos())
            w, h = self.press_size
            left, top, right, bottom = -w/2, -h/2, w/2, h/2
            if self.drag in (0, 6, 7): left = min(point.x(), right-4)
            if self.drag in (2, 3, 4): right = max(point.x(), left+4)
            if self.drag in (0, 1, 2): top = min(point.y(), bottom-4)
            if self.drag in (4, 5, 6): bottom = max(point.y(), top+4)
            self.size = (right-left, bottom-top)
            center = self.press_transform.map(QPointF((left+right)/2, (top+bottom)/2))
            self.setPos(parent.mapFromScene(center))
        bounds = self.mapRectToParent(self.rect())
        page_rect = parent.rect
        if bounds.width() > page_rect.width() or bounds.height() > page_rect.height():
            self.setPos(before[0]); self.size = before[1]; self.setRotation(before[2])
        else:
            dx = max(0, page_rect.left()-bounds.left())+min(0, page_rect.right()-bounds.right())
            dy = max(0, page_rect.top()-bounds.top())+min(0, page_rect.bottom()-bounds.bottom())
            self.setPos(self.pos()+QPointF(dx, dy))
        self.changed = self.pos() != self.press_pos or self.size != self.press_size or self.rotation() != self.press_rotation
        self.update()
        event.accept()

    def geometry(self):
        page = self.view.document.doc[self.index]
        parent = self.parentItem()
        paths = []
        for stroke in self.local_paths():
            points = [parent.mapFromScene(self.mapToScene(QPointF(x, y))) for x, y in stroke]
            paths.append([tuple(pymupdf.Point(p.x(), p.y())*page.derotation_matrix) for p in points])
        center = pymupdf.Point(self.pos().x(), self.pos().y())*page.derotation_matrix
        return paths, [center.x, center.y, *self.size, (self.rotation()-page.rotation)%360]

    def mouseReleaseEvent(self, event):
        changed = self.drag is not None and self.changed
        self.drag = None
        if changed:
            paths, frame = self.geometry()
            self.committed.emit(self.index, self.xref, paths, frame)
        event.accept()
