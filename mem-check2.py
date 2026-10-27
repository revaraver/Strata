import ctypes, ctypes.wintypes as W
G = 1024**3

class MS(ctypes.Structure):
    _fields_ = [("dwLength", W.DWORD), ("dwMemoryLoad", W.DWORD), ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
m = MS(); m.dwLength = ctypes.sizeof(MS)
ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
print(f"system: RAM {m.ullTotalPhys/G:.1f} GB free {m.ullAvailPhys/G:.1f} GB | "
      f"commit limit {m.ullTotalPageFile/G:.1f} GB used {(m.ullTotalPageFile-m.ullAvailPageFile)/G:.1f} GB")

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
import subprocess
out = subprocess.run(["powershell.exe","-NoProfile","-Command",
  "Get-Process strata,python -ErrorAction SilentlyContinue | ForEach-Object { $_.Id }"],
  capture_output=True, text=True).stdout.split()
pids = [int(x) for x in out]
tot_ws = tot_pf = 0.0
for pid in pids:
    h = k32.OpenProcess(0x1000 | 0x0400 | 0x1000, False, pid)  # QUERY_LIMITED | VM_READ | QUERY_NAME
    if not h: continue
    buf = ctypes.create_unicode_buffer(260); ln = W.DWORD(260)
    ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(ln))
    name = buf.value.rsplit("\\", 1)[-1].lower() if ok else ""
    if name in ("strata.exe", "python.exe"):
        c = PMC(); c.cb = ctypes.sizeof(PMC)
        if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
            ws, pf = c.WorkingSetSize / G, c.PagefileUsage / G
            if name == "strata.exe" or ws > 1.0:
                print(f"{name} pid {pid}: working set {ws:.2f} GB, commit {pf:.2f} GB")
                tot_ws += ws; tot_pf += pf
    k32.CloseHandle(h)
print(f"TOTAL (strata.exe + strata server python): working set {tot_ws:.2f} GB, commit {tot_pf:.2f} GB")
