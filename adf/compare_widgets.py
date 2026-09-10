"""Two-file comparison with synchronized, read-only page previews."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile

import pymupdf
from PySide6.QtCore import QProcess, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QBrush, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QGraphicsScene,
    QGraphicsView, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QSplitter, QVBoxLayout, QWidget)

from .document import PasswordRequired, _open_pdf, _require_permission


LABELS = {'replace': '텍스트 수정', 'insert': '텍스트 추가', 'delete': '텍스트 삭제',
          'visual': '화면 변경', 'size': '용지 크기 변경', 'page_insert': '페이지 추가', 'page_delete': '페이지 삭제'}


class CompareView(QGraphicsView):
    zoomRequested = Signal(int)
    resized = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(180, 180)
        self.page_size = (595, 842)
        self.page_key = None
        self.overlays = []

    def clear_page(self):
        self.scene().clear()
        self.page_key = None
        self.overlays = []

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoomRequested.emit(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resized.emit()

    def show_page(self, page, changes, side, selected=None, highlights=True):
        key = (id(page.parent), page.number) if page is not None else None
        for overlay in self.overlays:
            self.scene().removeItem(overlay)
        self.overlays = []
        if page is None:
            self.clear_page()
            self.page_size = (595, 842)
            self.scene().setSceneRect(0, 0, *self.page_size)
            label = self.scene().addText('해당 페이지 없음')
            label.setDefaultTextColor(QColor('#707988'))
            label.setPos(230, 390)
            return
        if key != self.page_key:
            self.clear_page()
            self.page_key = key
            self.page_size = (page.rect.width, page.rect.height)
            scale = min(3., 2200/max(self.page_size))
            pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
            image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            item = self.scene().addPixmap(QPixmap.fromImage(image))
            item.setScale(1/scale)
            self.scene().setSceneRect(-12, -12, self.page_size[0]+24, self.page_size[1]+24)
        if not highlights:
            return
        for index, change in enumerate(changes):
            color = QColor('#c54955' if side == 'left' else '#25835c')
            if change['kind'] in ('visual', 'size', 'replace'):
                color = QColor('#b87810')
            pen = QPen(color, 2 if index == selected else 1)
            pen.setCosmetic(True)
            fill = QColor(color)
            fill.setAlpha(48 if index == selected else 22)
            for rect in change[side+'_rects']:
                x0, y0, x1, y1 = rect
                overlay = self.scene().addRect(QRectF(x0, y0, x1-x0, y1-y0), pen, QBrush(fill))
                overlay.setZValue(1)
                self.overlays.append(overlay)


class CompareDialog(QDialog):
    def __init__(self, parent=None, initial_path=''):
        super().__init__(parent)
        self.setWindowTitle('PDF 두 버전 비교 — ADF')
        self.resize(1320, 860)
        self.setMinimumSize(940, 640)
        self.documents = []
        self.result = None
        self.worker = None
        self.job = None
        self.canceled = False
        self.close_pending = None
        self.syncing = False
        self.current_pair = -1
        self.current_change = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        self.paths, self.browse = [], []
        for index, label in enumerate(('이전 버전', '수정 버전')):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            entry = QLineEdit(initial_path if index == 0 else '')
            entry.setPlaceholderText('비교할 PDF 파일을 선택하세요')
            entry.setAccessibleName(label+' 파일')
            row.addWidget(entry, 1)
            button = QPushButton('파일 선택')
            button.clicked.connect(lambda _, side=index: self.choose_file(side))
            row.addWidget(button)
            outer.addLayout(row)
            self.paths.append(entry)
            self.browse.append(button)
        options = QHBoxLayout()
        hint = QLabel('선택한 파일의 저장본을 비교합니다')
        hint.setObjectName('muted')
        options.addWidget(hint, 1)
        self.start = QPushButton('비교 시작')
        self.start.setObjectName('primary')
        self.start.clicked.connect(self.start_comparison)
        self.cancel = QPushButton('비교 취소')
        self.cancel.clicked.connect(self.cancel_comparison)
        self.cancel.hide()
        options.addWidget(self.cancel)
        options.addWidget(self.start)
        outer.addLayout(options)
        self.status = QLabel('두 파일을 선택하면 바뀐 글과 화면을 확인할 수 있습니다')
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        outer.addWidget(self.status)
        controls = QHBoxLayout()
        self.pages = QComboBox()
        self.pages.setAccessibleName('비교 페이지 쌍')
        self.pages.currentIndexChanged.connect(self.page_selected)
        controls.addWidget(self.pages, 1)
        self.previous = QPushButton('이전 변경')
        self.previous.setShortcut('Alt+Up')
        self.previous.clicked.connect(lambda: self.navigate(-1))
        self.next = QPushButton('다음 변경')
        self.next.setShortcut('Alt+Down')
        self.next.clicked.connect(lambda: self.navigate(1))
        controls.addWidget(self.previous)
        controls.addWidget(self.next)
        self.highlights = QCheckBox('변경 표시')
        self.highlights.setChecked(True)
        self.highlights.toggled.connect(lambda: self.display_pair(self.current_pair, self.current_change))
        controls.addWidget(self.highlights)
        self.zoom = QComboBox()
        self.zoom.setAccessibleName('양쪽 문서 확대 배율')
        for title, value in [('쪽 맞춤', 0), ('50%', .5), ('75%', .75), ('100%', 1.), ('125%', 1.25), ('150%', 1.5), ('200%', 2.), ('300%', 3.)]:
            self.zoom.addItem(title, value)
        self.zoom.currentIndexChanged.connect(self.apply_zoom)
        controls.addWidget(self.zoom)
        outer.addLayout(controls)
        split = QSplitter()
        self.changes = QListWidget()
        self.changes.setAccessibleName('변경 내용 목록')
        self.changes.setMinimumWidth(210)
        self.changes.setMaximumWidth(350)
        self.changes.currentItemChanged.connect(self.change_selected)
        split.addWidget(self.changes)
        self.views, self.titles = [], []
        for side in range(2):
            panel = QWidget()
            layout = QVBoxLayout(panel)
            layout.setContentsMargins(4, 0, 4, 0)
            title = QLabel('이전 버전' if side == 0 else '수정 버전')
            title.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(title)
            view = CompareView()
            view.zoomRequested.connect(self.step_zoom)
            view.resized.connect(self.schedule_fit)
            for orientation, bar in enumerate((view.horizontalScrollBar(), view.verticalScrollBar())):
                bar.valueChanged.connect(lambda _, s=side, o=orientation: self.sync_scroll(s, o))
            layout.addWidget(view)
            split.addWidget(panel)
            self.views.append(view)
            self.titles.append(title)
        split.setSizes([250, 510, 510])
        split.setChildrenCollapsible(False)
        outer.addWidget(split, 1)
        footer = QHBoxLayout()
        legend = QLabel('빨강 삭제 · 초록 추가 · 노랑 수정   |   Ctrl+휠 확대 · Alt+↑/↓ 변경 이동')
        legend.setObjectName('muted')
        footer.addWidget(legend, 1)
        close = QPushButton('닫기')
        close.clicked.connect(self.reject)
        footer.addWidget(close)
        outer.addLayout(footer)
        self.fit_timer = QTimer(self)
        self.fit_timer.setSingleShot(True)
        self.fit_timer.timeout.connect(self.apply_zoom)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self.start.setDefault(True)
        for entry in self.paths:
            entry.textChanged.connect(self.inputs_changed)
        self.set_busy(False)

    def choose_file(self, side):
        path, _ = QFileDialog.getOpenFileName(self, '이전 버전 선택' if side == 0 else '수정 버전 선택', self.paths[side].text(), 'PDF 문서 (*.pdf)')
        if path:
            self.paths[side].setText(path)

    def clear_results(self):
        self.result = None
        self.current_pair = -1
        self.current_change = None
        self.pages.clear()
        self.changes.clear()
        for view in self.views:
            view.clear_page()
        for document in self.documents:
            document.close()
        self.documents = []

    def inputs_changed(self):
        if not self.worker:
            self.clear_results()
            self.status.setText('비교 시작을 누르면 선택한 파일의 변경 내용을 확인합니다')
            self.set_busy(False)

    def set_busy(self, busy):
        for control in self.paths + self.browse:
            control.setEnabled(not busy)
        self.start.setEnabled(not busy and all(p.text().strip() for p in self.paths))
        self.cancel.setVisible(busy)
        self.cancel.setEnabled(busy)
        for control in (self.pages, self.changes, self.highlights, self.zoom):
            control.setEnabled(not busy and self.result is not None)
        self.previous.setEnabled(not busy and self.changes.count() > 0)
        self.next.setEnabled(not busy and self.changes.count() > 0)

    def open_document(self, path):
        password = None
        while True:
            try:
                document = _open_pdf(path, password)
                try:
                    _require_permission(document, pymupdf.PDF_PERM_COPY)
                except PermissionError:
                    document.close()
                    password, ok = QInputDialog.getText(self, 'PDF 소유자 암호', f'{Path(path).name}\n추출이 제한된 PDF입니다. 비교하려면 소유자 암호를 입력하세요.', QLineEdit.EchoMode.Password)
                    if not ok:
                        return None
                    continue
                return document
            except PasswordRequired as error:
                password, ok = QInputDialog.getText(self, 'PDF 암호', f'{Path(path).name}\n{error}', QLineEdit.EchoMode.Password)
                if not ok:
                    return None

    def start_comparison(self):
        if self.worker or not all(p.text().strip() for p in self.paths):
            return
        from .app import _restrict_worker_directory, resource_path
        self.clear_results()
        try:
            for entry in self.paths:
                document = self.open_document(entry.text().strip())
                if document is None:
                    self.clear_results()
                    self.set_busy(False)
                    return
                self.documents.append(document)
            self.job = tempfile.TemporaryDirectory(prefix='adf-compare-')
            folder = Path(self.job.name)
            _restrict_worker_directory(folder)
            task = {}
            for side, document in zip(('left', 'right'), self.documents):
                path = folder/(side+'.pdf')
                path.write_bytes(document.tobytes(garbage=1, encryption=pymupdf.PDF_ENCRYPT_KEEP))
                task[side] = str(path)
                task[side+'_password'] = getattr(document, '_adf_password', '')
            request = folder/'request.json'
            request.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
            self.canceled = False
            self.worker = QProcess(self)
            self.worker.setProgram(sys.executable)
            self.worker.setArguments(([] if getattr(sys, 'frozen', False) else [str(resource_path('main.py'))]) + ['--compare-worker', str(request)])
            self.worker.readyReadStandardOutput.connect(self.read_progress)
            self.worker.finished.connect(self.worker_finished)
            self.worker.errorOccurred.connect(self.worker_error)
            self.set_busy(True)
            self.status.setText('문서의 페이지를 맞추고 있습니다…')
            self.worker.start()
        except Exception as error:
            self.clear_results()
            self.cleanup_job()
            self.set_busy(False)
            self.status.setText(f'비교하지 못했습니다 · {error}')

    def read_progress(self):
        if self.worker:
            data = bytes(self.worker.readAllStandardOutput()).decode('utf-8', errors='replace').strip()
            if data and not self.canceled:
                self.status.setText(data.splitlines()[-1])

    def worker_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.worker_finished(-1)

    def cancel_comparison(self):
        if self.worker:
            from .app import _kill_worker_process_tree
            self.canceled = True
            self.cancel.setEnabled(False)
            self.status.setText('비교를 취소하고 있습니다…')
            if self.worker.processId():
                try:
                    _kill_worker_process_tree(self.worker.processId())
                except Exception:
                    self.worker.kill()
            else:
                self.worker.kill()

    def cleanup_job(self):
        if self.worker:
            self.worker.deleteLater()
            self.worker = None
        if self.job:
            self.job.cleanup()
            self.job = None

    def worker_finished(self, exit_code, *_):
        if self.worker is None:
            return
        try:
            if self.canceled:
                self.clear_results()
                self.status.setText('비교를 취소했습니다')
            else:
                path = Path(self.job.name)/'result.json'
                payload = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'error': '비교 작업이 중단되었습니다.'}
                if exit_code != 0 or 'error' in payload:
                    raise ValueError(payload.get('error', '비교 작업을 완료하지 못했습니다.'))
                self.populate(payload['result'])
        except Exception as error:
            self.clear_results()
            self.status.setText(f'비교하지 못했습니다 · {error}')
        finally:
            self.cleanup_job()
            self.set_busy(False)
        if self.close_pending is not None:
            self.done(self.close_pending)

    @staticmethod
    def pair_title(pair):
        left = '없음' if pair['left'] is None else f'{pair["left"]+1}쪽'
        right = '없음' if pair['right'] is None else f'{pair["right"]+1}쪽'
        return f'{left} → {right}'

    def populate(self, result):
        self.result = result
        self.pages.blockSignals(True)
        for pi, pair in enumerate(result['pairs']):
            title = self.pair_title(pair)
            self.pages.addItem(title + (' · 변경 있음' if pair['changes'] else ' · 동일'))
            for ci, change in enumerate(pair['changes']):
                summary = LABELS[change['kind']]
                detail = (change['before'][:55] + ' → ' + change['after'][:55]) if change['before'] or change['after'] else '선택해서 위치 확인'
                item = QListWidgetItem(f'{title} · {summary}\n{detail}')
                item.setToolTip(f'{summary}\n{change["before"]}\n→ {change["after"]}')
                item.setData(Qt.ItemDataRole.UserRole, (pi, ci))
                self.changes.addItem(item)
        self.pages.blockSignals(False)
        self.status.setText(f'{result["changed_pages"]}개 페이지 쌍 · {result["change_count"]}개 변경 영역' if result['change_count'] else '텍스트와 화면에서 차이를 찾지 못했습니다')
        self.status.setToolTip('페이지는 내용과 순서를 기준으로 자동 대응합니다. 스캔 품질 차이도 화면 변경으로 표시될 수 있습니다. 메타데이터와 디지털 서명은 비교하지 않습니다.')
        if self.changes.count():
            self.changes.setCurrentRow(0)
        elif result['pairs']:
            self.display_pair(0)

    def page_selected(self, index):
        if not self.syncing:
            self.changes.blockSignals(True)
            self.changes.setCurrentRow(-1)
            self.changes.blockSignals(False)
            self.display_pair(index)

    def change_selected(self, current, *_):
        if current:
            pi, ci = current.data(Qt.ItemDataRole.UserRole)
            self.display_pair(pi, ci)

    def display_pair(self, index, selected=None):
        if not self.result or not 0 <= index < len(self.result['pairs']):
            return
        self.current_pair, self.current_change = index, selected
        pair = self.result['pairs'][index]
        self.syncing = True
        try:
            self.pages.setCurrentIndex(index)
            for side, key in enumerate(('left', 'right')):
                page_index = pair[key]
                page = None if page_index is None else self.documents[side][page_index]
                self.titles[side].setText(('이전 버전' if side == 0 else '수정 버전') + (' · 해당 페이지 없음' if page is None else f' · {page_index+1}쪽'))
                self.views[side].show_page(page, pair['changes'], key, selected, self.highlights.isChecked())
            self.apply_zoom()
            if selected is not None:
                change = pair['changes'][selected]
                for view, side in zip(self.views, ('left', 'right')):
                    rects = change[side+'_rects']
                    if rects:
                        rect = pymupdf.Rect(rects[0])
                        view.ensureVisible(QRectF(rect.x0, rect.y0, rect.width, rect.height), 24, 24)
        finally:
            self.syncing = False

    def navigate(self, delta):
        if self.changes.count():
            row = self.changes.currentRow()
            if row < 0:
                candidates = [i for i in range(self.changes.count())
                              if (self.changes.item(i).data(Qt.ItemDataRole.UserRole)[0]-self.current_pair)*delta >= 0]
                row = (candidates[0] if delta > 0 else candidates[-1]) if candidates else (0 if delta > 0 else self.changes.count()-1)
            else:
                row = (row+delta) % self.changes.count()
            self.changes.setCurrentRow(row)

    def schedule_fit(self):
        if hasattr(self, 'fit_timer') and not self.zoom.currentData():
            self.fit_timer.start(0)

    def apply_zoom(self, *_):
        if not self.views:
            return
        scale = self.zoom.currentData() or min(min((v.viewport().width()-28)/(v.page_size[0]+24),
                    (v.viewport().height()-28)/(v.page_size[1]+24)) for v in self.views)
        was_syncing = self.syncing
        self.syncing = True
        for view in self.views:
            view.resetTransform()
            view.scale(max(.05, scale), max(.05, scale))
        self.syncing = was_syncing

    def step_zoom(self, direction):
        scale = self.views[0].transform().m11()
        values = [(i, self.zoom.itemData(i)) for i in range(1, self.zoom.count())]
        candidates = [(i, v) for i, v in values if (v-scale)*direction > .01]
        if candidates:
            self.zoom.setCurrentIndex(candidates[0 if direction > 0 else -1][0])

    def sync_scroll(self, source, orientation):
        if self.syncing or len(self.views) != 2:
            return
        self.syncing = True
        try:
            views = [self.views[source], self.views[1-source]]
            bars = [v.verticalScrollBar() if orientation else v.horizontalScrollBar() for v in views]
            ratio = (bars[0].value()-bars[0].minimum()) / max(1, bars[0].maximum()-bars[0].minimum())
            bars[1].setValue(round(bars[1].minimum()+ratio*(bars[1].maximum()-bars[1].minimum())))
        finally:
            self.syncing = False

    def done(self, result):
        if self.worker:
            self.close_pending = result
            self.cancel_comparison()
            return
        self.fit_timer.stop()
        self.clear_results()
        self.cleanup_job()
        super().done(result)
