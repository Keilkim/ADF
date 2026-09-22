"""Short notices shown over the document instead of a status bar.

The notice floats above the page navigation, lets clicks pass through and
hides itself, so it never takes vertical space from the pages.
"""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QApplication, QLabel, QMenuBar, QWidget

DEFAULT_TIMEOUT = 6000


class Notice(QLabel):
    """Status-bar style API: showMessage, clearMessage and currentMessage."""

    def __init__(self, window, anchor=None):
        super().__init__(window)
        self.setObjectName('notice')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTextFormat(Qt.TextFormat.PlainText)
        # The widget above which the notice sits, normally the page navigation.
        self.anchor = anchor
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.clearMessage)
        window.installEventFilter(self)
        self.hide()

    def showMessage(self, text, timeout=0):
        self.setText(text)
        if not text:
            self.clearMessage()
            return
        # A floating notice would cover the page if it stayed, so every message
        # expires; the state it describes stays visible in the app itself.
        self.timer.start(timeout or DEFAULT_TIMEOUT)
        self.place()
        self.show()
        self.raise_()

    def clearMessage(self):
        self.timer.stop()
        self.setText('')
        self.hide()

    def currentMessage(self):
        return self.text()

    def place(self):
        window = self.parentWidget()
        self.setMaximumWidth(max(240, window.width() * 7 // 10))
        self.adjustSize()
        bottom = window.height() - 18
        anchor = self.anchor
        if anchor is not None and anchor.isVisible() and anchor.window() is window:
            bottom = min(bottom, anchor.mapTo(window, anchor.rect().topLeft()).y() - 10)
        self.move((window.width() - self.width()) // 2, max(0, bottom - self.height()))

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.place()
        return False


class MenuCorner(QWidget):
    """A menu bar corner widget whose contents change size.

    QMenuBar places its corner widgets only when the bar is resized or its
    actions, font or style change, so a longer save state or a newly shown
    update button would stay clipped. Ask the bar to place them again.
    """

    def event(self, event):
        result = super().event(event)
        bar = self.parentWidget()
        if event.type() == QEvent.Type.LayoutRequest and isinstance(bar, QMenuBar):
            QApplication.sendEvent(bar, QResizeEvent(bar.size(), bar.size()))
        return result
