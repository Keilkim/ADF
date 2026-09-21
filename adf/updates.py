"""Background updates from the ADF releases on GitHub.

ADF asks GitHub which release is the latest. When it is newer, the matching
installer is downloaded in the background and verified against the release's
SHA256SUMS file. Documents and file names are never sent. Windows prefers a
small patch installer built for the running version and falls back to the full
installer; the Mac app downloads the new disk image when the user asks for it.

Only one ADF process per user checks and downloads. The others show the staged
update that the owner recorded in the settings.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from PySide6.QtCore import QLockFile, QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import (QNetworkAccessManager, QNetworkInformation, QNetworkProxyFactory,
                               QNetworkReply, QNetworkRequest)

from . import __version__

RELEASES = 'https://github.com/Keilkim/ADF/releases'
CHECK_DELAY = 20_000              # after start, so opening a document stays fast
RECONNECT_DELAY = 5_000           # let a new connection settle before using it
RECHECK = 12*60*60*1000           # while ADF stays open
RETRY = 30*60*1000                # offline, behind a login page or GitHub unreachable
LATER = 60*60*1000                # the release is still being assembled
OWNER_RETRY = 10*60*1000          # another ADF window owns updates; take over when it closes
HASH_CHUNK = 8 << 20
SUMS_LIMIT = 256 << 10
KINDS = ('patch', 'setup', 'dmg', 'manual')
_NUMBER = re.compile(r'(\d+)\.(\d+)\.(\d+)')
_ASSET = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
_STAGED = re.compile(r'(?:ADF-(?:Update-\d+\.\d+\.\d+-to-|Setup-)?(\d+\.\d+\.\d+)(?:-macOS)?\.(?:exe|dmg)(?:\.part)?'
                     r'|install-(\d+\.\d+\.\d+)\.log)')


def parse_version(text):
    match = _NUMBER.fullmatch(text) if isinstance(text, str) else None
    return tuple(map(int, match.groups())) if match else None


def is_newer(candidate, current):
    candidate, current = parse_version(candidate), parse_version(current)
    return bool(candidate and current and candidate > current)


def tag_version(location, releases=RELEASES):
    """The version in GitHub's redirect from releases/latest to releases/tag/vX.Y.Z."""
    url, base = QUrl(location), QUrl(releases.rstrip('/') + '/tag/')
    if (url.scheme(), url.host(), url.port()) != (base.scheme(), base.host(), base.port()):
        return None
    path = url.path()
    if not path.startswith(base.path() + 'v'):
        return None
    version = path[len(base.path())+1:]
    return version if parse_version(version) else None


def parse_sums(text):
    sums = {}
    for line in text.lstrip('﻿').splitlines():
        digest, separator, name = line.strip().partition('  ')
        if separator and re.fullmatch(r'[0-9a-f]{64}', digest) and _ASSET.fullmatch(name):
            sums[name] = digest
    return sums


def sums_name(version, platform=sys.platform):
    return f'SHA256SUMS-{version}-macOS.txt' if platform == 'darwin' else f'SHA256SUMS-{version}.txt'


def staged_version(name):
    """The version a downloaded installer, partial download or install log belongs to."""
    match = _STAGED.fullmatch(name)
    return (match.group(1) or match.group(2)) if match else None


@dataclass(frozen=True)
class Package:
    version: str
    name: str
    sha256: str
    kind: str

    def valid(self):
        return (parse_version(self.version) is not None and self.kind in KINDS[:3]
                and bool(_ASSET.fullmatch(self.name)) and bool(re.fullmatch(r'[0-9a-f]{64}', self.sha256)))

    def url(self, releases=RELEASES):
        return f'{releases.rstrip("/")}/download/v{self.version}/{self.name}'


def choose_package(current, latest, sums, platform=sys.platform, patch=True):
    """The smallest published installer that updates this version, if any."""
    if platform == 'darwin':
        candidates = [('dmg', f'ADF-{latest}-macOS.dmg')]
    else:
        candidates = [('patch', f'ADF-Update-{current}-to-{latest}.exe')] if patch else []
        candidates.append(('setup', f'ADF-Setup-{latest}.exe'))
    for kind, name in candidates:
        if name in sums:
            return Package(latest, name, sums[name], kind)
    return None


