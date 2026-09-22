"""A document-only fullscreen stage with independently sliding edge controls."""

import sys

from PySide6.QtCore import QObject, QEvent, Property, QPropertyAnimation, QEasingCurve, QTimer, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QFrame, QMenuBar, QVBoxLayout, QWidget, QLineEdit, QAbstractSpinBox

from .theme import icon


def motion_enabled():
    if sys.platform == 'win32':
        import ctypes
        enabled = ctypes.c_int(1)
        # SPI_GETCLIENTAREAANIMATION: honor Windows' animation preference.
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
            return bool(enabled.value)
    return True


class EdgePanel(QFrame):
    def __init__(self, edge, stage):
        super().__init__(stage)
        self.edge = edge
        self.progress = 0.
        self.opened = False
        self.setObjectName('fullscreenPanel')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.animation = QPropertyAnimation(self, b'reveal', self)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.finished.connect(self.finish)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(220)
        self.hide_timer.timeout.connect(lambda: self.set_open(False))
        self.hide()

    def get_reveal(self):
        return self.progress

    def set_reveal(self, value):
        self.progress = value
        self.position()

    reveal = Property(float, get_reveal, set_reveal)

    def position(self):
        stage = self.parentWidget()
        if self.edge == 'top':
            self.move(0, round(-self.height()*(1-self.progress)))
        elif self.edge == 'left':
            self.move(round(-self.width()*(1-self.progress)), 0)
        else:
            self.move(0, round(stage.height()-self.height()*self.progress))

    def set_open(self, opened, animate=True):
        self.hide_timer.stop()
        if opened == self.opened:
            return
        self.opened = opened
        self.animation.stop()
        self.show()
        self.raise_()
        if animate and motion_enabled():
            self.animation.setDuration(max(1, round((190 if opened else 160)*abs(float(opened)-self.progress))))
            self.animation.setStartValue(self.progress)
            self.animation.setEndValue(float(opened))
            self.animation.start()
        else:
            self.set_reveal(float(opened))
            self.finish()

    def finish(self):
        if not self.opened:
            self.hide()

    def keep_open(self, wanted):
        if wanted:
            self.set_open(True)
        elif self.opened and not self.hide_timer.isActive():
            self.hide_timer.start()


