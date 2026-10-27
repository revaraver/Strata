"""serve/video.py - native video input: a {"type":"video"} part becomes timestamped frames.

The engine splices image embeddings only (serve/server.py's GENI path reads one record per
<|image_pad|> run), and the model's chat template writes a single <|video_pad|> block for a video
item - a token this GGUF does not carry as a special.  So the server expands a video into what the
pipeline already speaks: one text part with the frame's time ("t=MM:SS") followed by one image part
per frame, exactly the interleaving Qwen's own video processor produces.  Frames are JPEGs ffmpeg
writes into a per-source cache directory, so a client that resends the video every turn re-encodes
nothing: ffmpeg runs once per (source, params), and serve/server.py's image hash cache then skips
the GPU re-encode as well.

Request shape (OpenAI content part, QwenCloud-style):
    {"type": "video", "video": "E:\\videos\\clip.mp4", "fps": 2}          local path
    {"type": "video_url", "video_url": {"url": "https://.../a.mp4"}}      http(s) download
    {"type": "video", "video": ..., "fps": 1, "max_video_tokens": 8000}  per-request budget

Budget: a frame costs about (scaled_w/28) * (scaled_h/28) image tokens (Qwen's 14px patches with a
2x merge), so the frame count is capped by max_video_tokens / that, by max_frames, and by fps *
duration - whichever bites first.  The default 32768 tokens is a deliberate third of a 262K window:
the conversation keeps room to answer, and the parked snapshot of a video turn pays ~4 GB of KV,
not 27.

The config's "video" section (all keys optional; video input works with no section at all):
    {"fps": 2.0, "max_video_tokens": 32768, "max_frames": 64, "max_side": 1280,
     "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "timeout_s": 180}
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

DEFAULTS = {"fps": 2.0, "max_video_tokens": 32768, "max_frames": 64, "max_side": 1280,
            "ffmpeg": "ffmpeg", "ffprobe": "ffprobe", "timeout_s": 180}

_CACHE_ROOT = Path(tempfile.gettempdir())


def _cfg(vcfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    if vcfg:
        out.update({k: v for k, v in vcfg.items() if k in DEFAULTS})
    return out


def _find(tool: str) -> str:
    """An absolute path for ffmpeg/ffprobe: PATH, then the usual Windows homes (a service started from a
    bat does not inherit an interactive shell's PATH; ffmpeg installed by another tool lands in its own
    directory).  A missing tool is a clean ValueError, not a FileNotFoundError from subprocess."""
    found = shutil.which(tool)
    if found:
        return found
    roots = [Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "tools",
             Path("C:/ffmpeg/bin"), Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32"]
    for root in roots:
        try:
            for cand in sorted(root.rglob(f"{tool}.exe"), reverse=True):     # newest version dir first
                return str(cand)
        except OSError:
            continue
    raise ValueError(f"{tool} was not found: install ffmpeg or name it in the config's \"video\" section")


def _cache_dir(key: str) -> Path:
    d = _CACHE_ROOT / f"strata-video-{key}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _drop_stale_cache(keep: int = 24) -> None:
    """Best effort: old extraction directories from earlier runs/params go first; frames are
    reproducible, so deleting one that is still wanted only costs one ffmpeg re-run."""
    try:
        dirs = sorted((p for p in _CACHE_ROOT.glob("strata-video-*") if p.is_dir()),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for p in dirs[keep:]:
            shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def probe(src: str, cfg: dict) -> tuple[float, int, int]:
    """-> (duration_s, width, height)."""
    out = subprocess.run([_find(cfg["ffprobe"]), "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", src],
                         capture_output=True, text=True, timeout=cfg["timeout_s"])
    if out.returncode != 0:
        raise ValueError(f"ffprobe could not read the video: {(out.stderr or out.stdout).strip()[:200]}")
    data = json.loads(out.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    dur = float((data.get("format") or {}).get("duration") or stream.get("duration") or 0)
    w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if dur <= 0 or w <= 0 or h <= 0:
        raise ValueError("ffprobe reported no video stream")
    return dur, w, h


def tokens_per_frame(w: int, h: int, max_side: int) -> int:
    """The image-token cost of one frame after the scale filter: Qwen's patches are 14px with a
    2x merge, so one token covers 28px of each side (rounded up)."""
    if w <= 0 or h <= 0:
        return 1196     # an unknown source is planned as if it were 720p at 1280
    scale = min(1.0, max_side / max(w, h))
    sw, sh = max(1, int(w * scale)), max(1, int(h * scale))
    return -(-sw // 28) * -(-sh // 28)


def plan(duration: float, w: int, h: int, item: dict, cfg: dict) -> tuple[float, int, int]:
    """-> (fps, n_frames, max_side) for one video part: fps, budget and the frame cap all want a
    different count; the smallest wins, and fps is then re-derived so the frames spread over the
    whole video instead of clustering at its start."""
    fps = float(item.get("fps") or cfg["fps"])
    budget = int(item.get("max_video_tokens") or cfg["max_video_tokens"])
    max_side = int(item.get("max_side") or cfg["max_side"])
    per = tokens_per_frame(w, h, max_side)
    by_budget = max(1, budget // per)
    by_fps = max(1, int(fps * duration))
    n = min(by_budget, by_fps, int(cfg["max_frames"]))
    return (n / duration if duration > 0 else fps), n, max_side


def frames(src: str, item: dict, vcfg: dict | None, log=None) -> tuple[list[str], dict]:
    """-> (frame paths in time order, stats).  Cached by source identity + parameters."""
    cfg = _cfg(vcfg)
    local = src
    if src.startswith(("http://", "https://")):
        local = str(_download(src, cfg))
    path = Path(local)
    if not path.exists():
        raise ValueError(f"the video file does not exist: {local}")
    stat = path.stat()
    dur, w, h = probe(local, cfg)
    fps, n, max_side = plan(dur, w, h, item, cfg)
    key = hashlib.sha1(f"{local}|{stat.st_mtime_ns}|{stat.st_size}|{fps:.6f}|{n}|{max_side}".encode()).hexdigest()[:16]
    d = _cache_dir(key)
    existing = sorted(d.glob("frame*.jpg"))
    if len(existing) == n:
        paths = [str(p) for p in existing]
        fresh = False
    else:
        for p in existing:
            p.unlink(missing_ok=True)
        cmd = [_find(cfg["ffmpeg"]), "-hide_banner", "-loglevel", "error", "-i", local,
               "-vf", f"fps={fps:.6f},scale='min({max_side},iw)':-2", "-frames:v", str(n),
               "-q:v", "3", str(d / "frame%04d.jpg")]
        t0 = time.time()
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg["timeout_s"])
        if out.returncode != 0:
            raise ValueError(f"ffmpeg could not read the video: {(out.stderr or '').strip()[:200]}")
        paths = [str(p) for p in sorted(d.glob("frame*.jpg"))]
        if not paths:
            raise ValueError("ffmpeg produced no frames")
        fresh = True
        if log:
            log(f"[strata] video: {n} frames from {dur:.0f}s in {time.time() - t0:.1f}s "
                f"({tokens_per_frame(w, h, max_side)} tokens/frame)")
    stats = {"frames": len(paths), "duration_s": round(dur, 1), "fps": round(fps, 3),
             "tokens_per_frame": tokens_per_frame(w, h, max_side),
             "video_tokens": len(paths) * tokens_per_frame(w, h, max_side), "extracted": fresh}
    return paths, stats


def _stamp(t: float) -> str:
    s = int(round(t))
    return f"t={s // 60:02d}:{s % 60:02d}"


def _video_source(item: dict) -> str | None:
    v = item.get("video") or item.get("video_url")
    if isinstance(v, dict):
        v = v.get("url")
    return v if isinstance(v, str) and v else None


def _download(url: str, cfg: dict) -> Path:
    suffix = Path(url.split("?")[0]).suffix or ".mp4"
    d = _cache_dir("download")
    dest = d / (hashlib.sha1(url.encode()).hexdigest()[:16] + suffix)
    if not dest.exists():
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(url, timeout=cfg["timeout_s"]) as r, open(tmp, "wb") as f:
            while True:
                block = r.read(1 << 20)
                if not block:
                    break
                f.write(block)
        tmp.replace(dest)
    return dest


def expand_video_parts(messages: list, vcfg: dict | None, log=None) -> tuple[list, list]:
    """-> (messages with video parts replaced by timestamped frames, per-video stats).  Messages
    without a video part are passed through untouched (same objects)."""
    has_video = any(isinstance(m.get("content"), list) and
                    any(isinstance(i, dict) and (_video_source(i) is not None)
                        for i in m["content"])
                    for m in messages if isinstance(m, dict))
    if not has_video:
        return messages, []
    _drop_stale_cache()
    all_stats, out_messages = [], []
    for m in messages:
        content = m.get("content") if isinstance(m, dict) else None
        if not isinstance(content, list) or not any(isinstance(i, dict) and _video_source(i) is not None
                                                    for i in content):
            out_messages.append(m)
            continue
        parts = []
        for item in content:
            src = _video_source(item) if isinstance(item, dict) else None
            if src is None:
                parts.append(item)
                continue
            paths, stats = frames(src, item, vcfg, log)
            all_stats.append(stats)
            fps = stats["fps"] or 1.0
            for j, path in enumerate(paths):
                parts.append({"type": "text", "text": _stamp(j / fps)})
                parts.append({"type": "image", "source": path})
        m2 = dict(m)
        m2["content"] = parts
        out_messages.append(m2)
    return out_messages, all_stats
