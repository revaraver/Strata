import ctypes, ctypes.wintypes as W, subprocess
G = 1024**3
class MS(ctypes.Structure):
    _fields_ = [("dwLength", W.DWORD), ("dwMemoryLoad", W.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
m = MS(); m.dwLength = ctypes.sizeof(MS)
ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
inuse = (m.ullTotalPhys - m.ullAvailPhys) / G
print(f"in use: {inuse:.1f} GB of {m.ullTotalPhys/G:.1f}")

class PMC(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("PageFaultCount", W.DWORD),
                ("PeakWorkingSetSize", ctypes.c_ulonglong), ("WorkingSetSize", ctypes.c_ulonglong),
                ("QuotaPeakPagedPoolUsage", ctypes.c_ulonglong), ("QuotaPagedPoolUsage", ctypes.c_ulonglong),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_ulonglong), ("QuotaNonPagedPoolUsage", ctypes.c_ulonglong),
                ("PagefileUsage", ctypes.c_ulonglong), ("PeakPagefileUsage", ctypes.c_ulonglong)]
k32 = ctypes.windll.kernel32
k32.OpenProcess.restype = W.HANDLE
k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
out = subprocess.run(["powershell.exe","-NoProfile","-Command",
  "Get-Process -ErrorAction SilentlyContinue | ForEach-Object { $_.Id }"],
  capture_output=True, text=True).stdout.split()
strata_ws = other_ws = 0.0
for x in out:
    pid = int(x)
    h = k32.OpenProcess(0x1000, False, pid)
    if not h: continue
    buf = ctypes.create_unicode_buffer(260); ln = W.DWORD(260)
    ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(ln))
    name = buf.value.rsplit("\\", 1)[-1].lower() if ok else ""
    c = PMC(); c.cb = ctypes.sizeof(PMC)
    if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
        ws = c.WorkingSetSize / G
        if name == "strata.exe": strata_ws += ws
        else: other_ws += ws
    k32.CloseHandle(h)
print(f"strata.exe WS: {strata_ws:.1f} GB")
print(f"ALL other processes WS sum (includes pages shared with strata, e.g. python's arena map): {other_ws:.1f} GB")
