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
"""Replay a Hermes-shaped agent session and time what the agent waits for.

Why another benchmark
---------------------
bench.py measures throughput and agent-bench.py measures one growing prompt.
Neither looks like Hermes Agent, Claude Code or OpenCode, which all do this:

  1. a LARGE fixed prefix - a system prompt plus 30-50 tool schemas, sent as
     real `tools`, so the server renders them through the chat template
  2. many short appended turns: a tool call, then its result
  3. now and then a long tool call - a whole file inside write_file's arguments
  4. parallel subagents that share the prefix and differ after it
  5. a compaction: the middle of the history is replaced by a summary

Every phase is one thing an engine flag can make faster or slower, so each is
timed on its own, and the trace as a whole gives the headline number: total
wall-clock seconds to replay the session. That number is engine-agnostic - it
is measured on the client, so llama.cpp and MTPLX are compared on the same
clock - and it is what a user actually feels.

The trace is scripted. The assistant's turns are canned tool calls rather than
whatever the model happened to say, so two configs replay byte-identical
prompts and only the server differs. Generation is still real: every turn asks
the model to produce its next step, and that is what TTFT and decode measure.

Usage
-----
    python3 bench/hermes-replay.py                         # the running server
    python3 bench/hermes-replay.py --base http://127.0.0.1:8011/v1 --key ""
    python3 bench/hermes-replay.py --prefix-tokens 20000 --turns 16 --json out.json
    python3 bench/hermes-replay.py --phases prefix,turns   # a subset
"""

import argparse
import concurrent.futures as cf
import json
import os
import random
import statistics
import sys
import time
import urllib.error
import urllib.request

PHASES = ("prefix", "turns", "write", "subagents", "compress", "code")

# ── a Hermes-sized tool set ──────────────────────────────────────────────────
# Names and shapes follow what agent harnesses actually ship. Descriptions are
# long on purpose: real harnesses document every tool at length, and the
# prefix those descriptions make is the thing being measured.

_S = {"type": "string"}
_I = {"type": "integer"}
_B = {"type": "boolean"}
_A = {"type": "array", "items": {"type": "string"}}

_DOC = (
    "Use this tool when the task requires it and never speculatively. Read the "
    "result carefully before deciding the next step. Paths are relative to the "
    "workspace root unless they start with a slash. Errors are returned as text "
    "rather than raised, so check the output for a leading 'error:' before "
    "relying on it. Large outputs are truncated to the first and last 200 lines "
    "with a marker in between. Prefer narrow, specific calls over broad ones: "
    "they are faster, cheaper, and leave more of the context window for the "
    "work itself. "
)

