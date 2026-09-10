"""Prepare PDF metadata in a cancelable process without blocking the viewer."""
import json
from pathlib import Path
import sys
import tempfile

import pymupdf

from .document import PdfDocument, PasswordRequired, _open_pdf
from .task_progress import ProgressWriter, write_result


def prepare_pdf(task):
    source = Path(task['source'])
    folder = Path(task['result']).parent
    progress = ProgressWriter(task['progress'])
    prepared = folder/'document.pdf'
    progress('파일 읽기', 0, source.stat().st_size, source.name)
    with source.open('rb') as original, prepared.open('wb') as target:
        import os
        before = os.fstat(original.fileno())
        done = 0
        while chunk := original.read(4*1024*1024):
            target.write(chunk)
            done += len(chunk)
            progress('파일 읽기', done, before.st_size, f'{source.name} · 바이트')
        after = os.fstat(original.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise OSError('파일을 읽는 동안 PDF가 변경되었습니다. 다시 열어 주세요.')
    progress('PDF 구조 확인', detail='암호와 문서 구조를 확인합니다.')
    with _open_pdf(prepared, task.get('password')) as document:
        sizes = []
        for index in range(document.page_count):
            rect = document[index].rect
            sizes.append([rect.width, rect.height])
            progress('페이지 확인', index+1, document.page_count, '페이지 수와 크기를 확인합니다.')
        progress('첫 페이지 준비', detail='문서의 첫 화면을 그리고 있습니다.')
        page = document[0]
        scale = min(1.5, (4_000_000/max(1, page.rect.get_area()))**.5)
        page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False).save(folder/'preview.png')
    return dict(prepared=str(prepared), sizes=sizes, bytes=done)


def loading_worker(request):
    task = json.loads(Path(request).read_text(encoding='utf-8'))
    try:
        result = prepare_pdf(task)
    except Exception as error:
        result = dict(error=str(error), password_required=isinstance(error, PasswordRequired),
                      incorrect=getattr(error, 'incorrect', False))
    write_result(task['result'], result)
    return int('error' in result)


def load_pdf(path, password, parent):
    """Nested Qt event loop keeps existing open_path callers transactional."""
    from PySide6.QtCore import QProcess, QTimer
    from PySide6.QtWidgets import QDialog
    from .app import resource_path, _restrict_worker_directory, _kill_worker_process_tree
    from .progress_widgets import TaskProgressDialog

    with tempfile.TemporaryDirectory(prefix='adf-open-') as directory:
        folder = Path(directory)
        _restrict_worker_directory(folder)
        task = dict(source=str(Path(path).resolve()), password=password,
                    result=str(folder/'result.json'), progress=str(folder/'progress.json'))
        request = folder/'task.json'
        request.write_text(json.dumps(task, ensure_ascii=False), encoding='utf-8')
        dialog = TaskProgressDialog('PDF 열기', parent)
        parent.loading_dialog = dialog
        process = QProcess(dialog)
        process.setProgram(sys.executable)
        process.setArguments(([] if getattr(sys, 'frozen', False) else [str(resource_path('main.py'))])+
                             ['--load-worker', str(request)])
        poll = QTimer(dialog)
        poll.setInterval(80)
        state = dict(result=None, file=None, data=bytearray(), document=None, settled=False)

        def update():
            try:
                dialog.update_progress(json.loads(Path(task['progress']).read_text(encoding='utf-8')))
            except (OSError, ValueError):
                pass

        def end(code):
            state['settled'] = True
            poll.stop()
            if state['file'] is not None:
                state['file'].close()
                state['file'] = None
            dialog.done(code)

        def cancel():
            if process.state() != QProcess.ProcessState.NotRunning:
                if process.processId():
                    try:
                        _kill_worker_process_tree(process.processId())
                    except Exception:
                        process.kill()
                else:
                    process.kill()
            else:
                end(QDialog.DialogCode.Rejected)

        def read_next():
            if state['settled']:
                return
            if dialog.canceled:
                end(QDialog.DialogCode.Rejected)
                return
            try:
                chunk = state['file'].read(4*1024*1024)
                if chunk:
                    state['data'].extend(chunk)
                    dialog.update_progress(dict(stage='편집할 문서 준비', completed=len(state['data']),
                                                total=state['result']['bytes'], detail='확인한 PDF를 작업 화면으로 옮깁니다. · 바이트'))
                    QTimer.singleShot(0, read_next)
                else:
                    state['file'].close()
                    state['file'] = None
                    document = PdfDocument()
                    document.open_prepared(state['data'], path, password, state['result']['sizes'])
                    document.opening_preview = (document.revision, (folder/'preview.png').read_bytes())
                    state['document'] = document
                    end(QDialog.DialogCode.Accepted)
            except Exception as error:
                state['result'] = dict(error=str(error))
                end(QDialog.DialogCode.Rejected)

        def finished(code, status):
            if state['settled']:
                return
            poll.stop()
            if dialog.canceled:
                end(QDialog.DialogCode.Rejected)
                return
            try:
                result = json.loads(Path(task['result']).read_text(encoding='utf-8'))
                state['result'] = result
                if code != 0 or 'error' in result:
                    end(QDialog.DialogCode.Rejected)
                    return
                state['file'] = Path(result['prepared']).open('rb')
                read_next()
            except Exception as error:
                state['result'] = dict(error=str(error))
                end(QDialog.DialogCode.Rejected)

        def failed(error):
            if error == QProcess.ProcessError.FailedToStart:
                state['result'] = dict(error='문서 열기 작업을 시작하지 못했습니다. '+process.errorString())
                end(QDialog.DialogCode.Rejected)

        def start():
            if not dialog.canceled:
                process.start()
                poll.start()

        process.finished.connect(finished)
        process.errorOccurred.connect(failed)
        dialog.cancelRequested.connect(cancel)
        poll.timeout.connect(update)
        QTimer.singleShot(0, start)
        try:
            dialog.exec()
            if dialog.canceled:
                return None
            result = state['result'] or {}
            if result.get('password_required'):
                raise PasswordRequired(str(path), result.get('incorrect', False))
            if 'error' in result:
                raise RuntimeError(result['error'])
            return state['document']
        finally:
            poll.stop()
            if state['file'] is not None:
                state['file'].close()
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(3000)
            parent.loading_dialog = None
            dialog.deleteLater()
