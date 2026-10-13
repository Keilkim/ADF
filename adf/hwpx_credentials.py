"""Authenticated encryption for a private, ignored build credential bundle.

The bundle includes its random wrapping key so friends can run the application
without entering a credential. This is plaintext concealment, NOT a secret
boundary: a recipient who inspects the bundle or process can recover the key.
Use a server relay if the Gemini key must be inaccessible to recipients.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

from .hwpx_api import ConversionError

AAD = b'XDF HWPX Gemini credential v1'


def credential_directory():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'HWPX_CREDENTIALS'
    return Path(__file__).resolve().parents[1] / '.tools' / 'hwpx-credentials'


def has_developer_key():
    return bool(os.environ.get('XDF_HWPX_API_KEY') or os.environ.get('GEMINI_API_KEY') or
                (credential_directory() / 'credential.enc').is_file())


def create_bundle(directory, key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key.strip()):
        raise ConversionError('유효한 API 키를 입력해 주세요.')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Only this new private directory is restricted, never the workspace or a
    # shared parent. The source-packaging process excludes .tools entirely.
    from .app import _restrict_worker_directory
    _restrict_worker_directory(directory)
    wrapping_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    encrypted = nonce + AESGCM(wrapping_key).encrypt(nonce, key.strip().encode('utf-8'), AAD)
    for name, data in (('wrapping.bin', wrapping_key), ('credential.enc', encrypted)):
        temporary = directory / (name + '.tmp')
        temporary.write_bytes(data)
        temporary.replace(directory / name)


def load_bundle(directory):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    directory = Path(directory)
    try:
        wrapping_key = (directory / 'wrapping.bin').read_bytes()
        encrypted = (directory / 'credential.enc').read_bytes()
        if len(wrapping_key) != 32 or not 28 <= len(encrypted) <= 4096:
            raise ConversionError('배포용 API 설정 파일이 올바르지 않습니다.')
        return AESGCM(wrapping_key).decrypt(encrypted[:12], encrypted[12:], AAD).decode('utf-8')
    except (OSError, InvalidTag, UnicodeError):
        raise ConversionError('암호화된 배포용 API 설정을 읽지 못했습니다. 개발자에게 문의해 주세요.') from None


def load_developer_key():
    key = os.environ.get('XDF_HWPX_API_KEY', '') or os.environ.get('GEMINI_API_KEY', '')
    if key.strip():
        return key.strip()
    directory = credential_directory()
    if not (directory / 'credential.enc').is_file():
        raise ConversionError('Gemini 연결이 준비되지 않았습니다. 개발자가 배포용 API 키를 설정해야 합니다.')
    return load_bundle(directory)