_TOOL_SPECS = [
    ("terminal", "Run a shell command in the persistent terminal session.",
     [("command", _S), ("timeout", _I), ("background", _B), ("workdir", _S)], ["command"]),
    ("process", "Inspect, poll, or kill a background process.",
     [("action", _S), ("pid", _I)], ["action"]),
    ("read_file", "Read a file, optionally a line range.",
     [("path", _S), ("offset", _I), ("limit", _I)], ["path"]),
    ("write_file", "Create or overwrite a file with the given content.",
     [("path", _S), ("content", _S)], ["path", "content"]),
    ("patch", "Apply an exact search-and-replace edit to a file.",
     [("path", _S), ("old_string", _S), ("new_string", _S), ("replace_all", _B)],
     ["path", "old_string", "new_string"]),
    ("search_files", "Search file names or contents with a pattern.",
     [("pattern", _S), ("path", _S), ("target", _S), ("file_glob", _S), ("limit", _I)],
     ["pattern"]),
    ("list_directory", "List directory entries.", [("path", _S), ("depth", _I)], ["path"]),
    ("web_search", "Search the web.", [("query", _S), ("limit", _I)], ["query"]),
    ("web_extract", "Fetch pages and extract readable text.", [("urls", _A)], ["urls"]),
    ("browser_navigate", "Open a URL in the browser.", [("url", _S)], ["url"]),
    ("browser_snapshot", "Accessibility snapshot of the page.", [("full", _B)], []),
    ("browser_click", "Click an element by ref.", [("ref", _S)], ["ref"]),
    ("browser_type", "Type text into an element.", [("ref", _S), ("text", _S)], ["ref", "text"]),
    ("browser_scroll", "Scroll the page.", [("direction", _S)], ["direction"]),
    ("browser_back", "Go back in history.", [], []),
    ("browser_press", "Press a key.", [("key", _S)], ["key"]),
    ("browser_get_images", "List images on the page.", [], []),
    ("browser_vision", "Ask a question about a screenshot of the page.",
     [("question", _S)], ["question"]),
    ("vision_analyze", "Analyse an image file or URL.",
     [("image_url", _S), ("question", _S)], ["image_url", "question"]),
    ("image_generate", "Generate an image from a prompt.",
     [("prompt", _S), ("aspect_ratio", _S)], ["prompt"]),
    ("text_to_speech", "Synthesise speech to a file.", [("text", _S), ("voice", _S)], ["text"]),
    ("execute_code", "Run a Python script that may call other tools via RPC.",
     [("code", _S)], ["code"]),
    ("delegate_task", "Spawn a subagent with its own context for a subtask.",
     [("goal", _S), ("context", _S), ("toolsets", _A)], ["goal"]),
    ("todo", "Read or update the task list.",
     [("todos", {"type": "array", "items": {"type": "object"}}), ("merge", _B)], []),
    ("memory", "Add, replace or remove a persistent memory entry.",
     [("action", _S), ("target", _S), ("content", _S), ("old_text", _S)], ["action", "target"]),
    ("session_search", "Search past sessions.", [("query", _S), ("limit", _I)], ["query"]),
    ("skills_list", "List available skills.", [("category", _S)], []),
    ("skill_view", "Load a skill's instructions.", [("name", _S)], ["name"]),
    ("skill_manage", "Create or edit a skill.",
     [("action", _S), ("name", _S), ("content", _S)], ["action", "name"]),
    ("cronjob", "Schedule, list or remove a recurring job.",
     [("action", _S), ("schedule", _S), ("prompt", _S), ("job_id", _S)], ["action"]),
    ("send_message", "Send a message to a connected platform.",
     [("target", _S), ("message", _S)], ["target", "message"]),
    ("clarify", "Ask the user a clarifying question.", [("question", _S), ("choices", _A)], ["question"]),
    ("git_status", "Show working tree status.", [("path", _S)], []),
    ("git_diff", "Show changes.", [("path", _S), ("staged", _B)], []),
    ("git_log", "Show recent commits.", [("limit", _I)], []),
    ("git_commit", "Commit staged changes.", [("message", _S), ("files", _A)], ["message"]),
    ("run_tests", "Run the test suite or a subset.", [("path", _S), ("filter", _S)], []),
    ("lint", "Run the linter.", [("path", _S), ("fix", _B)], []),
    ("ha_call_service", "Call a Home Assistant service.",
     [("domain", _S), ("service", _S), ("entity_id", _S)], ["domain", "service"]),
    ("mixture_of_agents", "Ask several models and synthesise an answer.",
     [("user_prompt", _S)], ["user_prompt"]),
]


def _tool(name, desc, props, required, pad):
    return {"type": "function", "function": {
        "name": name,
        "description": desc + " " + pad,
        "parameters": {"type": "object",
                       "properties": {k: dict(v, description=f"The {k} argument.")
                                      for k, v in props},
                       "required": required}}}


