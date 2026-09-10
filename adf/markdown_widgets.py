"""Markdown export options and a cancelable, staged local conversion job."""
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import unquote
from uuid import uuid4

import pymupdf
from PySide6.QtCore import QProcess, QTimer, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QMessageBox)

from .document import _require_permission, parse_ranges
from .markdown_export import validate_markdown_name
from .progress_widgets import TaskProgressDialog


class MarkdownOptionsDialog(QDialog):
    def __init__(self, document, current, selected, parent=None):
        super().__init__(parent)
        self.document = document
        self.current = current
        self.selected = sorted(set(selected))
        self.options = None
        self.export_date = datetime.now().strftime('%Y%m%d')
        self.export_id = uuid4().hex[:12]
        source = Path(document.path) if document.path else Path.home()/'문서.pdf'
        self.parent_folder = source.parent
        self.setWindowTitle('OCR · Markdown 내보내기')
        self.setMinimumWidth(530)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 20)
        title = QLabel('문서를 읽는 순서대로 내보내기')
        title.setObjectName('introTitle')
        outer.addWidget(title)
        text = QLabel('문단·표를 복원하고 이미지·그래프·도형을 함께 저장합니다.\n'
                      '한글·영어를 이 PC에서 인식하며, 처리 중 취소할 수 있습니다.')
        text.setWordWrap(True)
        outer.addWidget(text)
        form = QFormLayout()
        self.pages = QComboBox()
        for label, value in [('전체 페이지', 'all'), ('현재 페이지', 'current'),
                             ('왼쪽에서 선택한 페이지', 'selected'), ('직접 입력', 'range')]:
            self.pages.addItem(label, value)
        self.ranges = QLineEdit()
        self.ranges.setPlaceholderText('예: 1,3,5-8')
        self.ranges.setEnabled(False)
        self.pages.currentIndexChanged.connect(lambda _: self.ranges.setEnabled(self.pages.currentData() == 'range'))
        form.addRow('내보낼 페이지', self.pages)
        form.addRow('페이지 범위', self.ranges)
        self.method = QComboBox()
        self.method.addItem('자동 · PDF 텍스트 우선, 필요한 영역 OCR', False)
        self.method.addItem('모든 글자를 OCR로 다시 인식', True)
        form.addRow('글자 인식', self.method)
        self.image_storage = QComboBox()
        self.image_storage.addItem('MD 안에 이미지 포함 · Base64 (기본)', 'embedded')
        self.image_storage.addItem('이미지 폴더로 분리 · 상대경로로 연결', 'files')
        form.addRow('이미지 저장', self.image_storage)
        self.originals = QCheckBox('확인용 전체 페이지 이미지도 함께 저장')
        self.originals.setChecked(True)
        form.addRow('', self.originals)
        self.location = QLineEdit(str(self.parent_folder))
        self.location.setReadOnly(True)
        location = QHBoxLayout()
        location.addWidget(self.location, 1)
        choose = QPushButton('폴더 선택…')
        choose.clicked.connect(self.choose_folder)
        location.addWidget(choose)
        form.addRow('저장 위치', location)
        self.name = QLineEdit(source.stem)
        form.addRow('문서 이름', self.name)
        outer.addLayout(form)
        self.output_preview = QLabel()
        self.output_preview.setWordWrap(True)
        self.output_preview.setTextFormat(Qt.TextFormat.PlainText)
        self.output_preview.setObjectName('muted')
        outer.addWidget(self.output_preview)
        self.image_note = QLabel()
        self.image_note.setWordWrap(True)
        self.image_note.setObjectName('muted')
        outer.addWidget(self.image_note)
        self.image_storage.currentIndexChanged.connect(self.update_preview)
        self.name.textChanged.connect(self.update_preview)
        self.update_preview()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('내보내기 시작')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('취소')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def bundle_name(self):
        return f'{self.export_date}_{self.name.text().strip()}_{self.export_id}'

    def update_preview(self, *args):
        name = self.bundle_name()
        example = f'{name}/\n  {name}.md'
        if self.image_storage.currentData() == 'files':
            example += f'\n  images/{name}_p00001_001.png'
            note = 'MD와 이미지에 날짜·문서 이름·고유 ID를 붙입니다. 상대경로로 연결하므로 저장된 폴더 전체를 함께 옮기세요.'
        else:
            note = '그림 데이터를 MD 안에 넣습니다. MD 파일만 옮겨도 이미지가 함께 유지됩니다. 일부 뷰어에서 표시되지 않으면 이미지 폴더 분리를 선택하세요.'
        self.output_preview.setText('저장할 파일 예시\n'+example)
        self.image_note.setText(note)

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Markdown 묶음을 저장할 위치', str(self.parent_folder))
        if folder:
            self.parent_folder = Path(folder)
            self.location.setText(folder)

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
                pages = list(dict.fromkeys(page for group in parse_ranges(self.ranges.text(), self.document.page_count) for page in group))
            if not pages:
                raise ValueError('내보낼 페이지를 한 개 이상 선택해 주세요.')
            name = self.name.text().strip()
            if not name or name in {'.', '..'} or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith(('.', ' ')):
                raise ValueError('문서 이름을 확인해 주세요.')
            if re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', name, re.I):
                raise ValueError('Windows에서 사용할 수 없는 폴더 이름입니다.')
            bundle = self.bundle_name()
            markdown_name = validate_markdown_name(bundle+'.md')
            output = self.parent_folder/bundle
            if output.exists():
                raise FileExistsError('같은 이름의 결과가 있습니다. 문서 이름을 바꾸거나 내보내기 창을 다시 열어 주세요.')
            self.options = dict(pages=pages, output=str(output), force_ocr=self.method.currentData(),
                                include_pages=self.originals.isChecked(),
                                image_storage=self.image_storage.currentData(), markdown_name=markdown_name)
            super().accept()
        except Exception as error:
            QMessageBox.warning(self, '내보내기 설정', str(error))


