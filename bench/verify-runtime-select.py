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
"""Regression tests for per-model llama.cpp runtime resolution.

Why this exists
---------------
Bonsai 2 27B cannot run on stock llama.cpp: PQ2_0 and PTQ1_0 are PrismML's own
ggml types and the weights need a Hadamard activation transform that is not
upstream yet. So the model needs a different binary - but only the model.

That "only" is the whole point, and it is what these tests protect. A runtime
requirement that leaks into the general path would quietly move every Gemma and
Qwen model off the build their published numbers were measured on. A runtime
requirement that fails to apply is worse in a different way: stock llama.cpp
loads the similarly-named Q2_0 ternary file WITHOUT a warning and emits
gibberish, so a silent fallback produces confident nonsense rather than an
error.

The other half is the failure path. When the runtime is missing the server has
to stop and name the command that installs it, because "it will not start" with
no explanation is what sends people to the issue tracker.

No model, no server, no network: the weights are empty files and the runtime is
a shell script standing in for a binary.

Run:  python3 bench/verify-runtime-select.py
"""

import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# Prints the runtime key a repo declares, or nothing.
SHIM_KEY = r'''
source "$1/lib/common.sh"
printf '%s\n' "$(runtime_key_for_model "$2")"
'''

# Prints the env.conf key a runtime is recorded in.
SHIM_ENVKEY = r'''
source "$1/lib/common.sh"
printf '%s\n' "$(runtime_env_key_for "$2")"
'''

# Resolves the alias -> repo -> runtime chain the launcher uses.
SHIM_ALIAS = r'''
source "$1/lib/common.sh"
printf '%s\n' "$(runtime_key_for_model "$(model_repo_for "$2")")"
'''

# Runs the real engine_build_args with a chosen PRISM_RUNTIME. Prints the binary
# it selected; the die message, if it dies, goes to stderr.
SHIM_BUILD = r'''
source "$1/lib/common.sh"
load_config
MODEL_REPO="$2"; MODEL_ALIAS="$3"; MODEL_DIR="$4"; MODELS_DIR="$5"
PRISM_RUNTIME="${6:-}"
source "$1/lib/engines/gguf.sh"
engine_build_args "$4"
printf 'BIN=%s\n' "${LLAMA_BIN:-}"
'''

# Proves the value is written back quoted, like every other path in env.conf.
SHIM_QUOTE = r'''
source "$1/lib/common.sh"
ENV_FILE="$2"
set_config_value PRISM_RUNTIME "/tmp/a path with spaces/llama-server"
grep '^PRISM_RUNTIME=' "$2"
'''

PASSED = 0
FAILED = 0

BONSAI_PQ = "dealignai/Bonsai-2-27B-Ternary-CRACK-GGUF"
BONSAI_PTQ = "dealignai/Bonsai-2-27B-1bit-CRACK-GGUF"
GEMMA = "OS-Software/gemma-4-26B-A4B-it-qat-q4_0-heretic-ja-GGUF"
QWEN_MLX = "hawhyhb/Qwen3.6-35B-A3B-Uncensored-Heretic-MTPLX-4bit-FP16"


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        print(f"  [PASS] {label}")
        PASSED += 1
    else:
        print(f"  [FAIL] {label}\n         got  {got!r}\n         want {want!r}")
        FAILED += 1


