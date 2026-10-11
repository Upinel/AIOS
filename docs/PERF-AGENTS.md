<!--
  Nova Upinel Chow, MSc, LLM, BBA, MENSA  ·  dev@upinel.com  ·  upinel.com
  Copyright (c) 2026 Nova Upinel Chow. All rights reserved.
  Upinel Personal Free License - see LICENSE.
-->
# Agent performance, October 2026

What changed in the performance pass of 2026-10-10, why, and the measurements
behind each decision. Everything here was measured on the reference machine —
**M5 Pro, 20-core GPU, 64 GB, macOS 27.0** — unless a row says otherwise.
The plan this executes is [plan.md](../plan.md).

## How it is measured

[`bench/hermes-replay.py`](../bench/hermes-replay.py) replays one agent session
the way Hermes Agent, Claude Code and OpenCode drive a server:

| phase | what it does | what it exposes |
|---|---|---|
| prefix | first turn: a system prompt plus 40 tool schemas (~17k tokens), sent as real `tools` | cold prefill |
| turns | 7 appended tool-call / tool-result turns | warm-turn TTFT, prefix reuse |
| write | `write_file` with a whole module in its arguments | decode on real agent output |
| subagents | 4 `delegate_task`-style children at once, sharing the prefix | prefix sharing across sessions |
| compress | the middle of the history replaced by a summary | re-prefill after a rewrite |

The assistant turns are scripted, so every config replays byte-identical
prompts; generation is real and sampled at temperature 0.6 (greedy pins draft
acceptance near 100% and flatters deep drafting). Timing is on the client, so
llama.cpp and MTPLX are compared on the same clock.

[`bench/matrix.py`](../bench/matrix.py) runs any set of server command lines
against that replay, round after round in a fresh random order, and reports
best-of and median. Two columns are designed to resist a model simply saying
less: **excl. write s** is the trace without the file-write step, and
**write e2e t/s** only counts a write that actually produced the complete file.

## 1. Runtimes were far behind, and pinned to nothing

| runtime | was | now |
|---|---|---|
| llama.cpp | b10809, Homebrew, floating | **b11541**, pinned and checksummed (`./lib/fetch-runtime.sh llama`, rollback with `--rollback`) |
| MTPLX | **2.9.1** actually running — the MTPLX app's `~/.mtplx/bin` shadowed Homebrew's 2.11.2 on PATH | **2.12.2**, and the engine now runs the newest installed `mtplx` |

Gemma 4 26B-A4B, same flags, 8k-token prefix, best of 2 interleaved:

| | total | cold TTFT | file-write | 4 subagents |
|---|---:|---:|---:|---:|
| b10809 | 42.0 s | 6.21 s | 92 t/s | 21.0 s |
| **b11541** | **34.7 s** | **4.98 s** | **96 t/s** | **17.1 s** |

−17% on the whole session from the upgrade alone: the M5 tensor-API flash
attention for f16 KV, and the new probabilistic draft sampling.

MTPLX, same machine, 17k-token prefix, best of 2 interleaved, each model at its
best depth:

| | 2.9.1 | **2.12.2** |
|---|---:|---:|
| Qwen 3.6 35B-A3B, whole session | 49.9 s | **32.6 s** (−35%) |
| Qwen 3.6 35B-A3B, file-write decode | 21 t/s | **70 t/s** (3.3x) |
| Qwen 3.6 35B-A3B, cold TTFT | 11.6 s | **7.5 s** |
| Qwen 3.8 27B, whole session | 234.7 s | **117.9 s** (−50%) |
| Qwen 3.8 27B, file-write decode | 9.6 t/s | **22.4 t/s** (2.3x) |

MTPLX buffers a tool call until it is complete, so its "TTFT" on a tool-call
turn includes generating the call; the totals are the comparable numbers.

## 2. The agent profile was giving each session a quarter of the context

`--parallel 4` without `--kv-unified` makes llama.cpp split `-c` evenly. The
server log said `n_slots = 4, n_ctx_slot = 32768`: Hermes was told 128k and had
32k. On a 16 GB Mac at 16k it had 4k. Sessions now share one pool. Speed is
unchanged (34.9 s against 34.7 s); the fix is capacity.

`PARALLEL_SLOTS` (llama.cpp) and `MAX_CONCURRENT` (MTPLX) are now one key,
`SESSIONS`. The shipped file had them disagree (1 and 4).

## 3. Speculative decoding: the bake-off

### Gemma 4 26B-A4B (llama.cpp b11541, 17k prefix, 2 rounds)

