"""The web page's validated view of the run config (GET / POST /config).

Only documented settings are editable. Network, credentials, executables and other
arguments are preserved. Saving is atomic and keeps <config>.bak. Request and service
settings apply live; engine/startup settings require a restart, which clears RAM conversation state.
"""
from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path

# (key, storage/validation, help). Argument switches and pairs live in args;
# the web page must never replace the entire argument list.
EDITABLE = [
    ("parallel", "int>=1", "同时生成的最大请求数；1 为单路，建议先用 2 或 4。每路有独立状态并占用额外显存。"),
    ("batch_mtp", ("flag", "--batch-mtp"), "并行时启用 MTP；每路每轮验证一个草稿。单路时仍使用原来的 MTP 窗口。"),
    ("conversation_cache_mib", ("arg", "--conversation-cache-mib"), "切换对话时保存完整状态的 RAM 上限，单位 MiB；32768 = 32 GiB，0 或留空关闭。按需占用，退出服务后清空。"),
    ("conversation_cache_slots", ("arg", "--conversation-cache-slots"), "RAM 中最多停放的对话数，引擎默认 4，本机设置为 8。实际复用的会话切走后重新放到队尾；槽位或 RAM 预算不足时，从队首淘汰最久未用的普通快照。没有时间过期，显式固定的共享前缀保留。"),
    ("conversation_cache_min_free_mib", ("arg", "--conversation-cache-min-free-mib"), "保存／恢复缓存时要求的最低可用系统 RAM，单位 MiB；8192 = 8 GiB。"),
    ("sampling.temperature", ("sampling", "num>=0"), "客户端未指定时的温度；0 为贪心采样。"),
    ("sampling.top_p", ("sampling", "0<x<=1"), "客户端未指定时的 Top-p，范围大于 0 且不超过 1。"),
    ("sampling.top_k", ("sampling", "1..64"), "客户端未指定时的 Top-k，范围 1～64。"),
    ("sampling.min_p", ("sampling", "0<=x<=1"), "客户端未指定时的 Min-p，范围 0～1。"),
    ("reasoning_budget_tokens", "int>=0", "每条请求的思考 token 上限；0 或留空表示不限制，不代表关闭思考。"),
    ("fit_max_tokens", "bool", "当输出额度超过剩余上下文时，自动缩短额度，避免返回 HTTP 400。"),
    ("anthropic_thinking", ("enum", ["model", "on_request"]), "Anthropic 客户端未要求思考时：按模型默认，或仅在明确请求时思考。"),
    ("effort_position", ("enum", ["start", "end"]), "思考强度标记放在提示词开头或末尾；放末尾可减少切换强度造成的前缀缓存失效。"),
    ("aliases", "names", "服务接受并列出的其他模型名称，用英文逗号分隔。"),
    ("model_profiles", "profiles", "分身模型：共用当前已加载模型的其他名称，每个可单独设置思考开关、思考预算和思考强度；保存后热生效并列入 /v1/models。明确设置优先于客户端与共享设置；留空项沿用原行为。普通别名仍独立保留。"),
    ("idle_unload_s", "num>=0", "空闲多少秒后卸载模型；0 或留空表示保持加载。"),
    ("lazy_load", "bool", "启动时暂不加载，由首次请求加载；仅支持纯文字配置。"),
    ("engine_silence_s", "num>=0", "引擎连续无响应多少秒后终止请求；默认 300 秒，0 表示一直等待。"),
    ("api_monitor", "bool", "在内存中保存最近 100 条 API 请求的输入和输出，用于 API 监视器。"),
    ("open_browser", "bool", "模型准备好后自动打开聊天网页。"),
    ("vram_reserve_mib", ("arg", "--vram-reserve-mib"), "为桌面及其他程序预留的显存，单位 MiB；引擎默认 700，当前机器建议保留 4096。"),
]
SPEC = {k: kind for k, kind, _ in EDITABLE}
HOT_KEYS = frozenset({"sampling.temperature", "sampling.top_p", "sampling.top_k", "sampling.min_p",
                      "reasoning_budget_tokens", "fit_max_tokens", "anthropic_thinking", "aliases",
                      "idle_unload_s", "engine_silence_s", "api_monitor", "model_profiles"})
