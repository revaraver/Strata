import ctypes, ctypes.wintypes as W
G = 1024**3
nt = ctypes.windll.ntdll
class U(ctypes.Structure):
    _fields_ = [("Length", W.WORD), ("MaximumLength", W.WORD), ("pad", W.DWORD), ("Buffer", ctypes.c_void_p)]
def query(cls):
    n = 1 << 20
    while True:
        buf = ctypes.create_string_buffer(n)
        r = nt.NtQuerySystemInformation(5, buf, n, ctypes.byref(v := W.DWORD()))
        if r == 0: return buf, v.value
        if r in (-1073741820, -2147483643, 0xC0000004, 0x80000005): n = max(n * 2, v.value + 65536); continue
        raise OSError(r)
buf, size = query(None)
off = 0
rows = []
while True:
    nxt = ctypes.cast(ctypes.byref(buf, off), ctypes.POINTER(W.DWORD))[0]
    name_u = ctypes.cast(ctypes.byref(buf, off + 8), ctypes.POINTER(U))[0]
    try:
        name = ctypes.wstring_at(name_u.Buffer, name_u.Length // 2) if name_u.Buffer else "?"
    except OSError:
        name = "?"
    pf = ctypes.cast(ctypes.byref(buf, off + 64), ctypes.POINTER(ctypes.c_ulonglong))[0]
    rows.append((pf / G, name.rsplit("\\", 1)[-1], ctypes.cast(ctypes.byref(buf, off + 4), ctypes.POINTER(W.DWORD))[0]))
    if not nxt: break
    off += nxt
rows.sort(reverse=True)
tot = sum(r[0] for r in rows)
print(f"kernel-side process commit sum: {tot:.1f} GB  ({len(rows)} processes)")
for pf, name, pid in rows[:12]:
    if pf < 0.5: break
    print(f"{name:28s} pid {pid:7d}  commit {pf:7.2f} GB")
