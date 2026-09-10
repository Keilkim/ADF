"""Wait for Windows worker descendants to release their file handles on cancel."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import time


def terminate_worker_tree(process_id):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
            ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
            ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
            ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
            ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260),
        ]

    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD

    # Keep the original worker alive as a kernel object, even if it exits
    # during enumeration. Only taskkill /T terminates the specified tree.
    root = kernel.OpenProcess(0x00100000, False, process_id)  # SYNCHRONIZE
    if not root:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: already gone
            return
        raise ctypes.WinError(error)
    handles = [root]
    try:
        snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)  # processes only
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            entry = ProcessEntry(dwSize=ctypes.sizeof(ProcessEntry))
            parents = {}
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
            if ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel.CloseHandle(snapshot)
        descendants = {process_id}
        while children := {pid for pid, parent in parents.items()
                           if parent in descendants and pid not in descendants}:
            descendants.update(children)
        for pid in descendants - {process_id}:
            handle = kernel.OpenProcess(0x00100000, False, pid)
            if handle:
                handles.append(handle)
            elif ctypes.get_last_error() != 87:
                raise ctypes.WinError(ctypes.get_last_error())
        command = Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32'/'taskkill.exe'
        subprocess.run([str(command), '/PID', str(process_id), '/T', '/F'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        # Termination is asynchronous. The virtualenv launcher may exit before
        # its Python child finishes cancelling I/O on staged PDF files.
        deadline = time.monotonic() + 10
        for handle in handles:
            result = kernel.WaitForSingleObject(handle, max(0, int((deadline-time.monotonic())*1000)))
            if result == 258:  # WAIT_TIMEOUT
                raise TimeoutError('취소한 작업 프로세스가 아직 종료되지 않았습니다.')
            if result != 0:  # WAIT_OBJECT_0
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        for handle in handles:
            kernel.CloseHandle(handle)
