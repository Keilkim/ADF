from collections import OrderedDict
import math
import time
import pymupdf
from PySide6.QtCore import Qt, QRectF, QPointF, QSize, QSizeF, QTimer, QElapsedTimer, Signal, QSignalBlocker, QEvent
from PySide6.QtGui import QColor, QFont, QPalette, QPainter, QPixmap, QImage, QPen, QBrush, QCursor, QTransform, QTextCursor, QTextCharFormat, QTextBlockFormat, QInputDevice
from PySide6.QtWidgets import (QGraphicsView, QGraphicsScene, QGraphicsItem,
    QGraphicsObject, QGraphicsProxyWidget, QListWidget, QListWidgetItem, QAbstractItemView, QToolButton, QApplication,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle)

from . import pinch
from .text_groups import text_group_at
from .page_layout import spread_groups
from .snapping import SnapIndex


RENDER_OVERSAMPLE = 1.5
PAGE_CACHE_BYTES = 96 * 1024 * 1024
THUMB_CACHE_BYTES = 24 * 1024 * 1024
THUMB_PIXMAP_ROLE = Qt.ItemDataRole.UserRole + 6


def page_raster(page, scale, clip=None, max_pixels=16_000_000):
    """Return pixels and their exact page extent, including MuPDF's rounding."""
    bounds = page.rect if clip is None else pymupdf.Rect(clip) & page.rect
    area = bounds.width * bounds.height
    scale = min(scale, math.sqrt(max_pixels / max(1, area)))
    pm = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=bounds,
                         alpha=False, colorspace=pymupdf.csRGB)
    pixmap = QPixmap.fromImage(QImage(pm.samples, pm.width, pm.height, pm.stride, QImage.Format.Format_RGB888).copy())
    return pixmap, QRectF(pm.x/scale, pm.y/scale, pm.width/scale, pm.height/scale)


def page_pixmap(page, scale):
    return page_raster(page, scale)[0]


def pixmap_bytes(pm):
    return pm.width() * pm.height() * max(1, pm.depth() // 8)


class PageItem(QGraphicsItem):
    def __init__(self, index, width, height):
        super().__init__()
        self.index = index
        self.rect = QRectF(0, 0, width, height)
        self.pixmap = None
        self.pixmap_rect = QRectF(self.rect)
        self.highlights = []

    def boundingRect(self):
        return self.rect.adjusted(-2, -2, 4, 5)

    def paint(self, painter, option, widget=None):
        painter.fillRect(self.rect.translated(2, 3), QColor(0, 0, 0, 22))
        painter.fillRect(self.rect, Qt.GlobalColor.white)
        if self.pixmap:
            painter.save()
            painter.setClipRect(self.rect)
            painter.drawPixmap(self.pixmap_rect, self.pixmap, QRectF(self.pixmap.rect()))
            painter.restore()
        else:
            painter.setPen(QColor('#a0a8b5'))
            painter.drawText(self.rect, Qt.AlignmentFlag.AlignCenter, str(self.index + 1))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 203, 65, 110))
        for rect in self.highlights:
            painter.drawRect(rect)


