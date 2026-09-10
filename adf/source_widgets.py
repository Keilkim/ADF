"""Read bundled source archives without invoking a file association."""
from pathlib import Path, PurePosixPath
import zipfile

from PySide6.QtCore import QIODevice, QSaveFile, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QProgressDialog, QPushButton, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget)


class SourceBrowser(QWidget):
    PREVIEW_LIMIT = 1024*1024

    def __init__(self, sources_path, source_files, parent=None):
        super().__init__(parent)
        self.sources_path = Path(sources_path)
        self.source_files = source_files
        self.selected = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 8)
        self.search = QLineEdit()
        self.search.setPlaceholderText('소스 파일 이름 검색')
        outer.addWidget(self.search)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(230)
        self.tree.setUniformRowHeights(True)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        split.addWidget(self.tree)
        split.addWidget(self.preview)
        split.setSizes([300, 600])
        outer.addWidget(split, 1)
        row = QHBoxLayout()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName('muted')
        row.addWidget(self.status, 1)
        self.save_button = QPushButton('선택한 소스 저장…')
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_selected)
        row.addWidget(self.save_button)
        outer.addLayout(row)
        self.tree.currentItemChanged.connect(self.show_source)
        self.search.textChanged.connect(self.filter_files)
        for index, filename in enumerate(source_files):
            archive_path = self.sources_path/filename
            root = QTreeWidgetItem(self.tree, ['ADF 프로그램 소스' if index == 0 else '외부 라이브러리 원본 소스'])
            root.setToolTip(0, filename)
            root.setData(0, Qt.ItemDataRole.UserRole, (filename, None))
            folders = {'': root}
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    for info in sorted(archive.infolist(), key=lambda entry: entry.filename.casefold()):
                        path = PurePosixPath(info.filename)
                        if path.is_absolute() or '..' in path.parts or '\\' in info.filename:
                            continue
                        parent_item = root
                        for count, part in enumerate(path.parts):
                            key = '/'.join(path.parts[:count+1])
                            if key not in folders:
                                item = QTreeWidgetItem(parent_item, [part])
                                item.setToolTip(0, info.filename)
                                folders[key] = item
                            parent_item = folders[key]
                        if not info.is_dir():
                            parent_item.setData(0, Qt.ItemDataRole.UserRole, (filename, info.filename))
                root.setExpanded(True)
                if root.childCount() == 1:
                    root.child(0).setExpanded(True)
            except (OSError, zipfile.BadZipFile) as error:
                root.setData(0, Qt.ItemDataRole.UserRole, None)
                root.setToolTip(0, str(error))
        self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def filter_files(self, query):
        query = query.strip().casefold()

        def visit(item):
            children = [visit(item.child(i)) for i in range(item.childCount())]
            match = not query or query in item.text(0).casefold() or any(children)
            item.setHidden(not match)
            if query and any(children):
                item.setExpanded(True)
            return match

        for index in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(index))

    def show_source(self, item, previous=None):
        self.selected = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self.save_button.setEnabled(bool(self.selected))
        if not self.selected:
            self.preview.setPlainText('목차에서 소스 파일을 선택하세요.')
            self.status.clear()
            return
        filename, member = self.selected
        self.status.setText(member or filename)
        if member is None:
            self.preview.setPlainText('이 소스 묶음은 설치 EXE에 포함되어 있습니다.\n\n'
                '왼쪽 목차에서 파일을 선택하면 앱 안에서 읽을 수 있습니다.\n'
                '묶음 전체를 보관하려면 아래의 선택한 소스 저장을 누르세요.\n\n'
                'ADF 사용을 위해 압축 프로그램을 설치하거나 이 파일을 따로 받을 필요는 없습니다.')
            return
        try:
            with zipfile.ZipFile(self.sources_path/filename) as archive:
                info = archive.getinfo(member)
                if info.file_size > self.PREVIEW_LIMIT or member.lower().endswith(('.zip', '.gz', '.bz2', '.xz', '.png', '.ico')):
                    self.preview.setPlainText('큰 파일 또는 원본 압축 자료입니다.\n선택한 소스 저장으로 원본 그대로 보관할 수 있습니다.')
                    return
                with archive.open(member) as stream:
                    data = stream.read(self.PREVIEW_LIMIT+1)
                if b'\x00' in data or len(data) > self.PREVIEW_LIMIT:
                    self.preview.setPlainText('텍스트로 표시할 수 없는 파일입니다. 선택한 소스 저장을 사용하세요.')
                else:
                    self.preview.setPlainText(data.decode('utf-8-sig', errors='replace'))
        except (OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
            self.preview.setPlainText('소스를 읽지 못했습니다. '+str(error))

    def save_selected(self):
        if not self.selected:
            return
        filename, member = self.selected
        suggested = PurePosixPath(member).name if member else filename
        destination, _ = QFileDialog.getSaveFileName(self, '소스를 저장할 위치', suggested, '모든 파일 (*)')
        if not destination:
            return
        archive = stream = None
        target = QSaveFile(destination)
        progress = QProgressDialog('설치된 소스를 복사하고 있습니다.', '취소', 0, 100, self)
        progress.setWindowTitle('소스 저장')
        progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        timer = QTimer(progress)
        state = dict(done=False, copied=0, error=None)
        try:
            if Path(destination).resolve() == (self.sources_path/filename).resolve():
                raise ValueError('설치된 원본과 다른 저장 위치를 선택해 주세요.')
            if member:
                archive = zipfile.ZipFile(self.sources_path/filename)
                total = archive.getinfo(member).file_size
                stream = archive.open(member)
            else:
                total = (self.sources_path/filename).stat().st_size
                stream = (self.sources_path/filename).open('rb')
            if not target.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(target.errorString())

            def step():
                try:
                    if progress.wasCanceled():
                        timer.stop()
                        progress.done(QDialog.DialogCode.Rejected)
                        return
                    data = stream.read(1024*1024)
                    if data:
                        if target.write(data) != len(data):
                            raise OSError(target.errorString())
                        state['copied'] += len(data)
                        progress.setValue(int(state['copied']*100/max(total, 1)))
                    else:
                        if not target.commit():
                            raise OSError(target.errorString())
                        state['done'] = True
                        timer.stop()
                        progress.done(QDialog.DialogCode.Accepted)
                except Exception as error:
                    state['error'] = error
                    timer.stop()
                    progress.done(QDialog.DialogCode.Rejected)

            timer.timeout.connect(step)
            timer.start(0)
            progress.exec()
            if state['error']:
                raise state['error']
            self.status.setText('소스를 저장했습니다: '+destination if state['done'] else '저장을 취소했습니다.')
        except Exception as error:
            self.status.setText('저장하지 못했습니다. '+str(error))
        finally:
            timer.stop()
            if not state['done']:
                target.cancelWriting()
                if target.isOpen():
                    target.commit()  # A canceled QSaveFile discards and closes its temporary file.
            if stream:
                stream.close()
            if archive:
                archive.close()
            progress.deleteLater()
