#!/usr/bin/env bash
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
# GGUF engine - llama.cpp.
#
# Sourced by start.sh and install.sh when the selected model is a GGUF
# checkpoint. Everything engine-specific lives here: which binary to run, how to
# recognise a complete model on disk, how to turn env.conf into command-line
# flags, and how to launch.
#
# The shared front end (config, registry, picker, chat, dashboard) knows none of
# this and only calls the engine_* functions below.

# ── presence ─────────────────────────────────────────────────────────────────
# Stock llama.cpp on PATH is the normal case. A runtime a model brings with it
# counts too: ./lib/fetch-runtime.sh installs a complete llama.cpp, so a machine
# serving only Bonsai 2 has everything it needs without Homebrew's copy - and
# telling it "llama.cpp is not installed" right after it installed one would be
# wrong.
engine_present() {
  command -v llama-server >/dev/null 2>&1 && return 0
  local key bin
  key="$(runtime_key_for_model "${MODEL_REPO:-}")"
  [[ -n "$key" ]] || return 1
  bin="$(runtime_server_for "$key")"
  [[ -n "$bin" && -x "$bin" ]]
}

engine_name() { echo "llama.cpp"; }

engine_version() {
  llama_version
}

engine_install_hint() {
  echo "Run ./install.sh first, or: brew install llama.cpp"
}

# Install the runtime. Idempotent: skips when it is already there.
engine_install() {
  if command -v llama-server >/dev/null 2>&1; then
    ok "llama.cpp already installed: $(llama_version)"
    return 0
  fi
  require_bin brew "Install Homebrew from https://brew.sh, then re-run ./install.sh"
  info "brew install llama.cpp"
  brew install llama.cpp || die "brew install llama.cpp failed."
  ok "llama.cpp ready: $(llama_version)"
}

# Does this engine have a speculative-depth sweep worth running? GGUF ships an
# MTP draft head, so depth is a real knob. MLX does not sweep the same way.
engine_tunable() { return 0; }

# Nothing to install beyond the binary itself. LLAMA_SERVER in env.conf may
# point at a patched build for models whose draft head needs it.
# llama-server takes the weights directly; MTPLX needs the "serve" subcommand.
# Declared empty rather than left undefined so start.sh's $SERVE_WORD use is
# visibly correct for both engines instead of relying on a missing function.
engine_serve_word() { :; }

# ── model on disk ────────────────────────────────────────────────────────────
# A GGUF model directory is usable when it holds a main weights file. Draft
# heads and projectors are optional extras, not requirements.
engine_model_ok() {
  local dir="$1"
  [[ -d "$dir" ]] || return 1
  [[ -n "$(model_main_gguf "$dir" 2>/dev/null || true)" ]]
}

engine_main_file() {
  model_main_gguf "$1" 2>/dev/null || true
}

# Human-facing one-liner for the picker.
engine_model_summary() {
  local dir="$1" main
  main="$(engine_main_file "$dir")"
  [[ -n "$main" ]] || { echo "incomplete"; return 0; }
  echo "$(basename "$main")"
}

# Facts the dashboard needs about the model on disk. Sets, in the caller:
#   STATUS_MAIN        path to the weight file, or empty
#   STATUS_WEIGHTS_GB  its size in GB
#   STATUS_VISION_GB   size of the projector, or 0
#
# GGUF has a single weight file and an optional projector. MLX has a shard set
# and no projector, so it fills nothing - reporting zeros is more honest than
# reporting blank panels, and the dashboard says "not applicable" either way.
engine_status_extras() {
  local dir="$1" mm
  STATUS_MAIN="$(model_main_gguf "$dir" 2>/dev/null || true)"
  if [[ -n "$STATUS_MAIN" ]]; then
    STATUS_WEIGHTS_GB=$(( $(stat -f%z "$STATUS_MAIN" 2>/dev/null || echo 0) / 1000000000 ))
  fi
  mm="$(model_mmproj_gguf "$dir" 2>/dev/null || true)"
  if [[ -n "$mm" ]]; then
    STATUS_VISION_GB=$(( $(stat -f%z "$mm" 2>/dev/null || echo 0) / 1000000000 ))
  fi
}

# What to report about a freshly fetched model, beyond "it downloaded".
#
# Engine-specific on purpose: model_download.sh used to run these checks for
# every model regardless of engine, so an MLX pack was told it would "run
# autoregressive only" because it has no GGUF mmproj - a warning about a file
# the engine does not use, on a model whose MTP was working fine.
engine_post_fetch_notes() {
  local dir="$1" rtkey
  rtkey="$(runtime_key_for_model "$(model_repo_from_dir "$dir")")"
  if [[ -n "$(model_mmproj_gguf "$dir" || true)" ]]; then
    ok "Vision projector present."
  fi
  if [[ -n "$(model_draft_gguf "$dir" || true)" ]]; then
    ok "Speculative draft present - expect a real speedup."
  elif [[ "$rtkey" == "prism" ]]; then
    # Not a shortcoming here. Prism measure speculation on Apple Silicon as a
    # net slowdown except on ternary code and maths, so no draft head is
    # fetched on purpose - and warning about a file that is deliberately
    # absent would send people looking for a fix that does not exist.
    info "No draft head by design: speculation is a net slowdown on Metal here."
  else
    warn "No draft file: this model runs autoregressive only."
  fi
}