def installer_arguments(package, executable, document=None, log=None):
    """Show progress only. Installing ADF already required reading the notice."""
    arguments = ['/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                 f'/ADFACKNOTICE={package.version}', f'/ADFRELAUNCH={executable}']
    if document:
        arguments.append(f'/ADFOPEN={document}')
    if log:
        arguments.append(f'/LOG={log}')
    return arguments


def file_sha256(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class _Transfer:
    def __init__(self, file, hasher, offset):
        self.file, self.hasher, self.offset = file, hasher, offset
        self.checked = self.rejected = self.failed = False


class UpdateService(QObject):
    """Check, download and stage updates for the current user."""
    changed = Signal()
    notify = Signal()

    def __init__(self, settings, folder, current=__version__, platform=sys.platform,
                 releases=RELEASES, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.folder = Path(folder)
        self.current = current
        self.platform = platform
        self.releases = releases.rstrip('/')
        self.state = 'idle'
        self.package = None
        self.percent = 0
        self.open_when_ready = False
        self.lock = None
        self.network = None
        self.reply = None
        self.busy = False
        self.pending = False
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.check)
        self.owner_timer = QTimer(self)
        self.owner_timer.setInterval(OWNER_RETRY)
        self.owner_timer.timeout.connect(self.start)

    @property
    def enabled(self):
        return self.settings.value('updates/auto', True, type=bool)

    def set_enabled(self, enabled):
        self.settings.setValue('updates/auto', bool(enabled))
        self.start() if enabled else self.stop()

    def start(self):
        if not self.enabled or self.closed:
            return
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        if not self._own():
            self.owner_timer.start()
            self._load_ready(announce=False)
            return
        self.owner_timer.stop()
        self._review_attempt()
        self._clean()
        self._load_ready(announce=True)
        if self.network is None:
            self.network = QNetworkAccessManager(self)
            # Corporate networks often configure a proxy or PAC file for all apps.
            QNetworkProxyFactory.setUseSystemConfiguration(True)
            if QNetworkInformation.loadBackendByFeatures(QNetworkInformation.Feature.Reachability):
                QNetworkInformation.instance().reachabilityChanged.connect(self._reachability)
        if not self.busy:
            self.timer.start(CHECK_DELAY)

    def stop(self):
        self.timer.stop()
        self.owner_timer.stop()
        if self.reply is not None:
            self.reply.abort()
        if self.lock is not None:
            self.lock.unlock()
            self.lock = None

    def shutdown(self):
        self.closed = True
        self.stop()

    def check(self):
        if not self.enabled or self.closed or self.network is None:
            return
        if self.busy:
            return self.timer.start(RETRY)
        self.busy = True
        request = self._request(self.releases + '/latest')
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        reply = self.reply = self.network.head(request)
        reply.finished.connect(lambda: self._checked(reply))

    def download(self):
        package = self.package
        if package is None or package.kind == 'manual' or self.busy or self.closed:
            return
        self.busy = True
        for path in self.folder.glob('ADF-*'):
            if path.name not in (package.name, package.name + '.part'):
                self._remove(path)
        self.percent = 0
        self._set('downloading')
        part = self.folder/(package.name + '.part')
        hasher = hashlib.sha256()
        try:
            stream = part.open('rb') if part.is_file() and part.stat().st_size else None
        except OSError:
            stream = None
        if stream is None:
            return self._get(package, part, hasher, 0)

        def step():
            # Resume after rehashing what is already on disk, a slice per event loop turn.
            if self.closed:
                return stream.close()
            chunk = stream.read(HASH_CHUNK)
            if chunk:
                hasher.update(chunk)
                return QTimer.singleShot(0, step)
            offset = stream.tell()
            stream.close()
            self._get(package, part, hasher, offset)
        step()

    def staged(self):
        return self.folder/self.package.name

    def verify_staged(self):
        """Hash the staged installer again right before running it."""
        try:
            valid = file_sha256(self.staged()) == self.package.sha256
        except OSError:
            valid = False
        if not valid:
            self._remove(self.staged())
            self.settings.remove('updates/ready')
            self.package = None
            self._set('idle')
            self._retry(RECONNECT_DELAY)
        return valid

    def mark_attempt(self):
        self.settings.setValue('updates/attempt', json.dumps({'version': self.package.version, 'kind': self.package.kind}))
        self.settings.sync()

    def clear_attempt(self):
        self.settings.remove('updates/attempt')
        self.settings.sync()

    def log_path(self):
        return self.folder/f'install-{self.package.version}.log'

    def _own(self):
        if self.lock is not None and self.lock.isLocked():
            return True
        self.lock = QLockFile(str(self.folder/'update.lock'))
        # Only a lock whose process has ended is stale, however long ADF stays open.
        self.lock.setStaleLockTime(0)
        if self.lock.tryLock(0):
            return True
        self.lock = None
        return False

    def _stored(self, key):
        self.settings.sync()
        try:
            value = json.loads(str(self.settings.value(key, '') or 'null'))
        except ValueError:
            return None
        return value if isinstance(value, dict) else None

    def _review_attempt(self):
        """An installer that ran without changing the version failed; do not repeat it."""
        attempt = self._stored('updates/attempt')
        if attempt is None:
            return
        self.clear_attempt()
        version = attempt.get('version')
        if not is_newer(version, self.current):
            return
        ready = self._stored('updates/ready')
        if ready is not None:
            self._remove(self.folder/str(ready.get('name', '')))
            self.settings.remove('updates/ready')
        # A patch falls back to the full installer; a failed full installer is left to the user.
        self.settings.setValue('updates/no_patch' if attempt.get('kind') == 'patch' else 'updates/failed', version)

    def _clean(self):
        for path in self.folder.iterdir():
            version = staged_version(path.name)
            if version and not is_newer(version, self.current):
                self._remove(path)

    def _load_ready(self, announce):
        stored = self._stored('updates/ready')
        try:
            package = Package(**stored) if stored else None
        except TypeError:
            package = None
        if (package is not None and package.valid() and is_newer(package.version, self.current)
                and (self.folder/package.name).is_file()):
            if self.state != 'ready' or self.package != package:
                self.package = package
                self._set('ready', announce)
        elif self.state == 'ready' and self.lock is None:
            # Another window installed or discarded the staged update.
            self.package = None
            self._set('idle')

    def _checked(self, reply):
        self.reply = None
        reply.deleteLater()
        self.busy = False
        if self.closed:
            return
        location = reply.header(QNetworkRequest.KnownHeaders.LocationHeader)
        version = None
        if isinstance(location, QUrl) and location.isValid():
            version = tag_version(reply.url().resolved(location).toString(), self.releases)
        if version is None:
            # Offline, a login page, a proxy error or no published release.
            return self._retry()
        self.pending = False
        if not is_newer(version, self.current):
            return self.timer.start(RECHECK)
        if self.package is not None and self.package.version == version and self.state != 'idle':
            return self.timer.start(RECHECK)
        if self.settings.value('updates/failed', '') == version:
            self.package = Package(version, '', '', 'manual')
            self._set('manual', announce=True)
            return self.timer.start(RECHECK)
        self.busy = True
        reply = self.reply = self.network.get(self._request(f'{self.releases}/download/v{version}/{sums_name(version, self.platform)}'))
        reply.finished.connect(lambda: self._sums(reply, version))

    def _sums(self, reply, version):
        self.reply = None
        reply.deleteLater()
        self.busy = False
        if self.closed:
            return
        data = bytes(reply.read(SUMS_LIMIT)) if reply.error() == QNetworkReply.NetworkError.NoError else b''
        if not data:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            return self._retry(LATER if status == 404 else RETRY)
        patch = self.settings.value('updates/no_patch', '') != version
        package = choose_package(self.current, version, parse_sums(data.decode('utf-8', 'replace')), self.platform, patch)
        if package is None:
            return self._retry(LATER)
        self.timer.start(RECHECK)
        self.package = package
        if self._stored('updates/ready') == asdict(package) and self.staged().is_file():
            return self._set('ready', announce=True)
        # A disk image must be installed by hand. A full installer waits on metered networks.
        if package.kind == 'dmg' or (package.kind == 'setup' and self._metered()):
            return self._set('available', announce=True)
        self.download()

    def _get(self, package, part, hasher, offset):
        try:
            file = part.open('ab' if offset else 'wb')
        except OSError:
            self.busy = False
            self._set(self._waiting())
            return self._retry()
        transfer = _Transfer(file, hasher, offset)
        request = self._request(package.url(self.releases))
        if offset:
            request.setRawHeader(b'Range', b'bytes=%d-' % offset)
        reply = self.reply = self.network.get(request)
        reply.readyRead.connect(lambda: self._receive(reply, transfer))
        reply.downloadProgress.connect(lambda received, total: self._progress(transfer, received, total))
        reply.finished.connect(lambda: self._downloaded(reply, package, part, transfer))

    def _receive(self, reply, transfer):
        if not transfer.checked:
            transfer.checked = True
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status not in (200, 206):
                transfer.rejected = True
                return reply.abort()
            if transfer.offset and status == 200:
                # The server ignored the range and sends the whole file again.
                transfer.file.seek(0)
                transfer.file.truncate()
                transfer.hasher = hashlib.sha256()
                transfer.offset = 0
        data = bytes(reply.readAll())
        try:
            transfer.file.write(data)
        except OSError:
            transfer.failed = True
            return reply.abort()
        transfer.hasher.update(data)

    def _progress(self, transfer, received, total):
        if total > 0:
            percent = min(99, int(100*(transfer.offset+received)/(transfer.offset+total)))
            if percent != self.percent:
                self.percent = percent
                self.changed.emit()

    def _downloaded(self, reply, package, part, transfer):
        self.reply = None
        reply.deleteLater()
        transfer.file.close()
        self.busy = False
        if self.closed:
            return
        if transfer.rejected or transfer.failed:
            self._remove(part)
        if reply.error() != QNetworkReply.NetworkError.NoError or transfer.rejected or transfer.failed:
            # Keep a partial download; the next attempt continues from it.
            self._set(self._waiting())
            return self._retry()
        if transfer.hasher.hexdigest() != package.sha256:
            self._remove(part)
            self._set(self._waiting())
            return self._retry(LATER)
        try:
            os.replace(part, self.folder/package.name)
        except OSError:
            self._set(self._waiting())
            return self._retry()
        self.settings.setValue('updates/ready', json.dumps(asdict(package)))
        self.settings.sync()
        self._set('ready', announce=True)

    def _waiting(self):
        """The state after an unfinished download: ask again only if the user had to ask."""
        package = self.package
        if package is not None and (package.kind == 'dmg' or self.open_when_ready or (package.kind == 'setup' and self._metered())):
            return 'available'
        return 'idle'

    def _metered(self):
        info = QNetworkInformation.instance()
        return bool(info is not None and info.supports(QNetworkInformation.Feature.Metered) and info.isMetered())

    def _reachability(self, reachability):
        if reachability == QNetworkInformation.Reachability.Online and self.pending and not self.busy:
            self.timer.start(RECONNECT_DELAY)

    def _retry(self, delay=RETRY):
        self.pending = True
        self.timer.start(delay)

    def _request(self, url):
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f'ADF/{self.current}')
        # Abort only when nothing arrives for this long, so slow links still finish.
        request.setTransferTimeout(60_000)
        return request

    def _set(self, state, announce=False):
        self.state = state
        self.changed.emit()
        if announce:
            self.notify.emit()

    @staticmethod
    def _remove(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _windows_api():
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowThreadProcessId.restype = wintypes.DWORD
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    return ctypes, wintypes, user, kernel, callback


def other_windows(executable):
    """Main windows of other processes started from this ADF executable, by process ID.

    Dialogs are owned windows and are skipped: closing the main window lets it
    ask about unsaved changes, and Qt ignores the request while a dialog is modal.
    """
    ctypes, wintypes, user, kernel, callback = _windows_api()
    target = os.path.normcase(os.path.abspath(executable))
    images, found = {}, {}

    def image(pid):
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            return buffer.value if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) else None
        finally:
            kernel.CloseHandle(handle)

    def visit(hwnd, _):
        if user.IsWindowVisible(hwnd) and not user.GetWindow(hwnd, 4):  # GW_OWNER
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid = pid.value
            if pid != os.getpid():
                if pid not in images:
                    images[pid] = image(pid)
                if images[pid] and os.path.normcase(images[pid]) == target:
                    found.setdefault(pid, []).append(hwnd)
        return True

    user.EnumWindows(callback(visit), 0)
    return found


def close_windows(windows):
    _, _, user, _, _ = _windows_api()
    for handles in windows.values():
        for hwnd in handles:
            user.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE


def running(pids):
    _, _, _, kernel, _ = _windows_api()
    alive = set()
    for pid in pids:
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if handle:
            try:
                if kernel.WaitForSingleObject(handle, 0) == 258:  # WAIT_TIMEOUT
                    alive.add(pid)
            finally:
                kernel.CloseHandle(handle)
    return alive
