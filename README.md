<!--
  Nova Upinel Chow, MSc, LLM, BBA, MENSA  ·  dev@upinel.com  ·  upinel.com
  Copyright (c) 2026 Nova Upinel Chow. All rights reserved.

  Upinel Personal Free License: free for personal use, and free for creators
  (YouTubers, KOLs) to make content with - just email dev@upinel.com to say so.
  Other commercial use needs written permission. Derivatives must credit the
  author. Covers this project's own code only. See LICENSE.

  "Make it work, make it right, make it fast - then measure it, because
   the third one is only a claim until the numbers agree."
-->
<h1 align="center">Upinel/AIOS</h1>

<p align="center">
  <b>A fast, private, uncensored AI agent server for your Mac.</b><br>
  <b>239 tokens/s</b> on an M5 Pro (Qwen 3.6 35B-A3B on Splash) · <b>42 tokens/s</b> on a 16 GB M1<br>
  One command to install. One command to serve. Your data never leaves your network.<br>
  <sub>Apple Silicon · 16 GB and up · three engines: Splash, llama.cpp and MLX (MTPLX)</sub>
</p>

<p align="center">
  <sub>Built by <b>Nova Upinel Chow</b>, MSc, LLM, BBA, MENSA &nbsp;·&nbsp;
  <a href="mailto:dev@upinel.com">dev@upinel.com</a> &nbsp;·&nbsp;
  <a href="https://upinel.com">upinel.com</a></sub><br>
  <sub>Free for personal use, and free for creators — YouTubers, KOLs, streamers
  and bloggers, and <b>you keep the money you make</b>: just email dev@upinel.com
  to say so, no reply needed. Other commercial use by written permission.
  Derivatives must credit the author. See <a href="LICENSE">LICENSE</a>.</sub>
</p>

---

## What is this?

UpinelAIOS turns your Mac into a local AI server that agents like **Hermes
Agent, Claude Code, OpenCode, Cursor or Aider** can talk to, using the same
OpenAI-compatible API they already speak. It picks a model that fits your Mac,
installs everything, and tunes the whole stack for one job: **making agent work
fast** — big tool prompts, many short turns, file writes and parallel
subagents.

- **Uncensored models only.** There is no point being fast at something that
  will not answer.
- **Private.** Everything runs on your Mac. Nothing is sent anywhere.
- **Measured, not guessed.** Every default here was benchmarked on a real
  agent workload, on both a high-end and an entry-level Mac.
