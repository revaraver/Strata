"""Final deployment acceptance; the model itself must already be starting in a visible Windows console."""
import hashlib
import json
import subprocess
import sys
import time
import psutil
from verify_named_ram import api, ROOT

if __name__=='__main__':
    console_pid=int(sys.argv[1])
    print('Waiting for the Windows console server to be ready',flush=True)
    deadline=time.monotonic()+240
    while True:
        try:
            m=api('/metrics',timeout=2)
            if m.get('live',{}).get('state')=='idle':break
        except Exception:
            if time.monotonic()>deadline:raise
        assert time.monotonic()<deadline, 'visible server did not become ready'
        time.sleep(.5)
    p=psutil.Process(m['engine']['process_id'])
    assert console_pid in [x.pid for x in p.parents()]
    print('Visible Windows server ready; engine PID',p.pid,flush=True)
    result=ROOT/'bench/results/local-device-park'
    result.mkdir(parents=True,exist_ok=True)
    for script,source,target in [('verify_device_park.py','device-park-verification.json','locked-kv-acceptance.json'),
                                 ('verify_device_park_long.py','device-park-long-context.json','locked-kv-long-context.json')]:
        subprocess.run([sys.executable,'-X','utf8',str(ROOT/'tools'/script)],cwd=ROOT,check=True)
        (result/target).write_text((ROOT/source).read_text(encoding='utf-8'),encoding='utf-8')
    metadata={'passed':True,'console_pid':console_pid,'engine_pid':p.pid,
              'engine_sha256':hashlib.sha256((ROOT/'engine/strata.exe').read_bytes()).hexdigest(),
              'metrics':api('/metrics')['engine'],'finished_at':time.time()}
    (result/'final.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print('PASS: final deployed Windows build, resident historical KV, cycles and 232K context',flush=True)
