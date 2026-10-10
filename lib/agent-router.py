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
"""Prefix-snapshot router in front of llama-server, for agent harnesses.

The problem it solves
---------------------
An agent's system prompt and tool schemas are a large prefix - 10-25k tokens
for Hermes - that every request repeats. llama.cpp reuses a prefix only from
the slot that already holds it (or from its host-RAM prompt cache, which is
filled when a slot goes idle). Two cases fall through that:

  * Siblings. Hermes runs delegate_task children in parallel, and they share
    the parent's system prompt and tools exactly - their goal is their first
    user message. The first child gets the slot holding the prefix; every other
    child lands on an empty slot and prefills the whole prefix cold. Measured
    on Gemma 4 26B with a 17k-token prefix: four children took 35 s together,
    against 1.7 s when they happened to arrive one at a time.
  * Restarts. A fresh server has nothing cached, so the first turn of every
    session after ./restart.sh pays the full prefix again.

What it does
------------
The first time it sees a prefix (system message + tools, at least --min-prefix
tokens), it has llama-server compute exactly that prefix in a free slot and
save the slot to disk - the prefix had to be computed anyway, so this costs a
disk write and no extra prefill. From then on, whenever a request with that
prefix would land on a slot that does not hold it - a sibling running alongside
another, or the first request after a restart - the snapshot is restored into
a free slot and the request is pinned to it. A restore reads a file; a prefill
runs the model over every token.

Everything else passes through untouched, and any failure in the router's own
logic falls back to plain pass-through, so the worst case is the behaviour
llama-server already had.

Exactness: a restored slot holds the KV state llama-server computed for those
exact tokens, and the request extends them, so the output is what a cold
prefill would have produced. Hybrid (recurrent) and sliding-window models are
fine for the same reason - nothing is rolled back.

    lib/agent-router.py --listen 0.0.0.0:8000 --backend http://127.0.0.1:8100 \\
        --api-key-file run/api-key --snap-dir run/slots
"""

import argparse
import hashlib
import http.client
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
       "trailers", "transfer-encoding", "upgrade", "host", "content-length"}
CHAT_PATHS = {"/v1/chat/completions", "/chat/completions"}


def log(msg):
    sys.stderr.write(time.strftime("%H:%M:%S ") + "[router] " + msg + "\n")
    sys.stderr.flush()


class Backend:
    """Small JSON client for llama-server's own endpoints."""

    def __init__(self, url, key):
        u = urllib.parse.urlparse(url)
        self.host, self.port = u.hostname, u.port or 80
        self.key = key

    def conn(self, timeout=3600):
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout)

    def call(self, method, path, body=None, timeout=3600):
        c = self.conn(timeout)
        hdr = {"Content-Type": "application/json"}
        if self.key:
            hdr["Authorization"] = "Bearer " + self.key
        c.request(method, path, body=json.dumps(body).encode() if body is not None else None,
                  headers=hdr)
        r = c.getresponse()
        data = r.read()
        c.close()
        if r.status >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status}: {data[:200]!r}")
        return json.loads(data) if data else {}


class Prefix:
    def __init__(self, key):
        self.key = key
        self.lock = threading.Lock()
        self.tokens = None       # the exact shared prefix, as token ids
        self.snapshot = None     # filename under the slot-save path, once saved


