#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
#  Nova Upinel Chow, MSc, LLM, BBA, MENSA  ·  dev@upinel.com  ·  upinel.com
#  Copyright (c) 2026 Nova Upinel Chow. All rights reserved.
#
#  Upinel Personal Free License: free for personal use, and free for creators
#  (YouTubers, KOLs) to make content with - just email dev@upinel.com to say so.
#  Other commercial use needs written permission. Derivatives must credit the
#  author. Covers this project's own code only. See LICENSE.
#
#  "Make it work, make it right, make it fast - then measure it, because
#   the third one is only a claim until the numbers agree."
# ─────────────────────────────────────────────────────────────────────────────
"""Regression tests for the memory guard, and for how it prices a model.

Two bugs lived here, and they compounded.

The first: the guard added a flat 3 GB of headroom to the requirement and then
refused to start if the total exceeded the machine's RAM. So a 16 GB Mac was
told "Plan needs ~18 GB but this Mac has 16 GB. It will not load." for a model
whose weights and cache together needed 15 GB - and that model loaded fine in
LM Studio. A guess about headroom was being enforced as a hard limit.

The second: for any repo id the table did not know - which is every custom
model, the case the registry was least able to serve - the KV cost fell back to
a flat 64 KB per token. That is a 27B hybrid's cache. An 8B model with 8 KV
heads was priced at twice its real cache, which is often the whole reason a
plan crossed the line.

So these tests cover both halves: that the verdict separates what a model NEEDS
from what it would like, and that the cost is read from the model's own weights
rather than from a fallback that only fits this project's own models.

The GGUFs are synthetic - a real header, no tensors - so this runs offline in
well under a second and needs no downloads.

Run:  python3 bench/verify-memory-guard.py
"""

import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

SHIM_VERDICT = r'''
source "$1/lib/common.sh"
load_config
MEMORY_LIMIT_GB="$2"
load_plan_verdict "$3" "$4" "$5"
'''

SHIM_KV = r'''
source "$1/lib/common.sh"
load_config
kv_kb_from_gguf "$2"
'''

SHIM_DIR = r'''
source "$1/lib/common.sh"
load_config
MODEL_REPO="$3"
kv_kb_for_model_dir "$2" "$4"
'''

PASSED = 0
FAILED = 0

# ── a minimal but real GGUF header ───────────────────────────────────────────
U8, U32, I32, F32, BOOL, STR, ARR = 0, 4, 5, 6, 7, 8, 9


def _val(t, v):
    if t == STR:
        b = v.encode()
        return struct.pack("<Q", len(b)) + b
    if t == ARR:
        et, items = v
        out = struct.pack("<IQ", et, len(items))
        for it in items:
            out += _val(et, it)
        return out
    fmt = {U8: "<B", U32: "<I", I32: "<i", F32: "<f", BOOL: "<?", 10: "<Q"}[t]
    return struct.pack(fmt, v)


def make_gguf(path, meta, magic=b"GGUF"):
    """meta: list of (key, type, value) - written in order, as llama.cpp does."""
    out = magic + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(meta))
    for k, t, v in meta:
        kb = k.encode()
        out += struct.pack("<Q", len(kb)) + kb + struct.pack("<I", t) + _val(t, v)
    with open(path, "wb") as fh:
        fh.write(out)


