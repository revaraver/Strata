"""A ~232K-token context must survive device parking without serialization or re-prefill."""
import verify_device_park as v
v.REPORT = v.ROOT / 'device-park-long-context.json'
messages = [{'role':'user', 'content':'这是一段需要保持不变的对话记录。'*29000 + '\n请记住编号 72931，并只回复收到。'}]
if __name__ == '__main__':
    v.phase('long-seed', lambda:v.generate(messages))
    before = v.rows[-1]['result']['usage']['prompt_tokens']
    assert before > 220000, before
    v.phase('long-park', v.park_and_wait)
    v.phase('long-explicit-wake', lambda:v.api('/load',{}))
    v.phase('long-reuse', lambda:v.generate(messages))
    usage = v.rows[-1]['result']['usage']
    assert usage['prompt_tokens_details']['cached_tokens'] > before*.99, usage
    assert v.rows[-1]['after']['device']['host_bytes'] == 0
    print('PASS: 220K+ context, same PID, original KV reused, snapshot fully released',flush=True)
