"""Documents in tabs or in windows.

Every document keeps its own MainWindow. A DocumentWindow hosts one or more of
them under a title bar of its own: with one document the bar shows the title
as before, and from two documents on it shows them as tabs. A tab dragged out
becomes a window; a window dropped on another window's tabs joins them.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QEvent, QMargins, QObject, QPoint, QRectF, QSettings, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (QColor, QCursor, QGuiApplication, QKeySequence, QMouseEvent, QPainter, QPainterPath,
                           QPen, QShortcut)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel, QMenu, QStackedWidget,
                               QTabBar, QToolButton, QVBoxLayout, QWidget)

from .theme import ACCENT, ACCENT_SELECTED, ACCENT_TEXT

OPEN_MODE = 'documents/open_in'
TAB, WINDOW = 'tab', 'window'
TITLE_HEIGHT = 34
TAB_HEIGHT = 30
RESIZE_BORDER = 6
PAGES_MIME = 'application/x-xdf-pages'
GLYPHS = {'minimize': '', 'maximize': '', 'restore': '', 'close': '', 'add': ''}

STYLE = '''
QWidget#titleBar { background: #e9edf3; }
QLabel#windowTitle { color: #28313e; font-size: 9pt; padding-left: 2px; }
QLabel#windowTitle[inactive="true"] { color: #8b9099; }
QToolButton#caption, QToolButton#closeCaption, QToolButton#tabClose, QToolButton#newTab {
    font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets"; border: none; padding: 0; background: transparent; color: #28313e; }
QToolButton#caption, QToolButton#closeCaption { border-radius: 0; font-size: 10px; }
QToolButton#caption:hover { background: #dde2e9; }
QToolButton#caption:pressed { background: #d2d8e0; }
QToolButton#closeCaption:hover { background: #c42b1c; color: white; }
QToolButton#closeCaption:pressed { background: #c94f4f; color: white; }
QToolButton#tabClose { border-radius: 4px; font-size: 8px; }
QToolButton#tabClose:hover { background: #d5dbe3; }
QToolButton#newTab { border-radius: 6px; font-size: 11px; }
QToolButton#newTab:hover { background: #dde2e9; }
QTabBar#documentTabs { background: transparent; font-size: 9pt; }
QTabBar#documentTabs::tab { background: transparent; color: #4b5563; border: none; font-size: 9pt;
    border-top-left-radius: 9px; border-top-right-radius: 9px; padding: 0 4px 0 12px; margin-right: 2px; }
QTabBar#documentTabs::tab:hover:!selected { background: #dfe4eb; }
QTabBar#documentTabs::tab:selected { background: #f8fafc; color: #28313e; }
'''


def tabs_supported():
    # A macOS menu bar belongs to a window, so each document keeps its own window there.
    return sys.platform != 'darwin'


def custom_frame_supported():
    return sys.platform == 'win32' and QGuiApplication.platformName() == 'windows'


def open_mode(settings):
    if not tabs_supported():
        return WINDOW
    return WINDOW if settings.value(OPEN_MODE, TAB) == WINDOW else TAB


def tab_label(page):
    if not page.document.page_count:
        return '새 탭'
    return ('● ' if page.document.dirty else '') + page.document_name()


def same_file(first, second):
    try:
        return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(str(Path(second).resolve()))
    except (OSError, ValueError):
        return False


class DocumentTabs(QTabBar):
    detachRequested = Signal(int)
    closeRequested = Signal(int)

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('documentTabs')
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pressed = None
        self.ghost = -1   # Where a window dragged over this strip would land as tabs.

    def tabSizeHint(self, index):
        # Text, its padding and the close button; long names are shortened past the cap.
        width = self.fontMetrics().horizontalAdvance(self.tabText(index)) + 16 + 20 + 14
        return QSize(max(110, min(width, 240)), TAB_HEIGHT)

    def minimumTabSizeHint(self, index):
        return QSize(90, TAB_HEIGHT)

    def mousePressEvent(self, event):
        index = self.tabAt(event.position().toPoint())
        if event.button() == Qt.MouseButton.MiddleButton and index >= 0:
            self.closeRequested.emit(index)
            return
        self.pressed = index if event.button() == Qt.MouseButton.LeftButton and index >= 0 else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.pressed is not None and event.buttons() & Qt.MouseButton.LeftButton:
            y = event.position().y()
            # Pulled away from the strip, the tab leaves the window.
            if y < -24 or y > self.height() + 24:
                self.pressed = None
                index = self.currentIndex()
                release = QMouseEvent(QEvent.Type.MouseButtonRelease, event.position(), event.globalPosition(),
                                      Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
                super().mouseReleaseEvent(release)
                self.detachRequested.emit(index)
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.pressed = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not 0 <= self.ghost < self.count():
            return
        rect = QRectF(self.tabRect(self.ghost)).adjusted(.5, 2.5, -2.5, 0)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QPainterPath()
        shape.addRoundedRect(rect.adjusted(0, 0, 0, 12), 9, 9)
        painter.setClipRect(rect.adjusted(-1, -1, 1, 0))
        painter.setBrush(QColor(ACCENT_SELECTED))
        painter.setPen(QPen(QColor(ACCENT), 1.3, Qt.PenStyle.DashLine))
        painter.drawPath(shape)
        painter.setPen(QColor(ACCENT_TEXT))
        text_rect = rect.adjusted(12, 0, -10, 0)
        text = self.fontMetrics().elidedText(self.tabText(self.ghost), Qt.TextElideMode.ElideMiddle, int(text_rect.width()))
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), text)
        painter.end()


class TitleBar(QWidget):
    def __init__(self, frame, custom):
        super().__init__(frame)
        self.frame = frame
        self.custom = custom
        self.setObjectName('titleBar')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(STYLE)
        self.setFixedHeight(TITLE_HEIGHT)
        self.setAcceptDrops(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10 if custom else 6, 0, 0, 0)
        layout.setSpacing(0)
        self.icon = QLabel()
        self.icon.setFixedSize(26, TITLE_HEIGHT)
        self.icon.setVisible(custom)
        layout.addWidget(self.icon)
        self.title = QLabel()
        self.title.setObjectName('windowTitle')
        self.title.setVisible(custom)
        layout.addWidget(self.title, 1)
        self.tabs = DocumentTabs(self)
        layout.addWidget(self.tabs, 0, Qt.AlignmentFlag.AlignBottom)
        self.new_tab = self.glyph_button('newTab', 'add', 28, 28)
        self.new_tab.setToolTip('PDF를 새 탭으로 열기')
        layout.addSpacing(4)
        layout.addWidget(self.new_tab, 0, Qt.AlignmentFlag.AlignVCenter)
        self.stretch = QWidget()
        layout.addWidget(self.stretch, 1)
        self.buttons = {}
        for kind in ('minimize', 'maximize', 'close'):
            button = self.glyph_button('closeCaption' if kind == 'close' else 'caption', kind, 46, TITLE_HEIGHT)
            button.setVisible(custom)
            layout.addWidget(button)
            self.buttons[kind] = button
        self.buttons['minimize'].setToolTip('최소화')
        self.buttons['close'].setToolTip('닫기')
        self.buttons['minimize'].clicked.connect(frame.showMinimized)
        self.buttons['maximize'].clicked.connect(frame.toggle_maximized)
        self.buttons['close'].clicked.connect(frame.close)
        self.switch_timer = QTimer(self)
        self.switch_timer.setSingleShot(True)
        self.switch_timer.setInterval(450)
        self.switch_timer.timeout.connect(self.switch_to_hovered)
        self.hovered = -1
        self.set_tabbed(False)

    def glyph_button(self, name, glyph, width, height):
        button = QToolButton(self)
        button.setObjectName(name)
        button.setText(GLYPHS[glyph])
        button.setFixedSize(width, height)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def set_tabbed(self, tabbed):
        self.tabs.setVisible(tabbed)
        self.new_tab.setVisible(tabbed)
        self.stretch.setVisible(tabbed)
        self.title.setVisible(self.custom and not tabbed)

    def set_window_state(self, maximized, active):
        self.buttons['maximize'].setText(GLYPHS['restore' if maximized else 'maximize'])
        self.buttons['maximize'].setToolTip('이전 크기로 복원' if maximized else '최대화')
        if self.title.property('inactive') != (not active):
            self.title.setProperty('inactive', not active)
            self.title.style().unpolish(self.title)
            self.title.style().polish(self.title)

    def is_caption(self, pos):
        """True where a press should drag the window rather than reach a control."""
        child = self.childAt(pos)
        if child is None or child in (self.icon, self.title, self.stretch):
            return True
        if child is self.tabs:
            return self.tabs.tabAt(self.tabs.mapFrom(self, pos)) < 0
        return False

    def mouseDoubleClickEvent(self, event):
        # A custom frame gets this from Windows as a caption double-click instead.
        if not self.custom and self.is_caption(event.position().toPoint()):
            self.frame.toggle_maximized()
        super().mouseDoubleClickEvent(event)

    def dragEnterEvent(self, event):
        data = event.mimeData()
        if data.hasFormat(PAGES_MIME) or any(url.toLocalFile().lower().endswith('.pdf') for url in data.urls()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()
        if not event.mimeData().hasFormat(PAGES_MIME) or not self.tabs.isVisible():
            return
        # Pages dragged over a tab open that document, to be dropped into it.
        index = self.tabs.tabAt(self.tabs.mapFrom(self, event.position().toPoint()))
        if index != self.hovered:
            self.hovered = index
            self.switch_timer.stop()
            if index >= 0 and index != self.tabs.currentIndex():
                self.switch_timer.start()

    def dragLeaveEvent(self, event):
        self.hovered = -1
        self.switch_timer.stop()

    def switch_to_hovered(self):
        if 0 <= self.hovered < self.tabs.count():
            self.tabs.setCurrentIndex(self.hovered)

    def dropEvent(self, event):
        self.dragLeaveEvent(event)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.toLocalFile().lower().endswith('.pdf')]
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        QTimer.singleShot(0, lambda: self.frame.workspace.open_paths(paths, self.frame.current_page(), TAB))


class DocumentWindow(QWidget):
    def __init__(self, workspace):
        super().__init__(None)
        self.workspace = workspace
        self.closing = False
        self.move_size = None
        self.custom = custom_frame_supported()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.title_bar = TitleBar(self, self.custom)
        layout.addWidget(self.title_bar)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        tabs = self.title_bar.tabs
        tabs.currentChanged.connect(self.tab_changed)
        tabs.detachRequested.connect(lambda index: self.workspace.detach(self, self.page_at(index), QCursor.pos(), True))
        tabs.closeRequested.connect(lambda index: self.close_page(self.page_at(index)))
        tabs.customContextMenuRequested.connect(self.tab_menu)
        self.title_bar.new_tab.clicked.connect(self.open_in_tab)
        for keys, step in (('Ctrl+Tab', 1), ('Ctrl+Shift+Tab', -1), ('Ctrl+PgDown', 1), ('Ctrl+PgUp', -1)):
            QShortcut(QKeySequence(keys), self, lambda step=step: self.cycle(step))
        if self.custom:
            self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            from . import window_frame
            window_frame.install(int(self.winId()))
            self.windowHandle().screenChanged.connect(lambda _screen: window_frame.refresh(int(self.winId())))
        self.update_chrome()

    # Pages

    def pages(self):
        tabs = self.title_bar.tabs
        return [page for page in (tabs.tabData(index) for index in range(tabs.count())) if page is not None]

    def page_at(self, index):
        return self.title_bar.tabs.tabData(index) if 0 <= index < self.title_bar.tabs.count() else None

    def index_of(self, page):
        tabs = self.title_bar.tabs
        for index in range(tabs.count()):
            if tabs.tabData(index) is page:
                return index
        return -1

    def preview_drop(self, label, global_pos):
        """Show where a window dragged over the strip would join, as a placeholder tab."""
        tabs = self.title_bar.tabs
        x = tabs.mapFromGlobal(global_pos).x()
        real = [index for index in range(tabs.count()) if index != tabs.ghost]
        before = sum(1 for index in real if tabs.tabRect(index).center().x() < x)
        if tabs.ghost >= 0:
            if before == tabs.ghost:
                return
            self.clear_preview()
        tabs.ghost = tabs.insertTab(before, label)
        tabs.setTabData(tabs.ghost, None)
        tabs.setTabToolTip(tabs.ghost, '')
        self.update_chrome()
        tabs.update()

    def clear_preview(self):
        tabs = self.title_bar.tabs
        if tabs.ghost < 0:
            return -1
        index, tabs.ghost = tabs.ghost, -1
        tabs.removeTab(index)
        self.update_chrome()
        return index

    def current_page(self):
        return self.page_at(self.title_bar.tabs.currentIndex())

    def add_page(self, page, index=-1, activate=True):
        tabs = self.title_bar.tabs
        self.stack.addWidget(page)
        index = index if 0 <= index <= tabs.count() else tabs.count()
        index = tabs.insertTab(index, tab_label(page))
        tabs.setTabData(index, page)
        close = self.title_bar.glyph_button('tabClose', 'close', 20, 20)
        close.setToolTip('탭 닫기')
        close.clicked.connect(lambda _checked=False, page=page: self.close_page(page))
        tabs.setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
        if activate or tabs.count() == 1:
            tabs.setCurrentIndex(index)
            self.tab_changed(index)
        self.refresh_page(page)
        self.update_chrome()

    def remove_page(self, page):
        index = self.index_of(page)
        if index < 0:
            return
        self.stack.removeWidget(page)
        self.title_bar.tabs.removeTab(index)
        page.setParent(None)
        self.update_chrome()

    def set_current(self, page):
        index = self.index_of(page)
        if index >= 0:
            self.title_bar.tabs.setCurrentIndex(index)

    def close_page(self, page):
        if page is not None:
            self.set_current(page)
            page.close()

    def cycle(self, step):
        tabs = self.title_bar.tabs
        if tabs.count() > 1 and not self.isFullScreen():
            tabs.setCurrentIndex((tabs.currentIndex() + step) % tabs.count())

    def tab_changed(self, index):
        page = self.page_at(index)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        self.refresh_page(page)
        if page.document.page_count:
            page.view.setFocus(Qt.FocusReason.TabFocusReason)

    def refresh_page(self, page):
        index = self.index_of(page)
        if index < 0:
            return
        tabs = self.title_bar.tabs
        tabs.setTabText(index, tab_label(page))
        tabs.setTabToolTip(index, str(page.document.path or tab_label(page)))
        if page is self.current_page():
            self.setWindowTitle(page.windowTitle())
            self.title_bar.title.setText(page.windowTitle())
            self.setWindowIcon(page.windowIcon())
            self.title_bar.icon.setPixmap(page.windowIcon().pixmap(QSize(16, 16), self.devicePixelRatioF()))

    def update_chrome(self):
        tabbed = self.title_bar.tabs.count() > 1
        self.title_bar.set_tabbed(tabbed)
        self.title_bar.setVisible((self.custom or tabbed) and not self.isFullScreen())
        self.title_bar.set_window_state(self.isMaximized(), self.isActiveWindow())

    def tab_menu(self, pos):
        tabs = self.title_bar.tabs
        page = self.page_at(tabs.tabAt(pos))
        if page is None:
            return
        menu = QMenu(self)
        separate = menu.addAction('새 창으로 분리')
        separate.setEnabled(tabs.count() > 1)
        menu.addSeparator()
        close = menu.addAction('탭 닫기')
        others = menu.addAction('다른 탭 모두 닫기')
        others.setEnabled(tabs.count() > 1)
        chosen = menu.exec(tabs.mapToGlobal(pos))
        if chosen is separate:
            self.workspace.detach(self, page, None, False)
        elif chosen is close:
            self.close_page(page)
        elif chosen is others:
            for other in self.pages():
                if other is not page:
                    self.set_current(other)
                    if not other.close():
                        break
            self.set_current(page)

    def open_in_tab(self):
        paths, _ = QFileDialog.getOpenFileNames(self, 'PDF 열기', str(Path.home()), 'PDF 문서 (*.pdf)')
        if paths:
            self.workspace.open_paths(paths, self.current_page(), TAB)

    # Window

    def toggle_maximized(self):
        self.showNormal() if self.isMaximized() else self.showMaximized()

    def bring_to_front(self):
        if self.isMinimized():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized | Qt.WindowState.WindowActive)
        self.show()
        self.raise_()
        self.activateWindow()

    def fit_maximized(self):
        """A maximized captioned window overhangs the screen; keep the content on it."""
        margins = QMargins()
        if self.custom and self.isMaximized() and not self.isFullScreen() and self.screen() is not None:
            available, frame = self.screen().availableGeometry(), self.geometry()
            margins = QMargins(max(0, available.left() - frame.left()), max(0, available.top() - frame.top()),
                               max(0, frame.right() - available.right()), max(0, frame.bottom() - available.bottom()))
        if self.layout().contentsMargins() != margins:
            self.layout().setContentsMargins(margins)

    def changeEvent(self, event):
        super().changeEvent(event)
        kind = event.type()
        if kind == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self.workspace.last_frame = self
            self.update_chrome()
        elif kind == QEvent.Type.WindowStateChange:
            self.update_chrome()
            self.fit_maximized()
            page = self.current_page()
            if page is not None and page.fullscreen.active and not self.isFullScreen():
                QTimer.singleShot(0, page.fullscreen.exit)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_maximized()

    def moveEvent(self, event):
        super().moveEvent(event)
        self.fit_maximized()

    def hit_test(self, global_pos):
        from . import window_frame
        local = self.mapFromGlobal(global_pos)
        if not (self.isMaximized() or self.isFullScreen()):
            edge = window_frame.edge_hit(local.x(), local.y(), self.width(), self.height(), RESIZE_BORDER)
            if edge is not None:
                return edge
        if self.title_bar.isVisible():
            pos = self.title_bar.mapFrom(self, local)
            if self.title_bar.rect().contains(pos) and self.title_bar.is_caption(pos):
                return window_frame.HTCAPTION
        return None

    def nativeEvent(self, event_type, message):
        if self.custom and bytes(event_type) == b'windows_generic_MSG':
            from . import window_frame
            msg = window_frame.message(message)
            if msg.message == window_frame.WM_NCCALCSIZE:
                # The whole window is client area; XDF draws the title bar.
                return True, 0
            if msg.message == window_frame.WM_NCHITTEST:
                hit = self.hit_test(QCursor.pos())
                if hit is not None:
                    return True, hit
            elif msg.message == window_frame.WM_ENTERSIZEMOVE:
                self.move_size = self.size()
            elif msg.message == window_frame.WM_MOVING and self.move_size is not None:
                self.workspace.track_move(self, QCursor.pos())
            elif msg.message == window_frame.WM_EXITSIZEMOVE and self.move_size is not None:
                moved = self.move_size == self.size()
                self.move_size = None
                self.workspace.finish_move(self, QCursor.pos(), moved)
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event):
        self.closing = True
        for page in sorted(self.pages(), key=lambda page: page is not self.current_page()):
            self.set_current(page)
            if not page.close():
                self.closing = False
                event.ignore()
                return
        self.workspace.frame_closed(self)
        event.accept()


class Workspace(QObject):
    """Every open document of this XDF process, in tabs or windows."""

    def __init__(self, factory, settings=None, smoke=False):
        super().__init__()
        self.factory = factory
        self.smoke = smoke
        self.settings = settings or QSettings('ADF', 'ADF')
        self.frames = []
        self.retained = []
        self.last_frame = None
        self.server = None

    def start(self):
        page = self.create_page(services=True)
        frame = self.new_frame()
        geometry = None if self.smoke else self.settings.value('geometry')
        if not geometry or not frame.restoreGeometry(geometry):
            frame.resize(1320, 900 + TITLE_HEIGHT)
        frame.add_page(page)
        frame.show()
        return page

    def create_page(self, services=False):
        page = self.factory(hosted=True, services=services)
        page.workspace = self
        page.actions['new_window'].setVisible(True)
        page.windowTitleChanged.connect(self.title_changed)
        return page

    def new_frame(self, cascade_from=None):
        frame = DocumentWindow(self)
        self.frames.append(frame)
        if cascade_from is not None:
            geometry = cascade_from.normalGeometry() if cascade_from.isMaximized() else cascade_from.geometry()
            frame.setGeometry(geometry.translated(32, 32))
        else:
            frame.resize(1320, 900 + TITLE_HEIGHT)
        return frame

    def pages(self):
        return [page for frame in self.frames for page in frame.pages()]

    def frame_of(self, page):
        for frame in self.frames:
            if frame.index_of(page) >= 0:
                return frame
        return None

    def active_frame(self):
        window = QApplication.activeWindow()
        if window in self.frames:
            return window
        if self.last_frame in self.frames:
            return self.last_frame
        return self.frames[-1] if self.frames else None

    def current_page(self):
        frame = self.active_frame()
        return frame.current_page() if frame is not None else None

    def title_changed(self, _title):
        page = self.sender()
        frame = self.frame_of(page)
        if frame is not None:
            frame.refresh_page(page)

    # Opening

    def find_open(self, path):
        for page in self.pages():
            if page.document.page_count and page.document.path and same_file(page.document.path, path):
                return page
        return None

    def show_page(self, page):
        frame = self.frame_of(page)
        if frame is not None:
            frame.set_current(page)
            frame.bring_to_front()

    def target_page(self, near, mode):
        """The page a document should open in, and whether it was made for it."""
        frame = self.frame_of(near) if near is not None else self.active_frame()
        if near is None and frame is not None:
            near = frame.current_page()
        if near is not None and not near.document.page_count and frame is not None:
            return near, False
        page = self.create_page()
        if mode == TAB and frame is not None:
            frame.add_page(page, frame.index_of(near) + 1 if near is not None else -1)
        else:
            frame = self.new_frame(cascade_from=frame)
            frame.add_page(page)
            frame.show()
        return page, True

    def open_paths(self, paths, near=None, mode=None):
        mode = mode or open_mode(self.settings)
        opened = False
        for path in paths:
            existing = self.find_open(path)
            if existing is not None:
                self.show_page(existing)
                opened = True
                continue
            page, created = self.target_page(near, mode)
            if page.open_path(path):
                opened = True
                near = page
            elif created:
                page.close()
        return opened

    def new_window(self, near=None):
        frame = self.new_frame(cascade_from=self.frame_of(near) if near is not None else self.active_frame())
        page = self.create_page()
        frame.add_page(page)
        frame.show()
        return page

    def open_forwarded(self, paths):
        """Another XDF launch handed its files to this process."""
        if paths:
            self.open_paths(paths)
        elif open_mode(self.settings) == WINDOW or not self.frames:
            self.new_window()
        frame = self.active_frame()
        if frame is not None:
            frame.bring_to_front()

    # Tabs and windows

    def detach(self, frame, page, global_pos, follow):
        if page is None or len(frame.pages()) < 2:
            return None
        geometry = frame.normalGeometry() if frame.isMaximized() else frame.geometry()
        frame.remove_page(page)
        window = self.new_frame()
        window.resize(geometry.size())
        if global_pos is None:
            window.move(geometry.topLeft() + QPoint(32, 32))
        else:
            window.move(global_pos - QPoint(min(140, geometry.width() // 4), TITLE_HEIGHT // 2))
        window.add_page(page)
        window.show()
        window.bring_to_front()
        if follow and QApplication.mouseButtons() & Qt.MouseButton.LeftButton and window.windowHandle():
            # Keep the window under the pointer until the button is released.
            QTimer.singleShot(0, lambda: window.windowHandle() and window.windowHandle().startSystemMove())
        return window

    def drop_target(self, frame, global_pos):
        for other in reversed(self.frames):
            if other is frame or not other.isVisible() or other.isMinimized() or not other.title_bar.isVisible():
                continue
            bar = other.title_bar
            if bar.rect().contains(bar.mapFromGlobal(global_pos)):
                return other
        return None

    def track_move(self, frame, global_pos):
        target = self.drop_target(frame, global_pos)
        pages = frame.pages()
        label = tab_label(frame.current_page()) if pages else ''
        if len(pages) > 1:
            label += f' 외 {len(pages) - 1}개'
        for other in self.frames:
            if other is target:
                other.preview_drop(label, global_pos)
            else:
                other.clear_preview()
        # Over another window's tabs the moving window disappears into its placeholder tab.
        frame.setWindowOpacity(0. if target is not None else 1.)

    def finish_move(self, frame, global_pos, moved):
        target = self.drop_target(frame, global_pos) if moved else None
        index = -1
        for other in self.frames:
            placed = other.clear_preview()
            if other is target:
                index = placed
        if target is None:
            frame.setWindowOpacity(1.)
            return
        QTimer.singleShot(0, lambda: self.merge(frame, target, index))

    def merge(self, source, target, index=-1):
        if source not in self.frames or target not in self.frames or source is target:
            if source in self.frames:
                source.setWindowOpacity(1.)
            return
        tabs = target.title_bar.tabs
        index = index if 0 <= index <= tabs.count() else tabs.count()
        current = source.current_page()
        for page in source.pages():
            source.remove_page(page)
            target.add_page(page, index, activate=False)
            index += 1
        if current is not None:
            target.set_current(current)
        source.close()
        target.bring_to_front()

    # Closing

    def page_closed(self, page):
        """A page accepted its close event: take it out of its window."""
        frame = self.frame_of(page)
        owned_updates = page.updates is not None and not page.installing_update
        if frame is not None:
            frame.remove_page(page)
        if page.installing_update:
            self.retained.append(page)   # It goes on to start the installer.
        else:
            page.deleteLater()
        if owned_updates:
            successor = next(iter(self.pages()), None)
            if successor is not None and successor.updates is None:
                successor.start_updates()
        if frame is not None and not frame.pages() and not frame.closing:
            frame.close()

    def document_closed(self, page):
        """Closing a document closes its tab while the window keeps others."""
        frame = self.frame_of(page)
        if frame is not None and len(frame.pages()) > 1:
            page.close()

    def frame_closed(self, frame):
        if frame in self.frames:
            self.frames.remove(frame)
        if not self.smoke:
            self.settings.setValue('geometry', frame.saveGeometry())
        if self.last_frame is frame:
            self.last_frame = None

    def close_all(self, last):
        """Close every document, the given page last; False if one stays open."""
        for frame in list(self.frames):
            for page in frame.pages():
                if page is last:
                    continue
                frame.set_current(page)
                if not page.close():
                    return False
        return last.close()

    def adopt(self, page):
        """Show a page again that closed for an update that did not start."""
        if page in self.retained:
            self.retained.remove(page)
        frame = self.new_frame()
        frame.add_page(page)
        frame.show()
        frame.bring_to_front()

    # Later launches

    def listen(self):
        self.server = DocumentServer(self)
        if not self.server.listen():
            self.server = None


def server_name():
    user = os.environ.get('USERNAME') or os.environ.get('USER') or ''
    session = ''
    if sys.platform == 'win32':
        import ctypes
        value = ctypes.c_ulong()
        if ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
            session = str(value.value)
    digest = hashlib.sha256(f'{user}|{session}'.encode('utf-8')).hexdigest()[:20]
    return f'xdf-documents-{digest}'


def forward_supported():
    return sys.platform == 'win32'


def forward_to_running(paths, timeout=3000):
    """Hand files to an XDF already running for this user; True when it took them."""
    socket = QLocalSocket()
    socket.connectToServer(server_name())
    if not socket.waitForConnected(400):
        return False
    if sys.platform == 'win32':
        import ctypes
        # The running XDF may bring its window to the front for this launch.
        ctypes.windll.user32.AllowSetForegroundWindow(-1)
    socket.write(json.dumps({'files': paths}).encode('utf-8') + b'\n')
    socket.flush()
    answer = b''
    deadline = time.monotonic() + timeout / 1000
    # On Windows a wait can also end when the request has been written.
    while b'\n' not in answer and time.monotonic() < deadline and socket.state() == QLocalSocket.LocalSocketState.ConnectedState:
        socket.waitForReadyRead(100)
        answer += bytes(socket.readAll().data())
    socket.abort()
    return answer.startswith(b'ok')


class DocumentServer(QObject):
    LIMIT = 1 << 20

    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self.accept)
        self.buffers = {}

    def listen(self):
        return self.server.listen(server_name())

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            self.buffers[socket] = b''
            socket.readyRead.connect(lambda socket=socket: self.read(socket))
            socket.disconnected.connect(lambda socket=socket: self.drop(socket))

    def drop(self, socket):
        self.buffers.pop(socket, None)
        try:
            socket.deleteLater()
        except RuntimeError:
            pass  # The server, its parent, went first.

    def read(self, socket):
        data = self.buffers.get(socket, b'') + bytes(socket.readAll().data())
        if len(data) > self.LIMIT:
            socket.abort()
            return
        self.buffers[socket] = data
        if b'\n' not in data:
            return
        try:
            request = json.loads(data.split(b'\n', 1)[0].decode('utf-8'))
            paths = [str(path) for path in request.get('files', [])][:64]
        except (ValueError, AttributeError, TypeError):
            socket.abort()
            return
        socket.write(b'ok\n')
        # Send the answer before opening, which can keep this thread busy past the launch's wait.
        socket.waitForBytesWritten(1000)
        QTimer.singleShot(0, lambda: self.workspace.open_forwarded(paths))
