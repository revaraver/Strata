import ctypes, ctypes.wintypes as W

# system-wide memory
class MS(ctypes.Structure):
    _fields_=[("dwLength",W.DWORD),("dwMemoryLoad",W.DWORD),("ullTotalPhys",ctypes.c_ulonglong),
              ("ullAvailPhys",ctypes.c_ulonglong),("ullTotalPageFile",ctypes.c_ulonglong),
              ("ullAvailPageFile",ctypes.c_ulonglong),("ullTotalVirtual",ctypes.c_ulonglong),
              ("ullAvailVirtual",ctypes.c_ulonglong),("ullAvailExtendedVirtual",ctypes.c_ulonglong)]
m=MS(); m.dwLength=ctypes.sizeof(MS); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
G=1024**3
print(f"system: total RAM {m.ullTotalPhys/G:.1f} GB, free {m.ullAvailPhys/G:.1f} GB | "
      f"commit limit {m.ullTotalPageFile/G:.1f} GB, used {(m.ullTotalPageFile-m.ullAvailPageFile)/G:.1f} GB")

# per-process working set + pagefile commit
k32=ctypes.windll.kernel32
k32.OpenProcess.restype=W.HANDLE
k32.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD]
class PMC(ctypes.Structure):
    _fields_=[("cb",W.DWORD),("PageFaultCount",W.DWORD)]+[("x",ctypes.c_ulonglong) for _ in range(7)]
import subprocess, json
out=subprocess.run(["powershell.exe","-NoProfile","-Command",
  "Get-Process strata,python -ErrorAction SilentlyContinue | Where-Object {$_.WorkingSet64 -gt 100MB} | ForEach-Object { $_.Id }"],
  capture_output=True,text=True).stdout.split()
tot_ws=tot_pf=0
for pid in out:
    pid=int(pid)
    h=k32.OpenProcess(0x1000,False,pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h: continue
    c=PMC(); c.cb=ctypes.sizeof(PMC)
    ok=k32.GetProcessMemoryInfo(h,ctypes.byref(c),c.cb)
    ws=subprocess.run  # placeholder
    import psutil if False else None
    k32.CloseHandle(h)
