"""Same-depth content A/B probe: two ~150K-token prompts, different content
genres, same engine/cache state. Engine logs the t/s; we just fire and label.
"""
import json, time, urllib.request, sys

KEY = "qwe123"
URL = "http://127.0.0.1:8080/v1/chat/completions"

prose_para = [
    "The discussion of speculative decoding keeps returning to the tradeoff between draft length and verification cost, because the marginal row is never free.",
    "When the expert cache is warm, a verification round costs almost the same whether the window carries one row or five, and the accepted tokens arrive essentially gratis.",
    "Memory bandwidth is the real currency of decode; every optimization is ultimately a claim about how many bytes per token must move.",
    "The user pushed back on the framing, insisting that the comparison had to hold context depth fixed before any conclusion about degradation could be drawn.",
    "Long conversations pay a quiet tax on attention, but the tax is small next to the tax of a cold expert streaming over the bus mid-round.",
    "A drafter that guesses code structure can keep its acceptance rate high deep into a session, while a drafter facing creative prose degrades gracefully.",
    "The snapshot system keys everything on the token fingerprint, so any client that re-renders history pays the re-prefill bill without noticing.",
    "Sleep states matter more than people think: suspend to ram keeps the arena alive but kills the gpu context, and the first request pays the re-warm.",
    "Benchmarking a local engine is mostly the art of not fooling yourself about what changed between two numbers.",
    "The panel mode that frees thirty gigabytes sells the cold tail of the expert distribution, which is cheap until a request routes into it.",
]
json_line = '{"tool":"terminal","arguments":{"command":"grep -an \\"engine started\\" strata.log | tail -1","timeout":60},"id":"call_%d"}'

def build(kind, target_chars):
    if kind == "prose":
        unit = " ".join(prose_para)
    else:
        unit = "\n".join(json_line % i for i in range(40))
    reps = target_chars // len(unit) + 1
    return (unit * reps)[:target_chars]

def probe(kind, chars):
    text = build(kind, chars) + "\n\nSummarize the pattern above in a few sentences."
    body = json.dumps({
        "model": "strata_4096",
        "messages": [{"role": "user", "content": text}],
        "max_tokens": 800, "stream": False,
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Content-Type": "application/json", "Authorization": f"Bearer {KEY}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    with opener.open(req, timeout=600) as r:
        d = json.load(r)
    u = d.get("usage", {})
    print(f"{kind}: prompt={u.get('prompt_tokens')} completion={u.get('completion_tokens')} "
          f"wall={time.time()-t0:.1f}s tag=PROBE_{kind.upper()}", flush=True)

# 150K tokens ~ 520K chars of this filler; the engine logs the real token count
probe("prose", 520_000)
time.sleep(2)
probe("json", 520_000)
