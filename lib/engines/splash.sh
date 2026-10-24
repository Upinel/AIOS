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
# Splash engine - incoai/splash (Apache-2.0), an Apple-silicon runtime built
# around a few model families: DFlash2 speculative decoding with drafts trained
# for each family, Metal kernels for their exact shapes, automatic batching and
# a prefix cache shared across sessions.
#
# Unlike gguf.sh and mlx.sh, no alias names this engine. It takes over a model
# whose own engine is llama.cpp or MTPLX when all of these hold (see
# splash_takes_over in lib/common.sh): Splash is installed, the chip is M3 or
# newer, the model is a Splash family (Qwen 3.8 27B, Qwen 3.6 35B-A3B), and
# Splash accepts the files on disk. Anything else - Gemma, an M1/M2, a pack in
# tensor types Splash does not load - keeps its own engine, and SPLASH=off in
# env.conf turns it off entirely.
#
# Same contract as the other two engines, so nothing shared knows which runs.

engine_present() { [[ -n "$(splash_python)" ]]; }

engine_name() { echo "Splash"; }

engine_version() {
  local r; r="$(splash_root)"
  [[ -n "$r" ]] || return 0
  python3 -c 'import json,sys; print("Splash", json.load(open(sys.argv[1])).get("version", "?"))' \
    "$r/release.json" 2>/dev/null || splash --version 2>/dev/null | head -1
}

engine_install_hint() {
  echo "brew tap incoai/tap && brew install incoai/tap/splash   (M3 or newer, macOS 26.4+)"
}

# Splash comes from a third-party Homebrew tap, so it is installed only when
# asked for - this never runs it unattended.
engine_install() {
  if engine_present; then ok "Splash already installed: $(engine_version)"; return 0; fi
  warn "Splash is not installed. To use it for the Qwen 27B and 35B on this Mac:"
  warn "    $(engine_install_hint)"
  return 1
}

# Splash picks its own draft block and verify rows per model; there is no
# depth to sweep.
engine_tunable() { return 1; }

# Splash's server is a Python module started by lib/splash-serve.py, so the
# "binary" is Splash's Python and the serve word is that script.
engine_serve_word() { echo "-P $REPO_DIR/lib/splash-serve.py"; }

engine_binary() { splash_python; }

# A model Splash serves is a model another engine owns on disk; that engine's
# notion of a complete model applies.
engine_model_ok() {
  local dir="$1"
  [[ -n "$(splash_target_for_dir "$dir")" ]]
}

engine_main_file() { splash_target_for_dir "$1"; }

engine_model_summary() {
  local t; t="$(splash_target_for_dir "$1")"
  [[ -n "$t" ]] && basename "$t" || echo "incomplete"
}

engine_status_extras() { :; }
engine_post_fetch_notes() { :; }
engine_resolved_profile() { :; }

# Splash shares one prefix cache across all sessions and batches concurrent
# requests itself - the prefix router has nothing to add.
engine_router_command() { :; }

# ── command line ─────────────────────────────────────────────────────────────
engine_build_args() {
  local dir="$1" target draft lo=() kv effort
  target="$(splash_target_for_dir "$dir")"
  [[ -n "$target" ]] || die "No model Splash can serve in $dir"
  draft="$(splash_draft_for_repo "${MODEL_REPO:-}")"
  [[ -n "$draft" ]] || die "$MODEL_REPO is not a Splash family."
  # Vision needs the GGUF's projector, or an MLX pack's processor config; text
  # only unless asked for, which is also what the other engines default to.
  (( ${ENABLE_VISION:-0} )) || lo=( --language-only )

  case "$(kv_quant_for gguf)" in
    f16) kv="bf16" ;;            # Splash has no f16 cache; bf16 is the unquantised one
    *)   kv="int8" ;;
  esac
  case "${THINKING:-off}" in
    off)     effort="none" ;;
    minimal) effort="minimal" ;;
    low)     effort="low" ;;
    medium)  effort="medium" ;;
    *)       effort="high" ;;
  esac

  ARGS=( --target "$target" --draft-repo "$draft" --model-id "${MODEL_REPO:-local/model}" "${lo[@]}" --
         --host "$HOST" --port "$PORT"
         --served-model-name "$SERVED_MODEL_NAME" --announce-served-name
         --max-context "$CONTEXT_WINDOW"
         --kv-format "$kv"
         --default-reasoning-effort "$effort"
         --idle-release off
         --no-webui )
  (( ${MEMORY_LIMIT_GB:-0} > 0 )) && ARGS+=( --max-memory "${MEMORY_LIMIT_GB}G" )
  [[ -n "${API_KEY:-}" ]] && ARGS+=( --api-key "$API_KEY" )
  # Prefixes kept on SSD across restarts - the same promise the MLX engine's
  # SSD session cache and the llama.cpp router's snapshots make.
  [[ "${SSD_SESSION_CACHE:-on}" == "off" ]] || ARGS+=( --persistent-cache )

  ENGINE_DEPTH="DFlash2"
  ENGINE_SPLASH_TARGET="$target"
}

engine_export_env() { :; }

engine_banner() {
  log "  speculative  DFlash2, chosen per request by Splash   ($(basename "$(splash_draft_for_repo "${MODEL_REPO:-}")"))"
  log "  weights      $(basename "${ENGINE_SPLASH_TARGET:-?}")  (linked in place, not copied)"
  log "  sessions     batched by Splash, one shared prefix cache"
}

engine_apply_settings() {
  local mac; mac="$(sw_vers -productVersion 2>/dev/null || echo 0)"
  case "$mac" in
    2[6-9].*|[3-9][0-9].*) ;;
    *) die "Splash needs macOS 26.4 or later (this Mac runs $mac). Set SPLASH=off in env.conf." ;;
  esac
}