LABELS = {
    "parallel": "并行请求数", "batch_mtp": "并行 MTP 加速",
    "conversation_cache_mib": "RAM 对话缓存预算（MiB）",
    "conversation_cache_slots": "RAM 缓存对话数",
    "conversation_cache_min_free_mib": "最低可用 RAM（MiB）",
    "sampling.temperature": "采样温度", "sampling.top_p": "Top-p 概率阈值",
    "sampling.top_k": "Top-k 候选数量", "sampling.min_p": "Min-p 概率阈值",
    "reasoning_budget_tokens": "思考预算（token）", "fit_max_tokens": "自动适配输出额度",
    "anthropic_thinking": "Anthropic 默认思考行为", "effort_position": "思考强度标记位置",
    "aliases": "模型别名", "model_profiles": "分身模型", "idle_unload_s": "空闲卸载时间（秒）",
    "lazy_load": "首次请求时加载", "engine_silence_s": "引擎静默超时（秒）",
    "api_monitor": "保留 API 请求记录", "open_browser": "启动后打开网页",
    "vram_reserve_mib": "显存预留（MiB）",
}
CHOICE_LABELS = {
    "anthropic_thinking": {"model": "按模型默认", "on_request": "仅在请求中启用"},
    "effort_position": {"start": "提示词开头", "end": "提示词末尾"},
}
# 分身模型的思考强度：与 serve/frontend.py 的 EFFORT 同一套拼写，存成规范值。
PROFILE_EFFORT = {"none": None, "off": None, "minimal": None, "disabled": None, "false": None,
                  "low": "low", "medium": "medium", "high": "high", "xhigh": "high", "max": "high", "maximum": "high"}


def clean_model_profiles(profiles, base_model, aliases) -> list | None:
    """Validate and normalize 分身模型 entries -> a list of {"name", "thinking", "reasoning_effort",
    "reasoning_budget_tokens"} dicts (None when empty). Names must be unique and must not collide with the
    model's own name or its aliases; thinking: inherit/off/on; effort: none/low/medium/high; budget: int >= 0."""
    if profiles is None:
        return None
    if not isinstance(profiles, list):
        raise ValueError(f"model_profiles：需要对象列表；收到 {profiles!r}")
    taken = {x.strip() for x in (aliases or []) if isinstance(x, str) and x.strip()}
    if isinstance(base_model, str) and base_model.strip():
        taken.add(base_model.strip())
    out = []
    for i, p in enumerate(profiles):
        if not isinstance(p, dict):
            raise ValueError(f"model_profiles[{i}]：需要对象；收到 {p!r}")
        name = p.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 128:
            raise ValueError(f"model_profiles[{i}].name：需要 1～128 字符的名称；收到 {name!r}")
        name = name.strip()
        if name in taken:
            raise ValueError(f"model_profiles[{i}].name：{name!r} 与模型名或别名重复")
        taken.add(name)
        thinking = p.get("thinking")
        if thinking not in (None, "inherit", "off", "on"):
            raise ValueError(f"model_profiles[{i}].thinking：可选 inherit、off、on；收到 {thinking!r}")
        effort = p.get("reasoning_effort")
        if effort is not None:
            key = str(effort).strip().lower()
            if key not in PROFILE_EFFORT:
                raise ValueError(f"model_profiles[{i}].reasoning_effort：可选 none、low、medium、high；收到 {effort!r}")
            if PROFILE_EFFORT[key] is None:
                thinking = "off"
            else:
                effort = PROFILE_EFFORT[key]
        budget = p.get("reasoning_budget_tokens")
        if budget is not None:
            if isinstance(budget, float) and budget.is_integer():
                budget = int(budget)
            if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
                raise ValueError(f"model_profiles[{i}].reasoning_budget_tokens：需要不小于 0 的整数；收到 {budget!r}")
        prof = {"name": name}
        if thinking in ("off", "on"):
            prof["thinking"] = thinking
        if effort is not None and thinking != "off":
            prof["reasoning_effort"] = effort
        if budget is not None and thinking != "off":
            prof["reasoning_budget_tokens"] = budget
        out.append(prof)
    return out or None


