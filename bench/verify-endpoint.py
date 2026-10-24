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
"""Check the running server's OpenAI-compatible API the way a client sees it.

Whatever engine is serving - llama.cpp, MTPLX or Splash - an agent only sees
http://<lan-ip>:8000/v1. This hits that address with the real API key and
checks the things agents depend on:

  * GET  /v1/models lists the served model name
  * POST /v1/chat/completions answers, non-streamed and streamed (SSE)
  * a tool definition comes back as a parseable tool call
  * a wrong API key is refused with 401

    python3 bench/verify-endpoint.py                       # LAN IP, port and key from env.conf
    python3 bench/verify-endpoint.py --base http://10.0.1.69:8000/v1
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PASSED = FAILED = 0


def check(label, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [PASS] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label}" + (f"\n         {detail}" if detail else ""))


def conf(key, default=""):
    try:
        out = subprocess.run(["bash", "-c", f'source "{REPO}/lib/common.sh"; load_config; printf %s "${key}"'],
                             capture_output=True, text=True, timeout=30).stdout
        return out or default
    except Exception:                                           # noqa: BLE001
        return default


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def req(base, path, key, body=None, timeout=300):
    hdr = {"Content-Type": "application/json"}
    if key:
        hdr["Authorization"] = "Bearer " + key
    r = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                               headers=hdr)
    return urllib.request.urlopen(r, timeout=timeout)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="")
    ap.add_argument("--key", default=None)
    a = ap.parse_args()
    port = conf("PORT", "8000")
    host = conf("HOST", "0.0.0.0")
    base = a.base or f"http://{lan_ip() if host == '0.0.0.0' else host}:{port}/v1"
    key = a.key if a.key is not None else (open(conf("API_KEY_FILE")).read().strip()
                                           if os.path.exists(conf("API_KEY_FILE")) else "")
    model = conf("SERVED_MODEL_NAME", "Upinel-AIOS")
    print(f"\n  OpenAI-compatible endpoint   {base}   model={model}\n")

    try:
        d = json.load(req(base, "/models", key, timeout=20))
        ids = [m.get("id") for m in d.get("data", [])]
        check("GET /v1/models lists the served name", model in ids, f"got {ids}")
    except Exception as e:                                      # noqa: BLE001
        check("GET /v1/models answers", False, f"{type(e).__name__}: {e}")
        print(f"\n  {PASSED} passed, {FAILED} failed")
        return 1

    msgs = [{"role": "user", "content": "Reply with the single word: ready"}]
    try:
        d = json.load(req(base, "/chat/completions", key,
                          {"model": model, "messages": msgs, "max_tokens": 64, "temperature": 0,
                           "reasoning_effort": "none",
                           "chat_template_kwargs": {"enable_thinking": False}}))
        text = (d["choices"][0]["message"].get("content") or "").strip()
        check("chat completion, non-streamed", bool(text), f"empty reply: {d}")
        check("usage is reported", bool((d.get("usage") or {}).get("completion_tokens")))
    except Exception as e:                                      # noqa: BLE001
        check("chat completion, non-streamed", False, f"{type(e).__name__}: {e}")

    try:
        r = req(base, "/chat/completions", key,
                {"model": model, "messages": msgs, "max_tokens": 64, "temperature": 0, "stream": True,
                 "reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False}})
        chunks, done = 0, False
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if line == "data: [DONE]":
                done = True
            elif line.startswith("data:"):
                chunks += 1
        check("chat completion, streamed (SSE)", chunks > 0 and done, f"{chunks} chunks, [DONE]={done}")
    except Exception as e:                                      # noqa: BLE001
        check("chat completion, streamed (SSE)", False, f"{type(e).__name__}: {e}")

    tools = [{"type": "function", "function": {
        "name": "read_file", "description": "Read a file from disk.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]}}}]
    try:
        d = json.load(req(base, "/chat/completions", key,
                          {"model": model, "tools": tools, "max_tokens": 256, "temperature": 0,
                           "reasoning_effort": "none", "chat_template_kwargs": {"enable_thinking": False},
                           "messages": [{"role": "user", "content": "Read the file README.md."}]}))
        calls = d["choices"][0]["message"].get("tool_calls") or []
        args = json.loads(calls[0]["function"]["arguments"]) if calls else {}
        check("tool call returned with parseable arguments",
              bool(calls) and calls[0]["function"]["name"] == "read_file" and "path" in args,
              f"message: {d['choices'][0]['message']}")
    except Exception as e:                                      # noqa: BLE001
        check("tool call returned with parseable arguments", False, f"{type(e).__name__}: {e}")

    if key:
        try:
            req(base, "/models", "wrong-key", timeout=20)
            check("a wrong API key is refused", False, "request succeeded")
        except urllib.error.HTTPError as e:
            check("a wrong API key is refused (401)", e.code == 401, f"HTTP {e.code}")
        except Exception as e:                                  # noqa: BLE001
            check("a wrong API key is refused", False, f"{type(e).__name__}: {e}")

    print(f"\n  {PASSED} passed, {FAILED} failed\n")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
