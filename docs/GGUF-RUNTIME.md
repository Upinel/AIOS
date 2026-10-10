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
# The GGUF runtime, and the two patched builds

UpinelAIOS runs everything through **llama.cpp**. That is a deliberate
constraint rather than a limitation we have not got around to: llama.cpp is the
only runtime that can serve an uncensored Gemma 4 at all, and it serves every
other GGUF equally well, which is why the Qwen family lives here too.

Homebrew's llama.cpp is the default, and the right answer for almost everything.
Two models need a build it does not ship, for entirely different reasons, and
they are kept apart on purpose — a runtime only one model needs should not
become everybody's runtime:

| model | needs | why | install |
|---|---|---|---|
| `gguf-q-27b` | the FastMTP patch | its draft head trims the drafter's vocabulary and adds a `d2t` remap tensor | `./lib/build-fastmtp.sh` |
| `gguf-b-27b`, `gguf-b-27b-ptq1` | the PrismML fork | ternary weight types and a Hadamard activation transform, neither of them upstream | `./lib/fetch-runtime.sh prism` |

One of the two needs saying plainly, because it costs a factor of three on the 27B.

## The Qwen 27B MTP head needs a patched llama.cpp

HauhauCS publishes `Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-FastMTP-32K.gguf`
as the MTP draft head for the 27B. It is a genuine speed win — the same model
through MTPLX in the sister project runs 2.6–3.4x faster with MTP than without.

It does **not** work on a stock llama.cpp. Its provenance file says so:

```
runtime_base:  ggerganov/llama.cpp@4df29be4f4c3673f428170fda944a5b19f743bb8
runtime_patch: HauhauCS-FastMTP-llama.cpp.patch
```

That 53-line patch adds a `d2t` tensor: the drafter's output vocabulary is
**trimmed** from 248,320 tokens to 32,768, and `d2t` remaps between them. Stock
llama.cpp does not know the tensor, compares the trimmed output against the full
vocabulary, and refuses:

```
error loading model: check_tensor_dims: tensor 'output.weight' has wrong shape;
  expected 5120, 248320, got 5120, 32768
```

**And llama-server treats a failed draft as fatal.** It does not fall back — it
exits. So attaching that head on a stock build does not cost you speed, it
stops the server booting.

### What UpinelAIOS does about it

It detects the `d2t` tensor in the draft's GGUF and, unless you tell it
otherwise, **skips the draft and runs autoregressive**, printing why:

```
warn Qwen3.8-27B-...-FastMTP-32K.gguf needs a patched llama.cpp (trimmed draft vocab).
warn Running autoregressive to keep the server up.
```

Detection is on the tensor, not the filename, so any future head built the same
way is caught too. `model_draft_gguf` and `draft_needs_patched_runtime` in
`lib/common.sh` do the work.

### What it actually buys, measured

Built 2026-08 on the reference machine and measured before and after. This is
the whole picture, including the part that is worse:

**Qwen 27B (`gguf-q-27b`), same prompts, greedy-ish, median of five:**

| runtime | draft | decode | acceptance |
|---|---|---:|---:|
| stock Homebrew | none | 7.6 t/s | — |
| patched | none | 8.4 t/s | — |
| **patched** | **FastMTP depth 1** | **15.4 t/s** | **77%** |
| patched | FastMTP depth 3 | 10.6 t/s | 53% |
| patched | FastMTP depth 5 | 8.1 t/s | 40% |

**Depth 1, not 3.** Acceptance collapses with depth (77% → 53% → 40%), so
drafting further costs more than it returns. That is the opposite of the
Gemma MTP head, where depth 3 wins — different head, different profile, and
worth re-measuring rather than assuming.

So the honest number is **2.0x, 7.6 → 15.4 t/s**, not the ~3x the MTPLX side
gets. Real, but short of what MTP achieves on Metal through MLX.

**Gemma 26B-A4B (`gguf-g-26ba4b`), `llama-bench`, same machine:**

| runtime | prefill | decode |
|---|---:|---:|
| stock Homebrew | 1924.7 t/s | **81.0 t/s** |
| patched | 1888.1 t/s | 78.7 t/s |

**The patched build makes Gemma slower** — about 2% on prefill, 3% on decode.
Expected in hindsight: the patch only touches `src/models/qwen35.cpp`, so it
gives Gemma nothing, while pinning the build to an older upstream commit
(build 10454 against the current 10809) loses whatever landed since.

