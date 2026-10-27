import ctypes, ctypes.wintypes as W, subprocess
G = 1024**3
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
rows = []
for x in out:
    pid = int(x)
    h = k32.OpenProcess(0x1000 | 0x1000, False, pid)
    if not h: continue
    buf = ctypes.create_unicode_buffer(260); ln = W.DWORD(260)
    ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(ln))
    name = buf.value.rsplit("\\", 1)[-1] if ok else "?"
    c = PMC(); c.cb = ctypes.sizeof(PMC)
    if ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb):
        rows.append((c.PagefileUsage / G, c.WorkingSetSize / G, name, pid))
    k32.CloseHandle(h)
rows.sort(reverse=True)
tot = sum(r[0] for r in rows)
print(f"process commit sum: {tot:.1f} GB")
for pf, ws, name, pid in rows[:15]:
    if pf < 0.5: break
    print(f"{name:24s} pid {pid:7d}  commit {pf:7.2f} GB  ws {ws:7.2f} GB")