class ImagePlacement(QGraphicsObject):
    def __init__(self, pixmap, width, parent, view=None):
        super().__init__(parent)
        self.view = view
        self.dragging = False
        self._geometry_active = False
        self._press_offset = QPointF()
        self._drag_scene_pos = QPointF()
        self.pm = pixmap
        self.size = QSizeF(width, width * pixmap.height() / max(1, pixmap.width()))
        self.resizing = False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(20)

    def boundingRect(self):
        return QRectF(0, 0, self.size.width(), self.size.height()).adjusted(-3, -3, 5, 5)

    def paint(self, painter, option, widget=None):
        r = QRectF(0, 0, self.size.width(), self.size.height())
        painter.drawPixmap(r, self.pm, QRectF(self.pm.rect()))
        painter.setPen(QPen(QColor('#62656b'), 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r)
        painter.setBrush(QColor('white'))
        painter.drawRect(QRectF(r.right()-4, r.bottom()-4, 8, 8))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.dragging = True
        self._geometry_active = False
        self._press_offset = event.pos()
        self._drag_scene_pos = event.scenePos()
        self.resizing = (event.pos() - QPointF(self.size.width(), self.size.height())).manhattanLength() < 22
        if self.resizing:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._geometry_active = True
        self.update_geometry(event.scenePos(), event.modifiers())
        event.accept()

    def update_geometry(self, scene_pos, modifiers):
        self._drag_scene_pos = scene_pos
        local = self.mapFromScene(scene_pos)
        if self.resizing:
            self.prepareGeometryChange()
            width = min(max(15, local.x()), self.parentItem().rect.width() - self.pos().x())
            height = width * self.pm.height() / self.pm.width()
            maxh = self.parentItem().rect.height() - self.pos().y()
            if height > maxh:
                height = maxh
                width = height * self.pm.width() / self.pm.height()
            self.size = QSizeF(width, height)
        else:
            pos = self.parentItem().mapFromScene(scene_pos) - self._press_offset
            self.setPos(max(0, min(pos.x(), self.parentItem().rect.width()-self.size.width())),
                        max(0, min(pos.y(), self.parentItem().rect.height()-self.size.height())))
        if self.view:
            rect = self.view.snap_geometry(self.parentItem(), QRectF(self.pos(), self.size),
                modifiers, resize=self.resizing, ratio=self.pm.height()/self.pm.width(), minimum=(15., 0.))
            self.prepareGeometryChange()
            self.setPos(rect.topLeft())
            self.size = rect.size()
        self.update()

    def mouseReleaseEvent(self, event):
        if self._geometry_active:
            self.update_geometry(event.scenePos(), event.modifiers())
        self.dragging = False
        self.resizing = False
        if self.view:
            self.view.clear_snap_guides()
        super().mouseReleaseEvent(event)


class TextPlacement(QGraphicsObject):
    """Directly manipulated preview for a PDF paragraph, including line breaks."""

    geometryChanged = Signal()

    def __init__(self, text, rect, parent, rotation=0, editor=None, view=None):
        super().__init__(parent)
        self.view = view
        self.dragging = False
        self._geometry_active = False
        self._press_offset = QPointF()
        self._drag_scene_pos = QPointF()
        self.original_text = text
        self.preview_text = text
        self.size = QSizeF(max(18.0, rect.width()), max(10.0, rect.height()))
        self.resizing = False
        self.rotation = rotation
        self._press_pos = QPointF()
        self._press_size = QSizeF()
        self.setPos(rect.topLeft())
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(30)
        self.editor = editor
        self.proxy = None
        if editor is not None:
            editor.setParent(None)
            self.proxy = QGraphicsProxyWidget(self)
            self.proxy.setWidget(editor)
            self.proxy.setMinimumSize(1, 1)
            self.layout_editor()
            editor.show()

    def layout_editor(self):
        if self.proxy is None:
            return
        width, height = self.size.width(), self.size.height()
        if self.rotation in (90, 270):
            width, height = height, width
        self.proxy.resize(max(1, width), max(1, height))
        self.proxy.setRotation(self.rotation)
        x, y = {0:(0,0), 90:(self.size.width(),0),
                180:(self.size.width(),self.size.height()), 270:(0,self.size.height())}[self.rotation]
        self.proxy.setPos(x, y)

    def detach_editor(self, parent):
        if self.proxy:
            editor = self.editor
            self.proxy.setWidget(None)
            editor.hide()
            editor.setParent(parent)
            self.editor = None

    def boundingRect(self):
        return QRectF(0, 0, self.size.width(), self.size.height()).adjusted(-4, -4, 6, 6)

    def paint(self, painter, option, widget=None):
        rect = QRectF(0, 0, self.size.width(), self.size.height())
        painter.setPen(QPen(QColor('#62656b'), 1.5, Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 2, 2)
        painter.setBrush(QColor('white'))
        painter.drawRect(QRectF(rect.right() - 4, rect.bottom() - 4, 8, 8))

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self.dragging = True
        self._geometry_active = False
        self._press_offset = event.pos()
        self._drag_scene_pos = event.scenePos()
        self.resizing = (event.pos() - QPointF(self.size.width(), self.size.height())).manhattanLength() < 18
        self._press_pos = self.pos()
        self._press_size = QSizeF(self.size)
        if self.resizing:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        self._geometry_active = True
        self.update_geometry(event.scenePos(), event.modifiers())
        event.accept()

    def update_geometry(self, scene_pos, modifiers):
        self._drag_scene_pos = scene_pos
        local = self.mapFromScene(scene_pos)
        previous_pos, previous_size = QPointF(self.pos()), QSizeF(self.size)
        if self.resizing:
            self.prepareGeometryChange()
            width = min(max(18.0, local.x()), self.parentItem().rect.width() - self.x())
            height = min(max(10.0, local.y()), self.parentItem().rect.height() - self.y())
            self.size = QSizeF(width, height)
        else:
            pos = self.parentItem().mapFromScene(scene_pos) - self._press_offset
            self.setPos(max(0.0, min(pos.x(), self.parentItem().rect.width() - self.size.width())),
                        max(0.0, min(pos.y(), self.parentItem().rect.height() - self.size.height())))
        if self.view:
            rect = self.view.snap_geometry(self.parentItem(), QRectF(self.pos(), self.size), modifiers,
                                           resize=self.resizing)
            self.prepareGeometryChange()
            self.setPos(rect.topLeft())
            self.size = rect.size()
        self.layout_editor()
        self.update()
        if self.pos() != previous_pos or self.size != previous_size:
            self.geometryChanged.emit()
            # Inline text can grow after rewrapping; guides describe the final box.
            if self.view:
                self.view.refresh_snap_guides(self.parentItem(), QRectF(self.pos(), self.size),
                                             modifiers, self.resizing)

    def mouseReleaseEvent(self, event):
        if self._geometry_active:
            self.update_geometry(event.scenePos(), event.modifiers())
        was_resizing = self.resizing
        self.dragging = False
        self.resizing = False
        if self.view:
            self.view.clear_snap_guides()
        if not was_resizing:
            super().mouseReleaseEvent(event)
        if self.pos() != self._press_pos or self.size != self._press_size:
            self.geometryChanged.emit()


class PdfView(QGraphicsView):
    pageChanged = Signal(int)
    zoomChanged = Signal(float)
    textRequested = Signal(int, object)
    textFinished = Signal()
    textGeometryChanged = Signal()
    textPreviewError = Signal(str)
    imageSelected = Signal(int, int)
    selectedText = Signal(str)
    selectionCleared = Signal()
    renderError = Signal(str)
    filesDropped = Signal(list)
    stampRequested = Signal(int, object)
    stampCanceled = Signal()
    inkTransformed = Signal(int, int, object, object)
    regionCopied = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor('#e8ebef'))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setAcceptDrops(True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.document = None
        self.pages = []
        self.current = 0
        self.mode = 'continuous'
        self.page_wheel_clock = QElapsedTimer()
        self.reset_page_wheel()
        self.fit_mode = 'page'
        self.start_right = False
        self.text_mode = False
        self.text_placement = None
        self.text_page = None
        self.text_selection_data = None
        self.text_preview_source = None
        self.text_preview_document = None
        self.ink_selection = None
        self.ink_preview_document = None
        self.ink_preview_pixmaps = {}
        self.text_preview_values = None
        self.text_preview_pixmaps = {}
        self.text_preview_timer = QTimer(self)
        self.text_preview_timer.setSingleShot(True)
        self.text_preview_timer.timeout.connect(self.render_text_preview)
        self.image_mode = False
        self.copy_region_mode = False
        self.stamp_pixmap = None
        self.stamp_width_mm = 20
        self.stamp_ghost = None
        self.stamp_press_page = None
        self.placement = None
        self.snap_enabled = True
        self.snap_cache = OrderedDict()
        self.snap_guides = []
        self.snap_page = None
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.selection_start = None
        self.selection_item = None
        self.selected_page = None
        self.cursor_text_cache = OrderedDict()
        self.page_text_cache = OrderedDict()
        self.last_double_click = None
        self.selection_pointer = None
        self.selection_extending = False
        # Dragging a selection past the window edge scrolls the document.
        self.selection_scroll = QTimer(self)
        self.selection_scroll.setInterval(30)
        self.selection_scroll.timeout.connect(self.scroll_selection)
        self.highlight_data = []
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self.render_visible)
        self.verticalScrollBar().valueChanged.connect(self.schedule_render)
        self.horizontalScrollBar().valueChanged.connect(self.schedule_render)
        self.verticalScrollBar().valueChanged.connect(self.update_stamp_cursor)
        self.horizontalScrollBar().valueChanged.connect(self.update_stamp_cursor)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        from .pen import PenInput
        self.pen = PenInput(self)

    def load(self, document, current=0):
        self.reset_page_wheel()
        self.pen.cancel()
        self.clear_ink_selection()
        self.clear_text_selection()
        self.clear_region_selection()
        self.snap_cache.clear()
        self.cursor_text_cache.clear()
        self.page_text_cache.clear()
        self.placement = None
        self.text_placement = None
        self.text_page = None
        self.selection_item = None
        self.selection_start = None
        self.stamp_ghost = None
        self.selected_page = None
        self.scene().clear()
        self.cache.clear()
        self.cache_bytes = 0
        self.document = document
        self.pages = []
        self.current = min(max(current, 0), max(0, document.page_count-1))
        if document.doc:
            for i in range(document.page_count):
                width, height = document.page_size(i)
                item = PageItem(i, width, height)
                self.scene().addItem(item)
                self.pages.append(item)
            if document.opening_preview and document.opening_preview[0] == document.revision:
                preview = QPixmap()
                if preview.loadFromData(document.opening_preview[1]):
                    self.pages[0].pixmap = preview
                    self.pages[0].pixmap_rect = QRectF(self.pages[0].rect)
                document.opening_preview = None
        self.layout_pages()
        self.goto(self.current)
        self.update_stamp_cursor()

    def invalidate_page(self, index):
        for key in list(self.snap_cache):
            if key[0] == index:
                del self.snap_cache[key]
        self.clear_snap_guides()
        for key in list(self.cache):
            if key[0] == index:
                self.cache_bytes -= pixmap_bytes(self.cache.pop(key)[0])
        self.schedule_render()

    def snap_index(self, page_item):
        exclusion = (tuple(self.text_selection_data['bbox'])
                     if self.text_selection_data and self.text_page == page_item.index else None)
        key = (page_item.index, self.document.revision, exclusion)
        if key not in self.snap_cache:
            page = self.document.doc[page_item.index]
            objects = []

            def add(rect):
                rect = pymupdf.Rect(rect) * page.rotation_matrix
                objects.append(QRectF(rect.x0, rect.y0, rect.width, rect.height))

            excluded = pymupdf.Rect(exclusion) if exclusion else None
            data = page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
            for block in data['blocks']:
                if block['type'] != 0:
                    continue
                block_rect = pymupdf.Rect()
                for line in block['lines']:
                    line_rect = pymupdf.Rect()
                    for span in line['spans']:
                        rect = pymupdf.Rect(span['bbox'])
                        if not span['text'].strip() or (excluded and excluded.contains((rect.tl + rect.br)/2)):
                            continue
                        add(rect)
                        line_rect |= rect
                    if not line_rect.is_empty:
                        add(line_rect)
                        block_rect |= line_rect
                if not block_rect.is_empty:
                    add(block_rect)
            for info in page.get_image_info():
                add(info['bbox'])
            for drawing in page.get_drawings():
                add(drawing['rect'])
            for annotation in page.annots() or ():
                add(annotation.rect)
            self.snap_cache[key] = SnapIndex(page_item.rect, objects)
            while len(self.snap_cache) > 8:
                self.snap_cache.popitem(last=False)
        self.snap_cache.move_to_end(key)
        return self.snap_cache[key]

    def clear_snap_guides(self):
        self.snap_guides = []
        self.snap_page = None
        self.viewport().update()

    def snapping_active(self, modifiers):
        return self.snap_enabled and not (modifiers & Qt.KeyboardModifier.AltModifier)

    def snap_geometry(self, page, rect, modifiers=Qt.KeyboardModifier.NoModifier, **options):
        if not self.snapping_active(modifiers):
            self.clear_snap_guides()
            return rect
        result, self.snap_guides = self.snap_index(page).snap(
            rect, 7.0 / max(.01, abs(self.transform().m11())), **options)
        self.snap_page = page
        self.viewport().update()
        return result

    def refresh_snap_guides(self, page, rect, modifiers, resize=False):
        if self.snapping_active(modifiers):
            self.snap_guides = self.snap_index(page).guides(rect, resize)
            self.snap_page = page
            self.viewport().update()
        else:
            self.clear_snap_guides()

    def set_snap_enabled(self, enabled):
        self.snap_enabled = enabled
        self.clear_snap_guides()
        self.refresh_snap_interaction(QApplication.keyboardModifiers())

    def refresh_snap_interaction(self, modifiers):
        for placement in (self.placement, self.text_placement):
            if placement is not None and placement.dragging and placement._geometry_active:
                placement.update_geometry(placement._drag_scene_pos, modifiers)
        if self.stamp_pixmap is not None:
            self.update_stamp_cursor(modifiers=modifiers)

    def has_snap_interaction(self):
        return self.stamp_pixmap is not None or any(
            item is not None and item.dragging and item._geometry_active
            for item in (self.placement, self.text_placement))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Alt and self.has_snap_interaction():
            self.refresh_snap_interaction(event.modifiers() | Qt.KeyboardModifier.AltModifier)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key.Key_Alt and self.has_snap_interaction():
            self.refresh_snap_interaction(event.modifiers() & ~Qt.KeyboardModifier.AltModifier)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self.clear_snap_guides()
        # Edge scrolling stops; a drag that goes on restarts it on the next move.
        self.selection_scroll.stop()
        super().focusOutEvent(event)

    def drawForeground(self, painter, rect):
        super().drawForeground(painter, rect)
        if not self.snap_guides or self.snap_page is None or not self.snap_page.isVisible():
            return
        painter.save()
        bounds = self.snap_page.rect
        pen = QPen(QColor('#d63f88'), 1, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for axis, value in set(self.snap_guides):
            start, end = ((QPointF(value, bounds.top()), QPointF(value, bounds.bottom())) if axis == 0
                          else (QPointF(bounds.left(), value), QPointF(bounds.right(), value)))
            painter.drawLine(self.snap_page.mapToScene(start), self.snap_page.mapToScene(end))
        painter.restore()

    def start_stamp(self, pixmap, width_mm):
        self.stop_stamp()
        self.stamp_pixmap = pixmap
        self.stamp_width_mm = width_mm
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.update_stamp_cursor()

    def stop_stamp(self):
        self.clear_snap_guides()
        if self.stamp_ghost is not None:
            self.scene().removeItem(self.stamp_ghost)
            self.stamp_ghost = None
        self.stamp_pixmap = None
        self.stamp_press_page = None
        self.viewport().unsetCursor()

    def stamp_target(self, scene_point, modifiers=None):
        if self.stamp_pixmap is None or not self.document or not self.document.editable:
            return None
        page = self.page_at(scene_point)
        if page is None:
            return None
        ratio = self.stamp_pixmap.height()/self.stamp_pixmap.width()
        width = min(self.stamp_width_mm*72/25.4, page.rect.width(), page.rect.height()/ratio)
        height = width*ratio
        local = page.mapFromScene(scene_point)
        x = max(0, min(local.x()-width/2, page.rect.width()-width))
        y = max(0, min(local.y()-height/2, page.rect.height()-height))
        modifiers = QApplication.keyboardModifiers() if modifiers is None else modifiers
        return page, self.snap_geometry(page, QRectF(x, y, width, height), modifiers)

    def update_stamp_cursor(self, *_, modifiers=None):
        if self.stamp_pixmap is None:
            return
        local = self.viewport().mapFromGlobal(QCursor.pos())
        if not self.viewport().rect().contains(local):
            self.clear_snap_guides()
            if self.stamp_ghost is not None:
                self.stamp_ghost.hide()
            return
        self.move_stamp_preview(self.mapToScene(local), modifiers)

    def move_stamp_preview(self, scene_point, modifiers=None):
        target = self.stamp_target(scene_point, modifiers)
        if target is None:
            self.clear_snap_guides()
            if self.stamp_ghost is not None:
                self.stamp_ghost.hide()
            self.viewport().setCursor(Qt.CursorShape.ForbiddenCursor)
            return
        page, rect = target
        self.viewport().setCursor(Qt.CursorShape.CrossCursor)
        if self.stamp_ghost is None:
            self.stamp_ghost = self.scene().addPixmap(self.stamp_pixmap)
            self.stamp_ghost.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            self.stamp_ghost.setOpacity(.6)
            self.stamp_ghost.setZValue(50)
        self.stamp_ghost.setParentItem(page)
        self.stamp_ghost.setScale(rect.width()/self.stamp_pixmap.width())
        self.stamp_ghost.setPos(rect.topLeft())
        self.stamp_ghost.show()

    def leaveEvent(self, event):
        self.clear_snap_guides()
        if self.stamp_ghost is not None:
            self.stamp_ghost.hide()
        super().leaveEvent(event)

    def layout_pages(self):
        self.pen.cancel()
        self.clear_snap_guides()
        if not self.pages:
            return
        gap = 24
        if self.mode == 'single':
            groups = [[self.current]]
        elif self.is_spread:
            groups = spread_groups(len(self.pages), self.start_right)
            if self.mode == 'spread':
                groups = [next(row for row in groups if self.current in row)]
        elif self.mode == 'grid':
            groups = [list(range(i, min(i+3, len(self.pages)))) for i in range(0, len(self.pages), 3)]
        else:
            groups = [[i] for i in range(len(self.pages))]
        for item in self.pages:
            item.hide()
        if self.is_spread:
            # Reserve both physical slots, including an absent first/last leaf.
            # Align pages to a shared gutter even when paper sizes differ.
            all_groups = spread_groups(len(self.pages), self.start_right)
            columns = [max(self.pages[row[col] if row[col] is not None else row[1-col]].rect.width()
                           for row in all_groups) for col in (0, 1)]
            maxwidth = sum(columns) + gap
        else:
            maxwidth = max(sum(self.pages[i].rect.width() for i in row) + gap*(len(row)-1) for row in groups)
        y = gap
        for row in groups:
            if self.is_spread:
                for col, i in enumerate(row):
                    if i is not None:
                        x = gap + columns[0] - self.pages[i].rect.width() if col == 0 else gap*2 + columns[0]
                        self.pages[i].setPos(x, y)
                        self.pages[i].show()
                y += max(self.pages[i].rect.height() for i in row if i is not None) + gap
                continue
            width = sum(self.pages[i].rect.width() for i in row) + gap*(len(row)-1)
            x = gap + (maxwidth-width)/2
            for i in row:
                self.pages[i].setPos(x, y)
                self.pages[i].show()
                x += self.pages[i].rect.width() + gap
            y += max(self.pages[i].rect.height() for i in row) + gap
        self.scene().setSceneRect(0, 0, maxwidth+gap*2, y)
        self.apply_fit()
        self.schedule_render()

    @property
    def is_spread(self):
        return self.mode in ('spread', 'spread_continuous')

    def current_group(self):
        if self.is_spread and self.pages:
            return [i for row in spread_groups(len(self.pages), self.start_right)
                    if self.current in row for i in row if i is not None]
        return [self.current]

    def navigation_target(self, step):
        if self.is_spread and self.pages:
            groups = spread_groups(len(self.pages), self.start_right)
            target = next(i for i, row in enumerate(groups) if self.current in row) + step
            if target < 0:
                return -1
            if target >= len(groups):
                return len(self.pages)
            return next(i for i in groups[target] if i is not None)
        return self.current + step

    def navigate(self, step):
        target = self.navigation_target(step)
        if 0 <= target < len(self.pages):
            self.goto(target)

    def set_mode(self, mode):
        self.reset_page_wheel()
        self.mode = mode
        self.layout_pages()
        self.goto(self.current)

    def set_zoom(self, zoom, manual=True):
        if manual:
            self.fit_mode = None
        zoom = max(.12, min(4, zoom))
        anchor = self.transformationAnchor()
        if not manual:
            # Opening a dock or toolbar refits the page without a mouse move.
            # The under-mouse anchor may still refer to an old page position.
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setTransform(QTransform.fromScale(zoom, zoom))
        self.setTransformationAnchor(anchor)
        self.zoomChanged.emit(zoom)
        self.schedule_render()

    def apply_fit(self):
        if not self.pages or not self.fit_mode:
            return
        width = self.sceneRect().width()
        zoom = (self.viewport().width()-16) / max(1, width)
        if self.fit_mode == 'page':
            height = max(self.pages[i].rect.height() for i in self.current_group())
            zoom = min(zoom, (self.viewport().height()-30)/(height+48))
        self.set_zoom(zoom, False)

    def fit(self, mode):
        self.fit_mode = mode
        self.apply_fit()

    def goto(self, index):
        if self.ink_selection is not None and self.ink_selection.index != index:
            self.clear_ink_selection()
        if self.pen.page is not None and self.pen.page.index != index:
            self.pen.cancel()
        if not self.pages:
            return
        self.current = max(0, min(index, len(self.pages)-1))
        if self.mode in ('single', 'spread'):
            self.layout_pages()
        page = self.pages[self.current]
        top = self.mapFromScene(page.scenePos()).y()
        self.verticalScrollBar().setValue(self.verticalScrollBar().value()+top-20)
        self.pageChanged.emit(self.current)
        self.schedule_render()

    def schedule_render(self, *args):
        if not self.render_timer.isActive():
            self.render_timer.start(12)

    def render_visible(self):
        if not self.document or not self.document.doc:
            return
        visible = self.mapToScene(self.viewport().rect()).boundingRect()
        nearby = visible.adjusted(0, -100, 0, 100)
        targets = [p for p in self.pages if p.isVisible() and p.sceneBoundingRect().intersects(nearby)]
        if targets:
            center = visible.center()
            distances = {p.index: abs(p.sceneBoundingRect().center().y()-center.y()) for p in targets}
            nearest = min(distances.values())
            current = self.current if self.current in distances and distances[self.current] <= nearest+1 else min(distances,key=distances.get)
            if current != self.current and self.mode not in ('single', 'spread'):
                self.current = current
                self.pageChanged.emit(current)
        scale = self.transform().m11() * self.viewport().devicePixelRatioF() * RENDER_OVERSAMPLE
        for p in targets:
            clip = None
            if p.rect.width() * p.rect.height() * scale * scale > 8_000_000:
                # Zoomed documents / large drawing sheets only need the visible
                # region at full screen resolution. Snap to a pixel grid with
                # overscan to reuse images during small scrolls.
                local = p.mapRectFromScene(visible)
                step = 256 / scale
                clip = pymupdf.Rect(math.floor(local.left()/step)*step-step,
                                    math.floor(local.top()/step)*step-step,
                                    math.ceil(local.right()/step)*step+step,
                                    math.ceil(local.bottom()/step)*step+step) & self.document.doc[p.index].rect
                if clip.is_empty:
                    continue
            region = tuple(clip) if clip is not None else None
            key = (p.index, scale, region)
            if self.ink_selection is not None and p.index == self.ink_selection.index and self.ink_preview_document is not None:
                try:
                    preview_key = (scale, region)
                    if preview_key not in self.ink_preview_pixmaps:
                        self.ink_preview_pixmaps = {preview_key: page_raster(self.ink_preview_document[0], scale, clip)}
                    p.pixmap, p.pixmap_rect = self.ink_preview_pixmaps[preview_key]
                    p.update()
                except Exception as error:
                    self.renderError.emit(str(error))
                continue
            if p.index == self.text_page and self.text_preview_document is not None:
                try:
                    preview_key = (scale, region)
                    if preview_key not in self.text_preview_pixmaps:
                        self.text_preview_pixmaps = {preview_key: page_raster(self.text_preview_document[0], scale, clip)}
                    p.pixmap, p.pixmap_rect = self.text_preview_pixmaps[preview_key]
                    p.update()
                except Exception as e:
                    self.textPreviewError.emit(str(e))
                continue
            if key not in self.cache:
                try:
                    raster = page_raster(self.document.doc[p.index], scale, clip)
                    self.cache[key] = raster
                    self.cache_bytes += pixmap_bytes(raster[0])
                    p.pixmap, p.pixmap_rect = raster
                    p.update()
                    while len(self.cache) > 18 or self.cache_bytes > PAGE_CACHE_BYTES:
                        oldkey, old = self.cache.popitem(last=False)
                        self.cache_bytes -= pixmap_bytes(old[0])
                        if self.pages[oldkey[0]].pixmap is old[0]:
                            self.pages[oldkey[0]].pixmap = None
                            self.pages[oldkey[0]].update()
                    self.render_timer.start(0)
                except Exception as e:
                    self.renderError.emit(str(e))
                return
            p.pixmap, p.pixmap_rect = self.cache[key]
            self.cache.move_to_end(key)
            p.update()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, 'render_timer'):
            self.schedule_render()
        return result

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_fit()
        self.schedule_render()

    def reset_page_wheel(self):
        self.page_wheel_clock.invalidate()
        self.page_wheel_delta = 0
        self.page_wheel_pixels = None
        self.page_wheel_turned = False

    def scroll_page_wheel(self, event, precise):
        if not precise:
            super().wheelEvent(event)
        else:
            # QGraphicsView's scene wheel path reduces precise pixel scrolling
            # to an angle delta. Keep macOS scrolling at its native distance.
            dx, dy = event.pixelDelta().x(), event.pixelDelta().y()
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                dx, dy = dy, dx
            horizontal, vertical = self.horizontalScrollBar(), self.verticalScrollBar()
            horizontal.setValue(horizontal.value() - dx)
            vertical.setValue(vertical.value() - dy)
        event.accept()

    def wheel_page(self, event):
        phase = event.phase()
        if (phase == Qt.ScrollPhase.ScrollBegin or
                (phase == Qt.ScrollPhase.NoScrollPhase and self.page_wheel_clock.isValid()
                 and self.page_wheel_clock.elapsed() > 250)):
            self.reset_page_wheel()
        if phase == Qt.ScrollPhase.ScrollEnd:
            self.reset_page_wheel()
            event.accept()
            return
        self.page_wheel_clock.start()
        # Cocoa also supplies estimated pixels for ordinary mouse notches.
        # Only precise devices / phased gestures should use pixel thresholds.
        pixels = not event.pixelDelta().isNull() and (
            phase != Qt.ScrollPhase.NoScrollPhase or
            bool(event.pointingDevice().capabilities() & QInputDevice.Capability.PixelScroll))
        delta = event.pixelDelta() if pixels else event.angleDelta()
        if delta.isNull():
            event.accept()
            return
        if (abs(delta.x()) > abs(delta.y()) or
                event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.page_wheel_delta = 0
            self.scroll_page_wheel(event, pixels)
            return
        # A trackpad gesture can deliver dozens of updates and momentum events.
        # Once it turns a page, leave that page still until the next gesture.
        if self.page_wheel_turned:
            event.accept()
            return
        scroll = self.verticalScrollBar()
        dy = delta.y()
        at_edge = (dy < 0 and scroll.value() >= scroll.maximum() - 1 or
                   dy > 0 and scroll.value() <= scroll.minimum() + 1)
        if not at_edge:
            self.page_wheel_delta = 0
            self.scroll_page_wheel(event, pixels)
            return
        event.accept()
        if phase == Qt.ScrollPhase.ScrollMomentum:
            return
        if self.page_wheel_pixels != pixels or self.page_wheel_delta * dy < 0:
            self.page_wheel_delta = 0
        self.page_wheel_pixels = pixels
        self.page_wheel_delta += dy
        # One mouse notch is 120 eighth-degrees; touch scrolling uses pixels.
        if abs(self.page_wheel_delta) < (48 if pixels else 120):
            return
        self.page_wheel_delta = 0
        target = self.navigation_target(1 if dy < 0 else -1)
        if 0 <= target < len(self.pages):
            self.goto(target)
            scroll.setValue(scroll.minimum() if dy < 0 else scroll.maximum())
            self.page_wheel_turned = pixels or phase != Qt.ScrollPhase.NoScrollPhase

    def wheelEvent(self, event):
        if pinch.zooms(event):
            self.reset_page_wheel()
            if event.angleDelta().y():
                self.set_zoom(self.transform().m11() * pinch.wheel_zoom(event))
            event.accept()
        elif self.mode in ('single', 'spread') and self.pages:
            self.wheel_page(event)
        else:
            super().wheelEvent(event)

    def viewportEvent(self, event):
        factor = pinch.gesture_zoom(event)
        if factor is None:
            return super().viewportEvent(event)
        self.reset_page_wheel()
        self.set_zoom(self.transform().m11() * factor)
        event.accept()
        return True

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile().lower().endswith('.pdf')]
            if paths:
                self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def page_at(self, scene_pos):
        for item in self.scene().items(scene_pos):
            if isinstance(item, PageItem):
                return item
        return None

    def set_highlights(self, matches):
        for p in self.pages:
            p.highlights = []
        for index, rect in matches:
            r = pymupdf.Rect(rect) * self.document.doc[index].rotation_matrix
            self.pages[index].highlights.append(QRectF(r.x0, r.y0, r.width, r.height))
        self.scene().update()

    def mouseDoubleClickEvent(self, event):
        if self.stamp_pixmap is not None:
            self.mousePressEvent(event)
            return
        pos = self.mapToScene(event.position().toPoint())
        item = self.page_at(pos)
        if item is not None and self.selects_text(event) and self.select_text_unit(item, pos, 'word'):
            self.last_double_click = (time.monotonic(), event.position().toPoint())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def selects_text(self, event):
        return (event.button() == Qt.MouseButton.LeftButton and not self.copy_region_mode and not self.text_mode
                and not self.image_mode and not self.placement and self.stamp_pixmap is None
                and self.dragMode() == QGraphicsView.DragMode.NoDrag)

    def page_text(self, index):
        from .selection_widgets import PageText
        key = (index, self.document.revision)
        if key not in self.page_text_cache:
            try:
                self.page_text_cache[key] = PageText(self.document.doc[index])
            except Exception:
                self.page_text_cache[key] = PageText(None)
            while len(self.page_text_cache) > 8:
                self.page_text_cache.popitem(last=False)
        self.page_text_cache.move_to_end(key)
        return self.page_text_cache[key]

    def page_point(self, item, scene_pos):
        local = item.mapFromScene(scene_pos)
        return pymupdf.Point(local.x(), local.y()) * self.document.doc[item.index].derotation_matrix

    def select_text_unit(self, item, pos, unit):
        """Select the word (double-click) or paragraph (triple-click) under the pointer."""
        from .selection_widgets import TextSelection
        text = self.page_text(item.index)
        page = self.document.doc[item.index]
        point = self.page_point(item, pos)
        span = text.word(point) if unit == 'word' else text.paragraph(page, point)
        if not span:
            return False
        self.selectionCleared.emit()
        self.clear_region_selection()
        self.selected_page = item
        self.selection_item = TextSelection(self, item, text, page, span[0])
        self.selection_item.select(*span)
        self.selectedText.emit(self.selection_item.finish())
        return True

    def start_selection(self, item, pos, event):
        """Drag text in reading order like Acrobat. Alt, the capture tool, a page
        without text, or a drag that starts on a picture away from text select an area."""
        from .selection_widgets import RegionSelection, TextSelection
        self.clear_region_selection()
        self.selection_start = pos
        self.selection_press_position = event.position().toPoint()
        self.selection_pointer = event.position().toPoint()
        self.selected_page = item
        page = self.document.doc[item.index]
        point = self.page_point(item, pos)
        text = None
        if not self.copy_region_mode and not event.modifiers() & Qt.KeyboardModifier.AltModifier:
            text = self.page_text(item.index)
            if not text.chars or (not text.near(point) and any(
                    point in pymupdf.Rect(info['bbox']) for info in page.get_image_info())):
                text = None
        if text is not None:
            self.selection_item = TextSelection(self, item, text, page, text.caret(point))
        else:
            self.selection_item = RegionSelection(self, item)
            self.selection_item.set_scene_rect(QRectF(pos, pos))

    def update_selection_drag(self, viewport_pos):
        from .selection_widgets import TextSelection
        scene = self.mapToScene(viewport_pos)
        selection = self.selection_item
        if isinstance(selection, TextSelection):
            selection.select(selection.anchor, selection.page_text.caret(self.page_point(self.selected_page, scene)))
            self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        else:
            selection.set_scene_rect(QRectF(self.selection_start, scene).normalized())
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def scroll_selection(self):
        pointer = self.selection_pointer
        # A release another window took (a lost grab) never reaches mouseReleaseEvent.
        if (self.selection_start is None or pointer is None
                or not QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
            self.selection_scroll.stop()
            return
        area, margin = self.viewport().rect(), 24

        def overshoot(value, low, high):
            if value < low+margin:
                return max(-60, value-low-margin)
            if value > high-margin:
                return min(60, value-high+margin)
            return 0
        moved = False
        for bar, amount in ((self.horizontalScrollBar(), overshoot(pointer.x(), area.left(), area.right())),
                            (self.verticalScrollBar(), overshoot(pointer.y(), area.top(), area.bottom()))):
            if amount:
                before = bar.value()
                bar.setValue(before + int(math.copysign(max(1, abs(amount)//2), amount)))
                moved = moved or bar.value() != before
        if moved:
            self.update_selection_drag(pointer)

    def mousePressEvent(self, event):
        pos = self.mapToScene(event.position().toPoint())
        if self.ink_selection is not None and self.ink_selection.contains(self.ink_selection.mapFromScene(pos)):
            super().mousePressEvent(event)
            return
        if (not self.copy_region_mode and not self.text_mode and not self.image_mode and not self.placement and self.stamp_pixmap is None
                and self.dragMode() == QGraphicsView.DragMode.NoDrag and event.button() == Qt.MouseButton.LeftButton):
            item = self.page_at(pos)
            if item is not None:
                from .ink_widgets import ink_at
                xref = ink_at(self, item, pos)
                if xref is not None:
                    self.selectionCleared.emit()
                    self.select_ink(item.index, xref)
                    event.accept()
                    return
        if self.stamp_pixmap is not None:
            if event.button() == Qt.MouseButton.RightButton:
                self.stampCanceled.emit()
            elif event.button() == Qt.MouseButton.LeftButton:
                target = self.stamp_target(pos, event.modifiers())
                self.stamp_press_page = target[0].index if target else None
            event.accept()
            return
        item = self.page_at(pos)
        if item is not None and self.selects_text(event):
            double = self.last_double_click
            self.last_double_click = None
            if (double and time.monotonic()-double[0] <= QApplication.doubleClickInterval()/1000
                    and (event.position().toPoint()-double[1]).manhattanLength() < 2*QApplication.startDragDistance()
                    and self.select_text_unit(item, pos, 'paragraph')):
                event.accept()
                return
            from .selection_widgets import TextSelection
            selection = self.selection_item
            if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier and isinstance(selection, TextSelection)
                    and selection.finished and self.selected_page is item):
                # Shift+click extends the selection from its first end, as in Acrobat.
                selection.finished = False
                self.selection_extending = True
                self.selection_start = pos
                self.selection_press_position = event.position().toPoint()
                self.selection_pointer = event.position().toPoint()
                self.update_selection_drag(self.selection_pointer)
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton and not self.text_mode and not self.placement:
            self.selectionCleared.emit()
        if not item and self.text_mode and event.button() == Qt.MouseButton.LeftButton:
            self.textFinished.emit()
        if item and not self.placement and event.button() == Qt.MouseButton.LeftButton:
            if self.text_mode:
                if self.text_placement and self.text_placement.contains(self.text_placement.mapFromScene(pos)):
                    super().mousePressEvent(event)
                    return
                page = self.document.doc[item.index]
                local = item.mapFromScene(pos)
                point = pymupdf.Point(local.x(), local.y()) * page.derotation_matrix
                group = text_group_at(page, point)
                if group:
                    group['click_point'] = (point.x, point.y)
                    self.textRequested.emit(item.index, group)
                    event.accept()
                    return
                self.textFinished.emit()
            if self.image_mode:
                if self.select_image_at(item, pos):
                    event.accept()
                    return
            if self.dragMode() == QGraphicsView.DragMode.NoDrag and not self.text_mode:
                self.start_selection(item, pos, event)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.stamp_pixmap is not None:
            self.move_stamp_preview(self.mapToScene(event.position().toPoint()), event.modifiers())
            event.accept()
            return
        if self.selection_start is not None:
            self.selection_pointer = event.position().toPoint()
            self.update_selection_drag(self.selection_pointer)
            if not self.selection_scroll.isActive():
                self.selection_scroll.start()
            event.accept()
            return
        super().mouseMoveEvent(event)
        self.update_content_cursor(self.mapToScene(event.position().toPoint()))

    def update_content_cursor(self, scene_pos=None):
        if self.pen.enabled:
            self.pen.update_cursor()
            return
        if self.copy_region_mode:
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)
            return
        if (self.stamp_pixmap is not None or self.placement is not None or self.text_placement is not None
                or self.dragMode() != QGraphicsView.DragMode.NoDrag):
            return
        cursor = Qt.CursorShape.ArrowCursor
        if scene_pos is None:
            scene_pos = self.mapToScene(self.viewport().mapFromGlobal(QCursor.pos()))
        if self.ink_selection is not None and self.ink_selection.contains(self.ink_selection.mapFromScene(scene_pos)):
            return
        item = self.page_at(scene_pos) if not self.image_mode else None
        if item is not None:
            page = self.document.doc[item.index]
            key = (item.index, self.document.revision)
            if key not in self.cursor_text_cache:
                try:
                    self.cursor_text_cache[key] = [pymupdf.Rect(word[:4]) for word in page.get_text('words')]
                except Exception:
                    self.cursor_text_cache[key] = []
                while len(self.cursor_text_cache) > 16:
                    self.cursor_text_cache.popitem(last=False)
            self.cursor_text_cache.move_to_end(key)
            local = item.mapFromScene(scene_pos)
            point = pymupdf.Point(local.x(), local.y()) * page.derotation_matrix
            if any(point in rect for rect in self.cursor_text_cache[key]):
                cursor = Qt.CursorShape.IBeamCursor
        self.viewport().setCursor(cursor)

    def mouseReleaseEvent(self, event):
        if self.stamp_pixmap is not None:
            target = self.stamp_target(self.mapToScene(event.position().toPoint()), event.modifiers())
            self.clear_snap_guides()
            if event.button() == Qt.MouseButton.LeftButton and target and target[0].index == self.stamp_press_page:
                page, shown = target
                self.stamp_press_page = None
                rect = pymupdf.Rect(shown.x(), shown.y(), shown.right(), shown.bottom()) * self.document.doc[page.index].derotation_matrix
                self.stampRequested.emit(page.index, rect)
            self.stamp_press_page = None
            event.accept()
            return
        if self.selection_start is not None:
            from .selection_widgets import TextSelection
            self.selection_scroll.stop()
            item = self.selected_page
            pos = self.mapToScene(event.position().toPoint())
            self.update_selection_drag(event.position().toPoint())
            # A click selects an image; a drag selects text, including OCR text
            # drawn over a scanned page image. Shift+click extends a selection.
            clicked = (event.position().toPoint() - self.selection_press_position).manhattanLength() < QApplication.startDragDistance()
            if clicked and not self.selection_extending:
                self.clear_region_selection()
                if not self.copy_region_mode:
                    self.select_image_at(item, pos)
                self.update_content_cursor(pos)
                event.accept()
                return
            if isinstance(self.selection_item, TextSelection) and self.selection_item.text_path.isEmpty():
                # Dragging across empty space selects nothing. Shift+clicking back onto the
                # anchor empties a selection, so its text must not stay copyable either.
                self.clear_region_selection()
                self.selectionCleared.emit()
                self.update_content_cursor(pos)
                event.accept()
                return
            text = self.selection_item.finish(self.document.doc[item.index], not self.copy_region_mode)
            self.selectedText.emit(text)
            self.selection_start = None
            self.selection_extending = False
            if self.copy_region_mode:
                self.regionCopied.emit()
            self.update_content_cursor(pos)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def select_ink(self, index, xref):
        from .ink_widgets import InkSelection
        self.clear_ink_selection()
        preview = pymupdf.open()
        try:
            original = self.document.doc[index]
            ordinal = [annotation.xref for annotation in original.annots()].index(xref)
            preview.insert_pdf(self.document.doc, from_page=index, to_page=index)
            page = preview[0]
            annotation = list(page.annots())[ordinal]
            page.delete_annot(annotation)
            self.ink_selection = InkSelection(self, self.pages[index], xref)
            self.ink_selection.committed.connect(self.inkTransformed.emit)
            self.ink_preview_document = preview
            self.ink_preview_pixmaps.clear()
            self.render_visible()
        except Exception:
            preview.close()
            self.clear_ink_selection()
            raise

    def clear_ink_selection(self):
        if self.ink_selection is not None:
            item = self.ink_selection
            self.ink_selection = None
            if item.scene() is not None:
                item.scene().removeItem(item)
            item.setParentItem(None)
            item.deleteLater()
        if self.ink_preview_document is not None:
            self.ink_preview_document.close()
            self.ink_preview_document = None
        self.ink_preview_pixmaps.clear()
        self.schedule_render()

    def clear_region_selection(self):
        self.selection_scroll.stop()
        self.selection_start = None
        self.selection_extending = False
        self.selected_page = None
        if self.selection_item is not None:
            self.selection_item.dispose()
            self.selection_item = None

    def selected_region(self):
        if self.selected_page is None or self.selection_item is None or self.selection_start is not None:
            return None
        item = self.selected_page
        r = self.selection_item.rect()
        if r.width() < .5 or r.height() < .5:
            return None
        return item.index, pymupdf.Rect(r.left(), r.top(), r.right(), r.bottom())

    def select_image_at(self, item, scene_pos):
        page = self.document.doc[item.index]
        local = item.mapFromScene(scene_pos)
        point = pymupdf.Point(local.x(), local.y()) * page.derotation_matrix
        for info in reversed(page.get_image_info(xrefs=True)):
            if info['xref'] and point in pymupdf.Rect(info['bbox']):
                self.clear_region_selection()
                self.set_highlights([(item.index, info['bbox'])])
                self.imageSelected.emit(item.index, info['xref'])
                return True
        return False

    def place_image(self, pixmap, width_mm=None, page_index=None, point=None):
        if not self.pages:
            return
        self.cancel_image()
        p = self.pages[self.current if page_index is None else page_index]
        cursor = self.viewport().mapFromGlobal(QCursor.pos())
        scene_cursor = self.mapToScene(cursor)
        under_cursor = self.page_at(scene_cursor) if self.viewport().rect().contains(cursor) else None
        if under_cursor and width_mm is None:
            p = under_cursor
        width = min(160, p.rect.width()*.4, p.rect.height()*.4*pixmap.width()/max(1,pixmap.height()))
        if width_mm is not None:
            width = min(width_mm*72/25.4, p.rect.width(), p.rect.height()*pixmap.width()/max(1,pixmap.height()))
        self.placement = ImagePlacement(pixmap, width, p, self)
        if point is not None:
            self.placement.setPos(max(0,min(point.x(),p.rect.width()-width)),max(0,min(point.y(),p.rect.height()-self.placement.size.height())))
        elif under_cursor and width_mm is None:
            local = p.mapFromScene(scene_cursor)
            self.placement.setPos(max(0,min(local.x(),p.rect.width()-width)),max(0,min(local.y(),p.rect.height()-self.placement.size.height())))
        else:
            self.placement.setPos((p.rect.width()-width)/2, min(p.rect.height()/3,p.rect.height()-self.placement.size.height()))
        rect = self.snap_geometry(p, QRectF(self.placement.pos(), self.placement.size), QApplication.keyboardModifiers())
        self.placement.setPos(rect.topLeft())
        self.clear_snap_guides()

    def cancel_image(self):
        self.clear_snap_guides()
        if self.placement:
            self.scene().removeItem(self.placement)
            self.placement = None

    def image_rect(self):
        p = self.placement
        index = p.parentItem().index
        rect = pymupdf.Rect(p.x(), p.y(), p.x()+p.size.width(), p.y()+p.size.height())
        return index, rect * self.document.doc[index].derotation_matrix

    def select_text(self, page_index, span, editor=None):
        self.clear_text_selection()
        page = self.document.doc[page_index]
        shown = pymupdf.Rect(span['bbox']) * page.rotation_matrix
        item = self.pages[page_index]
        self.text_placement = TextPlacement(
            span.get('text', ''), QRectF(shown.x0, shown.y0, shown.width, shown.height), item, page.rotation, editor, self)
        self.text_placement.geometryChanged.connect(self.textGeometryChanged.emit)
        self.text_page = page_index
        self.text_selection_data = span
        self.scene().setFocusItem(self.text_placement)
        if editor is not None:
            self.text_preview_values = ('', None, 11, (0,0,0), None)
            self.render_text_preview()

    def style_text_editor(self, font, size, color):
        placement = self.text_placement
        if not placement or placement.editor is None:
            return
        editor = placement.editor
        lineheight = self.text_selection_data.get('lineheight') or 1.4
        signature = (font.family(), size, color.name(), lineheight)
        if getattr(editor, '_adf_style', None) == signature and abs(editor.textCursor().blockFormat().lineHeight() - size * lineheight) < .01:
            return
        editor._adf_style = signature
        blocker = QSignalBlocker(editor)
        font = QFont(font)
        font.setPointSizeF(size * 72 / editor.logicalDpiY())
        family = font.family().replace('"', '')
        editor.setStyleSheet(f'QTextEdit {{ background: transparent; border: none; padding: 0; font-family: "{family}"; font-size: {size}px; color: {color.name()}; }}')
        editor.document().setDefaultFont(font)
        saved_cursor = editor.textCursor()
        cursor = QTextCursor(editor.document())
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        char_format = QTextCharFormat()
        char_format.setFont(font)
        char_format.setForeground(color)
        cursor.setCharFormat(char_format)
        block = QTextBlockFormat()
        block.setLineHeight(size * lineheight, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        cursor.setBlockFormat(block)
        cursor.endEditBlock()
        editor.setTextCursor(saved_cursor)
        editor.setCurrentCharFormat(char_format)
        palette = editor.palette()
        palette.setColor(QPalette.ColorRole.Text, color)
        palette.setColor(QPalette.ColorRole.Base, QColor(0,0,0,0))
        editor.setPalette(palette)

    def grow_text_editor(self, text, fontfile, size, fontbuffer=None):
        placement = self.text_placement
        if not placement or placement.editor is None or not text:
            return
        from .document import _font, _wrapped_text
        font, _, _ = _font(text, fontfile, fontbuffer)
        target = self.text_rect()
        lines = _wrapped_text(text, font, size, target.width)
        if lines is None:
            return
        factor = self.text_selection_data.get('lineheight') or max(font.ascender-font.descender, 1.2)
        height = max((font.ascender-font.descender + (len(lines)-1) * factor) * size,
                     placement.editor.document().size().height()) + 1
        if height <= target.height:
            return
        bounds = self.document.doc[self.text_page].rect * self.document.doc[self.text_page].derotation_matrix
        target.y1 = min(bounds.y1, target.y0 + height)
        shown = target * self.document.doc[self.text_page].rotation_matrix
        placement.prepareGeometryChange()
        placement.size = QSizeF(shown.width, shown.height)
        placement.setPos(shown.x0, shown.y0)
        placement.layout_editor()
        placement.update()

    def update_text_preview(self, text, fontfile, size, color, fontbuffer=None):
        if self.text_placement:
            try:
                self.grow_text_editor(text, fontfile, size, fontbuffer)
            except (ValueError, RuntimeError) as exc:
                self.textPreviewError.emit(str(exc))
            self.text_preview_values = (text, fontfile, size, color, fontbuffer)
            if not self.text_preview_timer.isActive():
                self.text_preview_timer.start(80)

    def render_text_preview(self):
        if not self.text_placement or self.text_preview_values is None:
            return
        # The inline widget paints new text. Its background only needs one
        # redaction of the original paragraph, never one redaction per keypress.
        if self.text_placement.editor is not None and self.text_preview_document is not None:
            return
        from .document import replace_page_text
        preview = None
        try:
            if self.text_preview_source is None:
                with pymupdf.open() as source:
                    source.insert_pdf(self.document.doc, from_page=self.text_page, to_page=self.text_page)
                    self.text_preview_source = source.tobytes()
            preview = pymupdf.open(stream=self.text_preview_source, filetype='pdf')
            text, fontfile, size, color, fontbuffer = self.text_preview_values
            selection = self.text_selection_data
            replace_page_text(preview[0], selection['bbox'], '' if self.text_placement.editor else text, font_size=size, color=color,
                              fontfile=fontfile, fit=True, target_rect=self.text_rect(),
                              source_rects=selection.get('source_rects'), lineheight=selection.get('lineheight'), fontbuffer=fontbuffer)
            if self.text_preview_document is not None:
                self.text_preview_document.close()
            self.text_preview_document = preview
            self.text_preview_pixmaps.clear()
            preview = None
            self.render_visible()
        except Exception as e:
            self.textPreviewError.emit(str(e))
        finally:
            if preview is not None:
                preview.close()

    def clear_text_selection(self):
        self.clear_snap_guides()
        self.text_preview_timer.stop()
        if self.text_preview_document is not None:
            self.text_preview_document.close()
        self.text_preview_document = None
        self.text_preview_source = None
        self.text_preview_values = None
        self.text_selection_data = None
        self.text_preview_pixmaps.clear()
        if self.text_placement:
            try:
                self.text_placement.detach_editor(self.window())
                self.scene().removeItem(self.text_placement)
                self.text_placement.deleteLater()
            except RuntimeError:
                pass
        self.text_placement = None
        self.text_page = None
        self.schedule_render()

    def text_rect(self):
        if not self.text_placement or self.text_page is None:
            return None
        placement = self.text_placement
        shown = pymupdf.Rect(placement.x(), placement.y(),
                             placement.x() + placement.size.width(), placement.y() + placement.size.height())
        return shown * self.document.doc[self.text_page].derotation_matrix


class ThumbnailDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        view = self.parent()
        body = view.item_body_rect(index.row())
        page = view.thumbnail_rect(index.row())
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#d5d5d8' if selected else '#ececee'))
            painter.drawRoundedRect(body.adjusted(5, 3, -5, -3), 12, 12)
        painter.fillRect(page.translated(1, 2), QColor(0, 0, 0, 24))
        painter.fillRect(page, Qt.GlobalColor.white)
        pm = index.data(THUMB_PIXMAP_ROLE)
        if isinstance(pm, QPixmap) and not pm.isNull():
            painter.drawPixmap(page, pm, QRectF(pm.rect()))
        painter.setPen(QColor('#62656b' if selected else '#697586'))
        painter.drawText(QRectF(body.left(), page.bottom()+6, body.width(), 22),
                         Qt.AlignmentFlag.AlignCenter, str(index.data(Qt.ItemDataRole.DisplayRole)))
        painter.restore()


class ThumbnailList(QListWidget):
    # Five 36px footer buttons and their four 6px gaps.
    THUMB_WIDTH = 204
    CELL_WIDTH = THUMB_WIDTH + 24
    reordered = Signal(list)
    filesDropped = Signal(list)
    filesInserted = Signal(list, int)
    insertionRequested = Signal(int, object)
    renderError = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('pageThumbnails')
        self.setStyleSheet('QListWidget#pageThumbnails::item { padding: 0; margin: 0; border: none; }')
        self.columns = 1
        self.cell_height = math.ceil(self.THUMB_WIDTH * 1.414) + 46
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Snap)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setWordWrap(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.hover_slot = None
        self.drag_active = False
        self.insert_button = QToolButton(self.viewport())
        from .theme import icon
        self.insert_button.setIcon(icon('plus', '#62656b'))
        self.insert_button.setToolTip('페이지 추가')
        self.insert_button.setAccessibleName('이 위치에 페이지 추가')
        self.insert_button.setFixedSize(30, 30)
        self.insert_button.setStyleSheet('QToolButton { background: white; border: 1px solid #bfc1c6; border-radius: 15px; padding: 3px; } QToolButton:hover { background: #e0e1e4; }')
        self.insert_button.hide()
        self.insert_button.clicked.connect(self.request_insertion)
        self.document = None
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.layout_signature = None
        self.drop_y = None
        self.drop_line = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.render_visible)
        self.verticalScrollBar().valueChanged.connect(lambda: self.timer.start(15))

    def load(self, document, current=0):
        self.set_hover_slot(None)
        self.blockSignals(True)
        self.clear()
        self.cache.clear()
        self.cache_bytes = 0
        self.document = document
        self.layout_signature = None
        self.cell_height = max((math.ceil(self.thumbnail_size(i).height()) + 46
                                for i in range(document.page_count)), default=334)
        for i in range(document.page_count):
            item = QListWidgetItem(str(i+1))
            item.setData(Qt.ItemDataRole.UserRole, i)
            self.addItem(item)
        self.update_thumbnail_layout()
        if self.count():
            self.setCurrentRow(min(current, self.count()-1))
        self.blockSignals(False)
        self.timer.start(0)

    def invalidate_page(self, index):
        if index in self.cache:
            self.cache_bytes -= pixmap_bytes(self.cache.pop(index))
        self.timer.start(0)

    def render_visible(self):
        if not self.document or not self.document.doc:
            return
        self.update_thumbnail_layout()
        viewport = self.viewport().rect().adjusted(0, -180, 0, 180)
        for row in range(self.count()):
            item = self.item(row)
            index = item.data(Qt.ItemDataRole.UserRole)
            if self.visualItemRect(item).intersects(viewport) and index not in self.cache:
                try:
                    page = self.document.doc[index]
                    rect = self.thumbnail_rect(row)
                    pm = page_pixmap(page, rect.width()/page.rect.width * self.viewport().devicePixelRatioF() * RENDER_OVERSAMPLE)
                    item.setData(THUMB_PIXMAP_ROLE, pm)
                    self.cache[index] = pm
                    self.cache_bytes += pixmap_bytes(pm)
                    while len(self.cache) > 48 or self.cache_bytes > THUMB_CACHE_BYTES:
                        old_index, old_pm = self.cache.popitem(last=False)
                        self.cache_bytes -= pixmap_bytes(old_pm)
                        self.item(old_index).setData(THUMB_PIXMAP_ROLE, None)
                    self.timer.start(0)
                except Exception as e:
                    self.renderError.emit(str(e))
                return
            if index in self.cache and self.visualItemRect(item).intersects(viewport):
                self.cache.move_to_end(index)

    def thumbnail_size(self, row):
        width = self.THUMB_WIDTH
        if not self.document or not self.document.doc:
            return QSizeF(width, width * 1.414)
        page_width, page_height = self.document.page_size(row)
        # Fixed artwork width; cap exceptionally tall pages to two widths.
        height = min(width * page_height / page_width, width * 2)
        return QSizeF(height * page_width / page_height, height)

    def row_size(self, row):
        gap = 40 if self.item(row).data(Qt.ItemDataRole.UserRole + 5) else 0
        return QSize(max(self.CELL_WIDTH, (self.viewport().width() - 1) // self.columns), self.cell_height + gap)

    def item_body_rect(self, row):
        rect = QRectF(self.visualItemRect(self.item(row)))
        gap = self.item(row).data(Qt.ItemDataRole.UserRole + 5)
        if gap == 'before':
            rect.adjust(0, 40, 0, 0)
        elif gap == 'after':
            rect.adjust(0, 0, 0, -40)
        return rect

    def thumbnail_rect(self, row):
        body = self.item_body_rect(row)
        size = self.thumbnail_size(row)
        return QRectF(body.center().x()-size.width()/2, body.top()+10, size.width(), size.height())

    def update_thumbnail_layout(self):
        signature = (self.viewport().width(), self.viewport().devicePixelRatioF())
        if signature == self.layout_signature:
            return
        old_signature = self.layout_signature
        self.layout_signature = signature
        self.columns = max(1, (self.viewport().width() - 1) // self.CELL_WIDTH)
        # Resizing only reflows fixed-size thumbnails. Re-render on a display
        # density change, but keep cached artwork when adding/removing columns.
        invalidate = old_signature is None or old_signature[1] != signature[1]
        if invalidate:
            self.cache.clear()
            self.cache_bytes = 0
        for row in range(self.count()):
            if invalidate:
                self.item(row).setData(THUMB_PIXMAP_ROLE, None)
            self.item(row).setSizeHint(self.row_size(row))
        self.doItemsLayout()
        self.position_insert_button()

    def position_insert_button(self):
        if self.hover_slot is not None:
            gap = self.hover_gap_rect()
            self.insert_button.move(round(gap.center().x()-15), round(gap.center().y()-15))

    def set_hover_slot(self, slot):
        if self.drag_active or QApplication.mouseButtons() != Qt.MouseButton.NoButton:
            slot = None
        if slot == self.hover_slot:
            return
        if self.hover_slot is not None and self.count():
            old = self.item(max(0, self.hover_slot - 1))
            if old is not None:
                old.setData(Qt.ItemDataRole.UserRole + 5, None)
                old.setSizeHint(self.row_size(self.row(old)))
        self.hover_slot = slot
        self.insert_button.hide()
        if slot is not None and self.count():
            item = self.item(max(0, slot - 1))
            item.setData(Qt.ItemDataRole.UserRole + 5, 'before' if slot == 0 else 'after')
            item.setSizeHint(self.row_size(self.row(item)))
        self.doItemsLayout()
        if slot is not None:
            self.position_insert_button()
            self.insert_button.show()
            self.insert_button.raise_()
        self.viewport().update()

    def hover_gap_rect(self):
        if self.hover_slot is None:
            return QRectF()
        rect = QRectF(self.visualItemRect(self.item(max(0, self.hover_slot - 1))))
        return QRectF(rect.left(), rect.top() if self.hover_slot == 0 else rect.bottom()-40,
                      rect.width(), 40)

    def request_insertion(self):
        if self.hover_slot is not None and not self.drag_active:
            slot = self.hover_slot
            self.insertionRequested.emit(slot, self.insert_button.mapToGlobal(self.insert_button.rect().bottomLeft()))
            self.set_hover_slot(None)

    def mouseMoveEvent(self, event):
        point = event.position()
        slot = None
        if self.hover_slot is not None and self.hover_gap_rect().adjusted(0, -10, 0, 10).contains(point):
            slot = self.hover_slot
        elif not self.drag_active and not event.buttons():
            for row in range(self.count()):
                rect = self.visualItemRect(self.item(row))
                if not rect.left() <= point.x() <= rect.right():
                    continue
                if abs(point.y() - rect.top()) < 12:
                    slot = row
                    break
                if abs(point.y() - rect.bottom()) < 12:
                    slot = row + 1
                    break
        self.set_hover_slot(slot)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        self.set_hover_slot(None)
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        if not self.viewport().rect().contains(self.viewport().mapFromGlobal(QCursor.pos())):
            self.set_hover_slot(None)
        super().leaveEvent(event)

    def wheelEvent(self, event):
        self.set_hover_slot(None)
        super().wheelEvent(event)

    def startDrag(self, actions):
        self.drag_active = True
        self.set_hover_slot(None)
        try:
            super().startDrag(actions)
        finally:
            self.drag_active = False
            self.drop_y = None
            self.drop_line = None
            self.viewport().update()

    def insertion_index_at(self, point):
        item = self.itemAt(point.toPoint())
        if item:
            rect = self.visualItemRect(item)
            after = point.x() > rect.center().x() if self.columns > 1 else point.y() > rect.center().y()
            return self.row(item) + int(after)
        if self.count() and point.y() < self.visualItemRect(self.item(0)).top():
            return 0
        # Empty space after the final tile of a visual row belongs to that row.
        for row in range(self.count() - 1, -1, -1):
            rect = self.visualItemRect(self.item(row))
            if rect.top() <= point.y() <= rect.bottom():
                return row + 1
        return self.count()

    def dropEvent(self, event):
        target = self.insertion_index_at(event.position())
        self.drag_active = False
        self.drop_y = None
        self.drop_line = None
        self.viewport().update()
        if event.mimeData().hasUrls():
            from .document import PAGE_FILE_SUFFIXES
            from pathlib import Path
            paths = [u.toLocalFile() for u in event.mimeData().urls()
                     if u.isLocalFile() and Path(u.toLocalFile()).suffix.lower() in PAGE_FILE_SUFFIXES]
            if paths:
                self.filesInserted.emit(paths, target)
            event.acceptProposedAction()
            return
        # Explicit ordering avoids QListWidget IconMode's visual-only move semantics.
        selected = sorted(self.row(i) for i in self.selectedItems())
        rest = [i for i in range(self.count()) if i not in selected]
        insert = target - sum(i < target for i in selected)
        order = rest[:insert] + selected + rest[insert:]
        event.accept()
        if order != list(range(self.count())):
            self.reordered.emit(order)

    def dragEnterEvent(self, event):
        self.drag_active = True
        self.set_hover_slot(None)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        self.drag_active = True
        self.set_hover_slot(None)
        item = self.itemAt(event.position().toPoint())
        if item:
            rect = self.visualItemRect(item)
            self.drop_y = rect.top()-2 if event.position().y() < rect.center().y() else rect.bottom()+2
        else:
            self.drop_y = self.visualItemRect(self.item(self.count()-1)).bottom()+2 if self.count() else 5
        if self.columns > 1 and self.count():
            slot = self.insertion_index_at(event.position())
            rect = self.visualItemRect(self.item(min(slot, self.count() - 1)))
            x = rect.right() - 3 if slot == self.count() else rect.left() + 3
            self.drop_line = (x, rect.top() + 8, x, rect.bottom() - 8)
        else:
            self.drop_line = (12, self.drop_y, self.viewport().width() - 12, self.drop_y)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self.drag_active = False
        self.drop_y = None
        self.drop_line = None
        self.viewport().update()
        super().dragLeaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.drop_line is not None:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor('#62656b'),3))
            painter.drawLine(*self.drop_line)
            painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_thumbnail_layout()
        self.timer.start(0)

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, 'timer'):
            self.update_thumbnail_layout()
            self.timer.start(0)
        return result
