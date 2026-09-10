"""Freehand PDF ink input and a small toolbar settings menu."""
import math
import pymupdf
from PySide6.QtCore import QObject, QEvent, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsPathItem


PEN_COLORS = [('검정', '#252525'), ('빨강', '#d53939'), ('파랑', '#2869cf'), ('초록', '#27824b')]
from .ink import KINDS, factors


def paint_strokes(painter, paths, pressures, color, width, kind, weights=None):
    from PySide6.QtCore import QPointF
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setOpacity(KINDS[kind][2])
    pen = QPen(QColor(color), width, Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    for index, (stroke, values) in enumerate(zip(paths, pressures)):
        if not stroke:
            continue
        if len(stroke) == 1:
            painter.setPen(pen)
            painter.drawPoint(QPointF(*stroke[0]))
        elif kind == 'brush':
            stroke_weights = weights[index] if weights is not None else factors(len(stroke), values)
            for i in range(len(stroke)-1):
                pen.setWidthF(width*(stroke_weights[i]+stroke_weights[i+1])/2)
                painter.setPen(pen)
                painter.drawLine(QPointF(*stroke[i]), QPointF(*stroke[i+1]))
        else:
            path = QPainterPath(QPointF(*stroke[0]))
            for point in stroke[1:]:
                path.lineTo(QPointF(*point))
            painter.setPen(pen)
            painter.drawPath(path)
    painter.restore()


class StrokePreview(QGraphicsPathItem):
    def __init__(self, pen, parent):
        super().__init__(parent)
        self.input = pen

    def paint(self, painter, option, widget=None):
        pen = self.input
        paint_strokes(painter, pen.paths, pen.pressures, pen.stroke_color, pen.stroke_width, pen.stroke_kind)


class PenInput(QObject):
    HOLD_MS = 600
    strokeReady = Signal(int, object, str, float, str, object)
    eraseReady = Signal(int, object, float)
    failed = Signal(str)
    canceled = Signal()

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.enabled = False
        self.color = PEN_COLORS[0][1]
        self.width = 1.2
        self.kind = 'pencil'
        self.tool = 'pen'
        self.eraser_width = 20
        self.sweeps = []
        self.erase_last = None
        self.cursor_key = None
        self.page = None
        self.preview = None
        self.paths = []
        self.pressures = []
        self.tablet = False
        self.hold_timer = QTimer(self)
        self.hold_timer.setSingleShot(True)
        self.hold_timer.setInterval(self.HOLD_MS)
        self.hold_timer.timeout.connect(self.straighten)
        self.hold_position = None
        self.line_origin = None
        self.line_direction = None
        view.viewport().installEventFilter(self)

    def update_cursor(self):
        if self.tool != 'eraser':
            self.view.viewport().setCursor(Qt.CursorShape.CrossCursor)
            return
        diameter = max(4, min(288, round(self.eraser_width*self.view.transform().m11())))
        if self.cursor_key != diameter:
            pm = QPixmap(diameter+6, diameter+6)
            pm.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor('white'), 3))
            painter.drawEllipse(QRectF(3, 3, diameter, diameter))
            painter.setPen(QPen(QColor('#62656b'), 1))
            painter.drawEllipse(QRectF(3, 3, diameter, diameter))
            painter.end()
            self.eraser_cursor = QCursor(pm)
            self.cursor_key = diameter
        self.view.viewport().setCursor(self.eraser_cursor)

    def set_enabled(self, enabled):
        self.cancel()
        self.enabled = enabled
        self.view.update_content_cursor()

    def cancel(self):
        self.hold_timer.stop()
        self.hold_position = None
        self.line_origin = self.line_direction = None
        if self.preview is not None and self.preview.scene() is not None:
            self.preview.scene().removeItem(self.preview)
            self.preview.setParentItem(None)
        self.preview = None
        self.page = None
        self.paths = []
        self.pressures = []
        self.tablet = False
        self.sweeps = []
        self.erase_last = None

    def begin(self, position, tablet=False, pressure=.8):
        self.cancel()
        view = self.view
        if not view.document or not view.document.editable:
            return
        scene = view.mapToScene(position.toPoint())
        self.page = view.page_at(scene)
        if self.page is None:
            return
        self.tablet = tablet
        self.stroke_tool = self.tool
        if self.stroke_tool == 'eraser':
            self.stroke_width = self.eraser_width
            try:
                self.preview = EraserPreview(self, self.page)
                self.append(position, pressure)
            except Exception as error:
                self.cancel()
                self.failed.emit(str(error))
            return
        self.stroke_color, self.stroke_width = self.color, self.width
        self.stroke_kind = self.kind
        self.preview = StrokePreview(self, self.page)
        self.preview.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.preview.setZValue(30)
        self.preview.setPen(QPen(QColor(self.stroke_color), self.stroke_width,
            Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        self.path = QPainterPath()
        self.append(position, pressure)

    def append(self, position, pressure=.8):
        if self.page is None:
            return
        point = self.page.mapFromScene(self.view.mapToScene(position.toPoint()))
        if not self.page.rect.contains(point):
            self.hold_timer.stop()
            self.hold_position = None
            self.line_origin = self.line_direction = None
            self.erase_last = None
            if self.paths and self.paths[-1]:
                self.paths.append([])
                self.pressures.append([])
            return
        if self.stroke_tool == 'eraser':
            matrix = self.view.document.doc[self.page.index].derotation_matrix
            point = tuple(pymupdf.Point(point.x(), point.y())*matrix)
            start = self.erase_last or point
            if self.erase_last is None or point != self.erase_last:
                self.sweeps.append((start, point))
                self.preview.erase(start, point, self.stroke_width/2)
            self.erase_last = point
            return
        if self.line_origin is not None:
            self.extend_line(point, pressure)
            return
        if not self.paths or not self.paths[-1]:
            if not self.paths:
                self.paths.append([])
                self.pressures.append([])
            self.path.moveTo(point)
            self.path.lineTo(point.x()+.01, point.y())
        else:
            last = self.paths[-1][-1]
            if abs(last[0]-point.x()) + abs(last[1]-point.y()) < .05:
                return
            self.path.lineTo(point)
        self.paths[-1].append((point.x(), point.y()))
        self.pressures[-1].append(max(0, min(1, pressure)))
        self.preview.setPath(self.path)
        if self.hold_position is None or math.hypot(position.x()-self.hold_position.x(), position.y()-self.hold_position.y()) >= 3:
            self.hold_position = QPointF(position)
            self.hold_timer.start()

    def straighten(self):
        if self.page is None or self.stroke_tool != 'pen' or len(self.paths) != 1 or len(self.paths[0]) < 2:
            return
        stroke = self.paths[0]
        a, b = stroke[0], stroke[-1]
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = math.hypot(dx, dy)
        scale = max(.01, self.view.transform().m11())
        if length*scale < 20:
            return
        deviation = max(abs((p[0]-a[0])*dy-(p[1]-a[1])*dx)/length for p in stroke)
        travelled = sum(math.dist(p, q) for p, q in zip(stroke, stroke[1:]))
        if deviation > max(5/scale, length*.10) or travelled > length*1.3:
            return
        self.line_origin = a
        self.line_direction = (dx/length, dy/length)
        self.pressures = [[self.pressures[0][0], self.pressures[0][-1]]]
        self.paths = [[a, b]]
        self.extend_line(QPointF(*b), self.pressures[0][-1])

    def extend_line(self, point, pressure):
        a, (ux, uy) = self.line_origin, self.line_direction
        distance = max(0, (point.x()-a[0])*ux+(point.y()-a[1])*uy)
        bounds = self.page.rect
        for origin, direction, low, high in ((a[0], ux, bounds.left(), bounds.right()),
                                            (a[1], uy, bounds.top(), bounds.bottom())):
            if abs(direction) > 1e-9:
                distance = min(distance, ((high if direction > 0 else low)-origin)/direction)
        end = (a[0]+ux*distance, a[1]+uy*distance)
        self.paths = [[a, end]]
        self.pressures[0][-1] = max(0, min(1, pressure))
        self.path = QPainterPath(QPointF(*a))
        self.path.lineTo(QPointF(*end))
        self.preview.setPath(self.path)

    def finish(self, position, pressure=.8):
        if self.page is None:
            return
        self.append(position, pressure)
        index = self.page.index
        if self.stroke_tool == 'eraser':
            sweeps, radius = self.sweeps[:], self.stroke_width/2
            self.cancel()
            if sweeps:
                self.eraseReady.emit(index, sweeps, radius)
            return
        matrix = self.view.document.doc[index].derotation_matrix
        paths = [[tuple(pymupdf.Point(p)*matrix) for p in path] for path in self.paths if path]
        pressures = [values for path, values in zip(self.paths, self.pressures) if path]
        kind = self.stroke_kind
        color, width = self.stroke_color, self.stroke_width
        self.cancel()
        if paths:
            self.strokeReady.emit(index, paths, color, width, kind, pressures)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.Hide, QEvent.Type.FocusOut, QEvent.Type.UngrabMouse,
                    QEvent.Type.WindowDeactivate):
            self.cancel()
        if not self.enabled:
            return False
        if kind in (QEvent.Type.TabletPress, QEvent.Type.TabletMove, QEvent.Type.TabletRelease):
            if kind == QEvent.Type.TabletPress:
                self.begin(event.position(), tablet=True, pressure=event.pressure())
            elif kind == QEvent.Type.TabletMove:
                if self.tablet:
                    self.append(event.position(), event.pressure())
            elif self.tablet:
                self.finish(event.position(), event.pressure())
            event.accept()
            return True
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            if event.button() == Qt.MouseButton.RightButton:
                self.cancel()
                self.canceled.emit()
            elif event.button() == Qt.MouseButton.LeftButton and not self.tablet:
                self.begin(event.position())
            event.accept()
            return True
        if kind == QEvent.Type.MouseMove:
            if not self.tablet:
                self.append(event.position())
            self.update_cursor()
            event.accept()
            return True
        if kind == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton and not self.tablet:
                self.finish(event.position())
            event.accept()
            return True
        if kind == QEvent.Type.ContextMenu or (kind == QEvent.Type.Wheel and self.page is not None):
            event.accept()
            return True
        return False


class EraserPreview(QGraphicsPathItem):
    def __init__(self, pen, parent):
        from .eraser import erasable
        from .ink import read_ink
        from .viewer import page_raster
        super().__init__()
        page = pen.view.document.doc[parent.index]
        self.matrix = page.rotation_matrix
        self.ink = {}
        with pymupdf.open() as preview:
            preview.insert_pdf(pen.view.document.doc, from_page=parent.index, to_page=parent.index)
            copied = preview[0]
            for original, annotation in zip(page.annots() or [], copied.annots() or []):
                data = read_ink(original) if erasable(original) else None
                if data is not None:
                    self.ink[original.xref] = data
                    copied.delete_annot(annotation)
            scale = max(1, pen.view.transform().m11()*pen.view.devicePixelRatioF())
            self.background, self.extent = page_raster(copied, scale)
        self.setParentItem(parent)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setZValue(30)
        path = QPainterPath()
        path.addRect(parent.rect)
        self.setPath(path)
        self.setPen(QPen(Qt.PenStyle.NoPen))

    def erase(self, start, end, radius):
        from .eraser import erase_sweep
        changed = False
        for xref, data in self.ink.items():
            updated = erase_sweep(data, start, end, radius)
            if updated is not None:
                self.ink[xref] = updated
                changed = True
        if changed:
            self.update()

    def paint(self, painter, option, widget=None):
        painter.save()
        painter.setClipRect(self.parentItem().rect)
        painter.drawPixmap(self.extent, self.background, QRectF(self.background.rect()))
        for data in self.ink.values():
            paths = [[tuple(pymupdf.Point(p)*self.matrix) for p in path] for path in data['paths']]
            paint_strokes(painter, paths, data['pressures'], data['color'], data['width'], data['kind'], data.get('weights'))
        painter.restore()


from .pen_widgets import PenMenu
