"""Windows acceptance: same-process sleep/wake, no model I/O, KV reuse, bounded RAM and timing.
Run only after starting run-iq3_s.bat in a visible Windows console.
"""
import concurrent.futures
import ctypes
import json
import os
from pathlib import Path
import threading
import time
import psutil
from verify_named_ram import api
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from serve.telemetry import _Nvml
_nv = _Nvml(0)
def gpu():
    m = _nv.Mem()
    assert _nv.lib.nvmlDeviceGetMemoryInfo(_nv.dev, ctypes.byref(m)) == 0
    return {'used':m.used,'free':m.free,'total':m.total}

def park_and_wait():
    started = time.perf_counter()
    result = api('/unload', {})
    api_s = time.perf_counter() - started
    assert 'error' not in result, result
    while gpu()['used'] > (8 << 30) and time.perf_counter() - started < 15:
        time.sleep(.02)
    result['api_s'] = round(api_s,4)
    result['release_settled_s'] = round(time.perf_counter()-started,4)
    result['gpu'] = gpu()
    assert result['gpu']['used'] < (8 << 30), result
    return result

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'device-park-verification.json'
rows = []


def snapshot():
    m = api('/metrics', timeout=10)
    assert 'error' not in m, m
    engines = [p for p in psutil.process_iter(['name', 'exe']) if p.info['name'] == 'strata.exe'
               and p.info['exe'] and str(ROOT).lower() in p.info['exe'].lower()]
    assert len(engines) == 1, [(p.pid, p.info['name']) for p in engines]
    p = engines[0]
    return {'gpu': gpu(), 'pid': p.pid, 'physical_used': psutil.virtual_memory().used,
            'engine_private': p.memory_info().private, 'process_io': p.io_counters()._asdict(),
            'state': m['live']['state'], 'parked': m['engine'].get('parked'),
            'ram': m['engine'].get('retained_ram'), 'device': m['engine'].get('device_park'),
            'vram': m['engine'].get('vram'), 'hardware': m.get('hardware'),
            'disk': {k: {'read': v.read_bytes, 'write': v.write_bytes}
                     for k, v in psutil.disk_io_counters(perdisk=True).items()}}


def phase(name, fn):
    before = snapshot(); samples = [before['physical_used']]
    stop = threading.Event()
    def sample():
        while not stop.wait(.05):
            samples.append(psutil.virtual_memory().used)
    thread = threading.Thread(target=sample, daemon=True); thread.start()
    started = time.perf_counter()
    try:
        result = fn()
    finally:
        elapsed = time.perf_counter() - started
        stop.set(); thread.join()
    after = snapshot()
    row = {'phase': name, 'elapsed_s': round(elapsed, 4), 'result': result, 'before': before, 'after': after,
           'peak_physical_used': max(samples+[after['physical_used']]),
           'process_io_delta': {k:after['process_io'][k]-v for k,v in before['process_io'].items()},
           'disk_delta': {d:{k:after['disk'][d][k]-v[k] for k in ('read','write')} for d,v in before['disk'].items()}}
    rows.append(row)
    REPORT.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    if 'park' in name or 'explicit-wake' in name:
        # Process I/O includes stdin/stdout: PARK/WAKE consume only a short command line.
        # A model/KV read or write would exceed this bound, even when whole-disk activity is unrelated.
        assert row['process_io_delta']['read_bytes'] <= 64, row['process_io_delta']
        assert row['process_io_delta']['write_bytes'] < (64 << 10), row['process_io_delta']
    print(json.dumps({'phase':name,'elapsed_s':row['elapsed_s'],'result':result,
                      'pid': after['pid'], 'parked':after['parked'], 'device':after['device'],
                      'vram':after['vram'], 'RAM_GiB':round(after['physical_used']/2**30,3),
                      'process_io_delta':row['process_io_delta'], 'disk_delta':row['disk_delta']},ensure_ascii=False),flush=True)
    assert before['pid'] == after['pid'], 'engine restarted'
    assert not isinstance(result,dict) or 'error' not in result, result
    return row


def generate(messages):
    result = api('/v1/chat/completions', {'model':'strata_4096','messages':messages, 'max_tokens':24,
                                        'temperature':0, 'reasoning_effort':'none'})
    assert 'error' not in result, result
    return {'usage':result['usage'], 'message':result['choices'][0]['message']}


if __name__ == '__main__':
    assert os.name == 'nt', 'Use the Windows Python interpreter'
    prompts = [[{'role':'user','content':f'测试编号 {i}。' + '这是一段需要保持不变的对话记录。'*1000 + '\n请只回复：内存验证通过。'}]
               for i in range(2)]
    def batch():
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            return list(pool.map(generate, prompts))
    phase('high-speed', lambda: api('/v1/vram', {'reserve_mib':4096}))
    phase('seed-batch', batch)
    # First explicit load and then actual conversation requests wake the parked process.
    for i in range(4):
        phase(f'park-{i}', park_and_wait)
        assert rows[-1]['after']['parked'], 'did not park'
        assert rows[-1]['after']['vram']['expert_cache_mib'] == 0
        if i == 0:
            phase('explicit-wake',lambda:api('/load',{}))
            assert not rows[-1]['after']['parked']
            assert rows[-1]['after']['device']['host_bytes'] == 0
        reuse = phase(f'chat-wake-{i}', batch)
        assert not reuse['after']['parked']
        assert reuse['after']['device']['host_bytes'] == 0, 'snapshot leaked'
        for r in reuse['result']:
            usage = r['usage']; cached = usage['prompt_tokens_details']['cached_tokens']
            assert cached > usage['prompt_tokens'] * .95, (usage, 'context was not reused')
        assert reuse['after']['vram']['reserve_mib'] == 4096
    phase('mid-speed',lambda:api('/v1/vram',{'reserve_mib':32768}))
    phase('mid-park',park_and_wait)
    phase('mid-chat-wake',lambda:generate(prompts[0]))
    assert rows[-1]['after']['vram']['reserve_mib'] == 32768
    phase('zero-speed',lambda:api('/v1/vram',{'reserve_mib':45056}))
    phase('zero-chat-stays-zero',lambda:generate(prompts[0]))
    assert rows[-1]['after']['vram']['reserve_mib'] == 45056
    phase('return-high',lambda:api('/v1/vram',{'reserve_mib':4096}))
    print('PASS: same PID, explicit and conversation wake, four cycles, KV reuse, snapshot cleanup, high/mid/zero semantics',flush=True)
