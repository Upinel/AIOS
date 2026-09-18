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
# Install a llama.cpp runtime that Homebrew does not ship.
#
#   ./lib/fetch-runtime.sh              list the runtimes, and what you have
#   ./lib/fetch-runtime.sh prism        download, verify, install and wire up
#   ./lib/fetch-runtime.sh prism --force   replace an existing install
#
# Why this exists
# ---------------
# Bonsai 2 27B stores its weights in PrismML's ternary packings, PQ2_0 and
# PTQ1_0. Those are ggml types stock llama.cpp does not define, and the model
# also needs a Hadamard activation transform that is not upstream yet, so a
# stock build refuses the file outright - and for the similarly-named Q2_0 it
# loads the file without a warning and emits gibberish. The kernels live in
# PrismML-Eng/llama.cpp, and this script installs their prebuilt macOS arm64
# binary into run/runtimes/prism and records the path in env.conf as
# PRISM_RUNTIME.
#
# It does not replace the Homebrew build. lib/engines/gguf.sh uses this binary
# only for the models that declare it (runtime_key_for_model in lib/common.sh),
# so Gemma, Qwen and the rest keep the stock runtime they were measured on.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/lib/common.sh"
load_config

# ── the pinned runtime ───────────────────────────────────────────────────────
# Pinned to a specific tag on purpose, and this one specifically: the release
# published after it (prism-b10687) lists macOS archives in its notes but does
# not actually attach them - every macOS link on that tag 404s. A pinned,
# checksummed archive that exists beats a "latest" that does not.
#
# The checksum is the asset's own sha256 from the GitHub releases API.
PRISM_TAG="prism-b10685-7dffb15"
PRISM_ASSET="llama-${PRISM_TAG}-bin-macos-arm64.tar.gz"
PRISM_SHA256="7fffa7a40c74f3e9bd78f3f2f9f12f9befb7b13af45d5a69c239cf3fd37b9045"
PRISM_URL="https://github.com/PrismML-Eng/llama.cpp/releases/download/${PRISM_TAG}/${PRISM_ASSET}"

FORCE=0
WANT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --force)  FORCE=1; shift ;;
    -h|--help) show_usage "${BASH_SOURCE[0]}"; exit 0 ;;
    prism)    WANT="prism"; shift ;;
    fastmtp)  die "The FastMTP runtime is not installed by this script.
    It is built from a patch: ./lib/build-fastmtp.sh" ;;
    *)        die "Unknown argument: $1  (try --help)" ;;
  esac
done

# Aliases that need a runtime, read out of the registry rather than retyped, so
# this line cannot go stale when a model is added.
aliases_for_runtime() {
  local key="$1" a out=""
  for a in $MODEL_ALIASES; do
    if [[ "$(runtime_key_for_model "$(model_repo_for "$a" 2>/dev/null)")" == "$key" ]]; then
      out="$out $a"
    fi
  done
  printf '%s\n' "${out# }"
}

report_runtime() {
  local key="$1" dir server configured stamp
  dir="$(runtime_default_dir "$key")"
  server="$dir/llama-server"
  configured="$(runtime_server_for "$key")"
  stamp=""
  [[ -f "$dir/.version" ]] && stamp="$(cat "$dir/.version")"

  log ""
  log "  ${C_BOLD}$key${C_RESET}   $(runtime_label_for "$key")"
  log "    ${C_DIM}needed by     ${C_RESET}$(aliases_for_runtime "$key")"
  if [[ -x "$server" ]]; then
    log "    ${C_DIM}installed     ${C_RESET}$server"
  elif [[ -n "$configured" && -x "$configured" ]]; then
    log "    ${C_DIM}installed     ${C_RESET}$configured"
  else
    log "    ${C_DIM}installed     ${C_RESET}no"
  fi
  [[ -n "$stamp" ]] && log "    ${C_DIM}pinned tag    ${C_RESET}$stamp"
  if [[ -n "$configured" ]]; then
    log "    ${C_DIM}env.conf      ${C_RESET}$(runtime_env_key_for "$key") is set"
  else
    log "    ${C_DIM}env.conf      ${C_RESET}$(runtime_env_key_for "$key") is empty"
  fi
}

list_runtimes() {
  step "llama.cpp runtimes"
  report_runtime prism
  log ""
  log "  ${C_DIM}Install the one you need:  ./lib/fetch-runtime.sh prism${C_RESET}"
  log ""
}

if [[ -z "$WANT" ]]; then
  list_runtimes
  exit 0
fi

# ── install prism ────────────────────────────────────────────────────────────
step "Installing the $(runtime_label_for prism)"

require_bin curl "curl is required to download the runtime."
require_bin shasum "shasum is required to verify the download."
require_bin tar "tar ships with macOS; something is very wrong if it is missing."

DEST="$(runtime_default_dir prism)"
SERVER="$DEST/llama-server"
STAMP=""

