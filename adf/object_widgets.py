"""A selected PDF shape or image with direct dragging and a delete control."""
import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsObject
from .theme import ACCENT


class ObjectSelection(QGraphicsObject):
    committed = Signal(int, int, object)
    deleteRequested = Signal()

    def __init__(self, view, page_item, target, pixmap, shown):
        super().__init__(page_item)
        self.view = view
        self.index, self.object_id = page_item.index, target['id']
        self.target = target
        self.pixmap = pixmap
        self.rect = QRectF(0,0,shown.width(),shown.height())
        self.setPos(shown.topLeft())
        self.origin = self.pos()
        self.press_scene = None
        self.setZValue(35)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setToolTip('끌어서 이동 · Delete 또는 × 버튼으로 삭제 · Esc로 선택 해제')

    def factor(self):
        return max(.01,self.view.transform().m11())

    def delete_rect(self):
        size = 22/self.factor()
        return QRectF(self.rect.right()-size,-size-4/self.factor(),size,size)

    def boundingRect(self):
        pad = 4/self.factor()
        return self.rect.united(self.delete_rect()).adjusted(-pad,-pad,pad,pad)

    def shape(self):
        path = QPainterPath()
        path.addRect(self.rect)
        path.addEllipse(self.delete_rect())
        return path

    def paint(self, painter, option, widget=None):
        painter.drawPixmap(self.rect,self.pixmap,QRectF(self.pixmap.rect()))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ACCENT),1.2/self.factor(),Qt.PenStyle.DashLine))
        painter.drawRect(self.rect)
        button = self.delete_rect()
        painter.setBrush(QColor('#ffffff'))
        painter.setPen(QPen(QColor(ACCENT),1/self.factor()))
        painter.drawEllipse(button)
        inset = 7/self.factor()
        x = button.adjusted(inset,inset,-inset,-inset)
        painter.drawLine(x.topLeft(),x.bottomRight())
        painter.drawLine(x.topRight(),x.bottomLeft())

    def hoverMoveEvent(self, event):
        self.setCursor(Qt.CursorShape.PointingHandCursor if self.delete_rect().contains(event.pos()) else Qt.CursorShape.SizeAllCursor)

    def mousePressEvent(self, event):
        if self.delete_rect().contains(event.pos()):
            self.deleteRequested.emit()
        else:
            self.press_scene, self.press_pos = event.scenePos(), self.pos()
        event.accept()

    def mouseMoveEvent(self, event):
        if self.press_scene is None:
            return
        page = self.parentItem()
        delta = page.mapFromScene(event.scenePos())-page.mapFromScene(self.press_scene)
        position = self.press_pos+delta
        shown = QRectF(position,self.rect.size())
        shown = self.view.snap_geometry(page,shown,event.modifiers())
        position = shown.topLeft()
        position.setX(max(0,min(position.x(),page.rect.width()-self.rect.width())))
        position.setY(max(0,min(position.y(),page.rect.height()-self.rect.height())))
        self.setPos(position)
        event.accept()

    def mouseReleaseEvent(self, event):
        changed = self.press_scene is not None and self.pos() != self.origin
        self.press_scene = None
        self.view.clear_snap_guides()
        if changed:
            page = self.view.document.doc[self.index]
            before = pymupdf.Point(self.origin.x(),self.origin.y())*page.derotation_matrix
            after = pymupdf.Point(self.pos().x(),self.pos().y())*page.derotation_matrix
            self.committed.emit(self.index,self.object_id,(after.x-before.x,after.y-before.y))
        event.accept()