- **The fastest engine for each model, picked for you.** On an M3 or newer the
  Qwen models run on [Splash](https://github.com/incoai/splash) — up to
  **239 tokens/s** — and everything else on llama.cpp or MLX.

## How fast is it?

Measured by replaying a real agent session — a Hermes-sized tool prompt, tool
calls, a file write, four parallel subagents, a history compaction — at the
temperature agents actually use, on both ends of the range this project
supports. Each row is the fastest engine for that model on that Mac.

> ### ⚡ 239 tokens/s
> **Qwen 3.6 35B-A3B on Splash, M5 Pro** — writing code at agent temperature,
> 2.4× what the same Mac managed on MTPLX (101 tokens/s).

| | engine | **M5 Pro · 64 GB** | **M1 · 16 GB** |
|---|---|---:|---:|
| **Writing code, Qwen 3.6 35B-A3B** | **Splash** | **239 tokens/s** | — *(needs 36 GB and an M3+)* |
| **Writing code, Qwen 3.8 27B** | **Splash** | **69 tokens/s** | — *(needs 36 GB and an M3+)* |
| Writing code, Gemma 4 E2B | llama.cpp | 123 tokens/s | **42 tokens/s** |
| Writing code, Gemma 4 E4B | llama.cpp | 109 tokens/s | 18.7 tokens/s |
| Writing code, Gemma 4 26B *(default model)* | llama.cpp | 105 tokens/s | — *(needs 32 GB)* |
| Writing code, Qwen 3.8 9B | MLX | 71 tokens/s | 17 tokens/s |
| First reply to a 17k-token agent prompt, Qwen 35B | Splash | **5.8 s** | — |
| Four subagents sharing one tool prompt (Gemma 26B / E2B) | llama.cpp + router | **2.3 s** *(was 35.6 s)* | **4.3 s** *(was 108.8 s)* |
| First reply after a restart (Gemma 26B) | llama.cpp + router | **0.5 s** *(was 16.2 s)* | — |

"Writing code" is a ~1,000-token Python module sampled at temperature 0.6.
Splash needs an M3 or newer, so on the M1 the Qwen models run on MLX instead.
Everything measured, and how: [Benchmarks](#benchmarks) and
[What we found](#what-we-found).

## Get started

You need an **Apple Silicon Mac with 16 GB or more** and macOS 14 or later.

```bash
git clone https://github.com/Upinel/UpinelAIOS && cd UpinelAIOS
./install.sh      # looks at your Mac, suggests a model, installs everything
./start.sh        # starts the server on http://<your-lan-ip>:8000/v1
./chat.sh         # say hello, right here in the terminal
```

That is it. `./start.sh` prints the URL, the model name and an API key — paste
those into your agent and you are done.

**On an M3 or newer with 36 GB+ and macOS 26.4 or later**, add Splash once —
it is what reaches **239 tokens/s**. It is optional; everything else works
without it:

```bash
brew tap incoai/tap && brew install incoai/tap/splash
./model_download.sh gguf-q-35ba3b    # the uncensored Qwen 3.6 35B-A3B (22 GB)
./start.sh --model gguf-q-35ba3b     # asks: Splash (default) or llama.cpp
```

Whenever a model can run on both, `./start.sh` and `./restart.sh` ask which
engine to use — press Enter for Splash, or pass `--engine splash` /
`--engine llama.cpp` to skip the question.

## Which model should I run?

`./install.sh` chooses for you, but here is what it is thinking:

| your Mac | good choice | why |
|---|---|---|
| **36 GB+, M3 or newer** | **`gguf-q-35ba3b` — Qwen 3.6 35B on Splash** | **the fastest model here: 239 tokens/s writing code** |
| **36 GB+, M3 or newer** | `gguf-q-27b` — Qwen 3.8 27B on Splash | the quality pick, 69 tokens/s on Splash (48 GB recommended) |
| **32 GB+, M1/M2** | `mlx-q-35ba3b` — Qwen 3.6 35B on MLX | the fastest big model on chips Splash does not support |
| **32 GB** | `gguf-g-26ba4b` — Gemma 4 26B *(the default)* | best all-rounder, with vision: 105 tokens/s |
| **24 GB** | `gguf-g-12b` with a long context | room for a 128k window |
| **16 GB** | `gguf-g-e2b` — Gemma 4 E2B | fastest small model: 42 tokens/s on an M1, uses ~4 GB |
| | `gguf-g-12b` — Gemma 4 12B | the most capable model that fits 16 GB |

On an **8 GB** Mac it still works with `gguf-g-e2b` and a small context — see
[the deep dive](docs/DEEP-DIVE.md#requirements) — but 16 GB is what this is
tuned for.

**Swap models any time:**

```bash
./model_download.sh                        # what is available, what you have
./model_download.sh --switch gguf-g-12b    # download if needed, switch, restart
./start.sh --model mlx-q-35ba3b            # serve a different model just this once
```

If you have several models downloaded, `./start.sh` asks which one to serve
(or takes your default after 30 seconds).

## Pick what this Mac is for

`./start.sh` asks once and remembers. Each profile is tuned for a different job:

| profile | best for | what it does |
|---|---|---|
| **agent** | Hermes, Claude Code, tool loops | thinking off, 4 sessions sharing one full-size context, tool prompt reused across subagents and restarts |
| **speed** | one person chatting | everything for a single fast conversation |
| **writer** | novels, long documents | long replies, settings that stay quick as the text grows |
| **manual** | a one-off run | asks for thinking, context, sessions and reply length, for this start only |
| **custom** | full control | uses `env.conf` exactly as written |

```bash
./start.sh --profile agent                        # skip the question
./start.sh --profile agent --set THINKING=high    # one setting changed, just this once
```

Profiles never change your model, context window or memory limit. For a
Qwen 27B or 35B on an M3 or newer, `./start.sh` also asks which engine — Splash
or llama.cpp — after the profile.

## Everyday use

```bash
./start.sh        # start (asks which model and profile if needed)
./stop.sh         # stop
./restart.sh      # apply changes from env.conf
./status.sh       # live dashboard — press t to change thinking, m to change model
./chat.sh         # chat in the terminal
```

**`./chat.sh`** streams replies, shows the model's thinking dimmed, and prints
speed after every answer. Paste a whole file or stack trace and it is sent as
one message. Type `/help` for commands such as `/reset`, `/system`,
`/thinking on`, `/save`.

## Connect your agent

Point any OpenAI-compatible agent at the address `./start.sh` prints:

| setting | value |
|---|---|
| Base URL | `http://<your-lan-ip>:8000/v1` |
| API key | printed by `./start.sh`, stored in `run/api-key` |
| Model | `Upinel-AIOS` |

Step-by-step setups for Cursor, Aider, Continue and others are in
[docs/CLIENTS.md](docs/CLIENTS.md). Three tips that make agents noticeably
faster and more reliable:

1. **Give file writes room.** A whole file travels inside one tool call. Allow
   at least **4096** `max_tokens` or long writes get cut off mid-file.
2. **Let history grow, don't rewrite it.** Appending turns is almost free;
   editing an old message makes the server re-read everything after it.
3. **Name required tool fields in your system prompt** if your agent reports
   *"missing required property"*. `./bench/verify-tools.sh` tells you which of
   the two causes you have.

## Benchmarks

Every figure comes from `bench/hermes-replay.py`, which replays one realistic
agent session: a system prompt with 40 tool definitions, tool-call turns, a
whole-file write, four parallel subagents, a history compaction, and a plain
code-writing step. Sampling is at temperature 0.6, like a real agent, and
configurations run interleaved, best of two rounds, on the pinned runtimes
(Splash 1.4.0, llama.cpp b11541, MTPLX 2.12.2).

### M5 Pro · 20-core GPU · 64 GB

17k-token tool prompt (8k for the two small models).

| model | engine | writing code | first reply, cold | 4 subagents |
|---|---|---:|---:|---:|
| **Qwen 3.6 35B `gguf-q-35ba3b`** | **Splash** | **⚡ 239 t/s** | **5.8 s** | **3.6 s** |
| **Qwen 3.8 27B `gguf-q-27b`** | **Splash** | **69 t/s** | **27.9 s** | 11.1 s |
| Gemma 4 E2B `gguf-g-e2b` | llama.cpp | 123 t/s | 2.8 s | 1.0 s |
| Gemma 4 E4B `gguf-g-e4b` | llama.cpp | 109 t/s | 4.5 s | — |
| Qwen 3.6 35B `gguf-q-35ba3b` | llama.cpp | 106 t/s | 12.0 s | 3.8 s |
| Gemma 4 26B `gguf-g-26ba4b` *(default)* | llama.cpp | 105 t/s | 11–12 s | 2.3 s |
| Qwen 3.6 35B `mlx-q-35ba3b` | MLX | 101 t/s | 7.5 s | 3.7 s |
| Qwen 3.8 9B `mlx-q-9b` | MLX | 71 t/s | 8.7 s | — |
| Qwen 3.8 27B `gguf-q-27b` | llama.cpp | 41 t/s | — | — |
| Qwen 3.8 27B `mlx-q-27b-4bit` | MLX | 35 t/s | 53 s | 6.0 s |

The same Qwen model on three engines shows what Splash adds: the 35B goes from
101–106 t/s to 239, the 27B from 35–41 t/s to 69, and both reply sooner to a
cold 17k-token prompt. Splash runs on M3 and newer only, and serves the Qwen
3.8 27B and Qwen 3.6 35B-A3B; Gemma and the Qwen 9B are not models it
supports. It loads the same uncensored GGUF files as the llama.cpp rows,
linked in place.

### M1 · 8-core GPU · 16 GB

8k-token tool prompt, a 64k context shared by four sessions.

| model | engine | writing code | first reply, cold | 4 subagents |
|---|---|---:|---:|---:|
| Gemma 4 E2B `gguf-g-e2b` | llama.cpp | **42 t/s** | 35 s | **4.3 s** |
| Gemma 4 E4B `gguf-g-e4b` | llama.cpp | 18.7 t/s | — | — |
| Qwen 3.8 9B `mlx-q-9b` | MLX | 17 t/s | 72 s | 13.4 s |

The E4B row is from its own M1 run with the new draft model: 18.7 tokens/s
decode and 177 tokens/s prefill.

**What the two machines tell you.** The M5 Pro writes about 3× faster than the
M1 on the same model, and reads a prompt about 12× faster — that is where the
M5's Neural Accelerators show. On both, the biggest win for agents is the same:
**never reading the same tool prompt twice.**

### What the October 2026 tuning changed

**On the M5 Pro**

| | before | after |
|---|---:|---:|
| **Qwen 35B writing code (Splash instead of MTPLX)** | 101 t/s | **⚡ 239 t/s** |
| **Qwen 35B whole agent session (Splash)** | 32.6 s | **24.8 s** |
| **Qwen 27B writing code (Splash instead of MTPLX)** | 35 t/s | **69 t/s** |
| **Qwen 27B whole agent session (Splash)** | 117.9 s | **81.2 s** |
| Whole agent session, Gemma 26B (llama.cpp upgrade) | 42.0 s | **34.7 s** |
| Whole agent session, Qwen 35B (MTPLX 2.9 → 2.12) | 49.9 s | **32.6 s** |
| Whole agent session, Qwen 27B (MTPLX 2.9 → 2.12) | 234.7 s | **117.9 s** |
| Four subagents sharing a tool prompt | 35.6 s | **2.3 s** |
| First reply after a restart | 16.2 s | **0.5 s** |
| Context each agent session actually gets (4 sessions, 128k) | 32k | **128k** |
| Qwen 27B on llama.cpp, writing code (new DFlash2 drafter) | 10 t/s | **41 t/s** |
| Gemma E4B writing code (new MTP draft model) | 64 t/s | **109 t/s** |
| Gemma E4B whole agent session | 69.9 s | **45.7 s** |

**On the M1 with 16 GB**

| | before | after |
|---|---:|---:|
| Gemma E2B writing code (new MTP draft model) | 33.5 t/s | **41.9 t/s** |
| Gemma E2B writing code, f16 cache instead of q8 | 28.3 t/s | **40.1 t/s** |
| Qwen 9B whole session (FP16 copy, because M1 lacks bfloat16) | 295.7 s | **236.4 s** |
| Qwen 9B first reply | 98.5 s | **71.6 s** |
| Four subagents sharing a tool prompt | 108.8 s | **4.3 s** |

Want to check your own Mac? `./bench/small-mac-check.sh` runs the small-Mac
suite and writes a report; `bench/matrix.py` compares any server settings you
like. The complete tables, including everything that was tried and *rejected*,
are in [docs/PERF-AGENTS.md](docs/PERF-AGENTS.md).

## What we found

The short version of every experiment behind the defaults — what worked, what
did not, and what to keep in mind. The full numbers are in
[docs/PERF-AGENTS.md](docs/PERF-AGENTS.md).

**What made the biggest difference**

- **Splash doubles the Qwen models on newer Macs.** On the M5 Pro the 35B writes
  code at **239 tokens/s** against 101 on MTPLX, and the 27B at 69 against 35,
  with faster first replies too (5.8 s against 7.5 s, and 27.9 s against 53 s). It needs an M3 or newer, serves only the Qwen 27B
  and 35B, and loads their GGUF builds — the MLX packs here store FP16 norms,
  which it refuses, so those stay on MTPLX.
- **Not re-reading the tool prompt is the biggest agent win.** An agent's system
  prompt and tool list are the same on every turn and for every subagent.
  Reusing them took four subagents from 35.6 s to 2.3 s on the M5 Pro and from
  108.8 s to 4.3 s on the M1, and the first reply after a restart from 16.2 s
  to 0.5 s.
- **Keeping the runtimes current was the cheapest win.** Moving to the current
  llama.cpp made a whole Gemma session 17% faster; the MTPLX update cut Qwen
  sessions by 35% (35B) and 50% (27B). Nothing else changed.
- **Every draft model is worth testing per model.** Gemma's E2B and E4B had no
  draft model at all; giving them one made them 25–70% faster at writing code.
- **Agent sessions had a quarter of the memory they were promised.** Four
  sessions used to split the 128k window into four 32k ones; they now share it.

**What did not work, and why**

- **The DFlash block drafters, on these uncensored models.** Published drafters
  are trained on the original (aligned) models. Against the uncensored
  fine-tunes they guessed right only 9–18% of the time on Gemma 26B and Qwen
  35B, which made things *slower*. On the Qwen 27B they help for long code,
  which is why it uses one on llama.cpp.
- **A compressed (q8) cache to save memory bandwidth.** It was slower on every
  Mac tested — 29% slower on the M1 — because decompressing it costs more than
  it saves. The cache stays f16 wherever it fits.
- **Splash for everything.** It has no Gemma support and no Qwen 9B, and on the
  27B its four-subagent fan-out took 11.1 s against MTPLX's 6.0 s.

**What depends on your chip**

- **M1 and M2 have no bfloat16**, which the uncensored MLX packs use. An FP16
  copy built once on those Macs made the Qwen 9B 20% faster per session and its
  first reply 27% faster.
- **The best draft depth differs by chip.** The Qwen 9B is fastest at depth 2 on
  the M5 Pro and depth 1 on the M1; defaults follow the chip.
- **Bigger prompt batches only help M5.** They cut first-reply time 9% on the M5
  Pro and made no measurable difference on the M1 (within run-to-run noise
  across three runs), so the larger size is M5-only.

**Notes on measuring**

- Benchmarks here sample at temperature 0.6. Greedy sampling makes draft models
  look far better than they are in real use.
- Configurations run interleaved, best of two or more rounds — a laptop's heat
  and background apps move single runs by 10% or more.
- Run engines back to back with a gap. MTPLX's memory guard refused requests
  when it started right after another engine still held memory.
- Small models do not always do what a scripted test asks (one answered a
  "write this file" step by reading files first), so code-writing speed is
  measured on a plain code prompt as well.

## How it gets this fast

You do not need to know any of this to use it. It is here for the curious.

- **Splash, an engine built around the model.** On an M3 or newer the Qwen 27B
  and 35B run on [Splash](https://github.com/incoai/splash), which pairs each
  model with a draft model trained for it and Metal kernels written for its
  exact shapes. That is where **239 tokens/s** comes from — about twice what
  the general-purpose engines reach on the same files. `./start.sh` offers
  Splash (the default) or llama.cpp whenever both can serve the model.
- **The right engine for everything else.** Gemma runs on llama.cpp; the Qwen
  9B, and every Qwen model on an M1 or M2, runs on Apple's MLX via MTPLX. Pick a model and the right engine comes
  with it.
- **A second model guesses ahead.** A small *draft* model proposes the next few
  words and the main model checks them in one go — speculative decoding. The
  draft only proposes; the uncensored model decides every word, so answers do
  not change, they just arrive sooner. The draft depth is tuned per model and
  per chip.
- **The tool prompt is remembered.** A small router in front of llama.cpp saves
  the agent's system prompt and tools to disk the first time it sees them.
  Subagents and restarts reload them in a fraction of a second instead of
  re-reading 17,000 tokens. Splash and MTPLX do this themselves.
- **Sessions share one context.** Four agent sessions draw on one 128k window
  instead of getting a quarter each.
- **An uncompressed cache is faster here.** Leaving the KV cache at f16 beat q8
  on every Mac tested — the M1 included — so it stays f16 wherever it fits.
- **Each chip gets what suits it.** M5 Neural Accelerators are used for prompt
  reading; M1 and M2, which lack bfloat16, get an FP16 copy of MLX models.
- **Pinned, checked runtimes.** The exact llama.cpp build these numbers were
  measured on is downloaded and verified, with a one-command rollback; Splash
  and MTPLX come from their own Homebrew taps.

The full story — every measurement, every correction, every option that did
not work — is in [docs/DEEP-DIVE.md](docs/DEEP-DIVE.md) and
[docs/PERF-AGENTS.md](docs/PERF-AGENTS.md).

## The models

Every model here is an **uncensored** fine-tune. Each alias starts with its
engine, so you can always tell what runs it.

| alias | model | size | notes |
|---|---|---:|---|
| **`gguf-q-35ba3b`** | Qwen 3.6 35B-A3B (MoE) | 22 GB | **⚡ the fastest model here — 239 tokens/s on Splash** (M3+, 36 GB+); llama.cpp elsewhere |
| **`gguf-q-27b`** | Qwen 3.8 27B | 18 GB | the quality pick on Splash: 69 tokens/s (M3+, 36 GB+); 41 on llama.cpp |
| **`gguf-g-26ba4b`** | Gemma 4 26B-A4B (MoE) | 15 GB | **the default** — fast, high quality, vision |
| `gguf-g-e2b` | Gemma 4 E2B | 4 GB | the fastest small model; best for 16 GB Macs |
| `gguf-g-12b` | Gemma 4 12B | 8 GB | the most capable model for 16 GB |
| `gguf-g-e4b` | Gemma 4 E4B | 6 GB | a step up from the E2B: 109 tokens/s on an M5 Pro with its draft model |
| `gguf-g-31b` | Gemma 4 31B | 20 GB | highest-quality dense Gemma |
| `mlx-q-35ba3b` | Qwen 3.6 35B-A3B (MoE) | 22 GB | the fastest big model on M1/M2: 101 tokens/s on MLX |
| `mlx-q-27b-4bit` / `mlx-q-27b-6bit` | Qwen 3.8 27B | 19 / 22 GB | the 27B on MLX, for M1/M2 or when you want the 6-bit |
| `mlx-q-9b` | Qwen 3.8 9B | 6 GB | Qwen for small Macs: 71 tokens/s on an M5 Pro, 17 on an M1 |
| `gguf-q-9b` | Qwen 3.8 9B | 6 GB | the 9B on llama.cpp; the MLX build is faster |
| `gguf-b-27b` | Bonsai 2 27B (ternary) | 7 GB | a 27B in 7 GB — needs `./lib/fetch-runtime.sh prism` once |

Splash serves the two `gguf-q-` models at the top on M3 or newer, from the same
files; the `mlx-q-` packs here store FP16 norms, which Splash does not load,
so they always run on MTPLX.

Bring your own: `./model_download.sh owner/some-uncensored-GGUF` works with any
Hugging Face repo, and the engine is worked out from the name.

## Settings you might change

Everything lives in **`env.conf`**, with comments explaining each option.
Change it, then `./restart.sh`.

| setting | what it does | default |
|---|---|---|
| `MODEL` | which model to serve | `gguf-g-26ba4b` |
| `CONTEXT_WINDOW` | how much text the model can see, in tokens | sized to your Mac by the installer |
| `SESSIONS` | how many requests run at once (agent + subagents) | `4` |
| `THINKING` | `off`, `minimal`, `low`, `medium`, `high` | `low` (the agent profile turns it off) |
| `ENABLE_VISION` | load the image model (costs ~24% speed) | `0` |
| `KV_QUANT` | cache precision — `f16` is faster, `q8` is smaller | `f16` |
| `MEMORY_LIMIT_GB` | how much memory the server may use | ¾ of your RAM |
| `AGENT_ROUTER` | the tool-prompt cache for llama.cpp models | `auto` |
| `SPLASH` | use Splash for the Qwen 27B / 35B on M3+ (`off` to never) | `auto` |
| `HOST` / `PORT` | `0.0.0.0` shares on your network; `127.0.0.1` keeps it to this Mac | `0.0.0.0:8000` |

## Updating

```bash
git pull
./update_env.sh   # keeps your settings, adds any new ones, tells you what changed
./restart.sh
```

If `git pull` complains about `env.conf`, run `git checkout -- env.conf`, then
the three commands above — `./update_env.sh` puts your settings back. Your
downloaded models are never touched by an update.

## When something goes wrong

| symptom | first thing to try |
|---|---|
| a model will not load, or the Mac starts swapping | a smaller model or `CONTEXT_WINDOW`, or `./start.sh --force-load` if you know it fits |
| agent says *"missing required property"* | `./bench/verify-tools.sh`, then raise `max_tokens` or name the fields |
| slower than the numbers above | close GPU-heavy apps; laptops throttle — try `FAN_MODE="max"` |
| anything to do with the prompt cache | set `AGENT_ROUTER=off` and restart to rule it out |
| Splash will not start, or behaves oddly | `./start.sh --engine llama.cpp` for one run, or `SPLASH="off"` in `env.conf` |

Everything else, engine by engine: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Documentation

| doc | what is in it |
|---|---|
| [docs/PERF-AGENTS.md](docs/PERF-AGENTS.md) | every benchmark on the M5 Pro and the M1 — Splash included — and every option that was tried and rejected |
| [docs/DEEP-DIVE.md](docs/DEEP-DIVE.md) | how each default was chosen: quants, draft depth, thinking budgets, prompt caching, vision, the M5 Neural Accelerators |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | every known failure mode, every engine |
| [docs/CLIENTS.md](docs/CLIENTS.md) | connecting Cursor, Aider, Continue and friends |
| [docs/GGUF-RUNTIME.md](docs/GGUF-RUNTIME.md) | llama.cpp specifics |
| [docs/MLX-TUNING.md](docs/MLX-TUNING.md) | MLX / MTPLX specifics |
| [docs/RESEARCH.md](docs/RESEARCH.md), [RESEARCH-MLX-STACK](docs/RESEARCH-MLX-STACK.md), [RESEARCH-LLAMACPP-MTP](docs/RESEARCH-LLAMACPP-MTP.md) | the research this started from |
| [plan.md](plan.md) | the performance plan and its status |

## Credits and licences

### This project

**UpinelAIOS is © 2026 Nova Upinel Chow, released under the
[Upinel Personal Free License](LICENSE).** In short:

| | |
|---|---|
| **Personal use** | Free. Use it, change it, share it. |
| **Creators — YouTubers, KOLs, streamers, bloggers** | Free — **you keep the money you make from the content**. Videos, streams, posts, articles, images, tutorials — and any text or images you generate with it and publish. Just email [dev@upinel.com](mailto:dev@upinel.com) to say you're doing it. **You do not need a reply and should not wait for one** — the permission takes effect the moment you hit send. |
| **Other commercial use** | Needs the author's written permission — email [dev@upinel.com](mailto:dev@upinel.com). This covers use inside a company, selling a product or service built on it, and hosting it for others as a paid service. |
| **Derivatives** | Must credit the author and keep this licence. Fork it, port it, improve it — just leave the name on it. |
| **Scope** | Covers this project's own code only. Splash, llama.cpp, MLX, MTPLX, Gemma 4, Qwen, and every model weight belong to other people and keep their own licences — see [section 3 of the licence](LICENSE) for the full list. |
| **Warranty** | None. It is a local inference server; you are responsible for what you run on it. |

So: making a video about it, reviewing it, or using it on stream to generate
content is free, forever — tell us and carry on. Selling it, or selling
something that runs on it, is the thing to ask about.

This is a **source-available** licence, not an open-source one: the
[OSI definition](https://opensource.org/osd) requires that a licence permit
commercial use, which this one deliberately does not. If you need a commercial
licence, ask — the answer is usually yes, and it is a short conversation. The
author is not a lawyer and the licence has not been reviewed by one; if you
are relying on it commercially, get your own advice.

`LICENSE` covers this project's code whichever engine runs it, and has not
drifted from the MLX edition's copy:

```bash
shasum -a 256 LICENSE
# 0906eccc1ebc7e22f8de876997a4f33b7b85c3516d2c5a28796aad759fde7ff7
```

### Third-party

UpinelAIOS stands on other people's work. Thank you to all of them.

**Runtimes**

- **[Splash](https://github.com/incoai/splash)** by Inco AI (Apache-2.0) — the
  engine behind **239 tokens/s**, with its DFlash2 draft models
  ([`incoai/Qwen3.6-35B-A3B-DFlash2`](https://huggingface.co/incoai/Qwen3.6-35B-A3B-DFlash2),
  [`incoai/Qwen3.8-27B-DFlash2`](https://huggingface.co/incoai/Qwen3.8-27B-DFlash2),
  Apache-2.0). Its GGUF kernels include MIT-licensed material from llama.cpp.
- **[llama.cpp](https://github.com/ggml-org/llama.cpp)** (MIT) — the GGUF engine.
- **[MLX](https://github.com/ml-explore/mlx)** (MIT) and
  **[MTPLX](https://github.com/youssofal/MTPLX)** (Apache-2.0) — the MLX engine.
- **[PrismML's llama.cpp fork](https://github.com/PrismML-Eng/llama.cpp)** (MIT)
  — the ternary kernels for Bonsai 2.

**Models**

- **[Gemma 4](https://ai.google.dev/gemma)** — Google's model, Apache-2.0, and
  additionally subject to Google's
  [Prohibited Use Policy](https://ai.google.dev/gemma/prohibited_use_policy),
  which this project's licence neither grants nor overrides.
- **[Qwen](https://github.com/QwenLM)** 3.6 and 3.8 — Alibaba's models,
  Apache-2.0.
- **The uncensored fine-tunes** this project serves — HauhauCS, llmfan46,
  hawhyhb, itrejomx, Foresee, OS-Software, mradermacher, barozp and dealignai.
- **The draft models** that make them fast — z-lab (DFlash2), amaranus (Gemma
  4 E2B/E4B MTP assistants), HauhauCS (Gemma 4 26B MTP) and Inco AI (above).

Each model's licence is on its Hugging Face card, and some fine-tunes carry the
Gemma licence rather than Apache-2.0; each is named against its model in
`lib/common.sh`.

**Nothing third-party is redistributed here.** `install.sh` downloads each
runtime from its own release page (pinned and checksum-verified) or through
Homebrew — Splash through its own tap, installed by you — runs it as a separate
process and talks to it over HTTP; every Python import in this project is
standard library. Everything upstream is permissive, with no copyleft anywhere
in the tree. The one incompatibility runs the other way: because this licence
forbids commercial use, this code **cannot be combined into a GPL or AGPL
project**.

Model weights carry their own upstream licences. Uncensored models are
uncensored: you are responsible for how you use the endpoint, and for the fact
that you just shared it on your network.