| config | total | excl. write | cold TTFT | write e2e | draft acceptance |
|---|---:|---:|---:|---:|---:|
| MTP depth 2 (old tuned) | 29.4 s | 17.6 s | 12.2 s | 69 t/s | 0.89 |
| **MTP depth 3** | **28.8 s** | 17.6 s | 12.3 s | **80 t/s** | 0.79 |
| MTP 2 + n-gram | 29.0 s | 17.3 s | 11.8 s | 70 t/s | 0.85 |
| MTP 2, `-ub 2048` | 29.8 s | **16.7 s** | **11.1 s** | 67 t/s | 0.83 |
| MTP 2, `mmap+mlock` | 30.0 s | 17.2 s | 12.0 s | 66 t/s | 0.87 |
| DFlash, block 8 | 40.8 s | 22.6 s | 12.6 s | 43 t/s | **0.16** |
| DFlash, block 16 | 55.2 s | 24.8 s | 12.1 s | 27 t/s | **0.09** |

- **Depth 3** is the new default: +16% on the file write. The old tuning file
  said 2 — measured on b10809, before probabilistic drafting.
- **`-ub 2048` on M5**: 9% less cold TTFT (prefill ~1,530 against ~1,400 t/s).
  It is now `UBATCH_SIZE="auto"`: 2048 on an M5 with 32 GB+, 512 elsewhere.
- **DFlash is rejected here.** The z-lab drafter was trained against the
  aligned Gemma; against this heretic fine-tune it accepts 9-16% of drafts.
- n-gram stacking and `mmap+mlock` are speed-neutral. `mmap+mlock` is adopted
  anyway: same residency guarantee as `mlock`, without copying the weights.

### Qwen 3.8 27B dense

| engine | config | total | cold TTFT | write e2e |
|---|---|---:|---:|---:|
| llama.cpp b11541 | HauhauCS Q4_K_P, no draft | 378 s | 68.5 s | ~10 t/s |
| llama.cpp b11541 | + z-lab DFlash2, block 8 | 365 s | 77.8 s | ~11 t/s |
| llama.cpp b11541 | + z-lab DFlash2, block 16 | 342 s | — | — |
| **MTPLX 2.12.2** | **depth 3** | **118 s** | 53 s | **22.4 t/s** |
| MTPLX 2.12.2 | depth 2 | 118 s | 48 s | 20.1 t/s |

DFlash2 accepts only ~50-60% of its drafts on the uncensored target, so the
27B stays on MTPLX, at MTPLX's own default depth 3. (A DFlash2 drafter
trained on this exact fine-tune does not exist on Hugging Face as a GGUF.)

### Qwen 3.6 35B-A3B on MTPLX 2.12.2

| depth | total | excl. write | write e2e | 4 subagents |
|---:|---:|---:|---:|---:|
| **1** | **32.6 s** | **19.8 s** | **70 t/s** | **3.7 s** |
| 2 | 33.6 s | 20.2 s | 69 t/s | 4.7 s |
| 3 | 34.0 s | 20.8 s | 62 t/s | 4.8 s |

Depth 1 stays the default, as it was on 2.9.1.

### Qwen 3.6 35B-A3B on llama.cpp — the native MTP head

`llmfan46/Qwen3.6-35B-A3B-uncensored-heretic-Native-MTP-Preserved-GGUF` keeps
Qwen's MTP head inside the weights (`qwen35moe.nextn_predict_layers = 1`), so
llama.cpp drafts from the main file with no sidecar and no patched build. The
engine now detects that from the GGUF metadata; `gguf-q-35ba3b` points at this
repo instead of a build that stripped the head.

| config (b11541) | plain-code decode | acceptance |
|---|---:|---:|
| no draft | 75.8 t/s | — |
| native MTP, depth 1 | — | 0.96 |
| **native MTP, depth 2** | **105.5 t/s** | 0.89 |
| native MTP, depth 3 | — | 0.85 |
| z-lab DFlash, block 8 | — | **0.18** |
| *MTPLX 2.12.2, depth 1 (for reference)* | *100.9 t/s* | |

Through the whole agent replay, with the router in front of llama.cpp:

| | excl. write | cold TTFT | 4 subagents | code decode |
|---|---:|---:|---:|---:|
| **MTPLX 2.12.2, depth 1** | **41.3 s** | **8.3 s** | 4.7 s | 100.6 t/s |
| llama.cpp native MTP 2 + router | 46.7 s | 12.0 s | **3.8 s** | 92.6 t/s |

So the 35B stays on MTPLX (its prefill is ~30% quicker on the 17k prefix),
and the GGUF build is now a close second rather than an autoregressive one.

### Qwen 3.8 27B on llama.cpp — DFlash2 on long code

On agent turns DFlash2 accepted ~50-60%; on long code it does much better:

