"""Independent combine/split windows launched by Windows Explorer."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pymupdf
from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QWidget, QFileDialog, QMessageBox, QInputDialog, QLineEdit, QLabel, QPushButton, QHBoxLayout, QFrame

from .document import PdfDocument, PasswordRequired


class ToolWindowController(QWidget):
    """Own a single visible operation dialog and its cancellable export job.

    This small controller is never shown. No document-reader/home window is
    constructed when Explorer asks to combine or split a selection.
    """

    def __init__(self, operation, files, on_closed=None):
        super().__init__()
        from .app import resource_path
        from .dialogs import MergeDialog, SplitDialog
        self.document = PdfDocument()
        self.worker = None
        self.worker_temp = None
        self.operation = operation
        self.files = list(files)
        self.on_closed = on_closed
        self.closing = False
        self.result_paths = []
        self.valid = False
        self.dialog = None
        if operation == 'merge':
            self.dialog = MergeDialog(parent=None, paths=files)
        elif operation == 'split' and len(files) == 1:
            password = None
            while True:
                try:
                    self.document.open(files[0], password)
                    break
                except PasswordRequired as exc:
                    title = '암호가 맞지 않습니다. 다시 입력해 주세요.' if exc.incorrect else '문서 열람 암호'
                    password, ok = QInputDialog.getText(None, 'PDF 암호', title, QLineEdit.EchoMode.Password)
                    if not ok:
                        self.document.close()
                        return
                except Exception as exc:
                    QMessageBox.warning(None, 'PDF 열기', str(exc))
                    self.document.close()
                    return
            self.dialog = SplitDialog(self.document, parent=None)
        else:
            raise ValueError('PDF 병합 또는 한 파일의 분할만 시작할 수 있습니다.')
        self.dialog.setWindowIcon(QIcon(str(resource_path('assets/adf.ico'))))
        self.dialog.setWindowModality(Qt.WindowModality.NonModal)
        self.dialog.submit_handler = self.start_export
        self.dialog.rejected.connect(self.close_tool)
        self.export_parent = self.dialog
        self.feedback = QFrame()
        self.feedback.setObjectName('exportResult')
        feedback_row = QHBoxLayout(self.feedback)
        feedback_row.setContentsMargins(12, 8, 12, 8)
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        feedback_row.addWidget(self.result_label, 1)
        self.open_result = QPushButton('PDF 열기')
        self.open_result.clicked.connect(self.open_pdf)
        self.open_folder = QPushButton('저장 폴더 열기')
        self.open_folder.clicked.connect(self.open_output_folder)
        feedback_row.addWidget(self.open_result)
        feedback_row.addWidget(self.open_folder)
        self.dialog.layout().insertWidget(max(0, self.dialog.layout().count()-1), self.feedback)
        self.feedback.hide()
        self.valid = True

    def show_tool(self):
        if self.valid:
            self.dialog.show()
            self.dialog.raise_()
            self.dialog.activateWindow()
        elif self.on_closed:
            QTimer.singleShot(0, self.on_closed)

    def start_export(self):
        if self.worker:
            return
        if self.operation == 'merge':
            initial = str(Path(self.dialog.paths[0]).parent/'병합.pdf') if self.dialog.paths else str(Path.home()/'병합.pdf')
            output, _ = QFileDialog.getSaveFileName(self.dialog, '병합한 PDF 저장', initial, 'PDF 문서 (*.pdf)')
            if not output:
                return
            if not output.lower().endswith('.pdf'):
                output += '.pdf'
            if Path(output).exists():
                self.error('같은 이름의 파일이 이미 있습니다. 병합 결과는 새 파일 이름으로 저장해 주세요.')
                return
            task = {'operation': 'merge', 'paths': self.dialog.paths, 'output': output,
                    'passwords': self.dialog.passwords, 'selected_ranges': self.dialog.selected_ranges}
            title = '선택한 PDF를 병합하고 있습니다'
        else:
            folder = QFileDialog.getExistingDirectory(self.dialog, '분할한 PDF를 저장할 폴더', str(Path(self.files[0]).parent))
            if not folder:
                return
            task = {'operation': 'split', 'groups': self.dialog.groups, 'output_dir': folder,
                    'stem': Path(self.files[0]).stem, 'include_remaining': self.dialog.include_remaining}
            title = 'PDF를 분할하고 있습니다'
        self.feedback.hide()
        self.run_worker(task, title, snapshot=self.operation=='split')

    def run_worker(self, task, title, snapshot=False):
        # Reuse the exact tested authentication, private snapshot and process
        # lifecycle used by document-window exports.
        from .app import MainWindow
        MainWindow.run_worker(self, task, title, snapshot)

    def cancel_worker(self):
        from .app import MainWindow
        MainWindow.cancel_worker(self)

    def worker_error(self, error):
        from .app import MainWindow
        MainWindow.worker_error(self, error)

    def error(self, error):
        QMessageBox.warning(self.dialog, '작업을 완료하지 못했습니다', str(error))

    def worker_finished(self, exit_code, status):
        if not self.worker:
            return
        from .app import _publish_worker_outputs
        task = self.worker_task
        canceled = self.closing or self.progress.wasCanceled()
        try:
            result_path = Path(task['result'])
            payload = json.loads(result_path.read_text(encoding='utf-8')) if result_path.exists() else {'error': '작업이 중단되었습니다.'}
        except Exception:
            payload = {'error': 'PDF 작업 결과를 읽을 수 없습니다.'}
        if not canceled and exit_code == 0 and 'error' not in payload:
            self.progress.setLabelText('완성된 PDF를 저장하고 있습니다')
            self.progress.setCancelButton(None)
            try:
                _publish_worker_outputs(payload.get('pending_outputs', []), result_path.parent)
            except Exception as exc:
                payload = {'error': str(exc)}
        self.progress.close()
        self.progress.deleteLater()
        self.worker.deleteLater()
        self.worker = None
        self.worker_temp.cleanup()
        self.worker_temp = None
        if self.closing:
            self.close_tool()
            return
        if canceled:
            self.result_label.setText('작업을 취소했습니다. 저장된 파일은 없습니다.')
            self.open_result.hide()
            self.open_folder.hide()
            self.feedback.show()
            return
        if exit_code != 0 or 'error' in payload:
            self.error(payload.get('error', '작업을 완료하지 못했습니다.'))
            return
        self.result_paths = [task['output']] if task['operation']=='merge' else payload['result']
        if task['operation']=='merge':
            self.result_label.setText(f'병합 완료 · {Path(task["output"]).name}')
        else:
            self.result_label.setText(f'분할 완료 · PDF {len(self.result_paths)}개를 저장했습니다.')
        self.result_label.setToolTip('\n'.join(self.result_paths))
        self.open_result.setVisible(len(self.result_paths)==1)
        self.open_folder.show()
        self.feedback.show()

    def open_pdf(self):
        if self.result_paths:
            from .app import resource_path
            from PySide6.QtCore import QProcess
            args = ([] if getattr(sys, 'frozen', False) else [str(resource_path('main.py'))]) + [self.result_paths[0]]
            QProcess.startDetached(sys.executable, args)

    def open_output_folder(self):
        if self.result_paths:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.result_paths[0]).parent)))

    def close_tool(self):
        if self.worker:
            self.closing = True
            self.cancel_worker()
            return
        self.document.close()
        if self.dialog:
            self.dialog.hide()
        if self.on_closed:
            self.on_closed()

    def smoke_report(self, output):
        """Inspectable packaged startup check without file writes to inputs."""
        from .app import MainWindow
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        report_path = Path(output)
        try:
            QTest.qWait(160)
            visible = [w for w in QApplication.topLevelWidgets() if w.isVisible()]
            assert self.valid and self.dialog.isVisible()
            assert not any(isinstance(w, MainWindow) for w in visible)
            if self.operation == 'merge':
                actual = self.dialog.paths
                actual_count = self.dialog.file_list.count()
                assert actual_count == len(self.files)
                assert [str(Path(p).resolve()) for p in actual] == [str(Path(p).resolve()) for p in self.files]
            else:
                actual_count = 1
                assert self.document.page_count > 0
            self.dialog.grab().save(str(report_path.with_suffix('.png')))
            report = {'ok': True, 'operation': self.operation, 'files': self.files,
                      'selected_file_count': actual_count, 'standalone': True,
                      'main_window_visible': False, 'dialog_title': self.dialog.windowTitle(),
                      'frozen': bool(getattr(sys, 'frozen', False))}
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            self.close_tool()
        except Exception as exc:
            report_path.write_text(json.dumps({'ok': False, 'error': str(exc)}), encoding='utf-8')
            QApplication.exit(1)