# llama.cpp has no profile concept - its equivalent knobs are explicit flags
# (flash attention, KV quant, speculative depth), all visible in the command
# line. Empty here, so the dashboard shows nothing rather than inventing a
# preset name for an engine that has none.
engine_resolved_profile() { :; }

# ── command line ─────────────────────────────────────────────────────────────
# Builds ARGS[] from the unified config. Declares ARGS global on purpose: the
# caller echoes it for --print and passes it to the binary.
engine_build_args() {
  local dir="$1"
  local main mmproj draft depth budget rtkey rtbin

  main="$(model_main_gguf "$dir" || true)"
  [[ -n "$main" ]] || die "No GGUF weights found in $dir"

  mmproj=""
  if (( ENABLE_VISION )); then
    mmproj="$(model_mmproj_gguf "$dir" || true)"
    [[ -n "$mmproj" ]] || warn "ENABLE_VISION=1 but no mmproj found; serving text only."
  fi

  draft="$(model_draft_gguf "$dir" || true)"
  depth="$(effective_depth)"

  # A trimmed-vocabulary draft head accepts fewer tokens the deeper you draft,
  # so depth 1 beats depth 3 for those. Only when the user left MTP_DEPTH=auto
  # and no tuned value exists.
  if [[ "$MTP_DEPTH" == "auto" ]] && [[ -n "$draft" ]] \
     && [[ -z "$(tuned_depth)" ]] && draft_needs_patched_runtime "$draft"; then
    depth=1
  fi
  if (( depth > 0 )) && [[ -z "$draft" ]]; then depth=0; fi

  # Some models cannot run on stock llama.cpp at all, and unlike the draft case
  # below there is no fallback to fall back to: the file is written in ggml
  # types a stock build does not define. Serving it would fail at load - and the
  # nearest-looking substitute is worse than a failure, because stock llama.cpp
  # loads it and emits gibberish. So a model-declared runtime is a hard
  # requirement: resolve it or stop with something the user can act on.
  LLAMA_BIN="llama-server"
  rtkey="$(runtime_key_for_model "${MODEL_REPO:-}")"
  if [[ -n "$rtkey" ]]; then
    rtbin="$(runtime_server_for "$rtkey")"
    if [[ -n "$rtbin" && -x "$rtbin" ]]; then
      LLAMA_BIN="$rtbin"
    else
      die "$MODEL_REPO needs the $(runtime_label_for "$rtkey").
    Stock llama.cpp cannot load this file - it does not define its weight types.
    Install the runtime once, then start again:
        $(runtime_fetch_hint "$rtkey")"
    fi
  fi

  # A draft built against a patched llama.cpp will not load on a stock one, and
  # llama-server treats that as fatal. Detect it rather than letting "a bit more
  # speed" become "will not start".
  if [[ -z "$rtkey" ]] && (( depth > 0 )) && [[ -n "$draft" ]] && draft_needs_patched_runtime "$draft"; then
    if [[ -n "${LLAMA_SERVER:-}" && -x "${LLAMA_SERVER:-}" ]]; then
      LLAMA_BIN="$LLAMA_SERVER"
    elif (( ${DRAFT_PATCHED_RUNTIME:-0} )); then
      LLAMA_BIN="llama-server"
    else
      warn "$(basename "$draft") needs a patched llama.cpp (trimmed draft vocab)."
      warn "Running autoregressive. See docs/GGUF-RUNTIME.md."
      depth=0; draft=""
    fi
  fi

  ARGS=(
    -m "$main"
    -ngl all
    -fa on
    -c "$CONTEXT_WINDOW"
    -b "$BATCH_SIZE"
    -ub "$UBATCH_SIZE"
    --parallel "$PARALLEL_SLOTS"
    -ctk "$(kv_quant_for gguf)"
    -ctv "$(kv_quant_for gguf)"
    --host "$HOST"
    --port "$PORT"
    --alias "$SERVED_MODEL_NAME"
    --no-webui
    --metrics
    --timeout 3600
  )

  # Keep the weights resident. Without this macOS can page them out and decode
  # becomes erratic rather than merely slow.
  (( USE_MLOCK )) && ARGS+=( -lm mlock )
  [[ -n "$mmproj" ]] && ARGS+=( --mmproj "$mmproj" )
  [[ -n "${API_KEY:-}" ]] && ARGS+=( --api-key "$API_KEY" )

  if (( depth > 0 )) && [[ -n "$draft" ]]; then
    ARGS+=( -md "$draft" --spec-type draft-mtp --spec-draft-n-max "$depth" --spec-draft-ngl all )
  fi

  # Thinking. Gemma 4's template reads enable_thinking; llama.cpp passes
  # --chat-template-kwargs straight through.
  ARGS+=( --chat-template-kwargs "$(thinking_kwargs)" )

  # Cap the thought channel. Without this "thinking on" is unbounded and the
  # model will spend 300 tokens reasoning about where to find a file.
  budget="$(effective_thinking_budget)"
  if [[ -n "$budget" ]]; then
    ARGS+=( --reasoning-budget "$budget" )
    [[ -n "${THINKING_BUDGET_MESSAGE:-}" ]] && \
      ARGS+=( --reasoning-budget-message "$THINKING_BUDGET_MESSAGE" )
  fi

  # Tool-call reliability. Gemma follows a schema's semantics but not its
  # `required` list, so harnesses see "missing required property" on fields the
  # model judged optional. Naming them in the template fixes it server-side.
  if (( TOOL_TEMPLATE )) && [[ -n "$main" ]]; then
    if python3 "$REPO_DIR/lib/tools-template.py" --gguf "$main" \
         --out "$RUN_DIR/tools-template.jinja" 2>>"$RUN_DIR/template.err"; then
      ARGS+=( --chat-template-file "$RUN_DIR/tools-template.jinja" )
    else
      log "  note: tool template unavailable, using the model's stock template"
    fi
  fi

  ENGINE_DEPTH="$depth"
  ENGINE_DRAFT="${draft:-}"
  ENGINE_MMPROJ="$mmproj"
}

