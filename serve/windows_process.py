"""A Windows console owns the server's process tree, including hidden engine children.

The kernel closes this non-inheritable job handle when the server exits (including
closing its console window), then terminates any remaining descendants. No watchdog
or background service survives the window.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes as W
import os

_job = None


def bind_console_process_tree():
    global _job
    if os.name != 'nt' or _job is not None:
        return
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                    ('flags', W.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                    ('active_limit', W.DWORD), ('affinity', ctypes.c_size_t),
                    ('priority', W.DWORD), ('scheduling', W.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ('reads', 'writes', 'other', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', IO), ('process_limit', ctypes.c_size_t),
                    ('job_limit', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, W.LPCWSTR]
    k.CreateJobObjectW.restype = W.HANDLE
    k.SetInformationJobObject.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD]
    k.SetInformationJobObject.restype = W.BOOL
    k.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
    k.AssignProcessToJobObject.restype = W.BOOL
    k.GetCurrentProcess.restype = W.HANDLE
    k.CloseHandle.argtypes = [W.HANDLE]
    handle = k.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limit = Extended()
    limit.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)) or not \
            k.AssignProcessToJobObject(handle, k.GetCurrentProcess()):
        code = ctypes.get_last_error()
        k.CloseHandle(handle)
        raise ctypes.WinError(code)
    _job = handle  # process-lifetime handle; never inherited by children