if [[ -x "$SERVER" && -f "$DEST/.version" ]]; then
  STAMP="$(cat "$DEST/.version")"
fi

if [[ -x "$SERVER" && "$STAMP" == "$PRISM_TAG" && "$FORCE" == "0" ]]; then
  ok "already installed: $SERVER  ($STAMP)"
  info "use --force to replace it."
else
  # Probe before downloading: a release that advertises a macOS archive without
  # publishing one should fail with a sentence, not a bare curl error.
  if ! curl -fsIL --max-time 60 "$PRISM_URL" >/dev/null 2>&1; then
    die "No macOS arm64 build at:
    $PRISM_URL
    Prism's release notes list macOS archives that some tags do not publish.
    Build the fork from source instead, then point PRISM_RUNTIME at it:
        git clone https://github.com/PrismML-Eng/llama.cpp
        cmake -B llama.cpp/build -S llama.cpp -DGGML_METAL=ON
        cmake --build llama.cpp/build -j\"\$(sysctl -n hw.ncpu)\" --target llama-server
        # then in env.conf:  PRISM_RUNTIME=\"<abs path>/llama.cpp/build/bin/llama-server\""
  fi

  info "downloading $PRISM_ASSET"
  mkdir -p "$DEST"
  # Staged OUTSIDE $DEST on purpose: the install below replaces that whole
  # directory, and a tarball living inside it is deleted a moment before it is
  # extracted. (That was a real bug: `rm -rf "$DEST"` took the download with it.)
  TMP="$RUN_DIR/.runtime-${WANT}.tar.gz"
  rm -f "$TMP"
  curl -fL --retry 5 --retry-delay 2 --retry-all-errors -o "$TMP" "$PRISM_URL" \
    || die "download failed."

  GOT="$(shasum -a 256 "$TMP" | awk '{print $1}')"
  if [[ "$GOT" != "$PRISM_SHA256" ]]; then
    rm -f "$TMP"
    die "Checksum MISMATCH for $PRISM_ASSET
    expected: $PRISM_SHA256
    actual:   $GOT
    Refusing to install a runtime that is not the archived one."
  fi
  ok "checksum matches the pinned release"

  info "extracting to $DEST"
  rm -rf "$DEST"
  mkdir -p "$DEST"
  tar -xzf "$TMP" -C "$DEST" --strip-components=1 || die "could not extract the archive."
  rm -f "$TMP"
  printf '%s\n' "$PRISM_TAG" > "$DEST/.version"

  # curl does not set the quarantine attribute, but a runtime copied out of a
  # browser download does, and macOS then refuses to exec an unsigned binary.
  # Clearing it is a no-op when it is not there.
  xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
  chmod +x "$DEST"/llama-* 2>/dev/null || true
fi

[[ -x "$SERVER" ]] || die "no llama-server at $SERVER after installing."
SERVER_VERSION="$("$SERVER" --version 2>&1 | grep -m1 '^version:' || true)"
[[ -n "$SERVER_VERSION" ]] \
  || die "$SERVER did not report a version - the archive may be corrupt."
ok "$SERVER_VERSION"

# The ternary kernels are the whole reason for this runtime, and their absence
# would not show up until the model failed to load. libggml-metal carries them.
#
# `grep -c`, not `grep -q`: -q exits on the first match, which SIGPIPEs the
# `strings` feeding it, and under `set -o pipefail` that SIGPIPE becomes the
# pipeline's status even though the match succeeded. Counting reads to the end
# and cannot fail that way.
METAL_LIB="$DEST/libggml-metal.0.dylib"
[[ -e "$METAL_LIB" ]] || METAL_LIB="$DEST/libggml-metal.0.21.0.dylib"
TERNARY_HITS="$(strings "$METAL_LIB" 2>/dev/null | grep -c 'PQ2_0' || true)"
if [[ "${TERNARY_HITS:-0}" -gt 0 ]]; then
  ok "ternary kernels present (PQ2_0/PTQ1_0 in libggml-metal)"
else
  warn "ternary kernels not found in libggml-metal - Bonsai 2 may refuse to load."
fi

if [[ "$(runtime_server_for prism)" == "$SERVER" ]]; then
  ok "env.conf already points at it ($(runtime_env_key_for prism))"
else
  info "recording the path in env.conf..."
  set_config_value PRISM_RUNTIME "$SERVER"
  ok "env.conf: $(runtime_env_key_for prism) set"
fi

log ""
info "Serve it:"
log "    ./start.sh --model $(aliases_for_runtime prism | awk '{print $1}')"
log ""
info "Measured by Prism on an M5 Pro, PQ2_0 (their figure, not ours):"
log "    28.1 t/s decode, 387 t/s prefill - a 27B at 6 GB, not a speed record."
log ""
info "If the log shows 'ggml_metal_library_init_from_source: error compiling source',"
info "set METAL_TENSOR_API=off in env.conf - it skips the probe, not the Metal path."
