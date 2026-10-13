"""PDF hyperlinks in the viewer: a preview while the pointer rests on one, a click to follow."""
from collections import OrderedDict
from urllib.parse import unquote, urlsplit

import pymupdf
from PySide6.QtCore import QObject, QPoint, QRectF, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from .theme import ACCENT

HOVER_DELAY = 350
PREVIEW_WIDTH = 240
# Opened with the system browser or mail app. Others (javascript:, file:,
# launch actions, other PDFs) are shown in the preview but never run.
OPEN_SCHEMES = {'http', 'https', 'mailto'}


def link_target(link):
    """('page', index, y) for a jump inside this PDF, ('url', uri) to open, ('blocked', text) otherwise."""
    kind = link.get('kind')
    if kind in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED) and isinstance(link.get('page'), int) and link['page'] >= 0:
        to = link.get('to')
        return ('page', link['page'], to.y if isinstance(to, pymupdf.Point) else None)
    if kind == pymupdf.LINK_URI:
        uri = (link.get('uri') or '').strip()
        if urlsplit(uri).scheme.lower() in OPEN_SCHEMES:
            return ('url', uri)
        return ('blocked', uri or '알 수 없는 링크')
    return ('blocked', link.get('file') or link.get('uri') or '다른 파일을 여는 링크')


class LinkPreview(QFrame):
    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setObjectName('linkPreview')
        self.setStyleSheet('#linkPreview{background:#ffffff;border:1px solid #c7c8cb;border-radius:6px}'
                           'QLabel{background:transparent;color:#303238}'
                           '#linkTitle{font-weight:600}#linkHint{color:#797d84}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)
        self.image = QLabel()
        self.image.setStyleSheet('border:1px solid #e1e3e7')
        self.title = QLabel(objectName='linkTitle')
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setMaximumWidth(PREVIEW_WIDTH + 80)
        self.hint = QLabel(objectName='linkHint')
        for label in (self.image, self.title, self.detail, self.hint):
            layout.addWidget(label)

    def show_link(self, document, link, global_pos):
        target = link_target(link)
        self.image.hide()
        self.detail.hide()
        if target[0] == 'page':
            self.title.setText(f'{target[1] + 1}쪽')
            self.hint.setText('클릭하면 이동')
            pixmap = self.page_preview(document, target[1], target[2])
            if pixmap is not None:
                self.image.setPixmap(pixmap)
                self.image.show()
        else:
            uri = target[1]
            parts = urlsplit(uri)
            if target[0] == 'url' and parts.scheme.lower() == 'mailto':
                self.title.setText(unquote(parts.path) or '메일')
                self.hint.setText('클릭하면 메일 앱에서 열기')
            elif target[0] == 'url':
                self.title.setText(parts.hostname or uri)
                self.hint.setText('클릭하면 브라우저에서 열기')
            else:
                self.title.setText('열지 않는 링크')
                self.hint.setText('XDF는 이 형식의 링크를 실행하지 않습니다.')
            # Long addresses wrap anywhere instead of widening the popup.
            self.detail.setText('​'.join(uri[:600]))
            self.detail.show()
        self.adjustSize()
        screen = self.screen().availableGeometry() if self.screen() else None
        pos = global_pos + QPoint(14, 20)
        if screen is not None:
            if pos.x() + self.width() > screen.right():
                pos.setX(max(screen.left(), global_pos.x() - self.width() - 14))
            if pos.y() + self.height() > screen.bottom():
                pos.setY(max(screen.top(), global_pos.y() - self.height() - 14))
        self.move(pos)
        self.show()
        self.raise_()

    def page_preview(self, document, index, y):
        from .viewer import page_raster
        try:
            page = document.doc[index]
            scale = PREVIEW_WIDTH / max(1, page.rect.width)
            height = page.rect.height * scale
            if height > PREVIEW_WIDTH * 1.5:
                scale *= PREVIEW_WIDTH * 1.5 / height
            pixmap, _ = page_raster(page, scale * self.devicePixelRatioF())
        except Exception:
            return None
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        if y is not None:
            # Mark where the destination starts on the page.
            point = pymupdf.Point(0, y) * page.rotation_matrix
            painter = QPainter(pixmap)
            painter.setPen(QPen(QColor(ACCENT), 2))
            line = point.y * scale
            painter.drawLine(0, round(line), round(pixmap.width() / self.devicePixelRatioF()), round(line))
            painter.end()
        return pixmap


class LinkHover(QObject):
    """Tracks the link under the pointer for one PdfView."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.cache = OrderedDict()
        self.current = None
        self.global_pos = QPoint()
        self.preview = LinkPreview(view)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(HOVER_DELAY)
        self.timer.timeout.connect(self.show_preview)

    def links(self, index):
        document = self.view.document
        key = (index, document.revision)
        if key not in self.cache:
            try:
                page = document.doc[index]
                self.cache[key] = [(page_rect(link['from'], page), link) for link in page.get_links()]
            except Exception:
                self.cache[key] = []
            while len(self.cache) > 16:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]

    def at(self, item, scene_pos):
        if item is None or self.view.document is None or self.view.document.doc is None:
            return None
        local = item.mapFromScene(scene_pos)
        # The topmost (last drawn) link wins where links overlap.
        for rect, link in reversed(self.links(item.index)):
            if rect.contains(local):
                return link
        return None

    def hover(self, item, scene_pos, global_pos):
        """Update the preview for the pointer; True while it is over a link."""
        link = self.at(item, scene_pos)
        self.global_pos = global_pos
        if link is None:
            self.hide()
            return False
        if link is not self.current:
            self.preview.hide()
            self.current = link
            self.timer.start()
        return True

    def show_preview(self):
        if self.current is not None and self.view.isVisible() and self.view.underMouse():
            self.preview.show_link(self.view.document, self.current, self.global_pos)

    def hide(self):
        self.timer.stop()
        self.current = None
        self.preview.hide()

    def follow(self, link):
        self.hide()
        target = link_target(link)
        if target[0] == 'page' and target[1] < len(self.view.pages):
            self.view.goto(target[1])
            if target[2] is not None:
                item = self.view.pages[target[1]]
                point = pymupdf.Point(0, target[2]) * self.view.document.doc[target[1]].rotation_matrix
                top = self.view.mapFromScene(item.mapToScene(0, point.y)).y()
                bar = self.view.verticalScrollBar()
                bar.setValue(bar.value() + top - 20)
            return True
        if target[0] == 'url':
            return QDesktopServices.openUrl(QUrl(target[1]))
        return False


def page_rect(rect, page):
    """A link's hot area in the viewer's displayed (rotated) page coordinates."""
    r = pymupdf.Rect(rect) * page.rotation_matrix
    return QRectF(r.x0, r.y0, r.width, r.height)