def build_tools(prefix_tokens):
    """The tool list, padded so the whole prefix lands near prefix_tokens.

    ~6.3 characters per token for this padded schema text (it repeats, so it
    tokenises densely); the real count is read back from the server.
    """
    base = sum(len(json.dumps(_tool(n, d, p, r, ""))) for n, d, p, r in _TOOL_SPECS)
    want_chars = max(0, int(prefix_tokens * 6.3) - base - len(SYSTEM))
    reps = max(0, want_chars // (len(_DOC) * len(_TOOL_SPECS)))
    pad = (_DOC * reps).strip()
    return [_tool(n, d, p, r, pad) for n, d, p, r in _TOOL_SPECS]


SYSTEM = (
    "You are Hermes, an autonomous agent with tools. You work in the user's "
    "workspace on their machine. Think about which tool moves the task forward, "
    "call it, read the result, and repeat until the task is done. Always supply "
    "every required argument. Prefer patch over write_file for small edits. "
    "Never invent file contents you have not read. When the task is complete, "
    "reply with a short summary of what changed.\n\n"
    "# Environment\nOS: macOS (Apple Silicon)\nShell: zsh\nWorkspace: ~/work/inventory-service\n"
    "# Memory\n- The user prefers small, reviewable commits.\n"
    "- Tests run with `pytest -q`; the linter is ruff.\n"
)

# Tool results that tokenise like the real thing: code, logs, listings.
_RESULTS = [
    "src/inventory/\n  __init__.py\n  api.py\n  models.py\n  store.py\n  pricing.py\n"
    "tests/\n  test_api.py\n  test_store.py\n  test_pricing.py\npyproject.toml\nREADME.md\n",
    "import decimal\nfrom dataclasses import dataclass\n\n@dataclass\nclass Item:\n"
    "    sku: str\n    name: str\n    qty: int\n    unit_price: decimal.Decimal\n\n"
    "    def total(self) -> decimal.Decimal:\n        return self.unit_price * self.qty\n" * 6,
    "============================= test session starts ==============================\n"
    "collected 37 items\n\ntests/test_api.py ........                                     [ 21%]\n"
    "tests/test_store.py ........F..                                  [ 51%]\n"
    "tests/test_pricing.py ..................                         [100%]\n\n"
    "FAILED tests/test_store.py::test_reserve_negative - AssertionError: expected "
    "ValueError for qty=-1\n========================= 1 failed, 36 passed in 1.84s "
    "=========================\n",
    "    def reserve(self, sku: str, qty: int) -> None:\n        item = self._items[sku]\n"
    "        if item.qty < qty:\n            raise OutOfStock(sku)\n        item.qty -= qty\n"
    "        self._log.append((\"reserve\", sku, qty))\n" * 4,
    "src/inventory/store.py:41:    def reserve(self, sku: str, qty: int) -> None:\n"
    "src/inventory/api.py:88:        store.reserve(body.sku, body.qty)\n"
    "tests/test_store.py:63:def test_reserve_negative(store):\n",
    "Applied 1 edit to src/inventory/store.py (+2 -0).\n",
    "ruff: All checks passed!\n",
]

_TURN_CALLS = [
    ("list_directory", {"path": "."}),
    ("read_file", {"path": "src/inventory/models.py"}),
    ("terminal", {"command": "pytest -q"}),
    ("read_file", {"path": "src/inventory/store.py", "offset": 30, "limit": 40}),
    ("search_files", {"pattern": "def reserve", "path": "src"}),
    ("patch", {"path": "src/inventory/store.py",
               "old_string": "        item = self._items[sku]\n",
               "new_string": "        if qty < 0:\n            raise ValueError(qty)\n"
                             "        item = self._items[sku]\n"}),
    ("lint", {"path": "src"}),
]

TASK = ("The test suite has a failing test about reserving a negative quantity. "
        "Find the cause, fix it with the smallest change, and make sure the "
        "tests and the linter pass.")

WRITE_ASK = (
    "Now create src/inventory/report.py: a module with a function "
    "`stock_report(store) -> str` that returns a fixed-width text table of every "
    "item (sku, name, qty, unit price, line total) sorted by sku, with a totals "
    "row, plus a `main()` that prints it for a demo store of six items. Use the "
    "write_file tool and put the complete file in `content`. Include docstrings "
    "and type hints. You have everything you need: call write_file now, in "
    "this turn, with the complete file."
)

CODE_ASK = (
    "Write a complete Python module implementing an LRU cache class with get, "
    "put, delete, a max_size argument and O(1) operations on a doubly linked "
    "list, plus a TTL option and a small test suite using unittest. Output only "
    "the code."
)

SUBTASKS = [
    "Delegate-style subtask: list every public function in src/inventory/api.py.",
    "Delegate-style subtask: find where pricing rounds to cents and report it.",
    "Delegate-style subtask: check whether README.md documents the reserve API.",
    "Delegate-style subtask: count the tests in tests/test_pricing.py.",
]


def tool_call_msg(i, name, args):
    return {"role": "assistant", "content": "",
            "tool_calls": [{"id": f"call_{i}", "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)}}]}


def tool_result_msg(i, text):
    return {"role": "tool", "tool_call_id": f"call_{i}", "content": text}


# ── the client ───────────────────────────────────────────────────────────────

def stream_chat(base, key, body, timeout, on_first=None):
    """One streamed request, timed on the client. Engine-agnostic.

    TTFT is the first delta of any kind - content, reasoning or a tool-call
    fragment - because that is when an agent UI starts showing progress.
    """
    body = dict(body, stream=True, stream_options={"include_usage": True})
    hdr = {"Content-Type": "application/json"}
    if key:
        hdr["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base + "/chat/completions",
                                 data=json.dumps(body).encode(), headers=hdr)
    t0 = time.perf_counter()
    t_first = None
    usage, timings, finish = {}, {}, None
    text, tool_args, tool_names, n_chunks = [], {}, {}, 0
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except json.JSONDecodeError:
                continue
            if ev.get("usage"):
                usage = ev["usage"]
            if ev.get("timings"):
                timings = ev["timings"]
            for ch in ev.get("choices") or []:
                d = ch.get("delta") or {}
                got = False
                if d.get("content"):
                    text.append(d["content"]); got = True
                if d.get("reasoning_content") or d.get("reasoning"):
                    got = True
                for tc in d.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        tool_names[idx] = fn["name"]
                    if fn.get("arguments"):
                        tool_args[idx] = tool_args.get(idx, "") + fn["arguments"]
                    got = True
                if got:
                    n_chunks += 1
                    if t_first is None:
                        t_first = time.perf_counter()
                        if on_first:
                            on_first()
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    t_end = time.perf_counter()
    if t_first is None:
        t_first = t_end
    n_out = usage.get("completion_tokens") or n_chunks
    gen_s = max(1e-6, t_end - t_first)
    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = timings.get("cache_n")
    calls = []
    for idx in sorted(set(tool_names) | set(tool_args)):
        raw_args = tool_args.get(idx, "")
        try:
            parsed = json.loads(raw_args) if raw_args else {}
            ok = isinstance(parsed, dict)
        except json.JSONDecodeError:
            parsed, ok = None, False
        calls.append({"name": tool_names.get(idx), "args_ok": ok,
                      "args_len": len(raw_args), "args": parsed if ok else None})
    return {
        "ttft_s": t_first - t0,
        "wall_s": t_end - t0,
        "prompt_tokens": usage.get("prompt_tokens") or timings.get("prompt_n"),
        "cached_tokens": cached,
        "completion_tokens": n_out,
        "decode_tps": (n_out - 1) / gen_s if n_out and n_out > 1 else 0.0,
        # Tokens over the whole request. The honest cross-engine number for a
        # tool call, since some servers release a tool call only once it is
        # complete - which makes the streamed rate above meaningless.
        "e2e_tps": n_out / (t_end - t0) if n_out else 0.0,
        "server_prefill_tps": timings.get("prompt_per_second"),
        "server_decode_tps": timings.get("predicted_per_second"),
        "draft_n": timings.get("draft_n"),
        "draft_accepted": timings.get("draft_n_accepted"),
        "finish": finish,
        "tool_calls": calls,
        "text_len": sum(len(t) for t in text),
    }


class Replay:
    def __init__(self, a):
        self.a = a
        self.tools = build_tools(a.prefix_tokens)
        self.rows = []
        # Unique per run, at the very front, so a run never rides a cache a
        # previous run left behind - the replay measures this run's reuse only.
        self.nonce = a.nonce or f"{random.randrange(16**8):08x}"
        self.system = {"role": "system", "content": f"Session {self.nonce}.\n" + SYSTEM}

    def body(self, messages, max_tokens, **kw):
        b = {"model": self.a.model, "messages": messages, "tools": self.tools,
             "max_tokens": max_tokens, "temperature": self.a.temp, "seed": 1}
        if self.a.temp == 0:
            b.update({"top_k": 1, "top_p": 1})
        if self.a.no_think:
            b["chat_template_kwargs"] = {"enable_thinking": False}
        b.update(kw)
        return b

    def send(self, phase, label, messages, max_tokens, **kw):
        try:
            r = stream_chat(self.a.base, self.a.key, self.body(messages, max_tokens, **kw),
                            self.a.timeout)
        except urllib.error.HTTPError as e:
            r = {"error": f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}"}
        except Exception as e:                                  # noqa: BLE001
            r = {"error": f"{type(e).__name__}: {e}"}
        r.update({"phase": phase, "label": label})
        self.rows.append(r)
        self.show(r)
        return r

    def show(self, r):
        if "error" in r:
            print(f"  {r['phase']:<10} {r['label']:<14} ERROR {r['error']}")
            return
        tc = r["tool_calls"]
        tcs = (f"{tc[0]['name']}{'' if tc[0]['args_ok'] else '!BAD'}" if tc else "-")
        acc = ""
        if r.get("draft_n"):
            acc = f"{100.0 * (r.get('draft_accepted') or 0) / r['draft_n']:.0f}%"
        print(f"  {r['phase']:<10} {r['label']:<14} {r['prompt_tokens'] or 0:>7} "
              f"{r['cached_tokens'] if r['cached_tokens'] is not None else '?':>7} "
              f"{r['ttft_s']:>7.2f} {r['completion_tokens'] or 0:>5} {r['decode_tps']:>7.1f} "
              f"{acc:>5} {r['finish'] or '':<10} {tcs}")

    # ── phases ───────────────────────────────────────────────────────────────
    def run(self):
        print(f"\n  Hermes replay   model={self.a.model}   base={self.a.base}")
        print(f"  tools={len(self.tools)}   prefix~{self.a.prefix_tokens} tok   "
              f"turns={self.a.turns}   temp={self.a.temp}   nonce={self.nonce}\n")
        print("  phase      step            prompt  cached    TTFT   out   tok/s   acc "
              "finish     tool")
        print("  " + "-" * 92)
        t0 = time.perf_counter()
        msgs = [self.system, {"role": "user", "content": TASK}]
        if "prefix" in self.a.phases:
            # The cold first turn: the whole tool prefix is new to the server.
            self.send("prefix", "cold", msgs, self.a.step_tokens)
        if "turns" in self.a.phases:
            for i in range(self.a.turns):
                name, args = _TURN_CALLS[i % len(_TURN_CALLS)]
                msgs = msgs + [tool_call_msg(i, name, args),
                               tool_result_msg(i, _RESULTS[i % len(_RESULTS)])]
                self.send("turns", f"turn {i + 1}", msgs, self.a.step_tokens)
        if "write" in self.a.phases:
            # The long tool call: a whole file in the arguments. This is the
            # decode-heavy step, and the one a short max_tokens truncates.
            wmsgs = msgs + [{"role": "user", "content": WRITE_ASK}]
            # Whether the model answers this with the file or with a read_file
            # first is partly the model's choice at temperature 0.6, and neither
            # llama.cpp nor MTPLX enforced a named tool_choice when it was
            # tried. So the write step is agent-shaped but soft; the "code"
            # phase below is the decode number to compare engines on.
            choice = ({"type": "function", "function": {"name": "write_file"}}
                      if self.a.write_choice == "named" else "required")
            self.send("write", "write_file", wmsgs, self.a.write_tokens,
                      tool_choice=choice)
        if "subagents" in self.a.phases:
            self.subagents(msgs)
        if "compress" in self.a.phases:
            # Compaction: the history between the task and the last two turns
            # becomes one summary. Every token after the edit point is new.
            summary = {"role": "user", "content":
                       "[Context summary] Listed the repo, read models.py and store.py, "
                       "ran the tests (1 failure: reserve accepts negative qty), and "
                       "located reserve() in store.py:41."}
            tail = msgs[-2:] if len(msgs) > 4 else []
            cmsgs = [self.system, {"role": "user", "content": TASK}, summary] + tail
            self.send("compress", "after", cmsgs, self.a.step_tokens)
            cmsgs = cmsgs + [tool_call_msg(900, "lint", {"path": "src"}),
                             tool_result_msg(900, _RESULTS[-1])]
            self.send("compress", "next turn", cmsgs, self.a.step_tokens)
        if "code" in self.a.phases:
            # Plain generation, no tools: the decode rate on real code, the same
            # way on every engine. The write step above is the agent-shaped
            # version, but whether a model answers it with the file or with a
            # read_file first is the model's choice, not the server's.
            body = {"model": self.a.model, "max_tokens": self.a.write_tokens,
                    "temperature": self.a.temp, "seed": 1,
                    "messages": [{"role": "user", "content": f"[{self.nonce}] " + CODE_ASK}]}
            if self.a.no_think:
                body["chat_template_kwargs"] = {"enable_thinking": False}
            try:
                r = stream_chat(self.a.base, self.a.key, body, self.a.timeout)
            except Exception as e:                              # noqa: BLE001
                r = {"error": f"{type(e).__name__}: {e}"}
            r.update({"phase": "code", "label": "plain code"})
            self.rows.append(r)
            self.show(r)
        total = time.perf_counter() - t0
        return self.summary(total)

    def subagents(self, msgs):
        # Hermes delegate_task: N children share the system prompt and tools,
        # and differ from their first user message on. Sent together.
        n = self.a.subagents
        bodies = [[self.system, {"role": "user", "content": SUBTASKS[i % len(SUBTASKS)]}]
                  for i in range(n)]
        t0 = time.perf_counter()
        with cf.ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(self.send, "subagents", f"child {i + 1}", b, self.a.step_tokens)
                    for i, b in enumerate(bodies)]
            for f in futs:
                f.result()
        wall = time.perf_counter() - t0
        self.rows.append({"phase": "subagents", "label": "wall", "wall_s": wall})
        print(f"  {'subagents':<10} {'all done':<14} {'':>7} {'':>7} {wall:>7.2f}s wall")

    def summary(self, total):
        ok = [r for r in self.rows if "ttft_s" in r]
        errs = [r for r in self.rows if "error" in r]
        by = {}
        for r in ok:
            by.setdefault(r["phase"], []).append(r)
        out = {"total_s": total, "nonce": self.nonce, "errors": len(errs)}
        print("  " + "-" * 92)
        print(f"  TOTAL replay wall time   {total:8.2f} s     errors: {len(errs)}")
        for ph in PHASES:
            rs = by.get(ph)
            if not rs:
                continue
            ttft = [r["ttft_s"] for r in rs]
            dec = [r["decode_tps"] for r in rs if r["completion_tokens"] and r["completion_tokens"] > 8]
            out[ph] = {"ttft_median": statistics.median(ttft), "ttft_max": max(ttft),
                       "decode_median": statistics.median(dec) if dec else None,
                       "n": len(rs)}
            print(f"  {ph:<10} TTFT median {statistics.median(ttft):6.2f} s   max {max(ttft):6.2f} s"
                  + (f"   decode median {statistics.median(dec):6.1f} t/s" if dec else ""))
        calls = [c for r in ok for c in r["tool_calls"]]
        bad = [c for c in calls if not c["args_ok"]]
        out["tool_calls"] = len(calls)
        out["tool_calls_bad"] = len(bad)
        print(f"  tool calls: {len(calls)} emitted, {len(bad)} with unparseable arguments")
        wr = by.get("write")
        if wr:
            w = wr[0]
            wc = [c for c in w["tool_calls"] if c["name"] == "write_file"]
            good = bool(wc and wc[0]["args_ok"] and wc[0]["args"]
                        and wc[0]["args"].get("path") and wc[0]["args"].get("content"))
            out["write_ok"] = good
            print(f"  write_file: {'complete' if good else 'MISSING or TRUNCATED'} "
                  f"({w['completion_tokens']} tokens, finish={w['finish']})")
        out["rows"] = self.rows
        return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base", default=os.environ.get("UPINEL_BASE", "http://127.0.0.1:8000/v1"))
    ap.add_argument("--key", default=None, help="API key; default reads --key-file")
    ap.add_argument("--key-file", default="run/api-key")
    ap.add_argument("--model", default=os.environ.get("UPINEL_MODEL", "Upinel-AIOS"))
    ap.add_argument("--prefix-tokens", type=int, default=15000,
                    help="approximate size of system prompt + tools (Hermes: 10-25k)")
    ap.add_argument("--turns", type=int, default=7)
    ap.add_argument("--subagents", type=int, default=4)
    ap.add_argument("--step-tokens", type=int, default=160,
                    help="max_tokens for an ordinary agent step")
    ap.add_argument("--write-tokens", type=int, default=2048,
                    help="max_tokens for the file-writing step")
    ap.add_argument("--temp", type=float, default=0.6,
                    help="sampling temperature; 0 = greedy (flatters drafting)")
    ap.add_argument("--no-think", action="store_true",
                    help="send enable_thinking=false in chat_template_kwargs")
    ap.add_argument("--write-choice", choices=("named", "required"), default="required",
                    help="tool_choice for the write step")
    ap.add_argument("--phases", default=",".join(PHASES))
    ap.add_argument("--nonce", default="")
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--json", help="write the full result here")
    a = ap.parse_args()
    a.phases = [p.strip() for p in a.phases.split(",") if p.strip()]
    if a.key is None:
        try:
            a.key = open(a.key_file).read().strip()
        except OSError:
            a.key = ""
    res = Replay(a).run()
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(res, fh, indent=2)
        print(f"\n  raw results -> {a.json}")
    return 1 if res["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