def bash(shim, *args):
    return subprocess.run(["bash", "-c", shim, "shim", REPO, *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def build_args(root, repo, alias, runtime):
    """Returns (selected binary or None, stderr)."""
    r = bash(SHIM_BUILD, repo, alias, root, root, runtime)
    out = [l for l in r.stdout.splitlines() if l.startswith("BIN=")]
    return (out[0][4:] if out else None), r.stderr


def main():
    print("\n  Per-model runtime resolution\n")
    root = tempfile.mkdtemp(prefix="runtimesel-")
    try:
        weights = os.path.join(root, "Bonsai-2-27B-PQ2_0-CRACK.gguf")
        open(weights, "w").close()
        gemma = os.path.join(root, "gemma-4-26B-A4B-it-qat-q4_0-heretic-ja-Q4_0.gguf")
        open(gemma, "w").close()

        fake_rt = os.path.join(root, "fake-llama-server")
        with open(fake_rt, "w") as fh:
            fh.write("#!/bin/sh\nexit 0\n")
        os.chmod(fake_rt, 0o755)

        # ── which models declare a runtime ────────────────────────────────
        check("the PQ2_0 Bonsai pack declares the prism runtime",
              bash(SHIM_KEY, BONSAI_PQ).stdout.strip(), "prism")
        check("the PTQ1_0 Bonsai pack declares it too",
              bash(SHIM_KEY, BONSAI_PTQ).stdout.strip(), "prism")

        # ...and nothing else does. This is the regression that matters: a
        # requirement leaking out here would move every other model off the
        # build its measured numbers came from.
        check("Gemma declares no runtime", bash(SHIM_KEY, GEMMA).stdout.strip(), "")
        check("an MLX pack declares no llama.cpp runtime",
              bash(SHIM_KEY, QWEN_MLX).stdout.strip(), "")
        check("an unknown repo declares no runtime",
              bash(SHIM_KEY, "someone/whatever-GGUF").stdout.strip(), "")

        # ── the alias -> repo -> runtime chain ────────────────────────────
        check("gguf-b-27b resolves to the prism runtime",
              bash(SHIM_ALIAS, "gguf-b-27b").stdout.strip(), "prism")
        check("gguf-b-27b-ptq1 resolves to the prism runtime",
              bash(SHIM_ALIAS, "gguf-b-27b-ptq1").stdout.strip(), "prism")
        check("a Gemma alias resolves to no runtime",
              bash(SHIM_ALIAS, "gguf-g-26ba4b").stdout.strip(), "")

        check("the runtime is recorded in PRISM_RUNTIME",
              bash(SHIM_ENVKEY, "prism").stdout.strip(), "PRISM_RUNTIME")

        # ── missing runtime: stop, and say what to run ────────────────────
        bin_none, err = build_args(root, BONSAI_PQ, "gguf-b-27b", "")
        check("a Bonsai model with no runtime does not start", bin_none, None)
        check("and the error names the fork", "PrismML" in err, True)
        check("and it names the command that installs it",
              "lib/fetch-runtime.sh prism" in err, True)
        check("and it says stock llama.cpp cannot load the file",
              "Stock llama.cpp cannot load this file" in err, True)

        # ── runtime present: it is the binary that gets used ──────────────
        bin_set, err = build_args(root, BONSAI_PQ, "gguf-b-27b", fake_rt)
        check("with the runtime set, that binary is selected", bin_set, fake_rt)
        check("and nothing is warned about", err.strip(), "")

        # ── and it stays scoped to the model that asked for it ────────────
        bin_gemma, _ = build_args(root, GEMMA, "gguf-g-26ba4b", fake_rt)
        # Stock means the pinned upstream build when it is installed, and
        # Homebrew's on PATH otherwise - never the Prism fork.
        check("a Gemma model still uses stock llama-server even with PRISM_RUNTIME set",
              bin_gemma in ("llama-server", os.path.join(REPO, "run/runtimes/llama/llama-server"))
              or bin_gemma.endswith("/llama-server") and "prism" not in bin_gemma, True)

        # ── the value written back to env.conf is quoted ──────────────────
        # A path with spaces is the reason: unquoted it becomes a different
        # value the moment env.conf is sourced.
        env_copy = os.path.join(root, "env.conf")
        shutil.copyfile(os.path.join(REPO, "env.conf"), env_copy)
        line = bash(SHIM_QUOTE, env_copy).stdout.strip()
        check("PRISM_RUNTIME is written back quoted",
              line, 'PRISM_RUNTIME="/tmp/a path with spaces/llama-server"')

        print(f"\n  {PASSED} passed, {FAILED} failed\n")
        return 1 if FAILED else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
