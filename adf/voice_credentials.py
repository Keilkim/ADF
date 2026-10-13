"""Protect saved Windows API credentials with the current user's DPAPI key."""
import base64
import sys


def protect(data, decrypt=False):
    if sys.platform != 'win32':
        raise ValueError('이 플랫폼에서는 API 키를 현재 실행 중에만 보관합니다.')
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ValueError('API 키를 보호하거나 읽을 수 없습니다. 키를 다시 입력해 주세요.')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


def load_key(settings):
    value = settings.value('voice/api_key_protected', '')
    if not value or sys.platform != 'win32':
        return ''
    try:
        return protect(base64.b64decode(value), decrypt=True).decode('utf-8')
    except (ValueError, UnicodeError):
        return ''


def save_key(settings, key, remember):
    if remember and key and sys.platform == 'win32':
        settings.setValue('voice/api_key_protected', base64.b64encode(protect(key.encode('utf-8'))).decode('ascii'))
    else:
        settings.remove('voice/api_key_protected')