def publish_bundle(staging, destination, markdown_name='document.md', image_storage='files'):
    """Atomic directory commit on the same volume, never replacing a target."""
    staging, destination = Path(staging), Path(destination)
    validate_markdown_name(markdown_name)
    if destination.exists():
        raise FileExistsError('같은 이름의 폴더가 생겼습니다. 새 이름으로 다시 내보내 주세요.')
    if staging.is_symlink() or not staging.is_dir():
        raise ValueError('완성된 Markdown 묶음이 없습니다.')
    for directory, folders, files in os.walk(staging):
        for name in folders+files:
            path = Path(directory)/name
            if path.is_symlink() or path.is_junction():
                raise ValueError('내보내기 결과에 예상하지 않은 링크가 있습니다.')
    if not (staging/markdown_name).is_file():
        raise ValueError('완성된 Markdown 파일이 없습니다.')
    if image_storage == 'files':
        # Embedded images can make very large MD files. Never read one wholesale
        # into the GUI process; retain enough overlap for an encoded asset path.
        tail = b''
        with (staging/markdown_name).open('rb') as source:
            while chunk := source.read(256*1024):
                text = tail+chunk
                for match in re.finditer(rb'\]\(((?:images|pages)/[^)\r\n]{1,2048})\)', text):
                    target = (staging/unquote(match[1].decode('utf-8'))).resolve()
                    if not target.is_relative_to(staging.resolve()) or not target.is_file():
                        raise ValueError('Markdown에 연결된 이미지 파일이 없습니다.')
                tail = text[-2064:]
    if sys.platform == 'win32':
        os.rename(staging, destination)  # Windows MoveFile rejects an existing directory.
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == 'darwin':
            result = libc.renamex_np(os.fsencode(staging), os.fsencode(destination), 4)  # RENAME_EXCL
        elif hasattr(libc, 'renameat2'):
            result = libc.renameat2(-100, os.fsencode(staging), -100, os.fsencode(destination), 1)
        else:
            raise OSError('이 운영체제에서는 안전한 폴더 저장을 지원하지 않습니다.')
        if result:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(destination))


