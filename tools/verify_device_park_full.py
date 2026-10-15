"""Explicit full unload is a cold reload; it releases weight RAM but keeps the configured session."""
import json
import time
import psutil
from verify_device_park import api, gpu, ROOT
from verify_device_park_long import messages


def state():
    m=api('/metrics',timeout=15)
    return {'ram':m['engine'].get('retained_ram'), 'parked':m['engine'].get('parked'),
            'state':m['live']['state'], 'gpu':gpu(), 'physical_used':psutil.virtual_memory().used,
            'engines':[p.pid for p in psutil.process_iter(['name','exe']) if p.info['name']=='strata.exe'
                       and p.info['exe'] and str(ROOT).lower() in p.info['exe'].lower()]}

if __name__=='__main__':
    records=[]
    for name,fn in [('full-unload',lambda:api('/unload?full=1',{})),
                    ('cold-load-after-full',lambda:api('/load',{})),
                    ('long-reuse-after-full',lambda:api('/v1/chat/completions',{'model':'strata_4096','messages':messages,
                                                                              'temperature':0,'max_tokens':24}))]:
        before=state(); started=time.monotonic(); result=fn(); elapsed=time.monotonic()-started; after=state()
        assert 'error' not in result,result
        if 'choices' in result:result={'usage':result['usage']}
        row={'phase':name,'elapsed_s':round(elapsed,4),'before':before,'after':after,'result':result}
        records.append(row)
        (ROOT/'device-park-full-unload.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(row,ensure_ascii=False),flush=True)
        if name=='full-unload':
            assert after['engines']==[],after
            assert after['ram']['weights_bytes']==0,after
            assert after['ram']['session_bytes']>0,after
        elif name=='cold-load-after-full':
            assert after['ram']['session_bytes']==0,after
        else:
            u=result['usage']; assert u['prompt_tokens_details']['cached_tokens'] >u['prompt_tokens']*.99,u
    print('PASS: full unload released the engine and all weight segments; cold load restored long-context KV',flush=True)
