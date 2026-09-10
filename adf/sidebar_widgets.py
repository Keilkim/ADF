"""Resizable page sidebar with a persistent, outward-facing toggle tab."""

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QSplitter, QToolButton, QVBoxLayout, QWidget

from .theme import icon


class PageSidebar(QWidget):
    expandedChanged = Signal(bool)

    RAIL_WIDTH = 12
    MIN_WIDTH = 248
    MAX_WIDTH = 16777215

    def __init__(self, sidebar, reader, parent=None):
        super().__init__(parent)
        self.sidebar = sidebar
        self.reader = reader
        self.expanded = True
        self.expanded_width = 260

        self.rail = QFrame()
        self.rail.setObjectName('pageSidebarRail')
        self.rail.setMinimumWidth(self.MIN_WIDTH)
        self.rail.setMaximumWidth(self.MAX_WIDTH)
        rail_layout = QVBoxLayout(self.rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setSpacing(0)
        rail_layout.addWidget(sidebar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.rail)
        self.splitter.addWidget(reader)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([self.expanded_width, 1040])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.splitter)

        # A sibling of the splitter can extend over the document without being
        # clipped by the narrow rail or becoming an extra splitter pane.
        self.toggle = QToolButton(self)
        self.toggle.setObjectName('pageSidebarToggle')
        self.toggle.setFixedSize(28, 60)
        self.toggle.setIconSize(QSize(18, 18))
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        self.toggle.toggled.connect(self.set_expanded)
        self.update_toggle()

        self.splitter.splitterMoved.connect(self.position_toggle)
        self.reader.installEventFilter(self)
        self.rail.installEventFilter(self)

    def set_expanded(self, expanded):
        if self.expanded == expanded:
            return
        if not expanded:
            self.expanded_width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, self.rail.width()))
        self.expanded = expanded
        if expanded:
            self.rail.setMaximumWidth(self.MAX_WIDTH)
            self.rail.setMinimumWidth(self.MIN_WIDTH)
            self.sidebar.show()
            width = self.expanded_width
        else:
            self.sidebar.hide()
            self.rail.setFixedWidth(self.RAIL_WIDTH)
            width = self.RAIL_WIDTH
        self.splitter.handle(1).setEnabled(expanded)
        available = max(0, self.splitter.width() - self.splitter.handleWidth())
        self.splitter.setSizes([width, max(0, available - width)])
        self.update_toggle()
        self.position_toggle()
        self.expandedChanged.emit(expanded)

    def update_toggle(self):
        label = '페이지 미리보기 접기' if self.expanded else '페이지 미리보기 펼치기'
        self.toggle.setChecked(self.expanded)
        self.toggle.setIcon(icon('left' if self.expanded else 'right'))
        self.toggle.setToolTip(label)
        self.toggle.setAccessibleName(label)
        self.toggle.setAccessibleDescription('왼쪽 페이지 패널을 접거나 펼칩니다.')

    def position_toggle(self, *_):
        boundary = self.reader.mapTo(self, QPoint(0, 0)).x()
        self.toggle.move(max(0, boundary - 1), max(0, (self.height() - self.toggle.height()) // 2))
        self.toggle.raise_()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show):
            self.position_toggle()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.position_toggle()

    def showEvent(self, event):
        super().showEvent(event)
        self.position_toggle()
