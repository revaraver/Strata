r"""Install the dedicated compact T5 English->Chinese CPU INT8 translator.
Windows: C:\Users\revar\AppData\Local\Programs\Python\Python313\python.exe tools\setup_nmt_translation.py
PyTorch is needed only in an isolated temporary conversion environment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = "utrobinmv/t5_translate_en_ru_zh_small_1024"
REVISION = "bb215079c5910bee0154f7f6c1350d57502c5d97"
FILES = ("config.json", "generation_config.json", "model.safetensors", "spiece.model",
         "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json")
DEST = ROOT / "pack/translation/t5-en-zh-small-ct2"


def download_source(source):
    source.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(f"https://huggingface.co/api/models/{REPO}/tree/{REVISION}",
                                     headers={"User-Agent": "Strata-CPU-NMT"})
    with urllib.request.urlopen(request, timeout=30) as response:
        manifest = {entry["path"]: entry for entry in json.load(response)}
    for name in FILES:
        path = source / name
        info = manifest[name]
        if not path.exists() or path.stat().st_size != info["size"]:
            partial = path.with_suffix(path.suffix + ".part")
            offset = partial.stat().st_size if partial.exists() else 0
            url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
            req = urllib.request.Request(url, headers={"User-Agent": "Strata-CPU-NMT", **({"Range": f"bytes={offset}-"} if offset else {})})
            with urllib.request.urlopen(req, timeout=60) as response:
                if offset and response.status != 206:
                    offset = 0
                with partial.open("ab" if offset else "wb") as out:
                    for chunk in iter(lambda: response.read(1024 * 1024), b""):
                        out.write(chunk)
            if partial.stat().st_size != info["size"]:
                raise RuntimeError(f"Incomplete {name}; run again to resume")
            partial.replace(path)
        digest = info.get("lfs", {}).get("oid")
        if digest:
            checksum = hashlib.sha256()
            with path.open("rb") as data:
                for chunk in iter(lambda: data.read(1024 * 1024), b""):
                    checksum.update(chunk)
            if checksum.hexdigest() != digest:
                raise RuntimeError(f"Checksum mismatch: {name}")
        print(f"Downloaded {name}: {path.stat().st_size / 2**20:.1f} MiB", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--convert", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    work = ROOT / "pack/translation/.t5-install"
    source = work / "source"
    if args.convert:
        from ctranslate2.converters import TransformersConverter
        converter = TransformersConverter(str(source), copy_files=["spiece.model", "tokenizer_config.json"])
        converter.convert(str(DEST), quantization="int8", force=True)
        (DEST / "SOURCE.json").write_text(json.dumps({"repo":REPO, "revision":REVISION,
            "license":"Apache-2.0", "engine":"CTranslate2 CPU INT8"}, indent=2), encoding="utf-8")
        return
    if not args.download_only:
        subprocess.run([sys.executable, "-m", "pip", "install", "--only-binary=:all:",
                        "ctranslate2==4.8.2", "sentencepiece==0.2.2"], check=True)
    if all((DEST / name).is_file() for name in ("model.bin", "config.json", "shared_vocabulary.json", "spiece.model")):
        print("CPU INT8 T5 translator is already installed.", flush=True)
        return
    download_source(source)
    if args.download_only:
        print("Source downloaded; run this installer on Windows to convert it.", flush=True)
        return
    if os.name != "nt":
        raise RuntimeError("This installer targets Windows; use --download-only from WSL")
    venv = work / "converter"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    python = venv / "Scripts/python.exe"
    subprocess.run([str(python), "-m", "pip", "install", "--only-binary=:all:",
        "torch==2.14.1", "transformers==4.57.3", "ctranslate2==4.8.2", "sentencepiece==0.2.2", "protobuf"], check=True)
    subprocess.run([str(python), "-X", "utf8", str(Path(__file__).resolve()), "--convert"], check=True)
    shutil.rmtree(work)
    print("Dedicated T5 NMT installed. Temporary checkpoint and converter removed; inference uses CPU INT8 only.", flush=True)


if __name__ == "__main__":
    main()
