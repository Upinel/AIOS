# UpinelAIOS — extreme performance plan

## Status, 2026-10-10 (branch `perf-overhaul`, uncommitted)

| plan item | status | measured result |
|---|---|---|
| 0.3 launch bugs (slot split, key merge, KV override, q8 pricing, memory cap, set -u) | **done** | agent sessions get the full 128k window, not 32k |
| Phase 1 Hermes replay bench + matrix runner | **done** | `bench/hermes-replay.py`, `bench/matrix.py` |
| Phase 2 pin + upgrade llama.cpp (b11541) and MTPLX (2.12.2) | **done** | Gemma 26B session −17%; Qwen 35B −35%; Qwen 27B −50% |
| Phase 2 retune depths, stamp tunings with runtime + chip | **done** | Gemma 26B 2 → 3; 35B 1; 27B 3; 9B 2; E2B 3 |
| Phase 3 spec-method registry; DFlash/DSpark bake-off | **done** | DFlash rejected for Gemma 26B and Qwen 35B (9-18% acceptance); **adopted** for `gguf-q-27b` (10 → 41 t/s); native MTP heads detected and used |
| Phase 3.4 third engine | not needed | llama.cpp + MTPLX cover every cell; no third engine added |
| Phase 4 shared KV, cache-ram tiers, ubatch auto | **done** | `-ub 2048` on M5: −9% cold TTFT |
| Phase 4 prefix snapshots / restart warm / subagents | **done** — `lib/agent-router.py` | 4 subagents 35.6 → 2.3 s; first turn after restart 16.2 → 0.48 s |
| Phase 5 per-tier KV, load mode, memory-sized context, 16 GB lineup | **done** | context sized from each model's KV cost |
| Phase 5 per-chip pack selection (FP16 vs BF16 MLX packs) | **done** — `lib/make-fp16-pack.py`, served automatically on M1/M2 | M1: session −20%, cold TTFT −27%, decode +21% |
| Phase 6 agent profile v2 | **done** | Hermes wiring skipped, as agreed |
| Phase 7 tuning invalidation, offline router test, docs | **done** | 442/444 offline checks pass; the 2 left compare against the last commit and pass once committed |
| Small-Mac validation | **done (M1 16 GB, two runs)** | E2B depth 3 +25-65%; router 112.8 → 4.2 s; q8 KV −34% → installer keeps f16; 9B depth 1 off-M5 |

Measurements: [docs/PERF-AGENTS.md](docs/PERF-AGENTS.md).

---

Original status: **proposal**. Written 2026-10-10 after studying
the repo at `66e1df5` (branch `fix-memory-guard`), the installed runtimes, and
upstream llama.cpp / MTPLX / MLX-ecosystem releases up to 2026-10-10.

Goal: maximum prefill and decode for **agent work** (Hermes Agent, Claude Code,
OpenCode, Pi), on the four target models, on **every Apple Silicon Mac with
16 GB or more**, without giving up the one-click install.

| target model | today's alias | today's engine | today's measured (M5 Pro, 8k) |
|---|---|---|---|
| Qwen 3.8 27B (dense) | `mlx-q-27b-4bit` | MTPLX 2.9.1, MTP | ~30 t/s decode, ~330-366 t/s prefill |
| Qwen 3.6 35B-A3B Q4 (MoE) | `mlx-q-35ba3b` | MTPLX 2.9.1, MTP d1 | ~141 t/s decode, ~1,760 t/s prefill |
| Gemma 4 26B-A4B (MoE) | `gguf-g-26ba4b` | llama.cpp b10809, MTP assistant d2 | ~89 t/s decode, ~1,390 t/s prefill |
| Gemma 4 E2B | `gguf-g-e2b` | llama.cpp b10809, **no draft** | ~100 t/s decode, 294 t/s prefill (old, short prompt) |

The principle stays the one the README already lives by: **every change is
measured A/B before it becomes a default, and everything here labelled
"expected" is a hypothesis until the numbers agree.**

---

## 0. What the study found

### 0.1 The runtimes are well behind upstream, and the gap is big