def _arg(cfg: dict, flag: str):
    a = cfg.get("args") or []
    if not isinstance(a, list):
        a = []
    # Consistent with CLI overrides: the last occurrence wins.
    found = None
    for i, item in enumerate(a[:-1]):
        if item == flag:
            v = str(a[i + 1])
            found = int(v) if v.isdigit() else v
    return found


def value_of(cfg: dict, key: str):
    kind = SPEC[key]
    if isinstance(kind, tuple) and kind[0] == "sampling":
        s = cfg.get("sampling")
        return s.get(key.split(".", 1)[1]) if isinstance(s, dict) else None
    if isinstance(kind, tuple) and kind[0] == "arg":
        return _arg(cfg, kind[1])
    if isinstance(kind, tuple) and kind[0] == "flag":
        return kind[1] in (cfg.get("args") or [])
    if key == "parallel":
        return cfg.get(key, _arg(cfg, "--batch"))
    return cfg.get(key)


def restart_values(cfg: dict) -> dict:
    return {key: value_of(cfg, key) for key in SPEC if key not in HOT_KEYS}


def view(cfg: dict, path: str | Path, startup_values: dict | None = None) -> dict:
    out = []
    for key, kind, help_ in EDITABLE:
        k = kind[0] if isinstance(kind, tuple) else kind
        number = k in ("sampling", "arg", "int>=0", "int>=1", "num>=0")
        whole = k in ("arg", "int>=0", "int>=1") or key == "sampling.top_k"
        group = "并行与对话缓存" if key in LABELS and (key == "parallel" or key == "batch_mtp" or key.startswith("conversation_cache_")) else \
                "采样与思考" if key.startswith("sampling.") or key in ("reasoning_budget_tokens", "fit_max_tokens", "anthropic_thinking", "effort_position") else "服务与资源"
        if key == "model_profiles":
            group = "分身模型"
        out.append({"key": key, "label": LABELS[key], "group": group,
                    "value": value_of(cfg, key), "help": help_,
                    "apply_mode": "hot" if key in HOT_KEYS else "restart",
                    "kind": "number" if number else "bool" if k == "flag" else k,
                    **({"min": 1 if k == "int>=1" or key in ("sampling.top_k", "conversation_cache_slots") else 0,
                        "step": 1 if whole else "any"} if number else {}),
                    **({"choices": kind[1], "choice_labels": CHOICE_LABELS.get(key, {})} if k == "enum" else {})})
    pending = [key for key, value in restart_values(cfg).items()
               if startup_values is not None and value != startup_values.get(key)]
    return {"file": Path(path).name, "keys": out, "hot_reload_supported": True,
            "restart_pending": pending,
            "note": "保存并备份配置；热生效项用于后续请求，需重启项在整个 Strata 服务重启后生效。客户端或共享设置中的明确值优先；RAM 对话缓存不跨重启保留。"}


