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
"""Offline checks for lib/agent-router.py, against a fake llama-server.

No model, no GPU: a stdlib HTTP server plays llama-server - it renders chat
templates, tokenises, lists slots, saves and restores them, and streams a chat
completion - and records every call the router makes, so the router's
decisions can be checked one by one:

  * the first conversation with a big prefix builds a snapshot, in a free slot,
    and the request is pinned to that slot
  * a second, different conversation sharing the prefix gets a restore
  * a later turn of a conversation is passed through untouched
  * a small prefix is passed through untouched
  * an unauthenticated request never makes the router touch a slot
  * streamed responses arrive intact
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
KEY = "test-key-123"

PASSED = 0
FAILED = 0
CALLS = []
SNAP_DIR = [""]


def check(label, got, want):
    global PASSED, FAILED
    if got == want:
        print(f"  [PASS] {label}")
        PASSED += 1
    else:
        print(f"  [FAIL] {label}\n         got  {got!r}\n         want {want!r}")
        FAILED += 1


class Fake(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        return self.headers.get("Authorization") == "Bearer " + KEY

    def do_GET(self):
        CALLS.append(("GET", self.path, None))
        if self.path == "/health":
            return self._json({"status": "ok"})
        if self.path == "/slots":
            return self._json([{"id": i, "is_processing": False, "n_past": 0} for i in range(4)])
        self._json({}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        CALLS.append(("POST", self.path, body))
        if not self._authed():
            return self._json({"error": "unauthorized"}, 401)
        if self.path == "/apply-template":
            text = "".join(m.get("content", "") for m in body["messages"])
            text += json.dumps(body.get("tools") or [])
            return self._json({"prompt": text})
        if self.path == "/tokenize":
            # One token per character: enough to make common prefixes exact.
            return self._json({"tokens": [ord(c) for c in body["content"]]})
        if self.path.startswith("/slots/") and "action=save" in self.path:
            # llama-server writes the file into --slot-save-path itself.
            open(os.path.join(SNAP_DIR[0], body["filename"]), "wb").write(b"kv")
            return self._json({"ok": True})
        if self.path.startswith("/slots/") or self.path == "/completion":
            return self._json({"ok": True})
        if self.path == "/v1/chat/completions":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for part in ("Hel", "lo"):
                ev = f'data: {json.dumps({"choices": [{"delta": {"content": part}}]})}\n\n'.encode()
                self.wfile.write(f"{len(ev):x}\r\n".encode() + ev + b"\r\n")
                self.wfile.flush()
                time.sleep(0.05)
            ev = b"data: [DONE]\n\n"
            self.wfile.write(f"{len(ev):x}\r\n".encode() + ev + b"\r\n0\r\n\r\n")
            return
        self._json({}, 404)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def chat(port, messages, key=KEY):
    body = {"model": "m", "messages": messages, "stream": True,
            "tools": [{"type": "function", "function": {"name": "t", "description": "d" * 50}}]}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"


def forwarded_chats():
    return [b for m, p, b in CALLS if p == "/v1/chat/completions"]


def main():
    bport, rport = free_port(), free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", bport), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory() as td:
        SNAP_DIR[0] = td
        keyf = os.path.join(td, "key")
        open(keyf, "w").write(KEY)
        proc = subprocess.Popen(
            [sys.executable, os.path.join(REPO, "lib", "agent-router.py"),
             "--listen", f"127.0.0.1:{rport}", "--backend", f"http://127.0.0.1:{bport}",
             "--api-key-file", keyf, "--snap-dir", td, "--ident", "t", "--min-prefix", "200"],
            stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{rport}/health", timeout=1)
                    break
                except Exception:                               # noqa: BLE001
                    time.sleep(0.1)

            big = {"role": "system", "content": "You are an agent. " * 40}
            print("\n  prefix snapshots\n")
            CALLS.clear()
            out = chat(rport, [big, {"role": "user", "content": "task one"}])
            check("a streamed reply arrives intact", "Hel" in out and "lo" in out and "[DONE]" in out, True)
            paths = [p for m, p, b in CALLS]
            check("the first conversation builds a snapshot",
                  "/completion" in paths and any(p.startswith("/slots/") and "save" in p for p in paths), True)
            check("and is pinned to the slot it was built in",
                  "id_slot" in forwarded_chats()[-1], True)

            CALLS.clear()
            chat(rport, [big, {"role": "user", "content": "task two"}])
            paths = [p for m, p, b in CALLS]
            check("a second conversation sharing the prefix is restored, not rebuilt",
                  (any("restore" in p for p in paths), "/completion" in paths), (True, False))
            check("and pinned", "id_slot" in forwarded_chats()[-1], True)

            CALLS.clear()
            chat(rport, [big, {"role": "user", "content": "task two"},
                         {"role": "assistant", "content": "ok"},
                         {"role": "user", "content": "next"}])
            paths = [p for m, p, b in CALLS]
            check("a later turn of that conversation passes straight through",
                  (any(p.startswith("/slots/") for p in paths), "id_slot" in forwarded_chats()[-1]),
                  (False, False))

            print("\n  what it leaves alone\n")
            CALLS.clear()
            chat(rport, [{"role": "system", "content": "short"}, {"role": "user", "content": "hi"}])
            paths = [p for m, p, b in CALLS]
            check("a prefix under --min-prefix is never snapshotted",
                  any(p.startswith("/slots/") and "action" in p for p in paths), False)

            CALLS.clear()
            out = chat(rport, [big, {"role": "user", "content": "task three"}], key="wrong")
            paths = [p for m, p, b in CALLS]
            check("an unauthenticated request gets the backend's 401", out, "HTTP 401")
            check("and never makes the router touch a slot",
                  [p for p in paths if p != "/v1/chat/completions"], [])

            stats = json.load(urllib.request.urlopen(f"http://127.0.0.1:{rport}/router/stats"))
            check("stats count one build and one restore",
                  (stats["created"], stats["restored"]), (1, 1))
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            srv.shutdown()
    print(f"\n  {PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
