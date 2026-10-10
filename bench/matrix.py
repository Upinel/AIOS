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
"""A/B any server command lines against the Hermes replay, interleaved.

Each config is a name and a shell command that starts a server on --port.
The runner starts one, waits for /health, replays the agent session, stops it,
and moves to the next - round after round, in a fresh random order each round,
so thermal drift and background load land on every config equally. The best
and the median of each metric are reported, because on a working desktop the
best-of ranks configs and the median says how much to trust the ranking.

    python3 bench/matrix.py --rounds 3 \\
        --config "old=llama-server -m M.gguf ... --port {port}" \\
        --config "new=run/runtimes/llama/llama-server -m M.gguf ... --port {port}"

`{port}` in a command is replaced with --port. Commands run from the repo root.
A file of configs (one `name=command` per line, # comments) works too:

    python3 bench/matrix.py --rounds 3 --configs-file run/ab-gemma.txt
"""

import argparse
import json
import os
import random
import shlex
import signal
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

METRICS = [
    ("total_s", "total s", min),
    ("fixed_s", "excl. write s", min),
    ("cold_ttft", "cold TTFT", min),
    ("turn_ttft", "turn TTFT", min),
    ("write_tps", "write e2e t/s", max),
    ("sub_wall", "subagents s", min),
    ("code_tps", "code decode t/s", max),
    ("compress_ttft", "compress s", min),
    ("bad_calls", "bad calls", min),
]


def wait_healthy(port, proc, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
                if r.status == 200 and b"loading" not in r.read().lower():
                    return True
        except Exception:                                       # noqa: BLE001
            pass
        time.sleep(1.5)
    return False


def stop(proc):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(60):
        if proc.poll() is not None:
            return
        time.sleep(0.5)
    os.killpg(proc.pid, signal.SIGKILL)


def flatten(res):
    rows = res.get("rows", [])
    def ph(name):
        return [r for r in rows if r.get("phase") == name and "ttft_s" in r]
    pre, turns, wr, comp = ph("prefix"), ph("turns"), ph("write"), ph("compress")
    sub = [r for r in rows if r.get("phase") == "subagents" and r.get("label") == "wall"]
    code = ph("code")
    return {
        "total_s": res.get("total_s"),
        "cold_ttft": pre[0]["ttft_s"] if pre else None,
        "turn_ttft": statistics.median([r["ttft_s"] for r in turns]) if turns else None,
        # End to end, tokens over the request's wall time. Engines that buffer
        # a tool call until it is complete (MTPLX does) make a per-token rate
        # meaningless; wall time is what the agent waits either way.
        # Only a COMPLETE file write counts: a model that answers the write
        # step with a 29-token read_file finishes sooner and would look faster.
        "write_tps": (wr[0]["completion_tokens"] / wr[0]["wall_s"])
                     if wr and res.get("write_ok") and wr[0].get("wall_s") else None,
        # The trace without the write step - fixed work across configs, since
        # how long a model's file is says nothing about the server.
        "fixed_s": (res.get("total_s") - (wr[0]["wall_s"] if wr else 0))
                   if res.get("total_s") is not None else None,
        "write_ok": res.get("write_ok"),
        "sub_wall": sub[0]["wall_s"] if sub else None,
        "code_tps": (code[0]["completion_tokens"] / max(1e-6, code[0]["wall_s"] - code[0]["ttft_s"]))
                    if code and code[0].get("completion_tokens") else None,
        "compress_ttft": comp[0]["ttft_s"] if comp else None,
        "bad_calls": res.get("tool_calls_bad"),
        "errors": res.get("errors"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", action="append", default=[], help="name=command")
    ap.add_argument("--configs-file")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--port", type=int, default=8011)
    ap.add_argument("--load-timeout", type=int, default=900)
    ap.add_argument("--cooldown", type=float, default=5.0)
    ap.add_argument("--out", default=os.path.join(REPO, "run", "matrix-results.json"))
    ap.add_argument("--replay-args", default="",
                    help="extra arguments for bench/hermes-replay.py")
    ap.add_argument("--summarise", action="store_true",
                    help="only re-print the table from --out's raw JSON logs")
    a = ap.parse_args()

    cfgs = []
    lines = list(a.config)
    if a.configs_file:
        lines += [l.strip() for l in open(a.configs_file) if l.strip() and not l.lstrip().startswith("#")]
    import re
    for line in lines:
        name, _, cmd = line.partition("=")
        # A config is name=command with a plain name. Anything else is noise
        # that got into the file - skip it loudly rather than try to run it.
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name.strip()) or not cmd.strip():
            print(f"  skipping a line that is not name=command: {line[:70]!r}")
            continue
        cfgs.append((name.strip(), cmd.strip().replace("{port}", str(a.port))))
    if not cfgs:
        ap.error("no configs")

    results = {n: [] for n, _ in cfgs}
    logdir = os.path.join(REPO, "run", "matrix-logs")
    os.makedirs(logdir, exist_ok=True)
    if a.summarise:
        import glob
        for n, _ in cfgs:
            for f in sorted(glob.glob(os.path.join(logdir, f"{n}.r*.json"))):
                try:
                    results[n].append(flatten(json.load(open(f))))
                except (OSError, json.JSONDecodeError):
                    pass
        a.rounds = 0
    for rnd in range(a.rounds):
        order = cfgs[:]
        random.shuffle(order)
        for name, cmd in order:
            print(f"\n=== round {rnd + 1}/{a.rounds}  {name}", flush=True)
            log = open(os.path.join(logdir, f"{name}.r{rnd + 1}.log"), "w")
            proc = subprocess.Popen(cmd, shell=True, cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            try:
                if not wait_healthy(a.port, proc, a.load_timeout):
                    print(f"  {name}: server did not come up; see {log.name}")
                    results[name].append({"errors": "no-start"})
                    continue
                out = os.path.join(logdir, f"{name}.r{rnd + 1}.json")
                rc = subprocess.call([sys.executable, os.path.join(HERE, "hermes-replay.py"),
                                      "--base", f"http://127.0.0.1:{a.port}/v1", "--key", "",
                                      "--json", out] + shlex.split(a.replay_args), cwd=REPO)
                try:
                    results[name].append(flatten(json.load(open(out))))
                except (OSError, json.JSONDecodeError):
                    results[name].append({"errors": f"replay rc={rc}"})
            finally:
                stop(proc)
                log.close()
            time.sleep(a.cooldown)

    if not a.summarise:
        with open(a.out, "w") as fh:
            json.dump({"configs": dict(cfgs), "results": results}, fh, indent=2)

    print("\n  best of rounds (median in brackets)\n")
    hdr = f"  {'config':<22}" + "".join(f"{label:>20}" for _, label, _ in METRICS)
    print(hdr)
    for name, _ in cfgs:
        rs = [r for r in results[name] if isinstance(r.get("total_s"), (int, float))]
        cells = []
        for key, _, best in METRICS:
            vals = [r[key] for r in rs if isinstance(r.get(key), (int, float))]
            if not vals:
                cells.append(f"{'-':>20}")
                continue
            cells.append(f"{best(vals):>11.2f} ({statistics.median(vals):>6.2f})")
        print(f"  {name:<22}" + "".join(cells))
    print(f"\n  raw -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