def export_markdown(parent, options):
    from .app import resource_path, _restrict_worker_directory, _kill_worker_process_tree
    _require_permission(parent.document.doc, pymupdf.PDF_PERM_COPY)
    pages = options['pages']
    markdown_name = validate_markdown_name(options.get('markdown_name', 'document.md'))
    image_storage = options.get('image_storage', 'embedded')
    if image_storage not in {'embedded', 'files'}:
        raise ValueError('이미지 저장 방식을 확인해 주세요.')
    if not pages or len(set(pages)) != len(pages) or any(page < 0 or page >= parent.document.page_count for page in pages):
        raise ValueError('내보낼 페이지를 확인해 주세요.')
    destination = Path(options['output']).resolve()
    if destination.exists() or not destination.parent.is_dir():
        raise FileExistsError('내보낼 새 폴더와 저장 위치를 확인해 주세요.')
    with tempfile.TemporaryDirectory(prefix='.adf-md-', dir=destination.parent) as directory:
        folder = Path(directory)
        _restrict_worker_directory(folder)
        models = resource_path('OCR_MODELS') if getattr(sys, 'frozen', False) else resource_path('.tools/ocr-models')
        task = dict(source=str(folder/'document.pdf'), staging=str(folder/'output'), models=str(models),
                    progress=str(folder/'progress.json'), result=str(folder/'result.json'),
                    page_numbers=[page+1 for page in pages], force_ocr=options['force_ocr'],
                    include_pages=options['include_pages'], image_storage=image_storage,
                    markdown_name=markdown_name)
        request = folder/'task.json'
        request.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
        dialog = TaskProgressDialog('OCR · Markdown 내보내기', parent)
        parent.markdown_progress = dialog
        process = QProcess(dialog)
        process.setProgram(sys.executable)
        process.setArguments(([] if getattr(sys, 'frozen', False) else [str(resource_path('main.py'))])+
                             ['--markdown-worker', str(request)])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: process.readAllStandardOutput())
        parent.worker = process
        timer = QTimer(dialog)
        timer.setInterval(80)
        snapshot = pymupdf.open()
        state = dict(index=0, done=False, result=None, error=None)

        def end(code):
            state['done'] = True
            timer.stop()
            dialog.done(code)

        def cancel():
            if process.state() != QProcess.ProcessState.NotRunning:
                try:
                    if process.processId():
                        _kill_worker_process_tree(process.processId())
                    else:
                        process.kill()
                except Exception:
                    process.kill()
            else:
                end(QDialog.DialogCode.Rejected)

        def prepare():
            if state['done'] or dialog.canceled:
                return
            try:
                index = state['index']
                if index < len(pages):
                    dialog.update_progress(dict(stage='현재 문서에서 페이지 준비', completed=index,
                        total=len(pages), detail=f'저장 전 수정 내용 포함 · 원본 {pages[index]+1}페이지'))
                    snapshot.insert_pdf(parent.document.doc, from_page=pages[index], to_page=pages[index],
                                        final=int(index == len(pages)-1))
                    state['index'] += 1
                    QTimer.singleShot(0, prepare)
                else:
                    dialog.update_progress(dict(stage='인식할 PDF 준비', detail='선택한 페이지를 임시 작업 파일로 저장합니다.'))
                    snapshot.save(task['source'], garbage=1, deflate=True)
                    snapshot.close()
                    QTimer.singleShot(0, start)
            except Exception as error:
                state['error'] = error
                end(QDialog.DialogCode.Rejected)

        def start():
            if state['done'] or dialog.canceled:
                return
            dialog.update_progress(dict(stage='OCR 작업 시작', detail='이 PC에서 문서를 처리합니다.'))
            process.start()
            timer.start()

        def poll():
            try:
                dialog.update_progress(json.loads(Path(task['progress']).read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass

        def finished(code, status):
            if state['done']:
                return
            if dialog.canceled:
                end(QDialog.DialogCode.Rejected)
                return
            try:
                result = json.loads(Path(task['result']).read_text(encoding='utf-8'))
                if code or not result.get('ok'):
                    raise RuntimeError(result.get('error', 'Markdown 변환 작업이 중단되었습니다.'))
                dialog.update_progress(dict(stage='완성된 묶음 저장', detail=str(destination)))
                publish_bundle(task['staging'], destination, markdown_name, image_storage)
                state['result'] = result['result']
                end(QDialog.DialogCode.Accepted)
            except Exception as error:
                state['error'] = error
                end(QDialog.DialogCode.Rejected)

        def failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                state['error'] = RuntimeError('OCR 작업을 시작하지 못했습니다. '+process.errorString())
                end(QDialog.DialogCode.Rejected)

        dialog.cancelRequested.connect(cancel)
        timer.timeout.connect(poll)
        process.finished.connect(finished)
        process.errorOccurred.connect(failed)
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
            if not snapshot.is_closed:
                snapshot.close()
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(3000)
            parent.worker = None
            parent.markdown_progress = None
            dialog.deleteLater()
