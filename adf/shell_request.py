"""Read one Explorer selection without command-line limits or time batching."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re


MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_FILES = 4096
REQUEST_NAME = re.compile(r'request-\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?\.json\Z')


def request_directory() -> Path:
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        raise ValueError('Windows 사용자 폴더를 찾을 수 없습니다.')
    return Path(local) / 'ADF' / 'ShellRequests'


def read_shell_request(path: str | Path) -> tuple[str, list[str]]:
    """Consume only a named request in ADF's private request directory.

    No arbitrary command-line file is removed. The extension supplies only
    operation and filenames; passwords and PDF contents are never in requests.
    """
    request = Path(path).absolute()
    if not REQUEST_NAME.fullmatch(request.name):
        raise ValueError('탐색기 요청 파일 이름이 올바르지 않습니다.')
    expected = request_directory().resolve()
    if request.parent.resolve() != expected or request.is_symlink():
        raise ValueError('ADF 탐색기 요청 폴더의 파일만 열 수 있습니다.')
    if not request.is_file() or request.stat().st_size > MAX_REQUEST_BYTES:
        raise ValueError('탐색기 요청 파일을 읽을 수 없습니다.')
    try:
        with request.open('rb') as stream:
            raw = stream.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError('한 번에 선택한 파일이 너무 많습니다.')
        payload = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(payload, dict):
            raise ValueError('탐색기 요청 형식이 올바르지 않습니다.')
        operation = payload.get('operation')
        files = payload.get('files')
        if operation not in ('merge', 'split') or not isinstance(files, list):
            raise ValueError('지원하지 않는 탐색기 요청입니다.')
        if not 1 <= len(files) <= MAX_FILES:
            raise ValueError('선택한 PDF 파일 수가 올바르지 않습니다.')
        if operation == 'split' and len(files) != 1:
            raise ValueError('PDF 분할은 한 개의 PDF를 선택해 주세요.')
        if operation == 'merge' and len(files) < 2:
            raise ValueError('PDF 병합은 두 개 이상의 PDF를 선택해 주세요.')
        for filename in files:
            if not isinstance(filename, str) or '\x00' in filename:
                raise ValueError('PDF 파일 경로가 올바르지 않습니다.')
            item = Path(filename)
            if not item.is_absolute() or item.suffix.lower() != '.pdf' or not item.is_file():
                raise ValueError(f'선택한 PDF를 찾을 수 없습니다: {item.name}')
        return operation, files
    finally:
        # The path has been validated as this application's one-time request.
        request.unlink(missing_ok=True)