So the two runtimes coexist rather than one replacing the other. `LLAMA_SERVER`
in `env.conf` points at the patched binary when you want it; leaving it empty
uses the Homebrew build, which is the right default for Gemma and for every
Qwen model without a FastMTP head.

### Turning it on


If you want the speed, build llama.cpp with the patch. One command does the
whole thing — clone, verify, patch, build, and record the result in `env.conf`:

```bash
./lib/build-fastmtp.sh
```

It refuses to build if the patch checksum does not match the provenance file
alongside it, and it works on a CommandLineTools-only machine (no full Xcode):
without the Metal compiler, shaders are compiled at runtime instead of embedded.

The patch ships inside the model repo you already downloaded:

```
models/HauhauCS--Qwen3.8-27B-Uncensored-HauhauCS-Aggressive-MTP-GGUF/
  HauhauCS-FastMTP-llama.cpp.patch
  FastMTP-PROVENANCE.json
  HauhauCS-FastMTP-Ed25519-PUBLIC.pem
```

```bash
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
git checkout 4df29be4f4c3673f428170fda944a5b19f743bb8     # the patch's base
git apply /path/to/HauhauCS-FastMTP-llama.cpp.patch
git apply --check   # ...before applying for real, so a mismatch is loud
```

Verify the patch before trusting it — the repo signs it, and the signature is
worth checking rather than taking on faith:

```bash
shasum -a 256 HauhauCS-FastMTP-llama.cpp.patch
# compare against runtime_patch_sha256 in FastMTP-PROVENANCE.json
```

Then point the server at your build and tell it the draft is usable:

```bash
LLAMA_SERVER=/path/to/llama.cpp/build/bin/llama-server   # or put it on PATH
DRAFT_PATCHED_RUNTIME=1                                   # in env.conf
./restart.sh
```

`DRAFT_PATCHED_RUNTIME=1` only says "this head is loadable" — it does not build
anything. With it set on a stock runtime, the server will exit, exactly as it
does today when the head is attached by hand.

## Bonsai 2 27B needs the PrismML fork

Bonsai 2 27B is PrismML's ternary pack of Qwen3.8-27B: the same architecture,
with the weights rotated blockwise by a Hadamard transform and then quantised to
{−1, 0, +1} with one FP16 scale per group of 128. It lands at 1.72–2.13 bits per
weight — a 27B in 6–7 GB — and it needs two things stock llama.cpp does not have:

