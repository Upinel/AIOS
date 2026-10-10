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
"""Build an FP16 copy of an MLX/MTPLX pack, for M1 and M2.

M1 and M2 GPUs have no native bfloat16, so a pack whose scales, biases and
norms are stored in BF16 runs those through a slow emulated path. MTPLX
publishes "-FP16" builds of its own packs for exactly this reason ("every
16-bit tensor cast to fp16"), but the uncensored community packs this project
serves mostly ship BF16 only. This makes the same cast locally:

  * every BF16 tensor in every *.safetensors becomes FP16; quantised (U32)
    weights and anything already FP16/FP32 are untouched
  * a tensor whose values would not fit FP16 (|x| > 65504) is left in BF16 and
    reported, rather than silently saturated
  * every other file (config, tokenizer, MTP runtime metadata, adapters) is
    copied as is

The result is written next to the pack as <pack>.fp16, which the MLX engine
prefers on M1/M2 automatically. Run with MTPLX's own Python, which has MLX:

    lib/make-fp16-pack.py models/<pack>            # builds models/<pack>.fp16
"""

import os
import shutil
import sys
import time

FP16_MAX = 65504.0


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = sys.argv[1].rstrip("/")
    dst = src + ".fp16"
    tmp = dst + ".partial"
    try:
        import mlx.core as mx
    except ImportError:
        print("error: this needs MLX - run it with MTPLX's Python", file=sys.stderr)
        return 2
    if not os.path.isdir(src):
        print(f"error: no pack at {src}", file=sys.stderr)
        return 2
    if os.path.isdir(dst):
        print(f"already built: {dst}")
        return 0
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp)
    t0 = time.time()
    cast = kept = 0
    for name in sorted(os.listdir(src)):
        s = os.path.join(src, name)
        d = os.path.join(tmp, name)
        if os.path.isdir(s):
            shutil.copytree(s, d)
            continue
        if not name.endswith(".safetensors"):
            shutil.copy2(s, d)
            continue
        arrays, meta = mx.load(s, return_metadata=True)
        out = {}
        for k, v in arrays.items():
            if v.dtype == mx.bfloat16:
                peak = mx.max(mx.abs(v.astype(mx.float32))).item() if v.size else 0.0
                if peak > FP16_MAX:
                    out[k] = v
                    kept += 1
                    print(f"  kept BF16 (|x| up to {peak:.0f}): {name}:{k}")
                    continue
                out[k] = v.astype(mx.float16)
                cast += 1
            else:
                out[k] = v
        mx.save_safetensors(d, out, metadata=meta or None)
        print(f"  {name}: done")
    os.rename(tmp, dst)
    print(f"built {dst} in {time.time() - t0:.0f}s - {cast} tensors cast to FP16, {kept} kept in BF16")
    return 0


if __name__ == "__main__":
    sys.exit(main())
