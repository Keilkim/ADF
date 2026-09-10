"""Cancelable process progress. Unknown durations never get a fake percent."""
import time
from PySide6.QtCore import Qt, QTimer, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget


class Spinner(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.started = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self.update)

    def showEvent(self, event):
        from .fullscreen import motion_enabled
        if motion_enabled():
            self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#e4e5e7'), 3))
        painter.drawEllipse(QRectF(4, 4, 20, 20))
        painter.setPen(QPen(QColor('#797d84'), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        angle = -int((time.monotonic()-self.started)*270) if self.timer.isActive() else 90
        painter.drawArc(QRectF(4, 4, 20, 20), angle*16, 105*16)


class TaskProgressDialog(QDialog):
    cancelRequested = Signal()

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.canceled = False
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumWidth(480)
        self.setMaximumWidth(680)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        outer.setSpacing(14)
        row = QHBoxLayout()
        self.spinner = Spinner()
        row.addWidget(self.spinner)
        self.stage = QLabel('작업을 시작하고 있습니다')
        self.stage.setWordWrap(True)
        row.addWidget(self.stage, 1)
        outer.addLayout(row)
        self.detail = QLabel('')
        self.detail.setWordWrap(True)
        self.detail.setObjectName('muted')
        outer.addWidget(self.detail)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        self.bar.setTextVisible(False)
        outer.addWidget(self.bar)
        self.count = QLabel('처리량을 확인하는 중입니다')
        self.count.setObjectName('muted')
        outer.addWidget(self.count)
        self.cancel_button = QPushButton('취소')
        self.cancel_button.clicked.connect(self.reject)
        outer.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignRight)

    def update_progress(self, data):
        if self.canceled:
            return
        self.stage.setText(str(data.get('stage', '처리 중')))
        self.detail.setText(str(data.get('detail', '')))
        done, total = data.get('completed'), data.get('total')
        if isinstance(done, (int, float)) and isinstance(total, (int, float)) and total > 0:
            percent = min(100, max(0, int(done*100/total)))
            self.bar.setRange(0, 100)
            self.bar.setValue(percent)
            self.count.setText(f'변환한 페이지 {done:,} / {total:,} · {percent}%'
                               if data.get('unit') == 'ocr_pages' else f'현재 단계 {percent}% · {done:,} / {total:,}')
        else:
            self.bar.setRange(0, 0)
            self.count.setText('처리 중 · 이 단계는 진행률을 계산할 수 없습니다')

    def reject(self):
        if self.canceled:
            return
        self.canceled = True
        self.stage.setText('작업을 취소하고 있습니다')
        self.detail.setText('진행 중인 처리를 멈추고 임시 파일을 정리합니다.')
        self.cancel_button.setEnabled(False)
        self.cancelRequested.emit()

    def closeEvent(self, event):
        self.reject()
        event.ignore()