| what | why stock llama.cpp fails |
|---|---|
| the `PQ2_0` / `PTQ1_0` weight types | Prism's own ggml types; a stock build rejects the file as an unknown type |
| the Hadamard activation transform | folded into the stored weights offline, so the runtime has to apply the matching transform to activations; not upstream yet (ggml-org/llama.cpp#27779) |

**The failure mode has a quiet half, and that is why this is written down at
all.** Stock llama.cpp refuses `PQ2_0` and `PTQ1_0` loudly. It also *loads* the
similarly-named `Q2_0` — an official ggml type at a different group size, kept in
a separate repo — **without any warning, and produces gibberish.** Nothing in the
log says so, which is worse than a crash: it looks like it worked. So the aliases
point only at the packed files, and unlike the Gemma draft-head case there is no
"substitute a nearby quant" fallback to reach for.

### Installing it

```bash
./lib/fetch-runtime.sh prism      # prebuilt macOS arm64, checksum verified
./start.sh --model gguf-b-27b
```

The script pins one PrismML release tag and verifies the archive against its
published sha256 before extracting. Pinned rather than tracking "latest", for a
concrete reason: the release published *after* the pinned one (`prism-b10687`)
lists macOS archives in its release notes but does not attach them, and every
macOS link on that tag 404s. A pinned archive that exists beats a latest that
does not. If the pinned asset ever disappears, the script prints the
build-from-source commands instead of failing with a bare curl error.

It also checks the runtime can actually do the job before wiring it up: it runs
`llama-server --version`, then confirms `libggml-metal` really contains `PQ2_0`.
The ternary kernels are the entire reason for the runtime, and their absence
would otherwise not surface until a model failed to load.

### It does not replace the Homebrew build

`lib/engines/gguf.sh` resolves the runtime **per model**, through
`runtime_key_for_model()` in `lib/common.sh`:

- a model that declares a runtime must have it, or `./start.sh` stops and names
  the install command — there is no autoregressive fallback here, because there
  is nothing to fall back to
- everything else keeps Homebrew's llama.cpp, which is the build the Gemma and
  Qwen figures in the README were measured on
- the FastMTP draft patch keeps its own knobs (`LLAMA_SERVER`,
  `DRAFT_PATCHED_RUNTIME`), because it is a property of a *draft head* rather
  than of a model

Running `./lib/fetch-runtime.sh` with no arguments lists what is available, what
is installed, and which aliases need it.

### Measured

| | PQ2_0 | PTQ1_0 |
|---|---:|---:|
| size | 7.21 GB | 5.95 GB |
| bits/weight | 2.13 | 1.75 |
| M5 Pro decode / prefill | 28.1 t/s / 387 t/s | — |

**Those are Prism's numbers, not this project's.** They were measured with
`llama-bench` at depth 0 on an M5 Pro. A first smoke test here — greedy, a
63-token prompt, 41 tokens generated, thinking on — measured **25.5 t/s** on the
reference machine, within 10% of their figure, which is why it is quoted as
usable rather than aspirational. That is a smoke test and not a benchmark: the
README's other numbers are 8k-context agent turns, best of several runs, and
Bonsai 2 has not had that treatment yet.

For scale: `mlx-q-35ba3b` does 141 t/s and `gguf-g-26ba4b` 106 t/s here. Bonsai 2
is not a speed model. At 6 GB it is a *capability per gigabyte* model, and the
honest comparison is the 9.4 GB conventional "2-bit" Qwen3.8-27B build, which
Prism measure twelve benchmark points lower at two-thirds again the size.

### On Apple Silicon, two knobs

**Speculation: leave it off.** Prism ship a `dspark` drafter (~0.6 GB) that is
worth 1.8–2.4x on CUDA. Their own documentation says that on Apple Silicon it
pays off only for ternary code and maths (~1.2x) and is a net slowdown
otherwise, so no draft head is fetched here.

**The Metal tensor API probe can fail on M5.** The Prism fork compiles its Metal
library from source to reach the tensor API, and on some M5 macOS builds that
compile fails:

```
ggml_metal_library_init_from_source: error compiling source
ggml_metal_device_init: - the tensor API is not supported in this environment - disabling
```

Observed here on an **M5 Pro running macOS 27.0**, which is outside the
26.2–26.4 window Prism document — so their range is not the whole story. ggml
recovers by itself and Metal keeps working; the cost is an alarming log line and
a few seconds of startup. To skip the attempt rather than fail it:

```conf
METAL_TENSOR_API="off"
```

That is a setting this project already exposes, and it costs nothing that was not
already lost — on a build where the probe fails, the tensor API is disabled
either way. `./start.sh` says so at startup when it sees a Prism-runtime model on
a chip that has Neural Accelerators.

### Vision

Prism ship a 0.63 GB Q8_0 vision projector and Bonsai 2 is a vision-language
model, but the **uncensored** community packs this project points at do not
include it, and pairing Prism's aligned projector with abliterated language
weights is untested. So `ENABLE_VISION` is not wired up for these aliases: text
and tool calling are what they serve today.

### What is not wired up yet

The tool-template rewrite (`TOOL_TEMPLATE=1`), which names a schema's required
fields so a harness stops reporting `missing required property`, is anchored on
Gemma's chat template. A Bonsai model therefore does not get it: `./start.sh`
prints `tool template unavailable, using the model's stock template` and serves
the model's own template instead. Nothing breaks, but the reliability work in
[the deep dive](DEEP-DIVE.md#agents-that-write-files) does not apply here yet, so
name the required fields in the agent's system prompt.

## Why this matters less than it sounds

The unpatched 27B runs at **7.6 t/s** here. That is not a bug: a dense 27B reads
about 15 GB of active weights per token, and ~7.6 t/s is what this memory
bandwidth supports. The Qwen 27B is a *dense* model — every parameter is active
on every token — unlike the Gemma 26B A4B, which is a mixture of experts with
only ~4B active and reaches 106 t/s for exactly that reason.

So the two families are not comparable on speed and should not be sold as if
they are:

| model | shape | active per token | decode here (GGUF) | MLX |
|---|---|---:|---:|---:|
| `gguf-g-26ba4b` (Gemma 4) | MoE, 8 of 128 experts | ~4B | **106.4 t/s** | — |
| `gguf-q-9b` | dense | 9B | 44.3 t/s | ~90 t/s |
| `gguf-q-27b` | dense | 27B | 13.5 t/s | ~30 t/s |

If you want Qwen 27B speed, the honest answer is the **MLX engine**: MTPLX's MTP
implementation works on Metal and llama.cpp's does not. Both engines ship in
this one install, so switching is a model change rather than a switch of repo.