def _number(key, v, whole=False, lo=0.0, hi=None, lo_open=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or \
            (whole and v != int(v)) or (v <= lo if lo_open else v < lo) or (hi is not None and v > hi):
        rng = f"{'大于' if lo_open else '不小于'} {lo:g}" + (f"，且不大于 {hi:g}" if hi is not None else "")
        raise ValueError(f"{key}：需要{'整数' if whole else '数值'}，{rng}；收到 {v!r}")
    return int(v) if whole else v


def check(key: str, v, cfg: dict):
    if key not in SPEC:
        raise ValueError(f"不能在此修改 {key!r}；仅允许编辑设置列表中的参数。")
    if v is None:
        return None
    kind = SPEC[key]
    k = kind[0] if isinstance(kind, tuple) else kind
    if k in ("bool", "flag"):
        if not isinstance(v, bool):
            raise ValueError(f"{key}：需要 true 或 false，收到 {v!r}")
        if key == "lazy_load" and v and cfg.get("vision"):
            raise ValueError("lazy_load：仅支持纯文字；当前配置启用了图片识别（vision）。")
        return v
    if k == "enum":
        if v not in kind[1]:
            raise ValueError(f"{key}：可选值为 {', '.join(kind[1])}；收到 {v!r}")
        return v
    if k == "names":
        if isinstance(v, str):
            v = [x.strip() for x in v.split(",") if x.strip()]
        if not isinstance(v, list) or not all(isinstance(x, str) and x.strip() and len(x) <= 128 for x in v):
            raise ValueError(f"{key}：需要模型名称列表；收到 {v!r}")
        return v or None
    if k == "profiles":
        return clean_model_profiles(v, cfg.get("model_name"), cfg.get("aliases"))
    if k in ("int>=0", "int>=1"):
        return _number(key, v, whole=True, lo=1 if k == "int>=1" else 0)
    if k == "num>=0":
        return _number(key, v)
    if k == "arg":
        return _number(key, v, whole=True, lo=1 if key == "conversation_cache_slots" else 0)
    rule = kind[1]
    if rule == "num>=0":
        return float(_number(key, v))
    if rule == "0<x<=1":
        return float(_number(key, v, hi=1, lo_open=True))
    if rule == "0<=x<=1":
        return float(_number(key, v, hi=1))
    return _number(key, v, whole=True, lo=1, hi=64)


def _set_arg(a: list, flag: str, v, switch=False):
    # Canonicalize duplicates while preserving all unrelated arguments and order.
    out, i = [], 0
    while i < len(a):
        if a[i] == flag:
            i += 1
            if not switch and i < len(a) and not str(a[i]).startswith("--"):
                i += 1
        else:
            out.append(a[i]); i += 1
    if switch:
        if v:
            out.append(flag)
    elif v is not None:
        out += [flag, str(v)]
    return out


def apply(cfg: dict, changes: dict) -> tuple[dict, list[str]]:
    if not isinstance(changes, dict) or not changes:
        raise ValueError('请提交 {"set": {"参数名": 值或null, ...}}')
    checked = {k: check(k, v, cfg) for k, v in changes.items()}
    new = json.loads(json.dumps(cfg))
    changed = []
    for key, v in checked.items():
        kind = SPEC[key]
        is_flag = isinstance(kind, tuple) and kind[0] == "flag"
        if value_of(new, key) == (bool(v) if is_flag else v):
            # A legacy --batch override must still be removed when explicitly setting parallel=1.
            if key != "parallel" or "--batch" not in new.get("args", []):
                continue
        if isinstance(kind, tuple) and kind[0] == "sampling":
            s = dict(new.get("sampling") or {}) if isinstance(new.get("sampling"), dict) else {}
            sub = key.split(".", 1)[1]
            if v is None:
                s.pop(sub, None)
            else:
                s[sub] = v
            if s:
                new["sampling"] = s
            else:
                new.pop("sampling", None)
        elif isinstance(kind, tuple) and kind[0] in ("arg", "flag"):
            new["args"] = _set_arg(list(new.get("args") or []), kind[1], v, is_flag)
        else:
            if key == "parallel" and "--batch" in new.get("args", []):
                new["args"] = _set_arg(new["args"], "--batch", None)
            if v is None:
                new.pop(key, None)
            else:
                new[key] = v
        changed.append(key)
    # a combined save must not let a 分身 collide with aliases changed in the same request
    clean_model_profiles(new.get("model_profiles"), new.get("model_name"), new.get("aliases"))
    return new, changed


def save(path: str | Path, cfg: dict) -> Path:
    path = Path(path)
    bak = path.with_name(path.name + ".bak")
    shutil.copyfile(path, bak)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)
    return bak


def load(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(cfg, dict):
        raise ValueError(f"{Path(path).name} 不是 JSON 对象")
    return cfg