# Extra environment the runtime needs, beyond the command line.
engine_export_env() { :; }

# Lines for the startup banner. Engine-specific facts the shared front end
# cannot know.
engine_banner() {
  local rtkey
  rtkey="$(runtime_key_for_model "${MODEL_REPO:-}")"
  if [[ -n "$rtkey" ]]; then
    log "  runtime      $(runtime_label_for "$rtkey")"
  fi
  if (( ${ENGINE_DEPTH:-0} > 0 )); then
    log "  speculative  depth $ENGINE_DEPTH   ($(basename "$ENGINE_DRAFT"))"
  else
    log "  speculative  off (plain autoregressive)"
  fi
  log "  vision       $([[ -n "${ENGINE_MMPROJ:-}" ]] && echo "on" || echo "off")"
  log "  tensor API   $(gguf_tensor_state)"
}

# Report the RESOLVED state, not the setting: "auto" means something different
# on an M5 than on an M3.
gguf_tensor_state() {
  if chip_has_neural_accelerator; then
    case "${METAL_TENSOR_API:-auto}" in
      off) echo "off (forced off; ~2x prefill left on the table)" ;;
      *)   echo "on ($(chip_family) Neural Accelerators, ~2x prefill)" ;;
    esac
  elif [[ "${METAL_TENSOR_API:-auto}" == "on" ]]; then
    echo "on (forced on a chip without them - expect no gain, possibly slower)"
  else
    echo "off (none before M5; standard Metal kernels)"
  fi
}

# Apply METAL_TENSOR_API to llama.cpp's env vars. It reads these at device init,
# so they must be set before the server starts.
engine_apply_settings() {
  case "${METAL_TENSOR_API:-auto}" in
    auto) ;;
    on)
      if ! chip_has_neural_accelerator; then
        warn "METAL_TENSOR_API=on, but this chip has no Neural Accelerators (M5+)."
        warn "llama.cpp measures the tensor API as ~5% SLOWER on M2 Ultra, neutral on M4."
      fi
      export GGML_METAL_TENSOR_ENABLE=1
      ;;
    off) export GGML_METAL_TENSOR_DISABLE=1 ;;
    *)   die "METAL_TENSOR_API=\"$METAL_TENSOR_API\" is not one of auto | on | off" ;;
  esac

  # The Prism fork compiles its Metal library from source to reach the tensor
  # API, and on some M5 macOS builds that compile fails:
  #
  #   ggml_metal_library_init_from_source: error compiling source
  #   ggml_metal_device_init: - the tensor API is not supported ... disabling
  #
  # Observed here on an M5 Pro running macOS 27.0; Prism document the same
  # failure on macOS 26.2-26.4. ggml recovers on its own, but the failed compile
  # is alarming in a log and costs a few seconds of startup. METAL_TENSOR_API=off
  # skips the attempt rather than failing it, and does not cost anything that
  # was not already lost - so say so where it actually applies, which is a chip
  # that has Neural Accelerators and a model that needs this runtime.
  if [[ -n "$(runtime_key_for_model "${MODEL_REPO:-}")" ]] \
     && chip_has_neural_accelerator \
     && [[ "${METAL_TENSOR_API:-auto}" != "off" ]]; then
    warn "Running the Prism runtime with the tensor API enabled."
    warn "If the log shows 'ggml_metal_library_init_from_source: error compiling source',"
    warn "set METAL_TENSOR_API=off - it keeps full Metal speed and only skips the probe."
  fi
}

engine_binary() { echo "${LLAMA_BIN:-llama-server}"; }