class Router:
    def __init__(self, a):
        self.a = a
        key = ""
        if a.api_key_file and os.path.exists(a.api_key_file):
            key = open(a.api_key_file).read().strip()
        self.be = Backend(a.backend, key)
        self.prefixes = {}
        self.plock = threading.Lock()
        self.reserved = set()     # slots pinned to an in-flight request by us
        self.slock = threading.Lock()
        self.seen = {}            # conversation-prefix hash -> last seen
        self.clock = threading.Lock()
        # Held from a restore until the pinned request's first byte. With
        # unified KV llama-server parks and clears every idle slot whenever a
        # new task arrives, so a slot restored while a sibling's request was
        # still on its way got wiped before its own request reached it -
        # measured: two of four concurrent siblings re-prefilled cold. Admitting
        # pinned requests one at a time closes that window. Each admission is a
        # restore plus a short suffix prefill, well under a second; decoding
        # still runs concurrently.
        self.admit = threading.Lock()
        self.ident = a.ident
        os.makedirs(a.snap_dir, exist_ok=True)
        self.stats = {"restored": 0, "created": 0, "passthrough": 0, "errors": 0}

    # ── prefix discovery ────────────────────────────────────────────────────
    def prefix_messages(self, msgs):
        """The leading system/developer messages: the part siblings share."""
        out = []
        for m in msgs:
            if m.get("role") in ("system", "developer"):
                out.append(m)
            else:
                break
        return out

    def key_for(self, body):
        head = self.prefix_messages(body.get("messages") or [])
        if not head and not body.get("tools"):
            return None
        mat = json.dumps({"h": head, "t": body.get("tools"),
                          "k": body.get("chat_template_kwargs"), "id": self.ident},
                         sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(mat.encode()).hexdigest()[:24]

    def compute_tokens(self, body):
        """Exact prefix tokens: the common start of renders that differ only in
        the first user message, tokenised the way the server tokenises a chat
        prompt. Taken in token space, not string space, so no boundary token
        can differ from what a real request produces.

        The probes must differ from their very first character. A shared
        leading character extends the common prefix past where real messages
        diverge, and on a sliding-window or recurrent model a slot cannot be
        rolled back even a few tokens - the request then re-prefills from zero.
        Three probes, and two tokens of margin, because the first content
        character can merge with the template's newline into one token."""
        head = self.prefix_messages(body["messages"])
        toks = []
        for probe in ("Alpha probe.", "zulu probe!", "7 probe?"):
            req = {"messages": head + [{"role": "user", "content": probe}]}
            if body.get("tools"):
                req["tools"] = body["tools"]
            if body.get("chat_template_kwargs"):
                req["chat_template_kwargs"] = body["chat_template_kwargs"]
            prompt = self.be.call("POST", "/apply-template", req)["prompt"]
            t = self.be.call("POST", "/tokenize",
                             {"content": prompt, "add_special": True, "parse_special": True})
            toks.append(t["tokens"])
        n = min(len(t) for t in toks)
        for i in range(n):
            if any(t[i] != toks[0][i] for t in toks[1:]):
                n = i
                break
        return toks[0][:max(0, n - 2)]

    # ── slots ───────────────────────────────────────────────────────────────
    def free_slot(self):
        """An idle slot nobody has pinned. Prefers the emptiest one, so a slot
        holding someone's conversation is the last to be overwritten (with
        unified KV llama-server has already parked idle slots in its prompt
        cache by the time a new task arrives, so this is belt and braces)."""
        slots = self.be.call("GET", "/slots", timeout=30)
        best = None
        with self.slock:
            for s in slots:
                sid = s.get("id")
                if s.get("is_processing") or sid in self.reserved:
                    continue
                used = s.get("n_past") or s.get("n_prompt_tokens") or len(s.get("prompt") or "")
                if best is None or used < best[1]:
                    best = (sid, used)
            if best is None:
                return None
            self.reserved.add(best[0])
            return best[0]

    def release(self, sid):
        with self.slock:
            self.reserved.discard(sid)

    def snap_name(self, key):
        return f"pfx-{key}.bin"

    def snap_path(self, key):
        return os.path.join(self.a.snap_dir, self.snap_name(key))

    def evict(self):
        files = [os.path.join(self.a.snap_dir, f) for f in os.listdir(self.a.snap_dir)
                 if f.startswith("pfx-") and f.endswith(".bin")]
        files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        total = 0
        for i, p in enumerate(files):
            total += os.path.getsize(p)
            if i >= self.a.max_snapshots or total > self.a.max_snap_gb * 1e9:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    # ── the decision ────────────────────────────────────────────────────────
    def conv_hashes(self, msgs, head_n):
        """Rolling hashes of every message-prefix longer than the shared head.
        A request whose prefix matches one already seen is a later turn of a
        conversation some slot already holds; a request matching none is a
        new conversation that only shares the head."""
        h = hashlib.sha256(self.ident.encode())
        out = []
        for i, m in enumerate(msgs):
            h.update(json.dumps(m, sort_keys=True, ensure_ascii=False).encode())
            if i + 1 > head_n:
                out.append(h.copy().hexdigest()[:24])
        return out

    def is_continuation(self, hashes):
        # The last hash is this request itself; any earlier one seen before
        # means the conversation was served already.
        with self.clock:
            hit = any(x in self.seen for x in hashes[:-1])
            for x in hashes:
                self.seen[x] = time.time()
            if len(self.seen) > 50000:
                for k in sorted(self.seen, key=self.seen.get)[:10000]:
                    del self.seen[k]
        return hit

    def new_conversation(self, body):
        """True for the first request of a conversation that shares a large
        head; False for a later turn, whose own slot (or llama-server's prompt
        cache) holds far more than the head - that choice stays llama-server's."""
        if self.key_for(body) is None:
            return False
        msgs = body["messages"]
        return not self.is_continuation(self.conv_hashes(msgs, len(self.prefix_messages(msgs))))

    def plan(self, body):
        """Return (slot to pin or None, Prefix or None) for a new conversation."""
        key = self.key_for(body)
        if key is None:
            return None, None
        with self.plock:
            p = self.prefixes.get(key)
            if p is None:
                p = self.prefixes[key] = Prefix(key)
                if os.path.exists(self.snap_path(key)):
                    p.snapshot = self.snap_name(key)
        with p.lock:
            if p.snapshot is None:
                if p.tokens is None:
                    p.tokens = self.compute_tokens(body)
                if len(p.tokens) < self.a.min_prefix:
                    return None, None
            sid = self.free_slot()
            if sid is None:
                return None, None
            try:
                t0 = time.time()
                if p.snapshot is None:
                    self.be.call("POST", "/completion",
                                 {"prompt": p.tokens, "n_predict": 0, "id_slot": sid,
                                  "cache_prompt": True})
                    self.be.call("POST", f"/slots/{sid}?action=save",
                                 {"filename": self.snap_name(p.key)})
                    p.snapshot = self.snap_name(p.key)
                    self.stats["created"] += 1
                    log(f"snapshot {p.key[:8]}: {len(p.tokens)} tokens, built in slot {sid} "
                        f"in {time.time() - t0:.2f}s")
                    self.evict()
                else:
                    self.be.call("POST", f"/slots/{sid}?action=restore",
                                 {"filename": p.snapshot})
                    try:                     # LRU order for evict(); cosmetic
                        os.utime(self.snap_path(p.key), None)
                    except OSError:
                        pass
                    self.stats["restored"] += 1
                    log(f"restored {p.key[:8]} into slot {sid} in {time.time() - t0:.2f}s")
                return sid, p
            except Exception as e:                              # noqa: BLE001
                self.stats["errors"] += 1
                log(f"snapshot step failed, passing through: {e}")
                if p.snapshot and not os.path.exists(self.snap_path(p.key)):
                    p.snapshot = None
                self.release(sid)
                return None, None


ROUTER = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "UpinelAIOS-router"

    def log_message(self, fmt, *args):                          # quiet access log
        pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _forward(self, method, raw, on_first=None):
        r = ROUTER
        c = r.be.conn()
        hdr = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        if raw:
            hdr["Content-Length"] = str(len(raw))
        try:
            c.request(method, self.path, body=raw or None, headers=hdr)
            resp = c.getresponse()
        except (ConnectionError, OSError) as e:
            self.send_error(502, f"backend unavailable: {e}")
            return
        self.send_response(resp.status, resp.reason)
        chunked = resp.getheader("Content-Length") is None
        for k, v in resp.getheaders():
            if k.lower() not in HOP:
                self.send_header(k, v)
        if chunked:
            self.send_header("Transfer-Encoding", "chunked")
        else:
            self.send_header("Content-Length", resp.getheader("Content-Length"))
        self.end_headers()
        try:
            while True:
                data = resp.read1(65536) if chunked else resp.read(65536)
                if on_first:
                    on_first()
                    on_first = None
                if not data:
                    break
                if chunked:
                    self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                else:
                    self.wfile.write(data)
                self.wfile.flush()
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                       # client went away; llama-server notices too
        finally:
            if on_first:
                on_first()
            c.close()

    def do_GET(self):
        if self.path == "/router/stats":
            data = json.dumps(dict(ROUTER.stats, prefixes=len(ROUTER.prefixes))).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self._forward("GET", b"")

    def do_DELETE(self):
        self._forward("DELETE", self._body())

    def do_OPTIONS(self):
        self._forward("OPTIONS", self._body())

    def do_POST(self):
        raw = self._body()
        path = self.path.split("?", 1)[0]
        if path not in CHAT_PATHS:
            self._forward("POST", raw)
            return
        sid, p = None, None
        held = []
        # Only an authenticated request may make the router touch slots or
        # write a snapshot. llama-server checks the key too, but only after the
        # router has acted, so an unauthenticated LAN client could otherwise
        # make it build and store snapshots before being told 401.
        if ROUTER.be.key and self.headers.get("Authorization", "") != "Bearer " + ROUTER.be.key:
            self._forward("POST", raw)
            return

        def unlock():
            if held:
                held.pop().release()

        try:
            body = json.loads(raw)
            if isinstance(body, dict) and body.get("messages") and "id_slot" not in body \
                    and ROUTER.new_conversation(body):
                ROUTER.admit.acquire()
                held.append(ROUTER.admit)
                sid, p = ROUTER.plan(body)
                if sid is not None:
                    body["id_slot"] = sid
                    raw = json.dumps(body).encode()
                else:
                    ROUTER.stats["passthrough"] += 1
                    unlock()
        except Exception as e:                                  # noqa: BLE001
            ROUTER.stats["errors"] += 1
            unlock()
            log(f"plan failed, passing through: {type(e).__name__}: {e}")
        try:
            self._forward("POST", raw, on_first=unlock if held else None)
        finally:
            unlock()
            if sid is not None:
                ROUTER.release(sid)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--listen", default="127.0.0.1:8000", help="host:port to serve on")
    ap.add_argument("--backend", default="http://127.0.0.1:8100")
    ap.add_argument("--api-key-file", default="")
    ap.add_argument("--snap-dir", required=True,
                    help="llama-server's --slot-save-path (snapshots are files there)")
    ap.add_argument("--ident", default="",
                    help="model + runtime identity; snapshots never cross it")
    ap.add_argument("--min-prefix", type=int, default=2048,
                    help="smaller prefixes are cheaper to prefill than to restore")
    ap.add_argument("--max-snapshots", type=int, default=8)
    ap.add_argument("--max-snap-gb", type=float, default=8.0)
    a = ap.parse_args()
    global ROUTER
    ROUTER = Router(a)
    host, _, port = a.listen.rpartition(":")
    srv = ThreadingHTTPServer((host or "127.0.0.1", int(port)), Handler)
    srv.daemon_threads = True
    log(f"listening on {a.listen} -> {a.backend}, snapshots in {a.snap_dir}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
