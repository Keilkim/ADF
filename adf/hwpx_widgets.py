"""HWPX export options and cancelable process lifetime inside XDF."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

import pymupdf
from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

from .document import _require_permission, parse_ranges
from .hwpx_api import DEFAULT_MODEL
from .progress_widgets import TaskProgressDialog


class HwpxOptionsDialog(QDialog):
    def __init__(self, document, current, selected, parent=None):
        super().__init__(parent)
        self.document = document
        self.current = current
        self.selected = list(dict.fromkeys(selected))
        self.options = None
        self.setWindowTitle('한글(HWPX)로 변환')
        self.setMinimumWidth(570)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        title = QLabel('PDF를 수정할 수 있는 한글 문서로')
        title.setObjectName('introTitle')
        outer.addWidget(title)
        intro = QLabel('문단·표는 편집할 수 있게 만들고, 그림·수식은 이미지로 넣습니다.\n'
                       '원본과 다른 글꼴·줄바꿈·배치는 변환 후 확인해 주세요.')
        intro.setWordWrap(True)
        outer.addWidget(intro)
        form = QFormLayout()
        self.pages = QComboBox()
        for text, value in [('전체 페이지', 'all'), ('현재 페이지', 'current'),
                            ('왼쪽에서 선택한 페이지', 'selected'), ('직접 입력', 'range')]:
            self.pages.addItem(text, value)
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText('예: 1,3,5-8')
        self.ranges.setEnabled(False)
        self.pages.currentIndexChanged.connect(lambda _: self.ranges.setEnabled(self.pages.currentData() == 'range'))
        form.addRow('내보낼 페이지', self.pages)
        form.addRow('페이지 범위', self.ranges)
        self.method = QComboBox()
        self.method.addItem('Gemini AI · 문단·표·스캔 문서 인식', 'gemini')
        self.method.addItem('로컬 추출 · API 사용 없음', 'local')
        if os.environ.get('XDF_HWPX_RELAY_URL'):
            self.method.addItem('Gemini AI · 변환 서버 사용', 'relay')
            self.method.setCurrentIndex(2)
        form.addRow('변환 방식', self.method)
        self.model = QComboBox()
        self.model.addItem('Gemini 3.5 Flash-Lite · 기본 · 비용 우선', DEFAULT_MODEL)
        self.model.addItem('Gemini 3.8 Flash · MEDIUM', 'gemini-3.8-flash')
        self.model.addItem('Gemini 3.1 Pro Preview · 복잡한 문서', 'gemini-3.1-pro-preview')
        form.addRow('AI 모델', self.model)
        self.check_button = QPushButton('연결 확인 · 문서 전송 없음')
        self.check_button.clicked.connect(self.check_connection)
        form.addRow('', self.check_button)
        source = Path(document.path) if document.path else Path.home() / '문서.pdf'
        output = source.with_suffix('.hwpx')
        counter = 1
        while output.exists():
            output = source.with_name(f'{source.stem}-{counter}.hwpx')
            counter += 1
        self.output = QLineEdit(str(output))
        self.output.setReadOnly(True)
        row = QHBoxLayout()
        row.addWidget(self.output, 1)
        choose = QPushButton('저장 위치…')
        choose.clicked.connect(self.choose_output)
        row.addWidget(choose)
        form.addRow('HWPX 파일', row)
        self.report = QCheckBox('페이지별 검토 항목과 토큰 사용량도 저장')
        self.report.setChecked(True)
        form.addRow('', self.report)
        outer.addLayout(form)
        self.upload = QCheckBox('선택한 페이지의 이미지·텍스트를 Gemini에 전송해 변환')
        self.upload.setChecked(False)
        outer.addWidget(self.upload)
        self.note = QLabel('새 HWPX 파일로 저장합니다.\n'
                           '한 번에 최대 100쪽을 변환합니다. 큰 문서는 페이지 범위를 나누어 주세요.')
        self.note.setObjectName('muted')
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('HWPX로 변환')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('취소')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.method.currentIndexChanged.connect(self.sync_method)
        from .hwpx_credentials import has_developer_key
        if self.method.currentData() == 'gemini' and not has_developer_key():
            self.method.setCurrentIndex(self.method.findData('local'))
        self.sync_method()

    def sync_method(self):
        online = self.method.currentData() != 'local'
        self.model.setEnabled(online)
        self.check_button.setEnabled(online)
        self.upload.setVisible(online)

    def connection_options(self):
        return dict(provider=self.method.currentData(), model=self.model.currentData(),
                    service_url=os.environ.get('XDF_HWPX_RELAY_URL', ''))

    def check_connection(self):
        try:
            result = run_hwpx_job(self.parent(), dict(operation='check', options=self.connection_options()), dialog_parent=self)
            if result is not None:
                QMessageBox.information(self, 'Gemini 연결', f'{result["model"]}에 접근할 수 있습니다.\n문서는 전송하지 않았습니다.')
        except Exception as error:
            QMessageBox.warning(self, 'Gemini 연결', str(error))

    def choose_output(self):
        output, _ = QFileDialog.getSaveFileName(self, '새 HWPX 파일로 저장', self.output.text(), '한글 문서 (*.hwpx)')
        if output:
            path = Path(output)
            if path.suffix.lower() != '.hwpx':
                path = Path(str(path) + '.hwpx')
            self.output.setText(str(path))

    def accept(self):
        try:
            mode = self.pages.currentData()
            if mode == 'all':
                pages = list(range(self.document.page_count))
            elif mode == 'current':
                pages = [self.current]
            elif mode == 'selected':
                pages = self.selected
            else:
                pages = list(dict.fromkeys(p for group in parse_ranges(self.ranges.text(), self.document.page_count) for p in group))
            if not pages or len(pages) > 100:
                raise ValueError('변환할 페이지를 1~100쪽 선택해 주세요.')
            if self.method.currentData() != 'local' and not self.upload.isChecked():
                raise ValueError('Gemini에 페이지를 보내 변환하려면 전송 항목을 선택해 주세요.')
            output = Path(self.output.text()).resolve()
            if output.suffix.lower() != '.hwpx' or not output.parent.is_dir():
                raise ValueError('HWPX 파일 이름과 저장 폴더를 확인해 주세요.')
            if output.exists() or (self.report.isChecked() and output.with_suffix('.conversion.json').exists()):
                raise FileExistsError('같은 이름의 결과가 있습니다. 새 파일 이름을 선택해 주세요.')
            self.options = dict(self.connection_options(), pages=pages, output=str(output), save_report=self.report.isChecked())
            super().accept()
        except Exception as error:
            QMessageBox.warning(self, 'HWPX 변환 설정', str(error))


def publish_hwpx(folder, output, save_report=True):
    """Commit fully copied files with exclusive links; never replace a result."""
    folder, output = Path(folder), Path(output)
    sources = [(folder / 'output.hwpx', output)]
    if save_report:
        sources.append((folder / 'conversion.json', output.with_suffix('.conversion.json')))
    if output.suffix.lower() != '.hwpx' or not output.parent.is_dir():
        raise ValueError('HWPX 저장 위치를 확인해 주세요.')
    for source, target in sources:
        if not source.is_file() or source.is_symlink() or target.exists():
            raise FileExistsError('완성된 결과와 저장 위치를 확인해 주세요. 기존 결과는 바꾸지 않습니다.')
    temporary, published = [], []
    try:
        for source, target in sources:
            fd, path = tempfile.mkstemp(prefix='.xdf-hwpx-', dir=target.parent)
            temporary.append((Path(path), target))
            with os.fdopen(fd, 'wb') as destination, source.open('rb') as original:
                shutil.copyfileobj(original, destination)
                destination.flush()
                os.fsync(destination.fileno())
        for source, target in temporary:
            os.link(source, target)
            published.append(target)
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path, _ in temporary:
            path.unlink(missing_ok=True)


def run_hwpx_job(parent, task, *, dialog_parent=None):
    from .app import resource_path, _restrict_worker_directory, _kill_worker_process_tree
    converting = task.get('operation') != 'check'
    if parent.worker:
        raise ValueError('진행 중인 작업이 끝난 뒤 변환해 주세요.')
    pages = task['options'].get('pages', [])
    if converting:
        _require_permission(parent.document.doc, pymupdf.PDF_PERM_COPY)
        if not pages or len(set(pages)) != len(pages) or len(pages) > 100 or any(type(p) is not int or p < 0 or p >= parent.document.page_count for p in pages):
            raise ValueError('변환할 페이지를 확인해 주세요.')
    with tempfile.TemporaryDirectory(prefix='xdf-hwpx-') as directory:
        folder = Path(directory)
        _restrict_worker_directory(folder)
        # Never serialize a credential, source path, or destination path into
        # the worker request. The worker writes fixed files in this private job.
        request_data = dict(operation=task.get('operation', 'convert'), options={
            k: task['options'][k] for k in ('provider', 'model', 'service_url') if k in task['options']},
            page_numbers=[p + 1 for p in pages])
        request = folder / 'task.json'
        request.write_text(json.dumps(request_data, ensure_ascii=False), encoding='utf-8')
        dialog = TaskProgressDialog('한글(HWPX)로 변환' if converting else 'Gemini 연결 확인', dialog_parent or parent)
        parent.hwpx_progress = dialog
        process = QProcess(dialog)
        process.setProgram(sys.executable)
        process.setArguments(([] if getattr(sys, 'frozen', False) else [str(resource_path('main.py'))]) + ['--hwpx-worker', str(request)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: process.readAllStandardOutput())
        parent.worker = process
        snapshot = pymupdf.open()
        state = dict(index=0, done=False, error=None, result=None)
        timer = QTimer(dialog)
        timer.setInterval(100)
        timeout = QTimer(dialog)
        timeout.setSingleShot(True)
        timeout.setInterval(10 * 60 * 1000)

        def end(code):
            state['done'] = True
            timer.stop()
            timeout.stop()
            dialog.done(code)

        def stop():
            if process.state() != QProcess.ProcessState.NotRunning:
                try:
                    _kill_worker_process_tree(process.processId()) if process.processId() else process.kill()
                except Exception:
                    process.kill()
            else:
                end(QDialog.DialogCode.Rejected)

        def timed_out():
            state['error'] = ValueError('변환 시간이 10분을 넘었습니다. 페이지 수를 줄여 다시 시도해 주세요.')
            stop()

        def prepare():
            if state['done'] or dialog.canceled:
                return
            try:
                if converting and state['index'] < len(pages):
                    index = state['index']
                    dialog.update_progress(dict(stage='현재 PDF에서 페이지 준비', completed=index, total=len(pages),
                                                detail=f'저장 전 수정 내용 포함 · 원본 {pages[index] + 1}쪽'))
                    # Keep visible annotations and form appearances but remove
                    # file attachments such as voice recordings from the job.
                    snapshot.insert_pdf(parent.document.doc, from_page=pages[index], to_page=pages[index],
                                        annots=True, widgets=True, final=int(index == len(pages) - 1))
                    copied_page = snapshot[-1]
                    for annotation in list(copied_page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]) or []):
                        copied_page.delete_annot(annotation)
                    state['index'] += 1
                    QTimer.singleShot(0, prepare)
                    return
                if converting:
                    snapshot.save(folder / 'document.pdf', garbage=1, deflate=True)
                snapshot.close()
                process.start()
                timer.start()
                timeout.start()
            except Exception as error:
                state['error'] = error
                end(QDialog.DialogCode.Rejected)

        def poll():
            try:
                dialog.update_progress(json.loads((folder / 'progress.json').read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass

        def finished(code, status):
            if state['done']:
                return
            if dialog.canceled or state['error']:
                end(QDialog.DialogCode.Rejected)
                return
            try:
                response = json.loads((folder / 'result.json').read_text(encoding='utf-8'))
                if code or not response.get('ok'):
                    raise ValueError(response.get('error', 'HWPX 변환이 중단되었습니다.'))
                if converting:
                    dialog.update_progress(dict(stage='HWPX 저장', detail=task['options']['output']))
                    publish_hwpx(folder, task['options']['output'], task['options'].get('save_report', True))
                state['result'] = response['result']
                end(QDialog.DialogCode.Accepted)
            except Exception as error:
                state['error'] = error
                end(QDialog.DialogCode.Rejected)

        def failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                state['error'] = ValueError('HWPX 변환 작업을 시작하지 못했습니다.')
                end(QDialog.DialogCode.Rejected)

        process.finished.connect(finished)
        process.errorOccurred.connect(failed)
        dialog.cancelRequested.connect(stop)
        timer.timeout.connect(poll)
        timeout.timeout.connect(timed_out)
        QTimer.singleShot(0, prepare)
        try:
            dialog.exec()
            if dialog.canceled:
                return None
            if state['error']:
                raise state['error']
            return state['result']
        finally:
            timer.stop()
            timeout.stop()
            if not snapshot.is_closed:
                snapshot.close()
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(5000)
            parent.worker = None
            parent.hwpx_progress = None
            parent.refresh_actions()
            dialog.deleteLater()
