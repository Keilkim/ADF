"""Background update checks, downloads and one-click installation."""
from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf import updates
from adf.updates import (Package, UpdateService, choose_package, installer_arguments, is_newer, parse_sums,
                         parse_version, staged_version, tag_version)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def wait_until(condition, timeout=10):
    deadline = time.monotonic()+timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError('Timed out waiting for the update service')
        QTest.qWait(20)


class Releases(ThreadingHTTPServer):
    """Answers like GitHub: releases/latest and downloads redirect elsewhere."""
    daemon_threads = True

    def __init__(self, latest, assets):
        self.latest, self.assets = latest, assets
        self.ranges = True
        self.requests = []
        super().__init__(('127.0.0.1', 0), ReleaseHandler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f'http://127.0.0.1:{self.server_port}'

    def downloaded(self, name):
        return [header for method, path, header in self.requests if method == 'GET' and path == '/assets/'+name]


class ReleaseHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        self.answer(body=False)

    def do_GET(self):
        self.answer(body=True)

    def reply(self, status, headers=(), data=b'', body=True):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)

    def answer(self, body):
        server = self.server
        server.requests.append((self.command, self.path, self.headers.get('Range')))
        prefix = f'/releases/download/v{server.latest}/'
        # A redirect's own body must never end up in a download.
        moved = b'<html><body>You are being redirected.</body></html>'
        if self.path == '/releases/latest':
            return self.reply(302, [('Location', f'{server.base}/releases/tag/v{server.latest}')], moved, body)
        if self.path.startswith(prefix) and self.path[len(prefix):] in server.assets:
            return self.reply(302, [('Location', f'{server.base}/assets/{self.path[len(prefix):]}')], moved, body)
        name = self.path.removeprefix('/assets/')
        if self.path.startswith('/assets/') and name in server.assets:
            data = server.assets[name]
            requested = self.headers.get('Range', '')
            if server.ranges and requested.startswith('bytes='):
                start = int(requested[6:].rstrip('-'))
                return self.reply(206, [('Content-Range', f'bytes {start}-{len(data)-1}/{len(data)}')], data[start:], body)
            return self.reply(200, data=data, body=body)
        self.reply(404, body=body)


class VersionTests(unittest.TestCase):
    def test_versions_compare_numerically(self):
        self.assertEqual(parse_version('0.3.27'), (0, 3, 27))
        self.assertIsNone(parse_version('v0.3.27'))
        self.assertIsNone(parse_version('0.3'))
        self.assertTrue(is_newer('0.3.100', '0.3.27'))
        self.assertFalse(is_newer('0.3.27', '0.3.27'))
        self.assertFalse(is_newer('nonsense', '0.3.27'))

    def test_only_this_repository_release_tags_are_versions(self):
        base = 'https://github.com/Keilkim/ADF/releases'
        self.assertEqual(tag_version(base+'/tag/v0.3.28'), '0.3.28')
        for location in (base+'/tag/v0.3.28-beta', base+'/tag/0.3.28', 'https://github.com/other/ADF/releases/tag/v9.0.0',
                         'http://github.com/Keilkim/ADF/releases/tag/v9.0.0', 'https://github.com/login', ''):
            self.assertIsNone(tag_version(location), location)

    def test_checksum_lists_ignore_malformed_lines(self):
        text = '\ufeff' + f'{"a"*64}  ADF-Setup-0.3.28.exe\r\n' + f'{"b"*64}  ../escape.exe\n' + 'nonsense\n'
        self.assertEqual(parse_sums(text), {'ADF-Setup-0.3.28.exe': 'a'*64})

    def test_patch_for_this_version_is_preferred_over_the_full_installer(self):
        sums = {'ADF-Update-0.3.27-to-0.3.28.exe': 'a'*64, 'ADF-Update-0.3.26-to-0.3.28.exe': 'b'*64,
                'ADF-Setup-0.3.28.exe': 'c'*64, 'ADF-0.3.28-macOS.dmg': 'd'*64}
        self.assertEqual(choose_package('0.3.27', '0.3.28', sums, 'win32'),
                         Package('0.3.28', 'ADF-Update-0.3.27-to-0.3.28.exe', 'a'*64, 'patch'))
        self.assertEqual(choose_package('0.3.25', '0.3.28', sums, 'win32').kind, 'setup')
        self.assertEqual(choose_package('0.3.27', '0.3.28', sums, 'win32', patch=False).kind, 'setup')
        self.assertEqual(choose_package('0.3.27', '0.3.28', sums, 'darwin').kind, 'dmg')
        self.assertIsNone(choose_package('0.3.27', '0.3.28', {}, 'win32'))

    def test_installer_restarts_adf_with_the_open_document(self):
        package = Package('0.3.28', 'ADF-Update-0.3.27-to-0.3.28.exe', 'a'*64, 'patch')
        arguments = installer_arguments(package, r'C:\Apps\ADF\ADF.exe', r'C:\문서\계약 초안.pdf', r'C:\log.txt')
        self.assertEqual(arguments, ['/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/ADFACKNOTICE=0.3.28',
                                     r'/ADFRELAUNCH=C:\Apps\ADF\ADF.exe', r'/ADFOPEN=C:\문서\계약 초안.pdf', r'/LOG=C:\log.txt'])
        self.assertNotIn('/ADFOPEN', ' '.join(installer_arguments(package, 'ADF.exe')))

    def test_staged_files_are_recognised_by_their_target_version(self):
        self.assertEqual(staged_version('ADF-Update-0.3.27-to-0.3.28.exe.part'), '0.3.28')
        self.assertEqual(staged_version('ADF-Setup-0.3.28.exe'), '0.3.28')
        self.assertEqual(staged_version('ADF-0.3.28-macOS.dmg'), '0.3.28')
        self.assertEqual(staged_version('install-0.3.28.log'), '0.3.28')
        self.assertIsNone(staged_version('update.lock'))


class UpdateServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.settings = QSettings(str(self.root/'settings.ini'), QSettings.Format.IniFormat)
        self.folder = self.root/'updates'
        self.patch = os.urandom(300_000)
        self.setup = os.urandom(500_000)
        self.server = Releases('0.3.28', {})
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.publish(**{'ADF-Update-0.3.27-to-0.3.28.exe': self.patch, 'ADF-Setup-0.3.28.exe': self.setup})

    def publish(self, sums_name='SHA256SUMS-0.3.28.txt', **assets):
        self.server.assets = dict(assets)
        self.server.assets[sums_name] = ''.join(f'{digest(data)}  {name}\r\n' for name, data in assets.items()).encode()

    def service(self, current='0.3.27', platform='win32', releases=None):
        service = UpdateService(self.settings, self.folder, current=current, platform=platform,
                                releases=releases or self.server.base+'/releases')
        self.addCleanup(service.shutdown)
        return service

    def checked(self, service):
        service.start()
        service.check()
        wait_until(lambda: not service.busy)
        return service

    def test_patch_is_downloaded_verified_and_announced_once(self):
        service = self.service()
        announced = []
        service.notify.connect(lambda: announced.append(service.state))
        self.checked(service)
        self.assertEqual(service.state, 'ready')
        self.assertEqual(service.package.kind, 'patch')
        self.assertEqual(service.staged().read_bytes(), self.patch)
        self.assertEqual(announced, ['ready'])
        self.assertEqual(json.loads(self.settings.value('updates/ready'))['name'], 'ADF-Update-0.3.27-to-0.3.28.exe')
        self.assertFalse(self.server.downloaded('ADF-Setup-0.3.28.exe'))
        self.assertFalse(list(self.folder.glob('*.part')))

    def test_interrupted_download_continues_where_it_stopped(self):
        self.folder.mkdir()
        (self.folder/'ADF-Update-0.3.27-to-0.3.28.exe.part').write_bytes(self.patch[:120_000])
        service = self.checked(self.service())
        self.assertEqual(service.state, 'ready')
        self.assertEqual(service.staged().read_bytes(), self.patch)
        self.assertEqual(self.server.downloaded('ADF-Update-0.3.27-to-0.3.28.exe'), ['bytes=120000-'])

    def test_server_without_ranges_sends_the_whole_file_again(self):
        self.folder.mkdir()
        (self.folder/'ADF-Update-0.3.27-to-0.3.28.exe.part').write_bytes(b'x'*50_000)
        self.server.ranges = False
        service = self.checked(self.service())
        self.assertEqual(service.state, 'ready')
        self.assertEqual(service.staged().read_bytes(), self.patch)

    def test_download_with_the_wrong_checksum_is_discarded(self):
        self.publish(**{'ADF-Update-0.3.27-to-0.3.28.exe': self.patch})
        self.server.assets['ADF-Update-0.3.27-to-0.3.28.exe'] = os.urandom(1000)
        service = self.checked(self.service())
        self.assertEqual(service.state, 'idle')
        self.assertTrue(service.pending)
        self.assertEqual(list(self.folder.glob('ADF-*')), [])
        self.assertIsNone(self.settings.value('updates/ready'))

    def test_offline_check_waits_for_the_connection(self):
        with socket.socket() as unused:
            unused.bind(('127.0.0.1', 0))
            port = unused.getsockname()[1]
        service = self.checked(self.service(releases=f'http://127.0.0.1:{port}/releases'))
        self.assertEqual(service.state, 'idle')
        self.assertTrue(service.pending)
        self.assertTrue(service.timer.isActive())
        self.assertEqual(service.timer.interval(), updates.RETRY)
        # A reconnection checks again soon instead of waiting for the retry.
        from PySide6.QtNetwork import QNetworkInformation
        service._reachability(QNetworkInformation.Reachability.Online)
        self.assertEqual(service.timer.interval(), updates.RECONNECT_DELAY)

    def test_current_version_only_schedules_the_next_check(self):
        service = self.checked(self.service(current='0.3.28'))
        self.assertEqual(service.state, 'idle')
        self.assertFalse(service.pending)
        self.assertEqual([path for _, path, _ in self.server.requests], ['/releases/latest'])
        self.assertEqual(service.timer.interval(), updates.RECHECK)

    def test_failed_patch_falls_back_to_the_full_installer(self):
        self.folder.mkdir()
        (self.folder/'ADF-Update-0.3.27-to-0.3.28.exe').write_bytes(self.patch)
        self.settings.setValue('updates/ready', json.dumps({'version': '0.3.28', 'name': 'ADF-Update-0.3.27-to-0.3.28.exe',
                                                            'sha256': digest(self.patch), 'kind': 'patch'}))
        self.settings.setValue('updates/attempt', json.dumps({'version': '0.3.28', 'kind': 'patch'}))
        service = self.checked(self.service())
        self.assertEqual(self.settings.value('updates/no_patch'), '0.3.28')
        self.assertEqual(service.state, 'ready')
        self.assertEqual(service.package.kind, 'setup')
        self.assertFalse((self.folder/'ADF-Update-0.3.27-to-0.3.28.exe').exists())

    def test_failed_full_installer_is_left_to_the_user(self):
        self.settings.setValue('updates/attempt', json.dumps({'version': '0.3.28', 'kind': 'setup'}))
        service = self.checked(self.service())
        self.assertEqual(service.state, 'manual')
        self.assertFalse(self.server.downloaded('ADF-Setup-0.3.28.exe'))

    def test_successful_attempt_is_forgotten_and_old_files_removed(self):
        self.folder.mkdir()
        for name in ('ADF-Setup-0.3.27.exe', 'ADF-Update-0.3.26-to-0.3.27.exe.part', 'install-0.3.27.log'):
            (self.folder/name).write_bytes(b'old')
        self.settings.setValue('updates/attempt', json.dumps({'version': '0.3.28', 'kind': 'patch'}))
        service = self.service(current='0.3.28')
        service.start()
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ['update.lock'])
        self.assertIsNone(self.settings.value('updates/attempt'))
        self.assertIsNone(self.settings.value('updates/no_patch'))

    def test_full_installer_waits_for_a_click_on_metered_networks(self):
        self.publish(**{'ADF-Setup-0.3.28.exe': self.setup})
        with patch.object(UpdateService, '_metered', return_value=True):
            service = self.checked(self.service())
            self.assertEqual(service.state, 'available')
            self.assertFalse(self.server.downloaded('ADF-Setup-0.3.28.exe'))
            service.download()
            wait_until(lambda: not service.busy)
        self.assertEqual(service.state, 'ready')
        self.assertEqual(service.staged().read_bytes(), self.setup)

    def test_mac_downloads_the_disk_image_when_asked(self):
        image = os.urandom(200_000)
        self.publish('SHA256SUMS-0.3.28-macOS.txt', **{'ADF-0.3.28-macOS.dmg': image})
        service = self.checked(self.service(platform='darwin'))
        self.assertEqual(service.state, 'available')
        service.download()
        wait_until(lambda: not service.busy)
        self.assertEqual(service.state, 'ready')
        self.assertEqual(service.staged().read_bytes(), image)

    def test_only_one_window_checks_and_the_others_show_its_update(self):
        owner = self.checked(self.service())
        self.assertEqual(owner.state, 'ready')
        requests = len(self.server.requests)
        other = self.service()
        announced = []
        other.notify.connect(lambda: announced.append(True))
        other.start()
        other.check()
        self.assertIsNone(other.network)
        self.assertEqual(other.state, 'ready')
        self.assertEqual(announced, [])
        self.assertEqual(len(self.server.requests), requests)
        self.assertTrue(other.owner_timer.isActive())

    def test_disabled_updates_never_contact_github(self):
        self.settings.setValue('updates/auto', False)
        service = self.service()
        service.start()
        service.check()
        self.assertIsNone(service.network)
        self.assertEqual(self.server.requests, [])

    def test_changed_staged_file_is_downloaded_again(self):
        service = self.checked(self.service())
        service.staged().write_bytes(b'changed')
        self.assertFalse(service.verify_staged())
        self.assertEqual(service.state, 'idle')
        self.assertIsNone(service.package)
        self.assertEqual(list(self.folder.glob('ADF-*')), [])


class UpdateWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        from adf.app import MainWindow
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.window = MainWindow(smoke=True)
        self.addCleanup(self.window.close)
        self.service = UpdateService(self.window.settings, self.root/'updates', current='0.3.27',
                                     platform='win32', releases='http://127.0.0.1:9/releases', parent=self.window)
        self.addCleanup(self.window.settings.remove, 'updates')
        # Installing keeps the lock until the process exits; tests release it.
        self.addCleanup(self.service.shutdown)
        self.window.start_updates(self.service)
        self.service.timer.stop()
        self.package = Package('0.3.28', 'ADF-Update-0.3.27-to-0.3.28.exe', digest(b'installer'), 'patch')
        (self.root/'updates'/self.package.name).write_bytes(b'installer')

    def ready(self):
        self.service.package = self.package
        self.service._set('ready', announce=True)

    def test_status_bar_button_appears_only_for_an_update(self):
        self.assertTrue(self.window.update_button.isHidden())
        self.ready()
        self.assertFalse(self.window.update_button.isHidden())
        self.assertEqual(self.window.update_button.text(), '업데이트 설치 · 0.3.28')
        self.assertTrue(self.window.update_button.isEnabled())
        self.service.package = self.package
        self.service.percent = 42
        self.service._set('downloading')
        self.assertEqual(self.window.update_button.text(), '업데이트 받는 중 · 42%')
        self.assertFalse(self.window.update_button.isEnabled())

    def test_one_click_closes_adf_and_starts_the_installer(self):
        self.ready()
        document = self.root/'열린 문서.pdf'
        document.write_bytes(b'%PDF-1.4\n%%EOF\n')
        self.window.document.path = str(document)
        application = Mock()
        with patch('adf.updates.other_windows', return_value={}), \
             patch('adf.app.QApplication.instance', return_value=application), \
             patch('adf.app.QProcess.startDetached', return_value=(True, 1)) as start:
            self.window.update_button.click()
        program, arguments = start.call_args.args
        self.assertEqual(Path(program), self.root/'updates'/self.package.name)
        self.assertIn('/ADFACKNOTICE=0.3.28', arguments)
        self.assertIn(f'/ADFRELAUNCH={sys.executable}', arguments)
        self.assertIn(f'/ADFOPEN={document}', arguments)
        application.quit.assert_called_once()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(json.loads(self.window.settings.value('updates/attempt')), {'version': '0.3.28', 'kind': 'patch'})

    def test_other_windows_that_stay_open_stop_the_update(self):
        self.ready()
        with patch('adf.updates.other_windows', return_value={4242: [1]}), \
             patch('adf.updates.close_windows') as close, \
             patch('adf.updates.running', return_value={4242}), \
             patch('adf.app.QMessageBox.information') as message, \
             patch('adf.app.QProcess.startDetached') as start:
            self.window.update_button.click()
            close.assert_called_once_with({4242: [1]})
            self.assertFalse(self.window.update_button.isEnabled())
            self.window.update_deadline = 0
            wait_until(lambda: message.called)
        start.assert_not_called()
        self.assertFalse(self.window.installing_update)
        self.assertTrue(self.window.update_button.isEnabled())

    def test_help_menu_turns_automatic_checks_off(self):
        help_menu = [action.menu() for action in self.window.menuBar().actions() if action.text() == '도움말'][0]
        self.assertIn(self.window.actions['auto_update'], help_menu.actions())
        self.assertTrue(self.window.actions['auto_update'].isChecked())
        self.window.actions['auto_update'].trigger()
        self.assertFalse(self.window.settings.value('updates/auto', True, type=bool))
        self.assertFalse(self.service.enabled)


if __name__ == '__main__':
    unittest.main()
