"""Real subprocess export, menu integration and cancellation without APIs."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
import pytest
from PySide6.QtCore import QProcess, QTimer
from PySide6.QtWidgets import QApplication, QLineEdit
from hwpx import HwpxDocument

from adf.app import MainWindow
from adf.hwpx_widgets import HwpxOptionsDialog, run_hwpx_job


@pytest.fixture
def window(tmp_path):
    application = QApplication.instance() or QApplication([])
    application.setQuitOnLastWindowClosed(False)
    source = tmp_path / 'original.pdf'
    with pymupdf.open() as pdf:
        for text in ('Original page one', 'Selected page two'):
            page = pdf.new_page()
            page.insert_text((50, 60), text)
        pdf.save(source)
    window = MainWindow(smoke=True)
    assert window.open_path(source)
    application.processEvents()
    yield window
    for timer in window.findChildren(QTimer):
        timer.stop()
    with patch.object(window, 'maybe_save', return_value=True):
        window.close()
    window.deleteLater()
    application.processEvents()


def test_dialog_exposes_no_api_key_and_page_selection_works(window, tmp_path):
    assert window.actions['hwpx'].isEnabled()
    dialog = HwpxOptionsDialog(window.document, 1, [1], window)
    assert len(dialog.findChildren(QLineEdit)) == 2  # range + result path
    dialog.method.setCurrentIndex(dialog.method.findData('local'))
    dialog.pages.setCurrentIndex(dialog.pages.findData('selected'))
    dialog.output.setText(str(tmp_path / 'selection.hwpx'))
    dialog.accept()
    assert dialog.options['pages'] == [1]
    assert dialog.options['provider'] == 'local'
    dialog.deleteLater()


def test_real_worker_exports_selected_page_with_unsaved_text(window, tmp_path):
    original = Path(window.document.path).read_bytes()
    window.document.doc[1].insert_text((50, 90), 'Unsaved edit 42')
    output = tmp_path / 'export.hwpx'
    options = dict(provider='local', model='gemini-3.8-flash', pages=[1], output=str(output), save_report=True)
    result = run_hwpx_job(window, dict(operation='convert', options=options))
    assert result['pages'] == 1
    assert result['provider'] == 'local'
    with HwpxDocument.open(output) as doc:
        assert 'Selected page two' in doc.text.plain()
        assert 'Unsaved edit 42' in doc.text.plain()
        assert 'Original page one' not in doc.text.plain()
    assert Path(window.document.path).read_bytes() == original
    assert output.with_suffix('.conversion.json').is_file()
    assert window.worker is None


def test_cancel_during_preparation_publishes_nothing(window, tmp_path):
    output = tmp_path / 'canceled.hwpx'
    options = dict(provider='local', model='gemini-3.8-flash', pages=[0, 1], output=str(output), save_report=True)
    def cancel():
        dialog = getattr(window, 'hwpx_progress', None)
        if dialog is not None:
            dialog.reject()
    QTimer.singleShot(0, cancel)
    result = run_hwpx_job(window, dict(operation='convert', options=options))
    assert result is None
    assert not output.exists()
    assert not output.with_suffix('.conversion.json').exists()
    assert window.worker is None


def test_cancel_running_process_publishes_nothing(window, tmp_path):
    output = tmp_path / 'canceled-worker.hwpx'
    options = dict(provider='local', model='gemini-3.8-flash', pages=[0, 1], output=str(output), save_report=True)
    canceled_running = []
    timer = QTimer(window)
    timer.setInterval(5)
    def cancel_when_running():
        if window.worker is not None and window.worker.state() == QProcess.ProcessState.Running:
            canceled_running.append(True)
            timer.stop()
            window.hwpx_progress.reject()
    timer.timeout.connect(cancel_when_running)
    timer.start()
    try:
        result = run_hwpx_job(window, dict(operation='convert', options=options))
    finally:
        timer.stop()
    assert canceled_running
    assert result is None
    assert not output.exists()
    assert window.worker is None
