"""Own Windows anonymous section views across engine restarts, without copying payloads.

Every retained view is VirtualLock'ed: merely keeping a pagefile-backed section's handle
would allow Windows to page it out and defeat an unload-to-RAM guarantee.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import threading


class MemoryKeeper:
    def __init__(self, namespace: str):
        self.namespace = namespace
        self.regions: dict[str, tuple[str, int, int, int]] = {}
        self.lock = threading.RLock()
        self.kernel = k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenFileMappingW.argtypes = [W.DWORD, W.BOOL, W.LPCWSTR]
        k.OpenFileMappingW.restype = W.HANDLE
        k.MapViewOfFile.argtypes = [W.HANDLE, W.DWORD, W.DWORD, W.DWORD, ctypes.c_size_t]
        k.MapViewOfFile.restype = ctypes.c_void_p
        k.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
        k.UnmapViewOfFile.restype = W.BOOL
        k.CloseHandle.argtypes = [W.HANDLE]
        k.CloseHandle.restype = W.BOOL
        k.VirtualLock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        k.VirtualLock.restype = W.BOOL
        k.VirtualUnlock.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        k.VirtualUnlock.restype = W.BOOL
        k.GetCurrentProcess.restype = W.HANDLE
        k.GetProcessWorkingSetSize.argtypes = [W.HANDLE, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        k.SetProcessWorkingSetSize.argtypes = [W.HANDLE, ctypes.c_size_t, ctypes.c_size_t]
        k.SetProcessWorkingSetSize.restype = W.BOOL
        self.error: str | None = None

    def keep(self, kind: str, size: int, name: str):
        if kind not in ("weights", "session") or not name.startswith(self.namespace + "-") or size <= 0:
            raise ValueError("unexpected engine shared memory descriptor")
        with self.lock:
            if name in self.regions:
                if self.regions[name][:2] != (kind, size):
                    raise ValueError("shared memory descriptor changed")
                return
            k = self.kernel
            h = k.OpenFileMappingW(0xF001F, False, name)  # FILE_MAP_ALL_ACCESS
            if not h:
                raise ctypes.WinError(ctypes.get_last_error())
            p = k.MapViewOfFile(h, 0xF001F, 0, 0, size)
            if not p:
                code = ctypes.get_last_error()
                k.CloseHandle(h)
                raise ctypes.WinError(code)
            # Account for all our locks and ordinary Python pages, with bounded 256 MiB slack.
            total = sum(r[1] for r in self.regions.values()) + size
            proc = k.GetCurrentProcess()
            low, high = ctypes.c_size_t(), ctypes.c_size_t()
            k.GetProcessWorkingSetSize(proc, ctypes.byref(low), ctypes.byref(high))
            reserve = total + (256 << 20)
            if not k.SetProcessWorkingSetSize(proc, max(low.value, reserve), max(high.value, reserve + (256 << 20))) or not k.VirtualLock(p, size):
                code = ctypes.get_last_error()
                k.UnmapViewOfFile(p)
                k.CloseHandle(h)
                raise OSError(code, "cannot keep model/session resident in RAM: " + str(ctypes.WinError(code)))
            self.regions[name] = kind, size, h, p

    def release(self, *, kind: str | None = None, prefix: str | None = None):
        with self.lock:
            for name, (typ, size, h, p) in list(self.regions.items()):
                if (kind is None or typ == kind) and (prefix is None or name == prefix or name.startswith(prefix + "-")):
                    self.kernel.VirtualUnlock(p, size)
                    self.kernel.UnmapViewOfFile(p)
                    self.kernel.CloseHandle(h)
                    del self.regions[name]
            total = sum(r[1] for r in self.regions.values())
            # Lower the raised quota too; it must not reserve the old 50 GiB after a full unload.
            self.kernel.SetProcessWorkingSetSize(self.kernel.GetCurrentProcess(), total + (128 << 20), total + (512 << 20))

    def stats(self) -> dict:
        with self.lock:
            return {"weights_bytes": sum(r[1] for r in self.regions.values() if r[0] == "weights"),
                    "session_bytes": sum(r[1] for r in self.regions.values() if r[0] == "session"),
                    "segments": len(self.regions), "locked": self.error is None, "error": self.error}
