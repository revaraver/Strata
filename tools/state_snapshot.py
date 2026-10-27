"""Strata state snapshot collector: run now, run again when the user reports
degradation, diff the two JSON files.

    python tools/state_snapshot.py [outfile.json]

Collects: GPU (nvidia-smi), engine process memory, system RAM/commit,
/metrics (vram, expert cache, conversation cache, totals), and the log's
cache/park/evict markers. Windows-only, stdlib only.
"""
import json, subprocess, sys, time, urllib.request

def ps(script):
    out = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                         capture_output=True, text=True, timeout=30,
                         encoding="utf-8", errors="replace").stdout.strip()
    try:
        return json.loads(out)
    except Exception:
        return {"raw": out}

def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=30,
                          encoding="utf-8", errors="replace").stdout.strip()

def main():
    snap = {"t": time.strftime("%Y-%m-%d %H:%M:%S")}
    # detailed memory split via Win32 APIs (no admin needed): tells file cache /
    # modified / standby apart, which is how a post-wake accumulation gets named
    snap["mem_detail"] = ps(
        '$g=Add-Type -MemberDefinition \'[DllImport("kernel32.dll")] public static extern void GlobalMemoryStatusEx(ref MEMORYSTATUSEX m); [StructLayout(LayoutKind.Sequential)] public struct MEMORYSTATUSEX { public uint dwLength, dwMemoryLoad; public ulong ullTotalPhys, ullAvailPhys, ullTotalPageFile, ullAvailPageFile, ullTotalVirtual, ullAvailVirtual, ullAvailExtendedVirtual; }\' -Name MS -Namespace W -PassThru; '
        '$m=[W.MS+MEMORYSTATUSEX]::new(); $m.dwLength=[System.Runtime.InteropServices.Marshal]::SizeOf($m); [W.MS]::GlobalMemoryStatusEx([ref]$m); '
        '[PSCustomObject]@{total_gb=[math]::Round($m.ullTotalPhys/1GB,1); avail_gb=[math]::Round($m.ullAvailPhys/1GB,1); '
        'commit_total_gb=[math]::Round($m.ullTotalPageFile/1GB,1); commit_avail_gb=[math]::Round($m.ullAvailPageFile/1GB,1)} | ConvertTo-Json -Compress')
    snap["cache_detail"] = ps(
        'Get-Counter -Counter "\\Memory\\Cache Bytes","\\Memory\\Modified Page List Bytes","\\Memory\\Standby Cache Reserve Bytes","\\Memory\\Available MBytes" -ErrorAction SilentlyContinue | '
        'ForEach-Object { $_.CounterSamples } | ForEach-Object { $_.Path.Split("\\")[-1] + "=" + [math]::Round($_.CookedValue/1MB,1) + "MB" } | ConvertTo-Json -Compress')
    snap["top_procs"] = ps('Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First 6 Name, '
                           '@{n="ws_gb";e={[math]::Round($_.WorkingSet64/1GB,1)}}, '
                           '@{n="priv_gb";e={[math]::Round($_.PrivateMemorySize64/1GB,1)}} | ConvertTo-Json -Compress')
    snap["gpu"] = sh('nvidia-smi --query-gpu=memory.used,memory.total,memory.free --format=csv,noheader,nounits')
    snap["gpu_apps"] = sh('nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader | grep -i strata')
    snap["proc"] = ps('$p = Get-Process strata -ErrorAction SilentlyContinue | Select-Object -First 1; '
                      'if($p){ [PSCustomObject]@{pid=$p.Id; ws_mb=[math]::Round($p.WorkingSet64/1MB); '
                      'private_mb=[math]::Round($p.PrivateMemorySize64/1MB); threads=$p.Threads.Count; '
                      'start=$p.StartTime.ToString("s")} } else { "no strata process" } | ConvertTo-Json -Compress')
    snap["sys"] = ps('$o=Get-CimInstance Win32_OperatingSystem; [PSCustomObject]@{'
                     'free_ram_mb=[math]::Round($o.FreePhysicalMemory/1KB); '
                     'total_ram_mb=[math]::Round($o.TotalVisibleMemorySize/1KB); '
                     'free_commit_mb=[math]::Round($o.FreeVirtualMemory/1KB); '
                     'total_commit_mb=[math]::Round($o.TotalVirtualMemorySize/1KB)} | ConvertTo-Json -Compress')
    try:
        req = urllib.request.Request("http://127.0.0.1:8080/metrics",
                                     headers={"Authorization": "***"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via HTTP_PROXY
        with opener.open(req, timeout=5) as r:
            d = json.load(r)
        e = d.get("engine", {})
        keep = {k: e[k] for k in e if any(s in k.lower() for s in ("vram", "cache", "park", "expert", "ram"))}
        snap["metrics"] = {"busy": d.get("busy"), "totals": d.get("totals"),
                           "engine": keep, "conversation_cache": d.get("conversation_cache")}
    except Exception as exc:
        snap["metrics"] = {"error": str(exc)}
    log = r"D:\git\Strata-latest\strata-iq3_s-test.log"
    marks = []
    try:
        with open(log, "rb") as f:
            lines = f.readlines()
        for ln in lines[-4000:]:
            s = ln.decode("utf-8", "replace")
            if any(k in s for k in ("engine started", "expert cache ", "kept free", "parking", "evicted", "shrinking")):
                marks.append(s.strip()[:200])
        snap["log_markers"] = marks[-12:]
    except Exception as exc:
        snap["log_markers"] = [str(exc)]
    out = sys.argv[1] if len(sys.argv) > 1 else r"D:\git\Strata-latest\data\snapshots\latest.json"
    import os
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    print(json.dumps(snap, ensure_ascii=False, indent=1)[:2500])
    print("saved ->", out)

if __name__ == "__main__":
    main()