| runtime | installed | upstream today | what the gap contains (from release notes / PRs) |
|---|---|---|---|
| llama.cpp | **b10809** (Homebrew, unpinned) | **b11541** | ~730 builds. Metal tensor-API flash attention for F16 KV on M5 (#29570, 1.6-2.6x on prefill FA shapes). Few-row MMA mat-mul for 2-16-row batches on M1-M4 (#29869, the speculative-verify shape: Q4_0 runs in 0.22-0.58x the time). Probabilistic draft and rejection sampling for MTP and simple drafts (#27694, higher acceptance at temp > 0). Gemma 4 DSpark drafts (#29226). DFlash fixes (#28587, #30111). Stop drafting at EOG (#29638). M3 and M2 Max FA tunings. |
| MTPLX | **2.9.1** | **2.12.2** | 27B decode +15% (3k), +29% (88k), +54% (147k), and 88k prefill +41% (2.10.0). Agent first token mid-session 1.8-2.2 s → 0.11 s. Tool turn after a file write re-prefilled 3,535 → 20 tokens (2.11.1). Hermes token-id reuse fix: 29,842 tokens re-prefilled → 0 (2.11.3). Draft acceptance up at every depth (2.11.3). Memory guard no longer refuses ordinary prompts on 8/16/32 GB Macs (2.12.2). NaN with 4/8-bit KV fixed (2.12.1). **A correctness gate for M1-M4** (2.11.2): the 27B turbo flash route produced wrong attention past 8k on non-M5 chips. |

The README's MTPLX-vs-llama.cpp verdict, its depth tables and the
"Tested and rejected" list were all measured on these old builds. Several will
not survive a re-measure.

### 0.2 Speculative decoding has moved on: block drafters exist for every target

Hugging Face now has DFlash / DFlash2 / DSpark block-diffusion drafters for all
four targets:

| target | drafters found (Hugging Face, 2026-10-10) |
|---|---|
| Qwen 3.8 27B | `z-lab/Qwen3.8-27B-DFlash2-GGUF`, `z-lab/Qwen3.8-27B-DFlash2`, `RadixArk/Qwen3.8-27B-DSpark`; uncensored-target variants such as `WaveCut/Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-DFlash2-*` |
| Qwen 3.6 35B-A3B | `z-lab/Qwen3.6-35B-A3B-DFlash`, several `*-DFlash-GGUF`, `mlx-community/Qwen3.6-35B-A3B-DFlash2-4bit` (MLX) |
| Gemma 4 26B-A4B | `z-lab/gemma-4-26B-A4B-it-DFlash`, `Anbeeld/...-DFlash-GGUF` and others |
| Gemma 4 E2B | `amaranus/Gemma-4-E2B-it-qat-assistant-MTP-Q8_0-GGUF` (MTP assistant) |

The installed llama-server already accepts
`--spec-type draft-dflash|draft-dspark|draft-eagle3|draft-mtp`.

The headline number comes from llama.cpp #29869. On an M3 Ultra, the
**Qwen3.8-27B with a DFlash2 drafter went from 32 t/s serial decode to 110 t/s
on code and 63 t/s on prose at T=0 (81 / 49 at T=1)**. Before that PR, master
was *slower* than serial. That is the kernel fix the project's
"llama.cpp MTP does not amortise on Metal" conclusion was waiting for.

Caveats, all of which Phase 2 has to measure rather than assume:
- The few-row MMA kernels run only on GPUs **without** the tensor API (M1-M4).
  On M5 the tensor-API path handles small batches. The PR tested both for
  correctness, but M5 speed is not shown.
- Most drafters were trained against the **aligned** base. Our targets are
  abliterated or heretic fine-tunes, so acceptance may be lower. Where an
  uncensored-target drafter exists, try it too.

### 0.3 Bugs and pitfalls in our own launch path (verified)

1. **The `agent` profile cuts each session's context to a quarter.**
   `apply_expert_profile` (lib/common.sh:538) sets `PARALLEL_SLOTS=4`.
   `lib/engines/gguf.sh:214` passes `--parallel 4 -c 131072` without
   `--kv-unified`. `run/server.log` confirms it:
   `n_slots = 4, n_ctx_slot = 32768, kv_unified = 'false'`. Hermes on the
   default model gets 32k, not 128k. On a 16 GB Mac at 16k context it gets 4k.
2. **Two keys for one concept.** GGUF reads only `PARALLEL_SLOTS`, MLX reads
   only `MAX_CONCURRENT`, and the `env.conf:373` comment says the opposite. On
   `custom` the shipped file gives GGUF 1 slot and MLX 4.
3. **Every profile forces `KV_QUANT=f16`** (lib/common.sh:520,531,550). That
   silently undoes the installer's q8 choice for 16-24 GB Macs.
4. **`-lm mlock`** (gguf.sh:227) is "mlock, no mmap". It copies the weights
   into locked memory: slower to load and risky at 16 GB.
5. **The 16 GB tier table and the engine menu disagree.** `recommend_config`
   writes `gguf-g-12b` for 16-47 GB, while the menu offers the 26B at 24 GB
   (lib/preflight.sh:193-275 against :142-191).
6. **`MEMORY_LIMIT_GB=48` is the built-in default.** A 16 GB Mac that skips
   the installer's suggestion hands MTPLX a 48 GB limit and an 8 GB session
   bank.
7. **The KV estimate ignores `KV_QUANT` on the GGUF-header path**
   (`kv_kb_for_model_dir`, lib/common.sh:1310). A q8 cache is priced 2x.
8. **`MTP_DEPTH=auto` falls back to 3** (lib/common.sh:1845), but install.sh
   says 1. Tuning files are keyed by model only, not by chip, runtime version
   or spec method, so a runtime upgrade keeps stale depths.
9. **`gguf-g-e2b` runs without any draft.** A matching MTP assistant now
   exists.
10. **No persistent prefix cache on GGUF.** There is no `--slot-save-path`, and
    `--cache-ram` stays at its 8 GiB default, which is a lot on 16 GB. MLX has
    an SSD session cache; GGUF loses every warm prefix on restart.
11. **Runtimes are unpinned** (`brew install llama.cpp`, `brew install
    youssofal/mtplx/mtplx`). A fresh install today gets a different binary
    from the one the README's numbers were measured on.
12. Small robustness bugs: `HW_RAM_GB` is unset under `--no-scan` in
    install.sh:436, and `TOOL_TEMPLATE` has no default in `load_config`. Both
    crash under `set -u`.

### 0.4 What an agent workload actually costs

A Hermes-style loop looks like this: a **large, fixed system and tool prefix**
(40+ tools, often 10-25k tokens), then many short appended turns, parallel
subagents sharing that prefix, and occasional `/compress` rewrites. So the
order of importance is:

1. **Cold prefill of the shared prefix**, paid once per session, or once per
   subagent if prefixes are not shared.
2. **Warm-turn TTFT**: how much of the prefix is actually reused. This is
   sensitive to hybrid and recurrent state (Qwen 3.6/3.8 Gated-DeltaNet) and
   to sliding-window attention (Gemma 4), where llama.cpp can only reuse up to
   a context checkpoint.
3. **Decode on short-to-medium replies**, mostly tool-call JSON, which is
   highly predictable. Block drafters and MTP shine here.
4. **Concurrency**: subagents in parallel, without splitting the context.

The plan below is ordered by that, not by the size of the benchmark number.

---

## Phase 1 — measure honestly first (no defaults change)

You cannot optimise what the bench does not represent. The existing tools
(`bench/bench.py`, `agent-bench.py`, `cache-reuse-test.py`, `sweep.py`) are
good; they need an agent-shaped workload and a matrix runner.

1. **`bench/hermes-replay.py`**, a deterministic agent trace replayer with no
   model-specific hacks. It sends:
   - a 40-tool schema plus a long system prompt (~15k tokens, configurable),
   - 20 appended turns of tool calls and tool results (file reads, a 4k-token
     file write, shell output),
   - 4 concurrent "subagents" that share the prefix,
   - one `/compress`-style history rewrite.

   It records, per turn: cold and warm TTFT, `cache_n` / reused tokens,
   prefill t/s, decode t/s, draft acceptance, tool-call validity
   (`finish_reason`, parseable JSON, required fields present), and
   end-to-end wall time. **End-to-end wall time for the whole trace is the
   headline metric.**
2. **`bench/matrix.sh`**: runs `{engine × spec method × depth/block × KV ×
   ubatch}` per model, interleaved, best-of-N plus median, with fans pinned
   (`FAN_MODE=max`) and the order randomised. This is the methodology the
   README already insists on; it becomes the default instead of something
   remembered.
3. **Re-baseline the four target models** on the *current* runtimes before
   upgrading, so every later gain has a real "before".
4. **Quality guard**: a fixed 30-task tool-calling set (the existing
   `verify-tools.py` cases plus Hermes-style multi-tool turns) and a
   perplexity/KLD spot check for any quant or KV change. A speed win that
   drops tool-call correctness is rejected.
5. **Chip coverage**: results are keyed by chip family (M1/M2/M3/M4/M5 ×
   base/Pro/Max/Ultra) and RAM. The M5 Pro is the reference machine. Before
   any default changes for non-M5 chips we need at least one **M1/M2 16 GB**
   and one **M3/M4 Pro** run, because the biggest new kernels (#29869) only
   fire there and MTPLX's M1-M4 correctness gate matters there.

Exit criterion: one command reproduces a baseline table for all four models.

---

## Phase 2 — runtime upgrades, pinned (biggest win per hour of work)

### 2.1 Pin, don't float

- Replace `brew install llama.cpp` with **a pinned upstream release** (`bNNNNN`
  macOS arm64 zip, SHA-256 verified), installed into `run/runtimes/llama/`
  exactly as `lib/fetch-runtime.sh` already does for Prism. Keep Homebrew as a
  fallback only.
- Pin MTPLX to a tested version (2.12.2 or later), and record it.
- Add `run/runtimes/MANIFEST.json`: name, version, SHA, date measured. The
  dashboard and `--print` show it, and the tuning files reference it.
- Keep the previous runtime directory so a rollback is one command
  (`./lib/fetch-runtime.sh --rollback llama`).
- Note: upstream llama-server's default port changed to 9931 (#30159). We
  always pass `--port`, so this is harmless, but add a test for it.

### 2.2 Upgrade llama.cpp b10809 → current pinned build

Expected (to be measured):
- **M5: prefill up**, from the tensor-API FA kernel for F16 KV. Our default
  KV *is* f16, which is a nice coincidence: the README's "f16 beats q8"
  finding now has a second reason.
- **M1-M4: speculative decode up**, from the few-row MMA kernels. These
  matter most for Q4_0, which is our default Gemma quant.
- **Higher MTP acceptance at agent temperatures**, from the probabilistic
  draft (#27694).
- Re-run `bench/bench.sh --tune` for every GGUF model; old depths are invalid.

### 2.3 Upgrade MTPLX 2.9.1 → 2.12.x

Expected: the release-note gains above on the 27B (decode at long context,
mid-session TTFT, tool-turn re-prefill, Hermes token-id reuse), plus the
non-M5 correctness gate and the small-Mac memory-guard fix. **Re-tune depth**:
2.9.0 fixed a tuner bias against deeper depths, and our tuning files predate
the acceptance gains.

### 2.4 Re-measure the "Tested and rejected" table on the new builds

`-ub 1024/2048` (the new FA kernel needs ≥ 20 queries and a large enough
`ceil(n/32)·heads·hs`, so bigger ubatches may now pay), `--cache-reuse`,
q4/q8 KV, depth 4, and n-gram speculation (#29924 fixed n-gram drafts being
rejected at temp > 0). Each is re-decided on data.

Exit criterion: pinned runtimes, retuned depths, an updated README table, and
no tool-call-quality regression.

---

## Phase 3 — next-generation speculative decoding

### 3.1 Make the speculative method a per-model registry field

The registry (lib/common.sh `model_repo_for` and friends) gains:

```
SPEC_METHOD   none | mtp | draft-mtp | dflash | dspark | eagle3 | ngram
SPEC_DRAFT    repo + file of the drafter (fetched like the Gemma MTP companion)
SPEC_N_MAX    tuned per (model, chip, runtime version)
```

`engine_build_args` maps these to `--spec-type ... -md ... --spec-draft-n-max`
on GGUF, or to MTPLX `--depth` / `--no-mtp`. `model_download.sh` fetches the
drafter the same way it already fetches the Gemma 26B's borrowed MTP head.
The "no draft → warn, half speed" path stays.

### 3.2 The bake-off: per model, every candidate on every runtime

| model | candidates |
|---|---|
| Qwen 3.8 27B | MTPLX 2.12 MTP (d1-3) · llama.cpp + **DFlash2** (n-max 4-16) · llama.cpp + DSpark · oMLX DFlash2 (optional third engine, 3.4) |
| Qwen 3.6 35B-A3B Q4 | MTPLX 2.12 MTP d1 · llama.cpp `draft-mtp` (native head, if the GGUF carries it) · llama.cpp + **DFlash** · MLX DFlash2 drafter |
| Gemma 4 26B-A4B | llama.cpp MTP assistant (today) · llama.cpp + **DFlash** · llama.cpp + **DSpark** (#29226) |
| Gemma 4 E2B | none (today) · llama.cpp + **E2B MTP assistant** · n-gram (cheap, no extra memory, good on tool JSON) |

The winner per (model, chip family) is stored in the registry. Because the
bake-off includes the dense 27B on llama.cpp + DFlash2, this is the phase
where the 27B could move from ~30 t/s to a multiple of that. On M3 Ultra
upstream reports 3.4x on code. **Our M5 Pro number is unknown until
measured.**

### 3.3 Uncensored-target acceptance

For each drafter, measure acceptance on the *uncensored* target against the
aligned base. If acceptance drops materially, there are two options:
(a) use a community drafter trained against the uncensored target where one
exists, or (b) distil a drafter against our target. MTPLX Forge does this for
MTP heads; DFlash training is heavier. Treat (b) as a later, optional project.

### 3.4 A third engine? Decide on data, not taste

oMLX 0.7.x reports batched DFlash and batched prefill for concurrent requests;
vllm-mlx 0.5 has prefix-trie caching and Gemma 4 MTP assistant drafting. The
engine contract in `lib/engines/*.sh` makes adding one cheap (about 300 lines,
like `mlx.sh`). **Add one only if it wins a bake-off cell by more than 10% on
the Hermes replay**, because every engine is a permanent install, test and
docs cost. Default assumption: llama.cpp + MTPLX remain the two engines.

---

## Phase 4 — prefill and prefix reuse for agents

1. **Fix the slot split (bug 0.3-1).** Pass `--kv-unified` whenever
   `--parallel > 1`, so four Hermes subagents share one 128k pool instead of
   getting 32k each. Collapse `PARALLEL_SLOTS` and `MAX_CONCURRENT` into one
   user-facing key (`SESSIONS`), with the old keys accepted as aliases through
   `update_env.sh`, the way the legacy model aliases already are.
2. **Persistent prefix cache on GGUF**, to match MTPLX's SSD cache:
   - `--slot-save-path run/slots/`, with save on idle and shutdown and
     restore on boot, so a restart does not cost a cold 15k-token tool
     prefix. Wire it through `stop.sh` / `restart.sh`.
   - Size `--cache-ram` by RAM tier: 8 GiB is fine at 64 GB, too much at 16.
     This host-RAM prompt cache is what lets a *different* slot pick up a
     prefix another slot already computed.
3. **Prefix pre-warm.** `./start.sh --warm <file>` (or a `WARM_PREFIX_FILE`
   key) sends a captured agent system and tool prompt once at boot, so the
   first real Hermes turn is warm. `./hermes.sh` (Phase 6) captures it
   automatically on the first session.
4. **Context checkpoints, tuned for agent traces.** Gemma 4 (SWA) and Qwen
   3.6/3.8 (hybrid recurrent) can reuse only up to the last checkpoint after
   any divergence. The defaults are 32 checkpoints, at least 8,192 tokens
   apart. Sweep `--ctx-checkpoints` and `--checkpoint-min-step` (2k-8k)
   against the Hermes replay, especially the `/compress` turn and
   "edit near the end" turns. Memory cost per checkpoint is measured and
   charged in the load plan.
5. **Batch and ubatch re-sweep** on the new FA kernels (2.4). The rule may end
   up chip-dependent: bigger `-ub` on M5, 512 elsewhere.
6. **MLX prefill chunk** re-sweep on MTPLX 2.12 (its memory guard now chunks
   on its own).
7. **Harness guidance stays.** "Append, never rewrite" is still the biggest
   lever, and documenting Hermes and Claude Code settings that keep the
   prefix byte-stable (no timestamps in the system prompt, stable tool
   order) is free speed.

---

## Phase 5 — decode, memory bandwidth and small Macs

1. **Quant per model and chip**, measured. Q4_0 stays the GGUF default where
   a QAT Q4_0 exists (and #29869 gives Q4_0 its own fast few-row kernel).
   For the MLX 35B, compare the `-FP16` pack against a BF16 pack: FP16 packs
   exist for M1/M2, which lack fast BF16, and M3+ may prefer BF16. Pick by
   chip at install.
2. **KV per tier, not per profile (bug 0.3-3).** Profiles stop forcing f16.
   f16 stays the default where memory allows (still measured faster); q8 on
   16-24 GB. Fix the q8 pricing bug (0.3-7) so the load plan is honest.
3. **Load mode.** Replace `-lm mlock` with mmap plus a soft lock, or plain
   mmap on 16 GB, and measure both load time and decode stability.
4. **The GPU wired limit on 16 GB Macs.** macOS caps GPU-wired memory at
   roughly two-thirds of RAM by default. Raising `iogpu.wired_limit_mb` is
   the single biggest enabler for 16 GB, but it is a system setting that
   needs sudo and does not survive a reboot. Keep it **opt-in with explicit
   consent** at install, as now, and fix the `HW_RAM_GB` crash.
5. **The 16 GB lineup, honestly.** The 26B-A4B (~19 GB) and 35B-A3B (~22 GB)
   do not fit 16 GB, and no flag changes that. 16 GB gets Gemma 4 E2B (with
   its new draft), E4B, Qwen 3.8 9B (MTPLX), and Bonsai 2 27B (Prism
   llama.cpp, or MTPLX 2.12's Bonsai support if an uncensored pack exists).
   24 GB gets the 26B-A4B; 32 GB gets the 35B-A3B Q4 and the 27B 4-bit.
   Reconcile `recommend_config` with `recommend_engines` (0.3-5) so the tier
   table and the menu agree, and set `MEMORY_LIMIT_GB` from RAM when env.conf
   still has the shipped 48 (0.3-6).
6. **Thermals.** Offer `FAN_MODE=smart` during sustained agent runs on
   laptops. Measure sustained t/s over 30 minutes, not peak.

---

## Phase 6 — agent integration (Hermes first)

1. **`./hermes.sh`**: one-click wiring. It detects Hermes Agent and writes its
   provider config (base URL, API key, model id, context length = per-session
   context, not the total) after showing the diff and asking. It does the
   same for Claude Code (Anthropic `/v1/messages`: llama.cpp and MTPLX both
   serve it), OpenCode and Pi. It extends `docs/CLIENTS.md` into code.
2. **An `agent` profile that is actually right**: unified KV, N sessions
   sharing the full window, thinking off, per-tier `--cache-ram`, slot
   persistence, prefix pre-warm, spec method from the registry, and
   `MAX_RESPONSE_TOKENS` ≥ 4096 so a file write never truncates.
3. **Tool-call reliability on every model.** Extend `TOOL_TEMPLATE` (today
   Gemma-only) to the Qwen templates, re-verify on the new llama.cpp PEG
   parser and on MTPLX 2.12, and add Hermes's real tool schemas to
   `verify-tools.py`.
4. **Thinking for agents.** Keep `off` by default. For `low`/`medium`, verify
   the reasoning-budget end on Qwen forces `\n</think>` cleanly (#28869
   pattern) so a capped thought still yields a tool call.

---

## Phase 7 — make the speed self-maintaining

1. **Tuning keyed by `(model, chip, runtime version, spec method)`**, which
   auto-invalidates on a runtime upgrade. `install.sh` runs a short tune; the
   dashboard shows "tuned on vX, running vY → retune?".
2. **`verify-all.sh` gains offline checks** for each bug in 0.3: slots imply
   unified KV, profile does not override tier KV, keys default under
   `set -u`, the load-plan q8 pricing, and pinned-runtime manifest integrity.
3. **A perf regression gate.** `bench/matrix.sh --quick` (5 min) against the
   stored baseline before any runtime pin is bumped; a regression beyond 10%
   blocks the bump.
4. **Docs.** Replace the conflicting numbers in `docs/MLX-TUNING.md`,
   `GGUF-RUNTIME.md` and `TROUBLESHOOTING.md` (best depth, 27B decode, 35B
   decode) with the Phase 1-3 tables, each stamped with chip and runtime
   version.

---

## Order of work and expected payoff

| # | work | effort | expected payoff (to be verified) | risk |
|---|---|---|---|---|
| 1 | Fix slot split, key merge, profile-vs-tier KV, small `set -u` bugs | S | Hermes sessions get the full window (4x context per session today); correct memory on 16 GB | low |
| 2 | Hermes replay bench + matrix runner + baselines | M | makes everything after it trustworthy | none |
| 3 | Pin and upgrade llama.cpp and MTPLX, retune | M | MTPLX 27B decode +15-54% (vendor numbers); M5 prefill up; M1-M4 spec decode up; mid-session TTFT ~0.1 s on MLX | medium: new bugs, so pinned with rollback |
| 4 | E2B draft (MTP assistant) | S | likely the cheapest decode win in the lineup | low |
| 5 | Spec-method registry + DFlash/DSpark bake-off | L | the largest single decode lever, especially for the dense 27B | medium: acceptance on uncensored targets |
| 6 | Slot persistence, cache-ram tiers, prefix pre-warm, checkpoint tuning | M | cold first turn → warm after restarts; fewer full re-prefills | low-medium (memory) |
| 7 | `./hermes.sh` and client wiring, agent profile v2, Qwen tool template | M | one-click agent setup | low |
| 8 | Per-chip quant/pack selection, load mode, 16 GB lineup, wired-limit opt-in | M | 16 GB Macs usable for agents, M1/M2 correctness | low |
| 9 | Optional third engine, only if it wins a bake-off cell | L | unknown | high maintenance |
| 10 | Self-maintaining tuning, regression gate, docs refresh | M | keeps it fast | none |

Items 1 and 4 can ship this week. Items 2 and 3 are the foundation for
everything else. Item 5 is where the big headline numbers are likely to come
from.

---

## Decisions I need from you

1. **Third engine.** OK to *evaluate* oMLX / vllm-mlx in the bake-off, on the
   understanding that it ships only if it clearly wins?
2. **Drafters trained on aligned bases.** A DFlash/DSpark drafter does not
   change what the target model says (rejection sampling keeps the target's
   output distribution exact), so this is about speed only. OK to ship
   aligned-base drafters next to uncensored targets, or should drafters also
   be uncensored-trained only?
3. **Hardware for validation.** Can we get runs on at least one M1/M2 16 GB
   and one M3/M4 Mac before changing their defaults?
4. **Publishing re-forged packs.** If the best path for the 27B or 35B on
   MTPLX is re-forging the uncensored packs with MTPLX 2.12's Forge, do you
   want them published to Hugging Face (under your account), or kept local
   and built at install?
5. **Hermes integration depth.** Should `./hermes.sh` write Hermes's config
   directly (with a diff and a confirmation), or only print it?
