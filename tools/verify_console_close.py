"""Close only the visible console created for this Strata acceptance run and verify cleanup."""
import ctypes
from ctypes import wintypes as W
import json
import time
import psutil
from verify_device_park import api, gpu, ROOT

if __name__ == '__main__':
    import sys
    if len(sys.argv) == 2 and sys.argv[1] == '--followup':
        path = ROOT/'device-park-console-close.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        remaining = [p for p in record['children'] if psutil.pid_exists(p)]
        listeners = [c.pid for c in psutil.net_connections(kind='tcp')
                     if getattr(c.laddr,"port",None) == 8080 and c.status == psutil.CONN_LISTEN]
        record['cleanup_followup'] = {'remaining_pids': remaining, 'port_8080_listeners': listeners,
                                      'gpu':gpu(), 'physical_used':psutil.virtual_memory().used}
        assert not remaining and not listeners, record['cleanup_followup']
        record['passed'] = True
        path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(record['cleanup_followup']),flush=True)
        print('PASS: full console-close cleanup confirmed after Windows retired the locked mappings',flush=True)
        sys.exit(0)
    engines=[p for p in psutil.process_iter(['name','exe']) if p.info['name']=='strata.exe' and p.info['exe']
             and str(ROOT).lower() in p.info['exe'].lower()]
    assert len(engines)==1
    server=engines[0].parent()
    assert any('server.py' in a for a in server.cmdline())
    children=server.children(recursive=True)
    pids=[server.pid]+[p.pid for p in children]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.AttachConsole.argtypes=[W.DWORD]; k.AttachConsole.restype=W.BOOL
    k.SetConsoleTitleW.argtypes=[W.LPCWSTR]; k.SetConsoleTitleW.restype=W.BOOL
    k.FreeConsole()
    assert k.AttachConsole(server.pid),ctypes.WinError(ctypes.get_last_error())
    title=f'Strata IQ3_S - RAM acceptance {server.pid}'
    assert k.SetConsoleTitleW(title)
    k.FreeConsole()
    u=ctypes.WinDLL('user32',use_last_error=True)
    callback=ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    found=[]
    @callback
    def scan(hwnd,param):
        if u.IsWindowVisible(hwnd):
            buf=ctypes.create_unicode_buffer(1024);u.GetWindowTextW(hwnd,buf,1024)
            if title in buf.value:
                owner=W.DWORD();u.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
                found.append((hwnd,owner.value,buf.value))
        return True
    for _ in range(20):
        found.clear();u.EnumWindows(scan,0)
        if found:break
        time.sleep(.1)
    assert len(found)==1,found
    hwnd,owner,actual_title=found[0]
    # A Terminal process can host several separate windows. The unique title was set through this
    # server's attached console; additionally pin the test to the exact shell we launched.
    import sys
    assert len(sys.argv) == 2, 'Pass the ConsolePID returned by start-visible-iq3.ps1'
    assert int(sys.argv[1]) in [p.pid for p in server.parents()]
    assert psutil.Process(owner).name().lower() in ('windowsterminal.exe', 'openconsole.exe', 'conhost.exe')
    before={'gpu':gpu(),'physical_used':psutil.virtual_memory().used,'ram':api('/metrics')['engine'].get('retained_ram')}
    u.PostMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM]
    assert u.PostMessageW(hwnd,0x10,0,0) # WM_CLOSE: the same path as the window's close button
    started=time.monotonic()
    while time.monotonic()-started<90 and any(psutil.pid_exists(p) for p in pids):time.sleep(.1)
    remaining=[p for p in pids if psutil.pid_exists(p)]
    listening=[c.pid for c in psutil.net_connections(kind='tcp') if getattr(c.laddr,"port",None)==8080 and c.status==psutil.CONN_LISTEN]
    after={'remaining_pids':remaining,'port_8080_listeners':listening,'gpu':gpu(),'physical_used':psutil.virtual_memory().used}
    record={'window':actual_title,'window_owner':owner,'server':server.pid,'children':pids,
            'elapsed_s':round(time.monotonic()-started,3),'before':before,'after':after}
    (ROOT/'device-park-console-close.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(record,ensure_ascii=False),flush=True)
    assert not remaining and not listening,after
    print('PASS: visible Windows window close released server, descendants and port 8080',flush=True)
