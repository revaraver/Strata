import ctypes, ctypes.wintypes as W
nt = ctypes.windll.ntdll
def query():
    n = 1 << 20
    v = W.DWORD()
    while True:
        buf = ctypes.create_string_buffer(n)
        r = nt.NtQuerySystemInformation(5, buf, n, ctypes.byref(v))
        if r == 0: return buf
        if r in (-1073741820, -2147483643): n = max(n * 2, v.value + 65536); continue
        raise OSError(r)
buf = query()
def u64(off): return ctypes.cast(ctypes.byref(buf, off), ctypes.POINTER(ctypes.c_ulonglong))[0]
def u32(off): return ctypes.cast(ctypes.byref(buf, off), ctypes.POINTER(W.DWORD))[0]
G = 1024**3
off = 0
target = None
rows = []
while True:
    nxt = u32(off)
    pid = u64(off + 128)
    pf = u64(off + 64)
    rows.append((pid, pf / G))
    if pid == 103128:
        target = off
    if not nxt: break
    off += nxt
tot = sum(r[1] for r in rows)
print(f"kernel commit sum: {tot:.1f} GB over {len(rows)} procs")
big = sorted(rows, key=lambda r: -r[1])[:8]
for pid, pf in big:
    print(f"pid {pid}: {pf:.2f} GB")
if target is not None:
    print("strata entry raw offsets:")
    for o in range(24, 144, 8):
        print(f"  +{o}: {u64(target + o) / G:.2f} GB")
