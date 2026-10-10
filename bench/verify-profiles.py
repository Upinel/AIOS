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
"""The expert profiles must actually change the launch, and must not abort.

Three profiles pick between jobs rather than points on a scale, so "it ran" is
not enough - each has to produce a different command line, and each has to
produce a valid one. Both halves have failed here:

  * ``(( expr )) && assign`` returns 1 when the test is false. lib/common.sh
    turns on set -e, so two of the three profiles aborted the script the moment
    they ran - silently, exit 1, no message, and only visible because
    ``--print`` showed a profile line and then nothing.
  * A profile that sets nothing is indistinguishable from a typo, so each is
    checked for a concrete effect on the command line rather than just for
    having been accepted.

The interesting assertions are "the launch command differs" and "the function
returns success for every profile", because those are the two ways this feature
can look like it works while doing nothing.

Run:  python3 bench/verify-profiles.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

PROFILES = ["speed", "agent", "writer"]

SHIM = r'''
source "$1/lib/common.sh"
load_config
EXPERT_PROFILE="$2"
# Start from deliberately wrong values so anything the profile sets shows up,
# and anything it leaves alone is visibly left alone.
KV_QUANT="q4_0"; MTP_DEPTH="9"; THINKING="high"; MAX_RESPONSE_TOKENS=128
PARALLEL_SLOTS=9; MAX_CONCURRENT=9; SESSIONS=""; PREFILL_CHUNK_TOKENS=99999
apply_expert_profile
finalize_runtime_settings 2>/dev/null
echo "rc=0"
echo "kv=$KV_QUANT"
echo "depth=$MTP_DEPTH"
echo "thinking=$THINKING"
echo "resp=$MAX_RESPONSE_TOKENS"
echo "slots=$PARALLEL_SLOTS"
echo "chunk=$PREFILL_CHUNK_TOKENS"
'''

PASSED = 0
FAILED = 0


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        print(f"  [PASS] {label}")
        PASSED += 1
    else:
        print(f"  [FAIL] {label}\n         got  {got!r}\n         want {want!r}")
        FAILED += 1


def applied(profile):
    """Apply a profile to a known-bad config and report the result."""
    r = subprocess.run(["bash", "-c", SHIM, "shim", REPO, profile],
                       capture_output=True, text=True, timeout=60)
    out = {}
    for line in r.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    out["_rc"] = r.returncode
    out["_err"] = r.stderr.strip()
    return out


def print_with(*args):
    """The command start.sh says it would run, for these arguments.

    Runs against a COPY of env.conf. Tests that write to the file you are
    working in are a menace, and this one did: checking each profile in turn
    left the last one persisted, so running the suite quietly changed which
    profile the user was on.

    Returns (lines, returncode, stderr).
    """
    env_file = os.path.join(REPO, "env.conf")
    backup = env_file + ".profiletest"
    shutil.copyfile(env_file, backup)
    try:
        r = subprocess.run([f"{REPO}/start.sh", "--print", *args],
                           capture_output=True, text=True, timeout=180, cwd=REPO)
        lines = [l.strip().rstrip("\\").strip() for l in r.stdout.splitlines()]
        return [l for l in lines if l], r.returncode, r.stderr
    finally:
        shutil.copyfile(backup, env_file)
        os.unlink(backup)


def print_args(profile):
    lines, rc, _err = print_with("--profile", profile)
    return lines, rc


def flag_value(lines, flag):
    """The argument after a flag in a printed command line, or None."""
    for i, l in enumerate(lines):
        if l == flag and i + 1 < len(lines):
            return lines[i + 1]
    return None


SHIM_MANUAL = r'''
source "$1/lib/common.sh"
ENV_FILE="$2"
load_config
[[ "${3:-}" == "forced" ]] && AIOS_MANUAL_FORCE_TTY=1
if ! prompt_manual_settings >/dev/null 2>&1; then echo "PROMPT-SKIPPED"; fi
echo "V=$MANUAL_VALUES"
echo "T=$THINKING|C=$CONTEXT_WINDOW|S=$PARALLEL_SLOTS|M=$MAX_CONCURRENT|R=$MAX_RESPONSE_TOKENS"
'''


def manual_shim(env_copy, answers, forced):
    r = subprocess.run(["bash", "-c", SHIM_MANUAL, "shim", REPO, env_copy,
                        "forced" if forced else "no"],
                       input=answers, capture_output=True, text=True, timeout=60)
    return r.stdout


def env_value(path, key):
    for line in open(path, encoding="utf-8"):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip().strip('"')
    return None


def main():
    print("\n  Expert profiles\n")

    # 1. Every profile must return success. This is the set -e trap.
    for p in PROFILES + ["custom"]:
        got = applied(p)
        check(f"{p}: apply_expert_profile succeeds", got.get("_rc"), 0)
        if got.get("_rc") != 0:
            print(f"         stderr: {got.get('_err', '')[:120]}")

    # 2. Every profile must actually set something, except custom which by
    #    definition sets nothing.
    for p in PROFILES:
        got = applied(p)
        changed = [
            k for k in ("kv", "depth", "thinking", "resp", "slots", "chunk")
            if got.get(k) != {"kv": "q4_0", "depth": "9", "thinking": "high",
                              "resp": "128", "slots": "9", "chunk": "99999"}[k]
        ]
        check(f"{p}: changes at least three settings", len(changed) >= 3, True)

    custom = applied("custom")
    check("custom leaves every setting exactly as written",
          [custom.get(k) for k in ("kv", "depth", "thinking", "resp", "slots", "chunk")],
          ["q4_0", "9", "high", "128", "9", "99999"])

    # manual is the other one that applies nothing: the answers come from the
    # prompts (or from the --set that carries them), and a preset must not
    # overwrite an answer the user just gave.
    manual = applied("manual")
    check("manual leaves every setting to its own answers",
          [manual.get(k) for k in ("kv", "depth", "thinking", "resp", "slots", "chunk")],
          ["q4_0", "9", "high", "128", "9", "99999"])
    check("manual is offered in the menu",
          "manual" in subprocess.run(
              ["bash", "-c", 'source "$1/lib/common.sh"; echo "$EXPERT_PROFILES"',
               "shim", REPO], capture_output=True, text=True).stdout.split(), True)

    # 3. The concrete differences that make the three worth having.
    speed, agent, writer = applied("speed"), applied("agent"), applied("writer")
    check("agent runs more parallel slots than speed",
          int(agent["slots"]) > int(speed["slots"]), True)
    check("agent turns thinking off", agent["thinking"], "off")
    check("writer turns thinking off", writer["thinking"], "off")
    check("agent reserves room for a tool call",
          int(agent["resp"]) >= 4096, True)
    check("writer reserves room for a long reply",
          int(writer["resp"]) >= 8192, True)
    # speed defers to the per-model tuned depth; writer pins a shallower one
    # because deeper drafting has more context to verify on every cycle.
    check("speed defers depth to the per-model tuner", speed["depth"], "auto")
    check("writer pins a shallow depth", writer["depth"], "1")
    # Profiles leave the KV type alone: f16 is the shipped default, and the
    # installer picks q8 on a 16-24 GB Mac where the gigabyte decides whether
    # the model fits. A profile overriding that undid the one choice made for
    # this machine, so the deliberately-wrong starting value must survive.
    check("profiles leave KV_QUANT to env.conf and the installer",
          {speed["kv"], agent["kv"], writer["kv"]}, {"q4_0"})

    # 4. And the launch commands really differ, which is the thing a user sees.
    print("\n  the launch commands differ\n")
    commands = {p: print_args(p) for p in PROFILES}
    for p, (lines, rc) in commands.items():
        check(f"{p}: --print produced a command", rc, 0)
    # Both sides must be non-empty as well as different: an empty command
    # differs from a real one, so the plain inequality passed while the profile
    # was aborting before printing anything.
    check("speed and writer produce different, non-empty commands",
          bool(commands["speed"][0]) and bool(commands["writer"][0])
          and commands["speed"][0] != commands["writer"][0], True)
    check("writer and agent produce different, non-empty commands",
          bool(commands["writer"][0]) and bool(commands["agent"][0])
          and commands["writer"][0] != commands["agent"][0], True)

    # 5. A bad name must say so rather than silently doing nothing.
    bad = subprocess.run(["bash", "-c",
                          f'source {REPO}/lib/common.sh; load_config; '
                          'EXPERT_PROFILE=nonsense; apply_expert_profile'],
                         capture_output=True, text=True, timeout=60)
    check("an unknown profile is rejected", bad.returncode != 0, True)
    check("and names the valid ones", "speed" in bad.stderr and "writer" in bad.stderr, True)

    # 6. manual, and the --set hand-over.
    #
    # ./restart.sh asks the manual questions and hands the answers to start.sh
    # as --set, so the questions are asked once. That receiver is the half that
    # can be exercised without a terminal - the prompt itself needs one, and a
    # sandbox without a pty cannot supply it.
    print("\n  manual, and --set\n")
    lines, rc, _ = print_with("--profile", "manual",
                              "--set", "CONTEXT_WINDOW=65536",
                              "--set", "PARALLEL_SLOTS=4",
                              "--set", "MAX_CONCURRENT=4",
                              "--set", "THINKING=high")
    check("manual with --set returns success", rc, 0)
    check("--set moves the context window", flag_value(lines, "-c"), "65536")
    check("--set moves the session count", flag_value(lines, "--parallel"), "4")
    check("--set moves thinking too",
          any('"enable_thinking":true' in l for l in lines), True)

    # An explicit setting has to beat a preset, or --set cannot be used to
    # correct one thing about a profile.
    lines, _, _ = print_with("--profile", "agent", "--set", "THINKING=high")
    check("--set overrides the profile it is combined with",
          any('"enable_thinking":true' in l for l in lines), True)
    lines, _, _ = print_with("--profile", "agent")
    check("...and the profile alone still turns thinking off",
          any('"enable_thinking":false' in l for l in lines), True)

    # The allowlist. A start script that assigns whatever it is handed can be
    # talked into setting PATH, and this one is handed values by a picker.
    _, rc, err = print_with("--set", "PATH=/tmp")
    check("--set refuses a key outside its allowlist", rc != 0, True)
    check("and prints the keys it does accept", "Settable:" in err, True)

    # A dry run must not sit waiting for answers it cannot use.
    _, rc, _ = print_with("--profile", "manual")
    check("--print --profile manual does not wait for input", rc, 0)

    # 7. The prompt bounds, as pure functions. Testable without a terminal, and
    #    named once so the question asked and the answer accepted cannot drift.
    bounds = subprocess.run(
        ["bash", "-c", r'''
source "$1/lib/common.sh"
show() { if "$1" "$2"; then echo "$1 $2 ok"; else echo "$1 $2 no"; fi; }
for v in 4095 4096 262144 262145 nope; do show manual_ok_context "$v"; done
for v in 0 1 16 17 nope; do show manual_ok_sessions "$v"; done
for v in 511 512 32768 32769 nope; do show manual_ok_reply "$v"; done
''', "shim", REPO],
        capture_output=True, text=True, timeout=60)
    check("the manual bounds hold at both edges, and reject text",
          bounds.stdout.split(),
          ["manual_ok_context", "4095", "no", "manual_ok_context", "4096", "ok",
           "manual_ok_context", "262144", "ok", "manual_ok_context", "262145", "no",
           "manual_ok_context", "nope", "no",
           "manual_ok_sessions", "0", "no", "manual_ok_sessions", "1", "ok",
           "manual_ok_sessions", "16", "ok", "manual_ok_sessions", "17", "no",
           "manual_ok_sessions", "nope", "no",
           "manual_ok_reply", "511", "no", "manual_ok_reply", "512", "ok",
           "manual_ok_reply", "32768", "ok", "manual_ok_reply", "32769", "no",
           "manual_ok_reply", "nope", "no"])

    # 8. The hand-over that stops the workload question being asked twice.
    #
    # Reported: ./restart.sh sometimes showed the selector twice. It asked, then
    # called start.sh WITHOUT --profile, and start.sh asks whenever it is not
    # handed one - the model picker had been handed over as --model for exactly
    # this reason and the profile had not.
    #
    # restart.sh cannot be driven end to end here: showing a picker at all needs
    # a pty, and this sandbox cannot allocate one. So this checks the contract
    # at the source instead, which is where it broke.
    print("\n  the profile is handed over, not re-asked\n")
    start_src = open(os.path.join(REPO, "start.sh"), encoding="utf-8").read()
    restart_src = open(os.path.join(REPO, "restart.sh"), encoding="utf-8").read()
    check("start.sh prompts for the workload exactly once",
          start_src.count("choose_expert_profile"), 1)
    check("and only when no profile was handed over",
          'if [[ -n "$PROFILE_OVERRIDE" ]]' in start_src, True)
    check("restart.sh hands its choice to start.sh",
          '--profile "$EXPERT_PROFILE"' in restart_src, True)
    check("restart.sh asks the manual questions once",
          restart_src.count("prompt_manual_settings"), 1)
    check("start.sh does not ask them again when --set carries the answers",
          '-z "$SET_VALUES"' in start_src, True)

    print("\n  the manual prompt itself\n")
    # Driven through the AIOS_MANUAL_FORCE_TTY seam, on a COPY of env.conf with
    # known values - the answers need a terminal, and a sandbox without a pty
    # has none, so without the seam this whole path would go untested.
    with tempfile.TemporaryDirectory() as td:
        env_copy = os.path.join(td, "env.conf")
        src = open(os.path.join(REPO, "env.conf"), encoding="utf-8").read()
        for k, v in (("THINKING", '"medium"'), ("CONTEXT_WINDOW", "32768"),
                     ("SESSIONS", "2"),
                     ("MAX_RESPONSE_TOKENS", "2048")):
            src = re.sub(rf"^{k}=.*$", f"{k}={v}", src, count=1, flags=re.M)
        with open(env_copy, "w", encoding="utf-8") as fh:
            fh.write(src)
        known = open(env_copy, encoding="utf-8").read()

        # No terminal, no seam: decline instead of blocking. A start in a pipe
        # or under launchd must never sit waiting on a question.
        out = manual_shim(env_copy, "high\n", forced=False)
        check("with no terminal the prompt declines", "PROMPT-SKIPPED" in out, True)
        check("and leaves the file alone",
              open(env_copy, encoding="utf-8").read() == known, True)

        # Enter straight through = keep everything = manual behaves like custom.
        out = manual_shim(env_copy, "\n\n\n\nn\n", forced=True)
        check("Enter through keeps every current value",
              "T=medium|C=32768|S=2|M=2|R=2048" in out, True)

        # The answers land, and are NOT written back by default.
        out = manual_shim(env_copy, "high\n65536\n4\n8192\nn\n", forced=True)
        check("the answers are applied",
              "T=high|C=65536|S=4|M=4|R=8192" in out, True)
        check("sessions set both spellings",
              "S=4|M=4" in out, True)
        check("this start only: env.conf untouched",
              open(env_copy, encoding="utf-8").read() == known, True)
        check("and it says so", "V=THINKING=high CONTEXT_WINDOW=65536" in out, True)

        # Bad answers are re-asked rather than accepted or fatal.
        out = manual_shim(env_copy,
                          "bogus\nhigh\nabc\n65536\n99\n4\n1\n8192\nn\n", forced=True)
        check("a bad answer is retried, not fatal",
              "T=high|C=65536|S=4|M=4|R=8192" in out, True)
        check("and nothing was written while retrying",
              open(env_copy, encoding="utf-8").read() == known, True)

        # Saying yes does write them, which is the one path that should.
        out = manual_shim(env_copy, "high\n65536\n4\n8192\ny\n", forced=True)
        check("keep=yes writes the answers to env.conf",
              [env_value(env_copy, k) for k in
               ("THINKING", "CONTEXT_WINDOW", "SESSIONS", "MAX_RESPONSE_TOKENS")],
              ["high", "65536", "4", "8192"])
        check("and the file still loads as config",
              subprocess.run(
                  ["bash", "-c",
                   f'source {REPO}/lib/common.sh >/dev/null 2>&1; '
                   f'ENV_FILE={env_copy}; load_config; echo "$CONTEXT_WINDOW"'],
                  capture_output=True, text=True, timeout=60).stdout.strip(),
              "65536")

    print(f"\n  {PASSED} passed, {FAILED} failed\n")
    return 1 if FAILED else 0

if __name__ == "__main__":
    sys.exit(main())