| config (b11541) | plain-code decode | acceptance |
|---|---:|---:|
| no draft | 10.0 t/s | — |
| native MTP head, depth 1 / 2 / 3 | 16.5 / 17.1 / 29.5 t/s | — |
| DFlash2, block 8 | 31.7 t/s | 0.77 |
| **DFlash2, block 16** | **40.8 t/s** | 0.86 |
| *MTPLX 2.12.2, depth 3 (for reference)* | *34.7 t/s* | |

`gguf-q-27b` now ships with the DFlash2 drafter as a companion download and
uses it at block 16 — four times its old speed, with no patched runtime (the
FastMTP sidecar it used to need one for is no longer used). For agent loops the
MLX build is still the faster 27B: prefix + turns + compaction took 72.5 s on
MTPLX against 99.9 s on llama.cpp + DFlash2, because MTPLX prefills faster
(343 against 246 t/s).

### Gemma 4 E2B and Qwen 3.8 9B (the small-Mac models, measured on the M5 Pro)

From `./bench/small-mac-check.sh --quick`, 8k prefix, 64k window:

| config | total | code decode |
|---|---:|---:|
| E2B, no draft (what it ran before) | 37.7 s | 85.8 t/s |
| E2B + MTP assistant, depth 2 | 33.6 s | 116.3 t/s |
| **E2B + MTP assistant, depth 3** | **30.3 s** | **123.2 t/s** |
| E2B, depth 2, `-ub 1024` | 33.6 s | 105.0 t/s |
| Qwen 9B MTPLX 2.12, depth 1 | 50.8 s | 60.4 t/s |
| **Qwen 9B MTPLX 2.12, depth 2** | **47.6 s** | **70.7 t/s** |
| Qwen 9B MTPLX 2.12, depth 3 | 56.8 s | 54.1 t/s |

The E2B's new draft is worth 44% on code decode. The 9B defaults to depth 2.
These are M5 numbers; the same script on an M1 16 GB decides the small-Mac
defaults.

### On an M1 with 16 GB (the small-Mac floor)

`./bench/small-mac-check.sh`, M1 8-core GPU, 16 GB, macOS 26.6.1, llama.cpp
b11541, MTPLX 2.12.2, 8k prefix, 64k window, best of 2:

| config | total | cold TTFT | code decode |
|---|---:|---:|---:|
| E2B, no draft | 250.8 s | 34.4 s | 26.2 t/s |
| E2B + MTP, depth 1 | 230.7 s | 36.3 s | 33.0 t/s |
| E2B + MTP, depth 2 | 218.4 s | 34.9 s | 38.7 t/s |
| **E2B + MTP, depth 3** | **212.8 s** | 35.0 s | **43.2 t/s** |
| E2B, depth 2, `-ub 1024` | 244.9 s | 36.5 s | 32.0 t/s |
| E2B, depth 2, **q8 KV** | 253.6 s | 35.6 s | **25.6 t/s** |
| Qwen 9B MTPLX (BF16 pack), depth 1 | 299.4 s | 98.4 s | 13.6 t/s |
| Qwen 9B MTPLX (BF16 pack), depth 2 | 328.2 s | 97.5 s | 12.2 t/s |
| Qwen 9B MTPLX (BF16 pack), depth 3 | 278.4 s | 106.3 s | 10.6 t/s |

Prefix router, same Mac, four subagents at once: **112.8 s → 4.24 s**
(second run: 108.8 s → 4.30 s).

What it decided:

- **E2B depth 3** holds on M1 too — already the default. Its margin over no
  draft is noisy on this machine: +65% on code decode in the first run (43.2
  against 26.2 t/s), +25% in a clean second run (41.9 against 33.5 t/s, 214
  against 225 s for the session). It won both runs.
- **`-ub 512` stays the default off-M5**; 1024 was slower on every column.
- **q8 KV was 34% slower than f16** on the M1, as on the M5. The installer now
  keeps f16 on 16-24 GB Macs whenever that still leaves a 32k window (it
  does for the 12B and the E2B), instead of switching to q8 below 30 GB.
- **The 9B prefers depth 1 off-M5** (depth 2 on M5): the default is now
  chip-dependent.
- **The 9B pack stores its scales and norms in BF16**, which M1 and M2 run
  emulated. No FP16 build of an uncensored 9B exists on Hugging Face, so
  [`lib/make-fp16-pack.py`](../lib/make-fp16-pack.py) builds one locally
  (692 tensors cast, none out of FP16 range, 4 s), and the MLX engine serves
  `<pack>.fp16` on M1/M2 automatically. On the M5 the FP16 copy decodes the
  same (84.7 against 82.8 t/s). On the M1 it is a clear win, depth 1, second
  run:

  | Qwen 9B on M1 16 GB | BF16 pack | **FP16 copy** |
  |---|---:|---:|
  | whole session | 310.8 s | **248.4 s** (−20%) |
  | cold TTFT, 8k prefix | 98.4 s | **71.7 s** (−27%) |
  | code decode | 13.7 t/s | **16.6 t/s** (+21%) |

  A third run (2026-10-11) repeated it within a few percent (16.2 against
  13.1 t/s, 72.0 against 98.9 s; router 109.1 → 4.4 s). A clean second run,
  two rounds, agreed: 236.4 against 295.7 s for the
  session, 71.6 against 98.5 s cold TTFT, 16.9 against 13.7 t/s. Depth 2 on the
  FP16 copy tied on the session (238.6 s) and decoded slower (15.1 t/s), so
  depth 1 stays the M1 default. The E2B again preferred depth 3
  (41.0 t/s on code, 214 s for the session).

