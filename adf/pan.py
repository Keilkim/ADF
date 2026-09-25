"""Temporary hand panning: hold Space or the wheel button and drag the pages."""
from PySide6.QtCore import QEvent, QObject, Qt, Signal


class TemporaryPan(QObject):
    """Pans while Space or the middle button is held, then leaves the tool as it was.

    It filters the viewport ahead of the pen and the scene, so a drag moves the
    pages without drawing, selecting or moving whatever is under the pointer.
    While Space is held the pages also follow the pointer without a click:
    laptop touchpads ignore taps while a key is down, so tap-and-drag never
    reaches ADF, but moving the finger still moves the pointer.
    """
    changed = Signal(bool)

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.sources = set()
        self.last = None
        self.hover = None
        view.viewport().installEventFilter(self)

    @property
    def active(self):
        return bool(self.sources)

    def begin(self, source):
        started = not self.sources
        self.sources.add(source)
        if source == 'space':
            self.hover = None
        if started:
            self.view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            self.changed.emit(True)

    def end(self, source):
        if source not in self.sources:
            return
        self.sources.discard(source)
        if source == 'middle':
            self.last = None
        if source == 'space':
            self.hover = None
        if self.sources:
            if self.last is None:
                self.view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return
        self.last = None
        self.restore_cursor()
        self.changed.emit(False)

    def cancel(self):
        for source in list(self.sources):
            self.end(source)

    def restore_cursor(self):
        view = self.view
        view.viewport().unsetCursor()
        if view.stamp_pixmap is not None:
            view.update_stamp_cursor()
        elif view.dragMode() == view.DragMode.ScrollHandDrag:
            view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            view.update_content_cursor()

    def scroll(self, delta):
        horizontal, vertical = self.view.horizontalScrollBar(), self.view.verticalScrollBar()
        horizontal.setValue(horizontal.value() - round(delta.x()))
        vertical.setValue(vertical.value() - round(delta.y()))

    def grab(self, event):
        self.last = event.position()
        self.view.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            if event.button() == Qt.MouseButton.MiddleButton:
                self.begin('middle')
                self.grab(event)
                return True
            if not self.active:
                return False
            if event.button() == Qt.MouseButton.LeftButton:
                self.grab(event)
            return True
        if kind == QEvent.Type.MouseMove and self.active:
            if self.last is not None:
                self.scroll(event.position() - self.last)
                self.last = event.position()
            elif 'space' in self.sources:
                if self.hover is not None:
                    self.scroll(event.position() - self.hover)
                self.hover = event.position()
            return True
        if kind == QEvent.Type.MouseButtonRelease and self.active:
            if event.button() == Qt.MouseButton.MiddleButton:
                self.end('middle')
            elif event.button() == Qt.MouseButton.LeftButton and 'middle' not in self.sources:
                self.last = None
                self.hover = event.position()
                self.view.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            return True
        if kind == QEvent.Type.ContextMenu and self.active:
            return True
        return False