class FullscreenReader(QObject):
    EDGE_SIZE = 12

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.active = False
        self.stage = None
        self.panels = {}
        self.poll = QTimer(self)
        self.poll.setInterval(60)
        self.poll.timeout.connect(self.update_pointer)

    def move_widget(self, widget, panel):
        parent = widget.parentWidget()
        layout = parent.layout()
        index = layout.indexOf(widget)
        self.moved.append((widget, parent, layout, index, layout.stretch(index),
                           layout.itemAt(index).alignment(), widget.isHidden()))
        hidden = widget.isHidden()
        layout.removeWidget(widget)
        panel.layout().addWidget(widget)
        widget.setVisible(not hidden)

    def enter(self):
        window, view = self.window, self.window.view
        if self.active or not window.document.page_count:
            return
        self.normal_geometry = window.saveGeometry()
        self.normal_rect = window.geometry()
        self.was_maximized = window.isMaximized()
        self.minimum_size = window.minimumSize()
        self.splitter_sizes = window.reader_splitter.sizes()
        self.sidebar_width = window.sidebar.width() if window.page_sidebar.expanded else window.page_sidebar.expanded_width
        self.fit_mode, self.zoom = view.fit_mode, view.transform().m11()
        self.background = view.backgroundBrush()
        self.scrollbars = (view.horizontalScrollBarPolicy(), view.verticalScrollBarPolicy())
        self.chrome = [(widget, widget.isVisible()) for widget in
                       (window.menuBar(), window.stamp_dock) if widget is not None]
        window.stop_stamp()
        window.clear_content_selection()
        self.moved = []
        self.stage = QWidget(window)
        self.stage.setObjectName('fullscreenStage')
        layout = QVBoxLayout(self.stage)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.panels = {edge: EdgePanel(edge, self.stage) for edge in ('left', 'top', 'bottom')}
        self.move_widget(view, self.stage)
        menu = QMenuBar(self.panels['top'])
        for action in window.menuBar().actions():
            menu.addAction(action)
        self.panels['top'].layout().addWidget(menu)
        for widget in (window.toolbar, window.textbar, window.searchbar, window.imagebar):
            self.move_widget(widget, self.panels['top'])
        self.move_widget(window.sidebar, self.panels['left'])
        window.sidebar.show()
        self.move_widget(window.page_nav, self.panels['bottom'])
        self.central = window.takeCentralWidget()
        self.central.hide()
        window.setCentralWidget(self.stage)
        for widget, _ in self.chrome:
            widget.hide()
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        window.setMinimumSize(0, 0)
        self.active = True
        self.armed = False
        self.entry_pointer = QCursor.pos()
        self.stage.installEventFilter(self)
        for panel in self.panels.values():
            panel.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        self.update_action()
        window.showFullScreen()
        self.layout_panels()
        view.fit('page')
        view.goto(window.current)
        view.setFocus()
        self.poll.start()

    def layout_panels(self):
        if not self.active:
            return
        width, height = self.stage.width(), self.stage.height()
        self.window.toolbar.reflow(width)
        for edge, panel in self.panels.items():
            if edge == 'left':
                panel.resize(min(max(248, self.sidebar_width), max(248, width//2)), height)
            else:
                panel.resize(width, panel.layout().sizeHint().height())
            panel.position()

    def update_action(self):
        action = self.window.actions['fullscreen']
        action.setChecked(self.active)
        action.setIcon(icon('fullscreen_exit' if self.active else 'fullscreen'))
        action.setText('전체 화면 해제' if self.active else '전체 화면')
        action.setToolTip('전체 화면 해제 (Esc / F11 / 문서 더블클릭)' if self.active else '전체 화면 (F11)')

    def reveal(self, edge):
        if self.active:
            self.panels[edge].set_open(True)

    def update_pointer(self, global_pos=None):
        if not self.active:
            return
        selection = self.window.view.ink_selection
        if self.window.view.pen.page is not None or (selection is not None and selection.drag is not None):
            for panel in self.panels.values():
                panel.keep_open(False)
            return
        pos = QCursor.pos() if global_pos is None else global_pos
        if not self.armed:
            if pos == self.entry_pointer:
                return
            self.armed = True
        if QApplication.activePopupWidget() or QApplication.activeModalWidget() or QApplication.mouseButtons():
            for panel in self.panels.values():
                panel.hide_timer.stop()
            return
        local = self.stage.mapFromGlobal(pos)
        inside = self.stage.rect().contains(local)
        focus = QApplication.focusWidget()
        for edge, panel in self.panels.items():
            at_edge = inside and (local.x() < self.EDGE_SIZE if edge == 'left' else
                                  local.y() < self.EDGE_SIZE if edge == 'top' else
                                  local.y() >= self.stage.height()-self.EDGE_SIZE)
            over_panel = inside and panel.isVisible() and panel.geometry().contains(local)
            editing = isinstance(focus, (QLineEdit, QAbstractSpinBox)) and panel.isAncestorOf(focus)
            panel.keep_open(at_edge or over_panel or editing)

    def eventFilter(self, watched, event):
        if not self.active:
            return False
        kind = event.type()
        if watched is self.stage and kind == QEvent.Type.Resize:
            self.layout_panels()
        elif watched in self.panels.values() and kind == QEvent.Type.LayoutRequest:
            self.layout_panels()
        elif kind == QEvent.Type.MouseMove and isinstance(watched, QWidget):
            if watched.window() is self.window:
                self.update_pointer(event.globalPosition().toPoint())
        elif kind == QEvent.Type.MouseButtonDblClick and watched is self.window.view.viewport():
            if (event.button() == Qt.MouseButton.LeftButton and not self.window.view.pen.enabled
                    and self.window.view.ink_selection is None):
                self.window.view.pen.cancel()
                self.exit()
                return True
        elif kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if (isinstance(watched, QWidget) and watched.window() is self.window
                    and not QApplication.activePopupWidget() and not QApplication.activeModalWidget()):
                self.window.view.pen.cancel()
                self.exit()
                return True
        return False

    def exit(self):
        if not self.active:
            return
        window, view = self.window, self.window.view
        current = window.current
        self.active = False
        self.poll.stop()
        QApplication.instance().removeEventFilter(self)
        for panel in self.panels.values():
            panel.animation.stop()
            panel.hide_timer.stop()
        window.takeCentralWidget()
        for widget, parent, layout, index, stretch, alignment, hidden in reversed(self.moved):
            visible = not (hidden if widget is window.sidebar else widget.isHidden())
            widget.setParent(parent)
            layout.insertWidget(index, widget, stretch, alignment)
            widget.setVisible(visible)
        window.setCentralWidget(self.central)
        self.central.show()
        for widget, visible in self.chrome:
            widget.setVisible(visible)
        window.setMinimumSize(self.minimum_size)
        window.restoreGeometry(self.normal_geometry)
        if not self.was_maximized:
            window.setGeometry(self.normal_rect)
        view.setHorizontalScrollBarPolicy(self.scrollbars[0])
        view.setVerticalScrollBarPolicy(self.scrollbars[1])
        view.setBackgroundBrush(self.background)
        window.reader_splitter.setSizes(self.splitter_sizes)
        window.page_sidebar.position_toggle()
        view.fit_mode = self.fit_mode
        view.set_zoom(self.zoom, manual=False)
        view.apply_fit()
        view.goto(current)
        view.setFocus()
        self.update_action()
        self.stage.hide()
        self.stage.deleteLater()
        self.stage = None
        self.panels = {}
        self.moved = []