### Gemma 4 E4B (added 2026-10-11)

The E4B also ran with no draft model. It now gets its MTP assistant
(`amaranus/Gemma-4-E4B-it-qat-assistant-MTP-Q8_0-GGUF`, 0.1 GB) as a companion
download. M5 Pro, 8k prefix, 64k window, best of 2:

| config | session | code decode | acceptance |
|---|---:|---:|---:|
| no draft (what it ran before) | 69.9 s | 64.1 t/s | — |
| MTP depth 1 | 62.3 s | 86.0 t/s | 0.73 |
| MTP depth 2 | 46.2 s | 100.6 t/s | 0.63 |
| **MTP depth 3** (the default) | **45.7 s** | **108.7 t/s** | 0.57 |

+70% on code and −35% on the session; depth 3 is already the generic default.
On the M1 16 GB, with the draft: 18.7 t/s decode and 177 t/s prefill.

## 4. Subagents and restarts: the prefix router

On llama.cpp a prefix is reused only from the slot holding it. Hermes runs
`delegate_task` children in parallel, and they share the parent's system
prompt and tools exactly — so the first child got the warm slot and every
other child prefilled the whole prefix cold.

[`lib/agent-router.py`](../lib/agent-router.py) sits on `PORT` with
llama-server behind it on a loopback port. It saves the shared prefix's slot
state to disk the first time it is computed (no extra prefill — the request
needed it anyway), and restores it into a free slot for each new conversation
that shares it.

Gemma 4 26B-A4B, 17k-token prefix, four children sent at once:

| | wall | children reusing the prefix |
|---|---:|---:|
| without the router | 35.6 s | 1 of 4 |
| **with the router** | **2.3 s** | **4 of 4** |
| first turn after a full restart, without | 10.1 s prompt | 0 tokens |
| **first turn after a full restart, with** | **0.24 s prompt** | **17,121 tokens** |

Gemma 4 E2B, 8k-token prefix, four children at once: 10.9 s without the
router, **1.04 s** with it. The live server through `./start.sh` (router, API
key, agent profile): four children in 2.74 s, and the first turn after
`./stop.sh` + `./start.sh` in **0.48 s** against 16.2 s cold.

At temperature 0 the output through a restored slot matches a cold prefill
exactly. MTPLX already shares a prefix across concurrent sessions (its four
children took 6-7 s at 17k with the prefix cached), so the router is
llama.cpp-only. `AGENT_ROUTER=off` removes it.

## 5. Smaller fixes

- **The installer sizes the context window from the model's KV cost.** It was
  one number per RAM size, and with sessions sharing a pool, four 8k-token
  subagents overflowed a 16 GB Mac's flat 16k ("Context size has been
  exceeded", measured on the E2B). The window written to env.conf is now the
  largest power of two the model's KV fits in three-quarters of the spare
  memory under the cap: 64k for the 12B on 16 GB, 128k for the 26B on 32 GB.
- **A native MTP head is used when the GGUF has one**, and preferred over a
  sidecar that needs a patched build. The HauhauCS 27B GGUF carries one too,
  and used to fall back to no drafting at all whenever its FastMTP sidecar
  could not load.

- Expert profiles no longer force `KV_QUANT=f16`, which undid the installer's
  q8 on 16-24 GB Macs.
- The load-plan check priced a q8 cache as f16 (2x) on the GGUF-header path.
- `MEMORY_LIMIT_GB` at or above the Mac's RAM is clamped to 3/4 of it.
- `--cache-ram` is sized to the Mac (1/4/8 GiB) instead of llama.cpp's 8 GiB.
- A tuned depth is ignored once the runtime or chip it was measured on
  changes; until it is re-tuned a measured per-model default applies.
- `./bench/bench.sh --tune` now tunes on the replay's file-write step at
  temperature 0.6, not on a greedy prompt.
- Gemma 4 E2B gets its MTP assistant as a companion download; it ran with no
  draft at all before.

## Still to measure

- Nothing outstanding from the plan. `./bench/small-mac-check.sh` stays as the
  way to re-check a small Mac after a runtime bump.
