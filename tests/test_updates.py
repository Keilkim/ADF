"""Background update checks, downloads and one-click installation."""
from __future__ import annotations

import errno
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
from unittest.mock import MagicMock, Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from adf import updates
from adf.updates import (Package, UpdateService, choose_package, installer_arguments, is_newer, parse_sums,
                         parse_version, smart_app_control_blocks_updates, staged_version, tag_version)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def without_smart_app_control(test):
    """This PC may enforce Smart App Control; each test decides whether it does."""
    patcher = patch('adf.updates.smart_app_control_blocks_updates', return_value=False)
    test.addCleanup(patcher.stop)
    return patcher.start()


class FullDisk:
    """A download file whose writes, or whose final flush, find no space left."""
    def __init__(self, file, write=True):
        self.file, self.write_fails = file, write

    def write(self, data):
        if self.write_fails:
            raise OSError(errno.ENOSPC, 'No space left on device')
        return self.file.write(data)

    def close(self):
        self.file.close()
        if not self.write_fails:
            raise OSError(errno.ENOSPC, 'No space left on device')

    def __getattr__(self, name):
        return getattr(self.file, name)


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

    def test_smart_app_control_blocks_updates_only_when_enforced(self):
        for state, blocked in ((0, False), (1, True), (2, False)):
            winreg = MagicMock(HKEY_LOCAL_MACHINE='HKLM')
            winreg.QueryValueEx.return_value = (state, 4)
            with patch.dict(sys.modules, {'winreg': winreg}):
                self.assertEqual(smart_app_control_blocks_updates('win32'), blocked, state)
            winreg.OpenKey.assert_called_once_with('HKLM', r'SYSTEM\CurrentControlSet\Control\CI\Policy')
            self.assertEqual(winreg.QueryValueEx.call_args.args[1], 'VerifiedAndReputablePolicyState')
        winreg = MagicMock()
        winreg.OpenKey.side_effect = FileNotFoundError
        with patch.dict(sys.modules, {'winreg': winreg}):
            self.assertFalse(smart_app_control_blocks_updates('win32'))
            self.assertFalse(smart_app_control_blocks_updates('darwin'))
        self.assertEqual(winreg.OpenKey.call_count, 1)


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
        self.smart_app_control = without_smart_app_control(self)

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
        service.requested = True
        service.download()
        wait_until(lambda: not service.busy)
        self.assertEqual(service.state, 'ready')
        self.assertEqual(service.staged().read_bytes(), image)
        self.assertFalse(service.requested)

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

    def verified(self, service):
        results = []
        service.verify_staged(service.package, results.append)
        self.assertEqual(results, [])  # The answer arrives later, after hashing off the GUI thread.
        wait_until(lambda: results)
        return results

    def test_changed_staged_file_is_downloaded_again(self):
        service = self.checked(self.service())
        service.staged().write_bytes(b'changed')
        self.assertEqual(self.verified(service), [False])
        self.assertEqual(service.state, 'idle')
        self.assertIsNone(service.package)
        self.assertEqual(list(self.folder.glob('ADF-*')), [])

    def test_staged_file_is_hashed_on_a_worker_thread(self):
        service = self.checked(self.service())
        threads, hash_file = [], updates.file_sha256
        with patch('adf.updates.file_sha256', side_effect=lambda path: threads.append(threading.current_thread()) or hash_file(path)):
            self.assertEqual(self.verified(service), [True])
        self.assertEqual(len(threads), 1)
        self.assertIsNot(threads[0], threading.main_thread())
        self.assertEqual(service.state, 'ready')
        self.assertTrue(service.staged().is_file())

    def test_smart_app_control_sends_users_to_the_download_page(self):
        self.smart_app_control.return_value = True
        service = self.service()
        announced = []
        service.notify.connect(lambda: announced.append((service.state, service.reason)))
        self.checked(service)
        self.assertEqual((service.state, service.reason, service.package.version), ('manual', 'blocked', '0.3.28'))
        self.assertEqual(announced, [('manual', 'blocked')])
        # Nothing is downloaded that Windows would refuse to run.
        self.assertEqual([path for _, path, _ in self.server.requests], ['/releases/latest'])
        self.assertEqual(service.timer.interval(), updates.RECHECK)
        service.check()
        wait_until(lambda: not service.busy)
        self.assertEqual(len(announced), 1)

    def test_staged_update_is_not_offered_under_smart_app_control(self):
        self.folder.mkdir()
        staged = self.folder/'ADF-Update-0.3.27-to-0.3.28.exe'
        staged.write_bytes(self.patch)
        self.settings.setValue('updates/ready', json.dumps({'version': '0.3.28', 'name': staged.name,
                                                            'sha256': digest(self.patch), 'kind': 'patch'}))
        self.smart_app_control.return_value = True
        service = self.service()
        service.start()
        self.assertEqual((service.state, service.reason), ('manual', 'blocked'))
        self.assertFalse(staged.exists())
        self.assertIsNone(self.settings.value('updates/ready'))

    def test_download_that_does_not_fit_on_the_disk_waits(self):
        with patch('adf.updates.shutil.disk_usage', return_value=Mock(free=len(self.patch)+updates.SPACE_MARGIN-1)):
            service = self.checked(self.service())
        self.assertEqual(service.state, 'idle')
        self.assertTrue(service.pending)
        self.assertEqual(service.timer.interval(), updates.LATER)
        self.assertEqual(list(self.folder.glob('ADF-*')), [])
        with patch('adf.updates.shutil.disk_usage', return_value=Mock(free=len(self.patch)+updates.SPACE_MARGIN)):
            service.check()
            wait_until(lambda: not service.busy)
        self.assertEqual(service.state, 'ready')

    def test_full_disk_removes_the_partial_download_and_waits(self):
        transfer = updates._Transfer
        for write in (True, False):
            with self.subTest(fails='write' if write else 'close'):
                self.settings.remove('updates')
                with patch('adf.updates._Transfer', side_effect=lambda file, *rest: transfer(FullDisk(file, write), *rest)):
                    service = self.checked(self.service())
                self.assertFalse(service.busy)
                self.assertEqual(service.state, 'idle')
                self.assertEqual(service.timer.interval(), updates.LATER)
                self.assertEqual(list(self.folder.glob('ADF-*')), [])
                self.assertIsNone(self.settings.value('updates/ready'))
                service.shutdown()

    def test_withdrawn_release_removes_the_staged_update(self):
        service = self.checked(self.service())
        self.assertEqual(service.state, 'ready')
        (self.folder/'install-0.3.28.log').write_bytes(b'log')
        self.server.latest = '0.3.27'
        service.check()
        wait_until(lambda: not service.busy)
        self.assertEqual(service.state, 'idle')
        self.assertIsNone(service.package)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ['update.lock'])
        self.assertIsNone(self.settings.value('updates/ready'))
        self.assertEqual(service.timer.interval(), updates.RECHECK)

    def test_repeated_digest_mismatch_leaves_the_update_to_the_user(self):
        name = 'ADF-Update-0.3.27-to-0.3.28.exe'
        self.publish(**{name: self.patch})
        self.server.assets[name] = os.urandom(1000)
        service = self.checked(self.service())
        self.assertEqual((service.state, service.timer.interval()), ('idle', updates.LATER))
        service.check()
        wait_until(lambda: not service.busy)
        self.assertEqual((service.state, service.reason), ('manual', 'mismatch'))
        self.assertEqual(len(self.server.downloaded(name)), 2)
        service.check()
        wait_until(lambda: not service.busy)
        self.assertEqual(service.state, 'manual')
        service.shutdown()
        # The count survives a restart while the published file stays the same.
        again = self.checked(self.service())
        self.assertEqual((again.state, again.reason), ('manual', 'mismatch'))
        self.assertEqual(len(self.server.downloaded(name)), 2)
        # A corrected upload changes the published digest and is downloaded.
        fixed = os.urandom(300_000)
        self.publish(**{name: fixed})
        again.check()
        wait_until(lambda: not again.busy)
        self.assertEqual((again.state, again.reason), ('ready', None))
        self.assertEqual(again.staged().read_bytes(), fixed)
        self.assertIsNone(self.settings.value('updates/mismatch'))

    def test_paused_service_keeps_the_package_being_installed(self):
        service = self.checked(self.service())
        package = service.package
        newer = os.urandom(1000)
        self.server.latest = '0.3.29'
        self.publish('SHA256SUMS-0.3.29.txt', **{'ADF-Update-0.3.27-to-0.3.29.exe': newer})
        requests = len(self.server.requests)
        service.check()  # Already asking GitHub when the user clicks install.
        service.pause()
        wait_until(lambda: not service.busy)
        service.check()
        service.download()
        QTest.qWait(100)
        self.assertEqual((service.state, service.package), ('ready', package))
        self.assertTrue(service.staged(package).is_file())
        self.assertFalse(service.timer.isActive())
        self.assertLessEqual(len(self.server.requests), requests+1)
        service.resume()
        self.assertFalse(service.paused)
        self.assertEqual((service.timer.isActive(), service.timer.interval()), (True, updates.CHECK_DELAY))


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
        without_smart_app_control(self)
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

    def test_opening_adf_with_a_downloaded_update_asks_once_to_install_it(self):
        self.window.show()
        self.ready()
        with patch.object(self.window, 'ask_to_update', return_value=True) as ask, \
             patch.object(self.window, 'install_update') as install:
            self.window.offer_update()
            self.window.offer_update()
        ask.assert_called_once_with('0.3.28', False)
        install.assert_called_once_with()

    def test_a_download_during_work_waits_for_the_next_document_and_later_keeps_the_button(self):
        self.window.show()
        with patch.object(self.window, 'ask_to_update', return_value=False) as ask, \
             patch.object(self.window, 'install_update') as install:
            self.ready()
            QTest.qWait(50)
            ask.assert_not_called()
            self.assertTrue(self.window.open_path(self.pdf()))
            wait_until(lambda: ask.called)
        ask.assert_called_once_with('0.3.28', False)
        install.assert_not_called()
        self.assertFalse(self.window.update_button.isHidden())
        self.assertEqual(self.window.update_button.text(), '업데이트 설치 · 0.3.28')

    def found(self, state='downloading'):
        self.service.package = self.package
        self.service.percent = 0
        self.service._set(state)
        self.service.found.emit()

    def test_a_new_version_found_after_start_is_offered_at_once_and_installed_when_downloaded(self):
        self.window.show()
        # Like the real one, installing starts at once and closes ADF.
        installing = lambda: setattr(self.window, 'installing_update', True)
        with patch.object(self.window, 'ask_to_update', return_value=True) as ask, \
             patch.object(self.window, 'install_update', side_effect=installing) as install:
            self.found()
            wait_until(lambda: ask.called)
            ask.assert_called_once_with('0.3.28', False, found=True)
            progress = self.window.update_when_ready
            self.assertIsNotNone(progress)
            self.service.percent = 40
            self.service._set('downloading')
            self.assertEqual(progress.value(), 40)
            install.assert_not_called()
            with patch.object(self.window.update_notifier, 'show') as notified:
                self.service._set('ready', announce=True)
            install.assert_called_once_with()
            notified.assert_not_called()
            self.assertIsNone(self.window.update_when_ready)
            # A later check of the same session does not ask again.
            self.window.offer_update()
            self.found('ready')
            QTest.qWait(50)
        ask.assert_called_once()

    def test_later_keeps_the_download_and_a_check_during_work_does_not_ask(self):
        self.window.show()
        with patch.object(self.window, 'ask_to_update', return_value=True), \
             patch.object(self.window, 'install_update') as install:
            self.found()
            wait_until(lambda: self.window.update_when_ready is not None)
            QTest.keyClick(self.window.update_when_ready, Qt.Key.Key_Escape)
            self.assertIsNone(self.window.update_when_ready)
            self.service._set('ready', announce=True)
        install.assert_not_called()
        self.assertEqual(self.window.update_button.text(), '업데이트 설치 · 0.3.28')
        self.window.update_offered = None
        with patch.object(self.window, 'ask_to_update') as ask:
            self.found()
            QTest.qWait(50)
        ask.assert_not_called()

    def test_a_download_that_fails_closes_the_progress(self):
        self.window.show()
        with patch.object(self.window, 'ask_to_update', return_value=True), \
             patch.object(self.window, 'install_update') as install:
            self.found()
            wait_until(lambda: self.window.update_when_ready is not None)
            self.service._set('idle')
        self.assertIsNone(self.window.update_when_ready)
        install.assert_not_called()

    def pdf(self, name='열린 문서.pdf'):
        path = self.root/name
        with pymupdf.open() as doc:
            for _ in range(2):
                doc.new_page(width=360, height=480)
            doc.save(path)
        return path

    def test_one_click_closes_adf_and_starts_the_installer(self):
        self.ready()
        document = self.root/'열린 문서.pdf'
        document.write_bytes(b'%PDF-1.4\n%%EOF\n')
        self.window.document.path = str(document)
        log = self.root/'updates'/'install-0.3.28.log'
        log.write_text('an earlier attempt')
        application = Mock()
        earlier_log = []

        def start(program, arguments):
            # The setup program that the installer unpacks writes this log as it starts.
            earlier_log.append(log.exists())
            Path(next(argument for argument in arguments if argument.startswith('/LOG='))[5:]).write_text('Log opened')
            return True, 4321
        with patch('adf.updates.other_windows', return_value={}), \
             patch('adf.updates.running', return_value={4321}), \
             patch('adf.app.QApplication.instance', return_value=application), \
             patch('adf.app.QProcess.startDetached', side_effect=start) as started:
            self.window.update_button.click()
            self.assertTrue(self.service.paused)
            wait_until(lambda: application.quit.called)
        program, arguments = started.call_args.args
        self.assertEqual(Path(program), self.root/'updates'/self.package.name)
        self.assertIn('/ADFACKNOTICE=0.3.28', arguments)
        self.assertIn(f'/ADFRELAUNCH={sys.executable}', arguments)
        self.assertIn(f'/ADFOPEN={document}', arguments)
        self.assertIn(f'/LOG={log}', arguments)
        self.assertEqual(earlier_log, [False])
        application.quit.assert_called_once()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(json.loads(self.window.settings.value('updates/attempt')), {'version': '0.3.28', 'kind': 'patch'})

    def test_blocked_setup_program_reopens_adf_with_the_document(self):
        document = self.pdf()
        self.assertTrue(self.window.open_path(document))
        self.ready()
        application = Mock()
        # The installer starts, but Windows stops the setup program it unpacks, so no log appears.
        with patch('adf.updates.other_windows', return_value={}), \
             patch('adf.updates.running', return_value=set()), \
             patch('adf.app.QApplication.instance', return_value=application), \
             patch('adf.app.QMessageBox.warning') as warning, \
             patch('adf.app.QProcess.startDetached', return_value=(True, 4321)):
            self.window.update_button.click()
            wait_until(lambda: warning.called)
        application.quit.assert_not_called()
        application.setQuitOnLastWindowClosed.assert_called_with(True)
        self.assertIn('Windows 보안 설정', warning.call_args.args[2])
        self.assertIn('다운로드 페이지', warning.call_args.args[2])
        self.assertTrue(self.window.isVisible())
        self.assertEqual(Path(self.window.document.path).resolve(), document.resolve())
        self.assertEqual(self.window.document.page_count, 2)
        self.assertEqual((self.service.state, self.service.reason, self.service.package.version), ('manual', 'security', '0.3.28'))
        self.assertFalse(self.service.paused)
        self.assertFalse(self.window.installing_update)
        self.assertEqual(self.window.update_button.text(), '새 버전 받기 · 0.3.28')
        self.assertTrue(self.window.update_button.isEnabled())
        self.assertIn('Windows 보안 설정', self.window.update_button.toolTip())
        self.assertEqual(self.window.settings.value('updates/failed'), '0.3.28')
        self.assertIsNone(self.window.settings.value('updates/attempt'))
        self.assertFalse((self.root/'updates'/self.package.name).exists())
        with patch('adf.app.QDesktopServices.openUrl') as opened:
            self.window.update_button.click()
        self.assertEqual(opened.call_args.args[0].toString(), f'{updates.RELEASES}/tag/v0.3.28')

    def test_setup_program_that_never_starts_is_given_up(self):
        self.ready()
        application = Mock()
        with patch('adf.updates.INSTALLER_START', 0.5), \
             patch('adf.updates.other_windows', return_value={}), \
             patch('adf.updates.running', return_value={4321}), \
             patch('adf.app.QApplication.instance', return_value=application), \
             patch('adf.app.QMessageBox.warning') as warning, \
             patch('adf.app.QProcess.startDetached', return_value=(True, 4321)):
            clicked = time.monotonic()
            self.window.update_button.click()
            wait_until(lambda: warning.called)
        self.assertGreaterEqual(time.monotonic()-clicked, 0.5)
        application.quit.assert_not_called()
        self.assertEqual((self.service.state, self.service.reason), ('manual', 'security'))
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.stack.currentIndex(), 0)

    def test_installer_that_cannot_start_leaves_an_empty_window(self):
        self.assertTrue(self.window.open_path(self.pdf()))
        self.window.show()
        # The document is gone by the time ADF closes, so nothing is reopened.
        self.window.document.path = str(self.root/'지운 문서.pdf')
        self.ready()
        application = Mock()
        with patch('adf.updates.other_windows', return_value={}), \
             patch('adf.app.QApplication.instance', return_value=application), \
             patch('adf.app.QMessageBox.warning') as warning, \
             patch('adf.app.QProcess.startDetached', return_value=(False, 0)):
            self.window.update_button.click()
            wait_until(lambda: warning.called)
        self.assertIn('시작하지 못했습니다', warning.call_args.args[2])
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.stack.currentIndex(), 0)
        self.assertEqual(self.window.thumbnails.count(), 0)
        self.assertEqual(self.window.document.page_count, 0)
        for key in ('save', 'close', 'print', 'next', 'rotate'):
            self.assertFalse(self.window.actions[key].isEnabled(), key)
        self.assertEqual(self.window.windowTitle(), 'ADF — 문서 작업, 가볍게')
        # The staged update stays ready for another try.
        self.assertEqual(self.service.state, 'ready')
        self.assertTrue(self.window.update_button.isEnabled())
        self.assertFalse(self.service.paused)
        self.assertIsNone(self.window.settings.value('updates/attempt'))

    def test_install_runs_the_package_that_was_verified(self):
        self.ready()
        application = Mock()
        alive = {4242}

        def start(program, arguments):
            Path(next(argument for argument in arguments if argument.startswith('/LOG='))[5:]).write_text('Log opened')
            return True, 4321
        with patch('adf.updates.other_windows', return_value={4242: [1]}), \
             patch('adf.updates.close_windows') as close, \
             patch('adf.updates.running', side_effect=lambda pids: set(pids) & alive), \
             patch('adf.app.QApplication.instance', return_value=application), \
             patch('adf.app.QProcess.startDetached', side_effect=start) as started:
            self.window.update_button.click()
            wait_until(lambda: close.called)
            # While another window asks about saving, checks and downloads wait.
            self.assertTrue(self.service.paused)
            self.assertFalse(self.service.timer.isActive())
            self.service.check()
            self.assertIsNone(self.service.reply)
            self.service.package = Package('0.3.29', 'ADF-Setup-0.3.29.exe', 'b'*64, 'setup')
            alive.clear()
            wait_until(lambda: application.quit.called)
        program, arguments = started.call_args.args
        self.assertEqual(Path(program), self.root/'updates'/self.package.name)
        self.assertIn('/ADFACKNOTICE=0.3.28', arguments)
        self.assertEqual(json.loads(self.window.settings.value('updates/attempt')), {'version': '0.3.28', 'kind': 'patch'})

    def test_other_windows_that_stay_open_stop_the_update(self):
        self.ready()
        with patch('adf.updates.other_windows', return_value={4242: [1]}), \
             patch('adf.updates.close_windows') as close, \
             patch('adf.updates.running', return_value={4242}), \
             patch('adf.app.QMessageBox.information') as message, \
             patch('adf.app.QProcess.startDetached') as start:
            self.window.update_button.click()
            wait_until(lambda: close.called)
            close.assert_called_once_with({4242: [1]})
            self.assertFalse(self.window.update_button.isEnabled())
            self.window.update_deadline = 0
            wait_until(lambda: message.called)
        start.assert_not_called()
        self.assertFalse(self.window.installing_update)
        self.assertFalse(self.service.paused)
        self.assertTrue(self.window.update_button.isEnabled())

    def test_changed_staged_file_is_not_run(self):
        self.ready()
        (self.root/'updates'/self.package.name).write_bytes(b'changed')
        with patch('adf.app.QMessageBox.warning') as warning, \
             patch('adf.app.QProcess.startDetached') as start:
            self.window.update_button.click()
            wait_until(lambda: warning.called)
        start.assert_not_called()
        self.assertIn('손상', warning.call_args.args[2])
        self.assertEqual(self.service.state, 'idle')
        self.assertFalse(self.service.paused)
        self.assertFalse(self.window.installing_update)
        self.assertFalse((self.root/'updates'/self.package.name).exists())

    def test_mac_download_waits_for_a_click_before_opening_the_disk_image(self):
        self.service.platform = 'darwin'
        image = Package('0.3.28', 'ADF-0.3.28-macOS.dmg', digest(b'image'), 'dmg')
        self.service.package = image
        self.service._set('available')

        def downloaded():
            self.service.staged().write_bytes(b'image')
            self.service._set('ready', announce=True)
        with patch.object(self.service, 'download', side_effect=downloaded) as download, \
             patch.object(self.window.update_notifier, 'show') as notified, \
             patch('adf.app.QDesktopServices.openUrl') as opened, \
             patch.object(self.window, 'close') as close:
            self.window.update_button.click()
            download.assert_called_once()
            self.assertTrue(self.service.requested)
            QTest.qWait(200)
            opened.assert_not_called()
            close.assert_not_called()
            self.assertEqual(self.window.update_button.text(), '업데이트 설치 · 0.3.28')
            self.assertTrue(self.window.update_button.isEnabled())
            self.assertEqual(notified.call_args.args[2], '설치 화면 열기')
            self.window.update_button.click()
            wait_until(lambda: opened.called)
        self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), self.root/'updates'/image.name)
        close.assert_called_once()

    def test_manual_state_says_why_and_opens_the_download_page(self):
        expected = {'failed': '자동 업데이트를 설치하지 못했습니다.', 'blocked': 'Windows 스마트 앱 컨트롤',
                    'security': 'Windows 보안 설정', 'mismatch': '공개된 파일과 달라'}
        for reason, sentence in expected.items():
            with self.subTest(reason=reason), patch.object(self.window.update_notifier, 'show') as notified:
                self.service._set('idle')
                self.service._manual('0.3.28', reason)
                self.assertEqual(self.window.update_button.text(), '새 버전 받기 · 0.3.28')
                self.assertIn(sentence, self.window.update_button.toolTip())
                self.assertIn('다운로드 페이지', self.window.update_button.toolTip())
                _, message, action = notified.call_args.args
                self.assertIn(sentence, message)
                self.assertEqual(action, '다운로드 페이지 열기')

    def test_help_menu_turns_automatic_checks_off(self):
        help_menu = [action.menu() for action in self.window.menuBar().actions() if action.text() == '도움말'][0]
        self.assertIn(self.window.actions['auto_update'], help_menu.actions())
        self.assertTrue(self.window.actions['auto_update'].isChecked())
        self.window.actions['auto_update'].trigger()
        self.assertFalse(self.window.settings.value('updates/auto', True, type=bool))
        self.assertFalse(self.service.enabled)


if __name__ == '__main__':
    unittest.main()
