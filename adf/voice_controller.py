"""The click → record → apply → hover workflow, including worker lifetime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

import pymupdf
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QProcess, QProcessEnvironment, QTimer, Qt
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QApplication, QCheckBox, QLabel, QLineEdit, QWidget)

from .voice_audio import VoicePlayer, VoiceRecorder
from .voice_notes import find_note, page_notes
from .voice_widgets import VoiceCard, VoiceMarker


class VoiceController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.view = window.view
        self.viewport = self.view.viewport()
        self.document = window.document
        from .voice_credentials import load_key
        self.api_key = load_key(window.settings)
        self.enabled = False
        self.visibility = window.settings.value('voice/visibility', 'half')
        if self.visibility not in ('hidden', 'half', 'full'):
            self.visibility = 'half'
        self.markers = []
        self.draft = None
        self.draft_marker = None
        self.active_id = None
        self.anchor = None
        self.queue = []
        self.process = None
        self.job_id = None
        self.job_temp = None
        self.progress = ''
        self.output = bytearray()
        self.recorder = VoiceRecorder(self)
        self.player = VoicePlayer(self)
        self.recorder.levelChanged.connect(self.meter)
        self.recorder.failed.connect(self.recording_failed)
        self.recorder.limitReached.connect(self.apply)
        self.player.failed.connect(self.report)
        self.card = VoiceCard(self.view.viewport())
        self.card.applyRequested.connect(self.apply)
        self.card.cancelRequested.connect(self.cancel)
        self.card.playRequested.connect(self.play)
        self.card.deleteRequested.connect(self.delete)
        self.card.retryRequested.connect(self.retry)
        self.player.playingChanged.connect(self.card.set_playing)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.setInterval(220)
        self.hide_timer.timeout.connect(self.hide_card)
        self.card.entered.connect(self.hide_timer.stop)
        self.card.left.connect(self.schedule_hide)
        self.view.viewport().installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        self.view.verticalScrollBar().valueChanged.connect(self.position_card)
        self.view.horizontalScrollBar().valueChanged.connect(self.position_card)
        self.view.zoomChanged.connect(self.position_card)
        self.view.pageChanged.connect(self.position_card)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.setInterval(10 * 60 * 1000)
        self.timeout.timeout.connect(self.worker_timeout)

    def report(self, message):
        self.window.notice.showMessage(message, 8000)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.view.voice_mode = enabled
        self.view.update_content_cursor()

    def set_visibility(self, mode):
        self.visibility = mode
        self.window.settings.setValue('voice/visibility', mode)
        for marker in self.markers:
            marker.visibility = mode
            marker.update()

    def before_reload(self, document):
        if document is not self.document:
            self.shutdown()
            self.document = document
        self.hide_timer.stop()
        self.card.hide()
        self.markers = []
        self.anchor = None
        self.draft_marker = None

    def after_reload(self):
        QApplication.instance().installEventFilter(self)
        self.refresh_markers()
        self.restore_draft()

    def refresh_markers(self):
        selected = self.active_id if self.card.isVisible() and not self.draft else None
        self.anchor = self.draft_marker
        for marker in self.markers:
            self.view.scene().removeItem(marker)
            marker.setParentItem(None)
            marker.deleteLater()
        self.markers = []
        if not self.document.doc:
            self.card.hide()
            return
        for item in self.view.pages:
            page = self.document.doc[item.index]
            for note in page_notes(page):
                note['key'] = (page.xref, note['id'])
                marker = self.make_marker(note, item)
                point = pymupdf.Point(note['point']) * page.rotation_matrix
                marker.setPos(point.x, point.y)
                self.markers.append(marker)
                if note['key'] == selected:
                    self.show_note(marker)

    def make_marker(self, note, item, draft=False):
        marker = VoiceMarker(note, item, self.visibility, draft)
        marker.hovered.connect(self.show_note)
        marker.left.connect(self.schedule_hide)
        marker.clicked.connect(self.show_note)
        return marker

    def restore_draft(self):
        if not self.draft or not 0 <= self.draft['page'] < len(self.view.pages):
            return
        item = self.view.pages[self.draft['page']]
        self.draft_marker = self.make_marker(dict(id='draft'), item, True)
        point = pymupdf.Point(self.draft['point']) * self.document.doc[item.index].rotation_matrix
        self.draft_marker.setPos(point.x, point.y)
        self.anchor = self.draft_marker
        self.card.recording()
        self.card.show()
        self.position_card()

    def eventFilter(self, watched, event):
        if watched is not self.viewport:
            if (self.draft and event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton and isinstance(watched, QWidget)
                    and watched.window() is self.window and watched is not self.card
                    and not self.card.isAncestorOf(watched)):
                return not self.apply()
            return False
        if event.type() == QEvent.Type.Resize:
            self.position_card()
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            if event.button() == Qt.MouseButton.RightButton and self.enabled:
                self.window.escape()
                return True
            if event.button() != Qt.MouseButton.LeftButton or self.view.pan.active:
                return False
            if self.markers or self.enabled or self.draft:
                pos = self.view.mapToScene(event.position().toPoint())
                # Memos always own their own click; never place a second memo on one.
                for marker in self.markers + ([self.draft_marker] if self.draft_marker else []):
                    local = marker.deviceTransform(self.view.viewportTransform()).inverted()[0].map(event.position())
                    if marker.isVisible() and marker.boundingRect().contains(local):
                        if self.draft and marker is not self.draft_marker:
                            identifier = marker.note['key']
                            if not self.apply():
                                return True
                            marker = next((saved for saved in self.markers if saved.note['key'] == identifier), None)
                            if marker is None:
                                return True
                        self.show_note(marker)
                        return True
                if not self.enabled and not self.draft:
                    return False
                item = self.view.page_at(pos)
                if self.draft and not self.apply():
                    return True
                if self.enabled and item is not None:
                    self.start(item.index, self.view.page_point(item, pos))
                return True
        return super().eventFilter(watched, event)

    def start(self, page_index, point):
        if self.window.worker or not self.document.editable:
            return
        self.player.stop()
        try:
            self.recorder.start()
        except Exception as error:
            self.report(str(error))
            return
        point = pymupdf.Point(point)
        context = ''
        if self.window.copy_allowed():
            page = self.document.doc[page_index]
            clip = pymupdf.Rect(point.x-200, point.y-100, point.x+200, point.y+200)
            try:
                context = page.get_text(clip=clip)[:2400]
            except Exception:
                # A damaged text layer must not strand an active microphone.
                context = ''
        self.draft = dict(page=page_index, point=(point.x, point.y), context=context)
        self.active_id = None
        self.restore_draft()

    def meter(self, level, seconds):
        if not self.draft:
            return
        self.card.time_label.setText(f'녹음 중  {int(seconds)//60:02d}:{int(seconds)%60:02d}')
        self.card.level.set_level(level)
        if self.draft_marker:
            self.draft_marker.level = level
            self.draft_marker.update()

    def recording_failed(self, message):
        self.cancel()
        self.report(message)

    def clear_draft_marker(self):
        if self.draft_marker:
            self.view.scene().removeItem(self.draft_marker)
            self.draft_marker.setParentItem(None)
            self.draft_marker.deleteLater()
            self.draft_marker = None
        self.anchor = None

    def apply(self):
        if self.draft is None:
            return True
        draft = self.draft
        if 'audio' not in draft:
            draft['audio'], draft['duration'] = self.recorder.finish()
        try:
            identifier = self.document.add_voice_note(draft['page'], draft['point'], draft['audio'],
                                                      draft['duration'], draft['context'])
        except Exception as error:
            if draft.get('duration', 0) < .2:
                self.cancel()
                self.report(str(error))
                return True
            self.card.status.setText('적용하지 못했습니다. 다시 적용하거나 취소해 주세요. ' + str(error))
            self.report(str(error))
            return False
        self.draft = None
        self.clear_draft_marker()
        self.card.hide()
        self.refresh_markers()
        self.window.refresh_actions()
        self.enqueue((self.document.doc[draft['page']].xref, identifier))
        return True

    def cancel(self):
        self.recorder.cancel()
        self.draft = None
        self.clear_draft_marker()
        self.card.hide()
        self.active_id = None

    def show_note(self, marker):
        self.hide_timer.stop()
        if self.draft:
            self.anchor = self.draft_marker
            self.card.show()
            self.position_card()
            return
        if self.active_id != marker.note['key']:
            self.player.stop()
        self.active_id = marker.note['key']
        self.anchor = marker
        marker.hovering = True
        marker.update()
        self.card.present(marker.note, self.document.editable,
                          self.progress if self.job_id == self.active_id else
                          ('전사 대기 중…' if self.active_id in self.queue else ''))
        self.card.show()
        self.card.raise_()
        self.position_card()

    def schedule_hide(self):
        if not self.draft and not self.player.playing:
            self.hide_timer.start()

    def hide_card(self):
        if self.draft or self.player.playing or self.card.underMouse():
            return
        if self.anchor:
            local = self.view.viewport().mapFromGlobal(QCursor.pos())
            rect = self.anchor.deviceTransform(self.view.viewportTransform()).mapRect(self.anchor.boundingRect())
            if rect.contains(local):
                return
            self.anchor.hovering = False
            self.anchor.update()
        self.card.hide()
        self.active_id = None
        self.anchor = None

    def position_card(self, *_):
        if not self.anchor or not self.card.isVisible():
            return
        if not self.anchor.isVisible():
            self.card.hide()
            self.player.stop()
            return
        rect = self.anchor.deviceTransform(self.view.viewportTransform()).mapRect(self.anchor.boundingRect())
        viewport = self.view.viewport().rect()
        if not viewport.intersects(rect.toAlignedRect()):
            self.card.hide()
            self.player.stop()
            return
        x, y = round(rect.right()+5), round(rect.top())
        if x + self.card.width() > viewport.width()-8:
            x = round(rect.left()-self.card.width()-5)
        self.card.move(max(8, min(x, viewport.width()-self.card.width()-8)),
                       max(8, min(y, viewport.height()-self.card.height()-8)))
        self.card.raise_()

    def play(self):
        if self.player.playing:
            self.player.stop()
            self.schedule_hide()
            return
        found = find_note(self.document.doc, self.active_id) if self.active_id else None
        if found:
            try:
                self.player.play(found[1].get_file())
            except Exception as error:
                self.report(str(error))

    def delete(self):
        if not self.active_id or not self.document.editable:
            return
        identifier = self.active_id
        self.player.stop()
        try:
            self.document.delete_voice_note(identifier)
        except Exception as error:
            self.report(str(error))
            return
        self.queue = [job for job in self.queue if job != identifier]
        if self.job_id == identifier:
            self.stop_worker()
        self.card.hide()
        self.active_id = None
        self.refresh_markers()
        self.window.refresh_actions()
        self.run_next()

    def retry(self):
        if self.active_id and self.document.editable:
            self.enqueue(self.active_id)

    def options(self):
        return dict(api_base=self.window.settings.value('voice/api_base', 'https://api.openai.com/v1'),
                    transcription_model=self.window.settings.value('voice/transcription_model', 'gpt-4o-mini-transcribe'),
                    language=self.window.settings.value('voice/language', 'ko'),
                    cleanup_model=self.window.settings.value('voice/cleanup_model', 'gpt-4.1-mini'))

    def enqueue(self, identifier):
        if identifier == self.job_id or identifier in self.queue:
            return
        self.queue.append(identifier)
        self.run_next()

    def run_next(self):
        if self.process or not self.document.doc or not self.document.editable:
            return
        while self.queue:
            identifier = self.queue.pop(0)
            found = find_note(self.document.doc, identifier)
            if found:
                break
        else:
            return
        try:
            self.job_temp = tempfile.TemporaryDirectory(prefix='xdf-voice-')
            folder = Path(self.job_temp.name)
            from .app import _restrict_worker_directory, resource_path
            _restrict_worker_directory(folder)
            note = found[2]
            request = dict(options=self.options(), context=note.get('context', ''), raw_text=note.get('raw_text', ''))
            if note.get('raw_text'):
                request['task'] = 'cleanup'
            else:
                (folder / 'audio.wav').write_bytes(found[1].get_file())
            path = folder / 'request.json'
            path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            self.job_id = identifier
            self.progress = '전사 준비 중…'
            self.output.clear()
            self.process = QProcess(self)
            self.process.setProgram(sys.executable)
            environment = QProcessEnvironment.systemEnvironment()
            key = self.api_key or os.environ.get('XDF_VOICE_API_KEY', '')
            if not key and self.options()['api_base'] == 'https://api.openai.com/v1':
                key = os.environ.get('OPENAI_API_KEY', '')
            environment.insert('XDF_VOICE_API_KEY', key)
            self.process.setProcessEnvironment(environment)
            self.process.setArguments(([] if getattr(sys, 'frozen', False) else [str(resource_path('main.py'))]) +
                                      ['--voice-worker', str(path)])
            self.process.readyReadStandardOutput.connect(self.read_progress)
            self.process.finished.connect(self.worker_finished)
            self.process.errorOccurred.connect(self.worker_error)
            self.process.start()
            self.timeout.start()
            if self.active_id == identifier and self.anchor:
                self.card.retry_button.hide()
                self.card.status.setText(self.progress)
        except Exception as error:
            self.stop_worker()
            self.report(str(error))

    def read_progress(self):
        if not self.process:
            return
        self.output.extend(bytes(self.process.readAllStandardOutput()))
        while b'\n' in self.output:
            line, _, rest = self.output.partition(b'\n')
            self.output = bytearray(rest)
            try:
                self.progress = json.loads(line).get('progress', self.progress)
            except (ValueError, AttributeError):
                continue
            if self.active_id == self.job_id and self.card.isVisible():
                self.card.status.setText(self.progress)
        if len(self.output) > 65536:
            self.output.clear()

    def worker_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.worker_finished(-1, QProcess.ExitStatus.CrashExit)

    def worker_timeout(self):
        if self.process:
            self.process.kill()

    def worker_finished(self, code, status):
        if not self.process:
            return
        identifier = self.job_id
        try:
            path = Path(self.job_temp.name) / 'result.json'
            if code == 0 and path.is_file():
                result = json.loads(path.read_text(encoding='utf-8'))
            else:
                result = dict(text='', raw_text='', error='전사를 완료하지 못했습니다. 모델 연결을 확인하고 다시 시도해 주세요.')
            self.document.complete_voice_note(identifier, **result)
            self.refresh_markers()
            self.window.refresh_actions()
        except Exception as error:
            self.report(str(error))
        finally:
            self.stop_worker()
            self.run_next()

    def stop_worker(self):
        self.timeout.stop()
        process, self.process = self.process, None
        if process:
            process.blockSignals(True)
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
                process.waitForFinished(3000)
            process.deleteLater()
        if self.job_temp:
            self.job_temp.cleanup()
            self.job_temp = None
        self.job_id = None
        self.progress = ''

    def shutdown(self):
        QApplication.instance().removeEventFilter(self)
        self.cancel()
        self.player.stop()
        self.queue.clear()
        self.stop_worker()
        self.card.hide()

    def settings_dialog(self):
        dialog = QDialog(self.window)
        dialog.setWindowTitle('음성 메모 설정')
        dialog.setMinimumWidth(500)
        layout = QFormLayout(dialog)
        info = QLabel('OpenAI 또는 같은 API 형식을 지원하는 서비스에 연결합니다.\n'
                      '적용한 녹음과 마킹 주변의 문장을 보내 전사하고 메모 문장을 정리합니다.')
        info.setWordWrap(True)
        layout.addRow(info)
        options = self.options()
        base = QLineEdit(options['api_base'])
        base.setAccessibleName('음성 메모 API 서비스 주소')
        layout.addRow('API 주소', base)
        key = QLineEdit(self.api_key)
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText('API 키 입력 · 비어 있으면 환경 변수 사용')
        key.setAccessibleName('음성 메모 API 키')
        layout.addRow('API 키', key)
        remember = QCheckBox('이 Windows 계정에 API 키를 암호화해서 저장')
        remember.setChecked(sys.platform == 'win32')
        remember.setVisible(sys.platform == 'win32')
        layout.addRow(remember)
        model = QLineEdit(options['transcription_model'])
        layout.addRow('STT 모델', model)
        language = QComboBox()
        for name, key in [('한국어', 'ko'), ('자동 감지', ''), ('영어', 'en')]:
            language.addItem(name, key)
        language.setCurrentIndex(max(0, language.findData(options['language'])))
        layout.addRow('녹음 언어', language)
        cleanup = QLineEdit(options['cleanup_model'])
        cleanup.setPlaceholderText('gpt-4.1-mini')
        layout.addRow('문장 정리 모델', cleanup)
        hint = QLabel('연결에 실패해도 녹음은 PDF에 보존됩니다. 메모에서 다시 시도할 수 있습니다.\n'
                      '키를 저장하지 않으면 현재 실행 중에만 보관합니다.\n'
                      '환경 변수: XDF_VOICE_API_KEY · OpenAI 기본 주소는 OPENAI_API_KEY도 사용')
        hint.setWordWrap(True)
        layout.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            from .voice_transcription import api_base
            from .voice_credentials import save_key
            try:
                address = api_base(base.text())
                save_key(self.window.settings, key.text().strip(), remember.isChecked())
            except ValueError as error:
                self.report(str(error))
                return
            self.api_key = key.text().strip()
            self.window.settings.setValue('voice/api_base', address)
            self.window.settings.setValue('voice/transcription_model', model.text().strip() or 'gpt-4o-mini-transcribe')
            self.window.settings.setValue('voice/language', language.currentData())
            self.window.settings.setValue('voice/cleanup_model', cleanup.text().strip() or 'gpt-4.1-mini')
