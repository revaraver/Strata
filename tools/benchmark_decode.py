r"""Benchmark the running Strata HTTP API; never changes GPU or server settings.

Windows example:
  C:\Users\revar\AppData\Local\Programs\Python\Python313\python.exe tools\benchmark_decode.py --context 4k --output 1k --rounds 3
Uses server-side token counting to size the complete input, including chat template.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = (
    "Write a complete Python module implementing an asynchronous task scheduler with "
    "priorities, cancellation, retry backoff, concurrency limits, graceful shutdown, "
    "and at least twenty substantial unit tests. Include all implementation details. "
    "Keep writing until the module and all tests are complete. Do not summarize or stop early. "
    "The reference material above is background only; do not copy it."
)
TOPICS = (
    "storage engines record durable transaction logs before acknowledging committed changes",
    "network services bound concurrency and handle cancellation without losing queued work",
    "memory allocators separate short-lived buffers from reusable persistent state",
    "query planners compare estimated costs and choose indexes using measured selectivity",
    "distributed workers use leases and idempotency keys to recover interrupted jobs",
    "streaming protocols publish ordered events and retain explicit completion markers",
    "schedulers track deadlines and apply exponential backoff to transient failures",
    "monitoring records latency distributions and distinguishes queue time from execution",
)


def token_count(value):
    match = re.fullmatch(r"(\d+)([kKmM]?)", value.strip())
    if not match:
        raise argparse.ArgumentTypeError("使用整数或 4k、32k、1m（k=1024）")
    return int(match[1]) * {"": 1, "k": 1024, "m": 1024 * 1024}[match[2].lower()]


class Client:
    def __init__(self, url, api_key, timeout):
        self.url = url.rstrip("/")
        if self.url.endswith("/v1"):
            self.url = self.url[:-3]
        self.timeout = timeout
        self.headers = {"Content-Type": "application/json"}
        if api_key:
            self.headers["Authorization"] = "Bearer " + api_key
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def open(self, path, body=None):
        request = urllib.request.Request(self.url + path, headers=self.headers,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None)
        try:
            return self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read(8192).decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code}: {detail}") from None

    def json(self, path, body=None):
        with self.open(path, body) as response:
            return json.load(response)

    def count(self, model, messages):
        # Strata renders the same disabled-thinking template on both API dialects.
        result = self.json("/v1/messages/count_tokens", {
            "model": model, "messages": messages, "thinking": {"type": "disabled"}})
        return int(result["input_tokens"])


def make_messages(client, model, target, task):
    def messages(padding):
        content = task if not padding else (
            "Reference material for this benchmark:\n<reference_material>\n" + padding
            + "\n</reference_material>\n\nTask:\n" + task)
        return [{"role": "user", "content": content}]

    minimum = client.count(model, messages(""))
    if target == 0:
        return messages(""), minimum
    if target < minimum:
        raise ValueError(f"上下文至少需要 {minimum} token 才能放下任务；可用 --context 0 只发送任务")
    if target == minimum:
        return messages(""), minimum
    # Vary records so the context is not just one repeated word or token.
    pieces, size, index = [], 0, 0
    while size < target * 8 + 4096:
        paragraph = (
            f"Reference record {index:06d}: {TOPICS[index % len(TOPICS)]}. "
            f"Scenario {(index * 37) % 997:03d} assigns a budget of {128 + index % 2048} units. "
            "Compare correctness, throughput, and recovery behavior under load.\n")
        pieces.append(paragraph)
        size += len(paragraph)
        index += 1
    corpus = "".join(pieces)
    lo, hi, best, best_count = 1, len(corpus), messages(""), minimum
    cache = {}
    for _ in range(32):
        if lo > hi:
            break
        mid = (lo + hi) // 2
        sample = messages(corpus[:mid])
        count = cache.setdefault(mid, client.count(model, sample))
        if abs(count - target) < abs(best_count - target):
            best, best_count = sample, count
        if count == target:
            return sample, count
        if count < target:
            lo = mid + 1
        else:
            hi = mid - 1
    # Token counts can change at BPE boundaries; check the immediate neighborhood.
    for length in range(max(1, hi - 12), min(len(corpus), lo + 12) + 1):
        sample = messages(corpus[:length])
        count = cache[length] if length in cache else client.count(model, sample)
        if abs(count - target) < abs(best_count - target):
            best, best_count = sample, count
        if count == target:
            return sample, count
    if abs(best_count - target) > 2:
        raise RuntimeError(f"无法构造目标上下文：目标 {target}，最近 {best_count}")
    print(f"提示：分词边界使输入为 {best_count} token，目标为 {target}。", flush=True)
    return best, best_count


def run_request(client, body, name, show_output=False):
    started = time.perf_counter()
    done = threading.Event()
    progress = {"first": None, "chars": 0}
    def heartbeat():
        while not done.wait(5):
            elapsed = time.perf_counter() - started
            phase = "等待首段输出 / prefill" if progress["first"] is None else "生成中"
            print(f"  [{name}] {elapsed:.0f}s，{phase}，已收到 {progress['chars']} 字符", flush=True)
    worker = threading.Thread(target=heartbeat, daemon=True)
    worker.start()
    parts, reasoning = [], []
    usage, timings, finish, response_model, completed = None, None, None, None, False
    try:
        with client.open("/v1/chat/completions", body) as response:
            for line in response:
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload == b"[DONE]":
                    completed = True
                    break
                item = json.loads(payload)
                if item.get("error"):
                    raise RuntimeError(json.dumps(item["error"], ensure_ascii=False))
                response_model = item.get("model", response_model)
                usage = item.get("usage") or usage
                timings = item.get("timings") or timings
                for choice in item.get("choices", []):
                    finish = choice.get("finish_reason") or finish
                    delta = choice.get("delta", {})
                    content = delta.get("content") or ""
                    thought = delta.get("reasoning_content") or ""
                    if content or thought:
                        if progress["first"] is None:
                            progress["first"] = time.perf_counter()
                        progress["chars"] += len(content) + len(thought)
                        parts.append(content)
                        reasoning.append(thought)
                        if show_output:
                            print(content + thought, end="", flush=True)
    finally:
        done.set()
        worker.join(1)
    wall = time.perf_counter() - started
    if not completed or not usage or not timings:
        raise RuntimeError("响应不完整或缺少 Strata 原生 timings，无法可靠计算 decode")
    if not timings.get("predicted_ms") or not timings.get("predicted_n"):
        raise RuntimeError("响应缺少有效 decode token 数或耗时")
    answer, thought = "".join(parts), "".join(reasoning)
    return {
        "name": name, "model": response_model, "wall_s": round(wall, 4),
        "ttft_s": round(progress["first"] - started, 4) if progress["first"] else None,
        "decode_tok_s": round(timings["predicted_n"] / (timings["predicted_ms"] / 1000), 3),
        "input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"],
        "cached_tokens": timings.get("cache_n", 0), "finish_reason": finish,
        "usage": usage, "timings": timings,
        "output_sha256": hashlib.sha256((thought + answer).encode("utf-8")).hexdigest(),
        "answer": answer, "reasoning": thought,
    }


def summary(rows):
    measured = [row for row in rows if not row["warmup"]]
    if not measured:
        return None
    rates = [row["decode_tok_s"] for row in measured]
    tokens = sum(row["timings"]["predicted_n"] for row in measured)
    seconds = sum(row["timings"]["predicted_ms"] for row in measured) / 1000
    return {"rounds": len(measured), "median_tok_s": round(statistics.median(rates), 3),
        "mean_tok_s": round(statistics.mean(rates), 3), "min_tok_s": min(rates),
        "max_tok_s": max(rates), "stdev_tok_s": round(statistics.stdev(rates), 3) if len(rates) > 1 else 0,
        "aggregate_tok_s": round(tokens / seconds, 3),
        "early_stops": sum(row["finish_reason"] != "length" for row in measured),
        "distinct_outputs": len({row["output_sha256"] for row in measured})}


def save(path, report):
    report["summary"] = summary(report["runs"])
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    columns = ["name", "warmup", "input_tokens", "output_tokens", "cached_tokens", "decode_tok_s",
               "ttft_s", "wall_s", "finish_reason", "output_sha256"]
    with path.with_suffix(".csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report["runs"])


def main(argv=None):
    parser = argparse.ArgumentParser(description="只发送 HTTP 请求的 Strata decode 测试；不修改 GPU 或服务器设置。")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="服务地址")
    parser.add_argument("--api-key", default="123", help="API 密钥（默认本机现有密钥）")
    parser.add_argument("--model", default="strata_compress", help="模型或别名，默认关闭思考的 strata_compress")
    parser.add_argument("--context", "--context-tokens", type=token_count, default=4096,
                        help="总输入 token 数，含模板；例如 4k/32k；0=只发送任务")
    parser.add_argument("--output", "--output-tokens", type=token_count, default=1024, help="最大输出 token 数")
    parser.add_argument("--rounds", "--repeats", type=int, default=3, help="正式测试轮数")
    parser.add_argument("--warmup", type=int, default=1, help="热身轮数，不计入统计")
    parser.add_argument("--seed", type=int, default=42, help="固定随机种子")
    parser.add_argument("--interval", type=float, default=0, help="请求之间间隔秒数")
    parser.add_argument("--timeout", type=float, default=600, help="HTTP 读取超时秒数")
    parser.add_argument("--label", default="manual", help="结果标签，例如 stock 或 mem1000")
    parser.add_argument("--prompt-file", type=Path, help="可选：自定义生成任务的 UTF-8 文本文件")
    parser.add_argument("--result-dir", type=Path, default=ROOT / "local-upgrade/decode-bench", help="结果目录")
    parser.add_argument("--show-output", action="store_true", help="实时显示生成文本")
    parser.add_argument("--dry-run", action="store_true", help="构造并计数输入，保存请求，不运行模型")
    args = parser.parse_args(argv)
    if args.output < 1 or args.rounds < 1 or args.warmup < 0 or args.seed < 1:
        parser.error("output、rounds、seed 必须大于 0，warmup 不能小于 0")
    if not math.isfinite(args.interval) or args.interval < 0 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("interval 必须 >=0，timeout 必须 >0，且均为有限数值")
    client = Client(args.url, args.api_key, args.timeout)
    status = client.json("/health")
    capacity = status.get("max_context")
    if capacity and args.context + args.output > capacity:
        parser.error(f"输入 {args.context} + 输出 {args.output} 超过服务器上下文上限 {capacity}")
    print(f"连接 {client.url}；实际模型 {status.get('model')}；请求别名 {args.model}", flush=True)
    print(f"准备输入 {args.context} token，输出上限 {args.output}，热身 {args.warmup}，正式 {args.rounds} 轮。", flush=True)
    task = args.prompt_file.read_text(encoding="utf-8-sig").strip() if args.prompt_file else DEFAULT_PROMPT
    messages, counted = make_messages(client, args.model, args.context, task)
    if capacity and counted + args.output > capacity:
        parser.error(f"实际输入 {counted} + 输出 {args.output} 超过服务器上下文上限 {capacity}")
    body = {"model": args.model, "messages": messages, "temperature": 0, "seed": args.seed,
            "reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False},
            "max_tokens": args.output, "stream": True, "stream_options": {"include_usage": True}}
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    label = re.sub(r"[^\w.-]+", "_", args.label) or "manual"
    args.result_dir.mkdir(parents=True, exist_ok=True)
    path = args.result_dir / f"{stamp}-{label}-ctx{counted}-out{args.output}.json"
    report = {"label": args.label, "started_at": datetime.now().isoformat(), "endpoint": client.url,
              "server": status, "requested_context_tokens": args.context, "counted_input_tokens": counted,
              "request": body, "warmup_rounds": args.warmup, "requested_rounds": args.rounds,
              "cache_policy": "Reuse the identical input; report cached token counts. No cache reset.",
              "runs": []}
    save(path, report)
    print(f"输入计数 {counted} token。结果：{path}", flush=True)
    if args.dry_run:
        print("dry-run 完成，未发送生成请求。", flush=True)
        return 0
    try:
        for index in range(args.warmup + args.rounds):
            warmup = index < args.warmup
            name = f"warmup-{index + 1}" if warmup else f"round-{index - args.warmup + 1}"
            print(f"[{name}] 开始，输出上限 {args.output} token...", flush=True)
            row = run_request(client, body, name, args.show_output)
            row["warmup"] = warmup
            report["runs"].append(row)
            save(path, report)
            print(f"\n[{name}] decode {row['decode_tok_s']:.2f} tok/s | 输入 {row['input_tokens']} | "
                  f"输出 {row['output_tokens']} | TTFT {row['ttft_s']}s | 总耗时 {row['wall_s']:.2f}s | "
                  f"缓存 {row['cached_tokens']} | {row['finish_reason']}", flush=True)
            if row["input_tokens"] != counted:
                print(f"提示：实际输入 {row['input_tokens']} 与计数 {counted} 不同，请按实际输入比较。", flush=True)
            if row["finish_reason"] != "length":
                print("提示：模型提前结束；该轮实际输出短于上限，报告已记录。", flush=True)
            if args.interval and index + 1 < args.warmup + args.rounds:
                time.sleep(args.interval)
    except (KeyboardInterrupt, Exception) as exc:
        report["error"] = "用户中断" if isinstance(exc, KeyboardInterrupt) else str(exc)
        save(path, report)
        print(f"\n测试停止：{report['error']}。已有结果保存在 {path}", file=sys.stderr)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1
    stats = report["summary"]
    print(f"\n正式 {stats['rounds']} 轮：中位数 {stats['median_tok_s']:.2f} tok/s；"
          f"均值 {stats['mean_tok_s']:.2f}；范围 {stats['min_tok_s']:.2f}～{stats['max_tok_s']:.2f}。")
    print(f"JSON（含逐轮原生 timings、输出）及 CSV 已保存：{path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        print(f"测试未能开始：{exc}", file=sys.stderr)
        raise SystemExit(1)