def bash(shim, *args):
    return subprocess.run(["bash", "-c", shim, "shim", REPO, *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        print(f"  [PASS] {label}")
        PASSED += 1
    else:
        print(f"  [FAIL] {label}\n         got  {got!r}\n         want {want!r}")
        FAILED += 1


def verdict(cap, weights, kv, ram):
    r = bash(SHIM_VERDICT, str(cap), str(weights), str(kv), str(ram))
    return r.stdout.strip()


def kv_of(path):
    return bash(SHIM_KV, path).stdout.strip()


def kv_for_dir(path, repo, engine="gguf"):
    return bash(SHIM_DIR, path, repo, engine).stdout.strip()


def qwen_meta(blocks, interval, kv_heads, head_dim, arch="qwen35"):
    return [
        ("general.architecture", STR, arch),
        (f"{arch}.block_count", U32, blocks),
        (f"{arch}.full_attention_interval", U32, interval),
        (f"{arch}.attention.head_count_kv", U32, kv_heads),
        (f"{arch}.attention.key_length", U32, head_dim),
    ]


def main():
    print("\n  Memory guard: what a model needs, and what it is told it needs\n")
    root = tempfile.mkdtemp(prefix="memguard-")
    try:
        # ── the reported case ─────────────────────────────────────────────
        # 13 GB of weights + 2 GB of cache on a 16 GB Mac. The old guard called
        # this an 18 GB plan and refused. The weight and cache alone are 15 GB,
        # so it is tight - it pages - but it is not impossible, and it must not
        # stop the server from starting.
        check("the reported 18/16 case is 'tight', not a refusal",
              verdict(16, 13, 2, 16), "tight 15 18")

        # ── a plan whose requirement alone exceeds RAM still asks ─────────
        check("weights + cache over RAM is 'unlikely'",
              verdict(16, 15, 8, 16), "unlikely 23 26")

        # ── comfortable ───────────────────────────────────────────────────
        check("a plan inside RAM and inside the cap is 'ok'",
              verdict(48, 15, 8, 64), "ok 23 26")

        # ── over the cap, but the Mac has room ────────────────────────────
        check("over MEMORY_LIMIT_GB but under RAM is 'tight'",
              verdict(48, 44, 8, 64), "tight 52 55")

        # A cap of 0 means "no cap configured", not "no memory allowed".
        check("a cap of 0 does not make everything tight",
              verdict(0, 44, 8, 64), "ok 52 55")

        # The boundary, from both sides: plan == RAM is fine, one over is not.
        check("a plan exactly equal to RAM is 'ok'",
              verdict(0, 13, 0, 16), "ok 13 16")
        check("one GB over RAM on a tight Mac is 'tight'",
              verdict(0, 14, 0, 16), "tight 14 17")

        # ── KV cost from the model itself ─────────────────────────────────
        # These are the shapes the table already knows, so a match here proves
        # the runtime derivation agrees with the numbers the project ships.
        q27 = os.path.join(root, "qwen-27b.gguf")
        make_gguf(q27, qwen_meta(65, 4, 4, 256))
        check("a Qwen-27B-shaped hybrid derives 64 KB/token", kv_of(q27), "64")

        small = os.path.join(root, "custom-8b.gguf")
        make_gguf(small, qwen_meta(32, 4, 8, 128, arch="qwen35"))
        check("an 8B hybrid derives its own 32 KB/token, not the flat fallback",
              kv_of(small), "32")

        gemma = os.path.join(root, "gemma-26b.gguf")
        # 30 blocks, 5 of them full-attention, and a per-layer head count that
        # is NOT uniform - the full layer has 2 heads where the sliding ones
        # have 8. Reading position 0 instead would report 4x too much.
        pattern = [True] * 30
        heads = [8] * 30
        for i in (5, 11, 17, 23, 29):
            pattern[i] = False
            heads[i] = 2
        make_gguf(gemma, [
            ("general.architecture", STR, "gemma4"),
            ("gemma4.block_count", U32, 30),
            ("gemma4.attention.sliding_window_pattern", ARR, (BOOL, pattern)),
            ("gemma4.attention.head_count_kv", ARR, (U32, heads)),
            ("gemma4.attention.key_length", U32, 512),
        ])
        check("a Gemma sliding-window model derives 20 KB/token", kv_of(gemma), "20")

        # ── and falls back honestly when it cannot tell ───────────────────
        unknown = os.path.join(root, "unknown.gguf")
        make_gguf(unknown, [
            ("general.architecture", STR, "something-new"),
            ("something-new.block_count", U32, 40),
        ])
        check("an unrecognised architecture derives nothing", kv_of(unknown), "")

        notgguf = os.path.join(root, "not-a-model.gguf")
        with open(notgguf, "wb") as fh:
            fh.write(b"NOPE" + b"\x00" * 64)
        check("a file that is not a GGUF derives nothing", kv_of(notgguf), "")

        missing = os.path.join(root, "does-not-exist.gguf")
        check("a missing file derives nothing", kv_of(missing), "")

        # ── the two together: a custom repo priced from its own weights ───
        # This is the fix in one check. The repo id is unknown to the table, so
        # the old path would have said 64 KB/token; the file says 32.
        d = os.path.join(root, "someone--custom-8B-GGUF")
        os.makedirs(d, exist_ok=True)
        shutil.copyfile(small, os.path.join(d, "custom-8b-Q4_K_M.gguf"))
        check("an unknown repo is priced from the file, not the fallback",
              kv_for_dir(d, "someone/custom-8B-GGUF"), "32")
        check("a known repo with a derivable file also uses the file",
              kv_for_dir(d, "HauhauCS/Gemma4-12B-QAT-Uncensored-HauhauCS-Balanced"), "32")

        # An MLX pack has no GGUF to read, so it keeps the table.
        empty = os.path.join(root, "mlx-pack")
        os.makedirs(empty, exist_ok=True)
        check("an MLX pack still falls back to the table",
              kv_for_dir(empty, "itrejomx/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTPLX-4bit", "mlx"),
              "64")

        print(f"\n  {PASSED} passed, {FAILED} failed\n")
        return 1 if FAILED else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
