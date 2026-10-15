"""Windows integration probe: real model, physical RAM, physical disk counters, and KV reuse."""
import concurrent.futures
import json
import threading
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import psutil

ROOT = Path(__file__).resolve().parents[1]
cfg = json.loads((ROOT / 'strata-iq3_s.json').read_text(encoding='utf-8-sig'))
URL = 'http://127.0.0.1:8080'
headers = {'Authorization': 'Bearer ' + cfg['api_key'], 'Content-Type': 'application/json'}

def api(path, body=None, timeout=360):
    req = Request(URL + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except HTTPError as e:
        return {'error': e.read().decode(), 'code': e.code}

def snapshot():
    m = api('/metrics', timeout=15)
    return {'state': m.get('live', {}).get('state'), 'ram': m.get('engine', {}).get('retained_ram'),
            'requests': [{k:r.get(k) for k in ['prompt_tokens','reused','prompt_ms','generated']} for r in m.get('requests', [])[:4]],
            'physical_used': psutil.virtual_memory().used,
            'disks': {k: {'read':v.read_bytes, 'write':v.write_bytes} for k,v in psutil.disk_io_counters(perdisk=True).items()}}

records = []
def phase(name, fn):
    before = snapshot()
    samples = [before['physical_used']]
    stop = threading.Event()
    def sample():
        while not stop.wait(.2):
            samples.append(psutil.virtual_memory().used)
    t = threading.Thread(target=sample, daemon=True); t.start()
    start = time.monotonic()
    try:
        result = fn()
    finally:
        elapsed = time.monotonic() - start
        stop.set(); t.join()
    after = snapshot()
    row = {'phase': name, 'elapsed_s': round(elapsed,3), 'result': result, 'before':before, 'after':after,
           'peak_physical_used': max(samples + [after['physical_used']]),
           'disk_delta': {d:{k:after['disks'][d][k]-v[k] for k in ('read','write')} for d,v in before['disks'].items()}}
    records.append(row)
    (ROOT / 'named-ram-verification.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return result

if __name__ == '__main__':
    # Two unrelated conversations fill different idle batch slots before parking.
    messages = [[{'role':'user','content':f'请记住测试编号 {i}。' + ('这是保留会话测试记录，请只回复已收到。' * 120)}] for i in range(2)]
    def generate(msg):
        r = api('/v1/chat/completions', {'model':'strata_4096','messages':msg,'max_tokens':24,'temperature':0})
        return {'usage':r.get('usage'), 'error':r.get('error'), 'choices':r.get('choices')}
    phase('seed-batch', lambda: list(concurrent.futures.ThreadPoolExecutor(2).map(generate, messages)))
    phase('unload-ram', lambda: api('/unload', {}))
    phase('load-ram', lambda: api('/load', {}))
    phase('reuse-batch', lambda: list(concurrent.futures.ThreadPoolExecutor(2).map(generate, messages)))
    phase('unload-full', lambda: api('/unload?full=1', {}))
    # Full unload must preserve session even after the weights are released.
    phase('full-already-unloaded', lambda: api('/unload?full=1', {}))
    phase('load-after-full', lambda: api('/load', {}))
    phase('reuse-after-full', lambda: generate(messages[0]))
