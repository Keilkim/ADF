"""Exercise the real installer checkbox without reaching the install step.

Uses only windows belonging to the newly launched, isolated installer process.
It never moves the mouse, types into another application, or sends mail.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

u, k = c.windll.user32, c.windll.kernel32
u.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
u.SendMessageW.restype = w.LPARAM
u.GetWindowLongW.argtypes = [w.HWND, c.c_int]
u.GetParent.argtypes = [w.HWND]
u.GetParent.restype = w.HWND
u.GetDlgItem.argtypes = [w.HWND, c.c_int]
u.GetDlgItem.restype = w.HWND
u.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.OpenProcess.restype = w.HANDLE
k.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
k.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
k.CreateToolhelp32Snapshot.restype = w.HANDLE
k.CloseHandle.argtypes = [w.HANDLE]
callback = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


class ProcessEntry(c.Structure):
    _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
        ('heap', c.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
        ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD), ('exe', w.WCHAR*260)]


k.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]
k.Process32NextW.argtypes = [w.HANDLE, c.POINTER(ProcessEntry)]


def children_of(root):
    snapshot = k.CreateToolhelp32Snapshot(2, 0)
    entry = ProcessEntry()
    entry.size = c.sizeof(entry)
    parents = {}
    try:
        ok = k.Process32FirstW(snapshot, c.byref(entry))
        while ok:
            parents[entry.pid] = entry.parent
            ok = k.Process32NextW(snapshot, c.byref(entry))
    finally:
        k.CloseHandle(snapshot)
    result = {root}
    while True:
        expanded = result | {pid for pid, parent in parents.items() if parent in result}
        if expanded == result:
            return result
        result = expanded


def windows(pids):
    result = []
    def add(hwnd, param):
        pid = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, c.byref(pid))
        if pid.value in pids:
            result.append(hwnd)
        return True
    u.EnumWindows(callback(add), 0)
    return result


def text(hwnd, class_name=False):
    value = c.create_unicode_buffer(1000)
    (u.GetClassNameW if class_name else u.GetWindowTextW)(hwnd, value, len(value))
    return value.value


def controls(hwnd):
    result = []
    def add(child, param):
        result.append(child)
        return True
    u.EnumChildWindows(hwnd, callback(add), 0)
    return result


def locally_visible(child, root):
    while child and child != root:
        if not u.GetWindowLongW(child, -16) & 0x10000000:
            return False
        child = u.GetParent(child)
    return bool(child)


def until(check, seconds=20):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        value = check()
        if value:
            return value
        time.sleep(.05)
    raise TimeoutError('Installer UI condition did not become true.')


def capture(hwnd, path):
    from PIL import Image
    g = c.windll.gdi32
    u.GetWindowDC.restype = w.HDC
    g.CreateCompatibleDC.argtypes = [w.HDC]
    g.CreateCompatibleDC.restype = w.HDC
    g.CreateCompatibleBitmap.argtypes = [w.HDC, c.c_int, c.c_int]
    g.CreateCompatibleBitmap.restype = w.HBITMAP
    g.SelectObject.argtypes = [w.HDC, w.HANDLE]
    g.SelectObject.restype = w.HANDLE
    g.DeleteObject.argtypes = [w.HANDLE]
    g.DeleteDC.argtypes = [w.HDC]
    u.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
    u.ReleaseDC.argtypes = [w.HWND, w.HDC]
    rect = w.RECT()
    u.GetWindowRect(hwnd, c.byref(rect))
    width, height = rect.right-rect.left, rect.bottom-rect.top
    dc = u.GetWindowDC(hwnd)
    memory = g.CreateCompatibleDC(dc)
    bitmap = g.CreateCompatibleBitmap(dc, width, height)
    old = g.SelectObject(memory, bitmap)
    try:
        assert u.PrintWindow(hwnd, memory, 0)
        header = c.create_string_buffer(40)
        import struct
        struct.pack_into('<IiiHHIIiiII', header, 0, 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        bits = c.create_string_buffer(width*height*4)
        g.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, c.c_void_p, c.c_void_p, w.UINT]
        assert g.GetDIBits(memory, bitmap, 0, height, bits, header, 0)
        Image.frombytes('RGB', (width, height), bits.raw, 'raw', 'BGRX').save(path)
    finally:
        g.SelectObject(memory, old)
        g.DeleteObject(bitmap)
        g.DeleteDC(memory)
        u.ReleaseDC(hwnd, dc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('installer', type=Path)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    token = uuid.uuid4().hex
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    temporary = tempfile.TemporaryDirectory(prefix='adf-notice-', dir=args.report.parent.resolve())
    process = subprocess.Popen([str(args.installer.resolve()), '/SP-', '/LANG=korean',
        '/ADFISOLATEDTEST='+token], startupinfo=startup,
        env=dict(os.environ, TEMP=temporary.name, TMP=temporary.name))
    report = dict(ok=False, isolated_token=token, never_reached_install=True, cleanup_complete=False)
    root = None
    try:
        root = until(lambda: next((h for h in windows(children_of(process.pid)) if text(h, True) == 'TWizardForm'), None))
        next_button = until(lambda: next((h for h in controls(root) if '다음' in text(h) and 'Button' in text(h, True)), None))
        u.SendMessageW(next_button, 0xF5, 0, 0)  # BM_CLICK: welcome -> notice
        until(lambda: not u.IsWindowEnabled(next_button))
        check = until(lambda: next((h for h in controls(root) if text(h, True) == 'TNewCheckListBox' and locally_visible(h, root)), None))
        report['unchecked_blocks_next'] = True
        capture(root, args.report.with_name(args.report.stem+'-unchecked.png'))
        u.SendMessageW(check, 0x186, 0, 0)  # LB_SETCURSEL
        u.SendMessageW(check, 0x100, 0x20, 0)  # WM_KEYDOWN / Space
        u.SendMessageW(check, 0x101, 0x20, 0)
        until(lambda: u.IsWindowEnabled(next_button))
        report['checked_enables_next'] = True
        capture(root, args.report.with_name(args.report.stem+'-checked.png'))
        u.SendMessageW(check, 0x100, 0x20, 0)
        u.SendMessageW(check, 0x101, 0x20, 0)
        until(lambda: not u.IsWindowEnabled(next_button))
        report['unchecking_blocks_again'] = True
        report['ok'] = True
    finally:
        # Close our own preview and confirm its exit dialog. Keep process handles
        # until every installer child has exited, before removing its executable.
        owned = children_of(process.pid)
        handles = [k.OpenProcess(0x100001, False, pid) for pid in owned]
        handles = [handle for handle in handles if handle]
        try:
            if root:
                u.PostMessageW(root, 0x10, 0, 0)
            deadline = time.monotonic()+5
            while time.monotonic() < deadline and any(k.WaitForSingleObject(handle, 0) == 258 for handle in handles):
                for dialog in windows(owned):
                    if dialog != root:
                        yes = u.GetDlgItem(dialog, 6)
                        if yes:
                            u.PostMessageW(yes, 0xF5, 0, 0)
                time.sleep(.05)
            for handle in handles:
                if k.WaitForSingleObject(handle, 0) == 258:
                    k.TerminateProcess(handle, 1)
                assert k.WaitForSingleObject(handle, 10000) == 0, 'Installer process did not exit'
            process.wait(timeout=10)
            assert Path(temporary.name).resolve().is_relative_to(args.report.parent.resolve())
            deadline = time.monotonic()+10
            while True:
                try:
                    temporary.cleanup()
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.2)
            report['cleanup_complete'] = True
        finally:
            for handle in handles:
                k.CloseHandle(handle)
            args.report.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
