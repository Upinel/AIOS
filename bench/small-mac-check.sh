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
# Measure the small-Mac defaults on a small Mac.
#
#   ./bench/small-mac-check.sh            run everything (about 40-60 minutes)
#   ./bench/small-mac-check.sh --quick    one round instead of two
#
# Every default in this project was measured on an M5 Pro. The ones that only
# matter on M1-M4 and 16 GB were reasoned, not measured - this script measures
# them, on the machine they are for:
#
#   * Gemma 4 E2B with its new MTP assistant draft, depth 1-3, against no draft
#     (llama.cpp's few-row mat-mul kernels for speculative verify only run on
#     M1-M4, so this is where they should show)
#   * ubatch 512 against 1024, and f16 against q8 KV, on 16 GB
#   * the prefix router: four subagents sharing a tool prefix, with and without
#   * MTPLX 2.12 on Qwen 3.8 9B, depth 1-3
#
# It stops a running UpinelAIOS server first (it needs the memory) and does not
# restart it. Results land in run/small-mac-report/; send report.txt back.

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$REPO_DIR/lib/common.sh"
load_config

ROUNDS=2
ROUTER_ONLY=0
for _a in "$@"; do
  case "$_a" in
    --quick)       ROUNDS=1 ;;
    --router-only) ROUTER_ONLY=1 ;;
  esac
done
OUT="$RUN_DIR/small-mac-report"
mkdir -p "$OUT"
PORT_T=8011

step "Small-Mac check on $(detected_chip), $(total_ram_gb) GB"

info "stopping any running server (it needs the memory)"
"$REPO_DIR/stop.sh" >/dev/null 2>&1 || true

# ── runtimes ─────────────────────────────────────────────────────────────────
"$REPO_DIR/lib/fetch-runtime.sh" llama
LLAMA="$RUN_DIR/runtimes/llama/llama-server"
[[ -x "$LLAMA" ]] || die "pinned llama.cpp did not install"
source "$REPO_DIR/lib/engines/mlx.sh"
if command -v brew >/dev/null 2>&1 && brew list --versions mtplx >/dev/null 2>&1; then
  brew upgrade youssofal/mtplx/mtplx >/dev/null 2>&1 || true
fi
_MTPLX_BIN_CACHE=""
MTPLX="$(mtplx_bin)"
info "llama.cpp $("$LLAMA" --version 2>&1 | sed -n 's/^version: //p')"
info "$("$MTPLX" --version 2>/dev/null | head -1 || echo 'mtplx: not installed')"

# ── models ───────────────────────────────────────────────────────────────────
need_model() {
  local alias="$1" repo dir
  repo="$(model_repo_for "$alias")"; dir="$MODELS_DIR/${repo//\//--}"
  if [[ ! -d "$dir" ]] || ! ls "$dir" | grep -q .; then
    if ask_yes_no "Download $alias ($repo)?" y; then
      "$REPO_DIR/model_download.sh" "$alias"
    else
      return 1
    fi
  else
    # Fetches the E2B's MTP companion if this install predates it.
    "$REPO_DIR/model_download.sh" "$alias" >/dev/null 2>&1 || true
  fi
  printf '%s\n' "$dir"
}

E2B_DIR="$(need_model gguf-g-e2b)" || die "gguf-g-e2b is needed"
E2B="$(model_main_gguf "$E2B_DIR")"
MODEL_REPO="$(model_repo_for gguf-g-e2b)"
E2B_DRAFT="$(model_draft_gguf "$E2B_DIR")"
[[ -n "$E2B_DRAFT" ]] || warn "no MTP assistant found for the E2B - the draft rows will fail"
python3 "$REPO_DIR/lib/tools-template.py" --gguf "$E2B" --out "$OUT/tpl-e2b.jinja" >/dev/null 2>&1 \
  && TPL=( --chat-template-file "$OUT/tpl-e2b.jinja" ) || TPL=()

Q9_DIR=""
if [[ -x "$MTPLX" ]]; then
  Q9_DIR="$(need_model mlx-q-9b)" || Q9_DIR=""
fi

# ── configs ──────────────────────────────────────────────────────────────────
# 64k, not the old flat 16k: four subagents with an 8k tool prefix share one
# pool and need ~34k, and the E2B's cache is ~0.45 GB at 64k (q8).
CTX=65536
BASE="$LLAMA -m $E2B -ngl all -fa on -c $CTX -b 2048 --host 127.0.0.1 --port {port} \
--alias Upinel-AIOS --no-webui --parallel 4 --kv-unified --cache-ram 1024 -lm mmap+mlock \
--chat-template-kwargs '{\"enable_thinking\":false}' ${TPL[*]:-}"
DRAFT="-md $E2B_DRAFT --spec-type draft-mtp --spec-draft-ngl all"
CFG="$OUT/configs.txt"
{
  echo "e2b-nodraft=$BASE -ub 512 -ctk f16 -ctv f16"
  echo "e2b-mtp1=$BASE -ub 512 -ctk f16 -ctv f16 $DRAFT --spec-draft-n-max 1"
  echo "e2b-mtp2=$BASE -ub 512 -ctk f16 -ctv f16 $DRAFT --spec-draft-n-max 2"
  echo "e2b-mtp3=$BASE -ub 512 -ctk f16 -ctv f16 $DRAFT --spec-draft-n-max 3"
  echo "e2b-mtp2-ub1024=$BASE -ub 1024 -ctk f16 -ctv f16 $DRAFT --spec-draft-n-max 2"
  echo "e2b-mtp2-q8=$BASE -ub 512 -ctk q8_0 -ctv q8_0 $DRAFT --spec-draft-n-max 2"
  if [[ -n "$Q9_DIR" ]]; then
    MX="$MTPLX serve --model $Q9_DIR --host 127.0.0.1 --port {port} --context-window $CTX \
--max-tokens 4096 --paged-kv-quantization q8 --batching-preset agent --max-active-requests 4 \
--stream-interval 1 --ssd-session-cache off --warmup-tokens 8 --model-id Upinel-AIOS \
--no-stats-footer --preserve-thinking scoped --reasoning off"
    echo "q9-mtplx-d1=$MX --depth 1"
    echo "q9-mtplx-d2=$MX --depth 2"
    echo "q9-mtplx-d3=$MX --depth 3"
  fi
} > "$CFG"

if (( ! ROUTER_ONLY )); then
step "1/2  engine settings ($ROUNDS round(s), interleaved)"
python3 "$REPO_DIR/bench/matrix.py" --rounds "$ROUNDS" --configs-file "$CFG" --port "$PORT_T" \
  --out "$OUT/matrix.json" \
  --replay-args "--prefix-tokens 8000 --turns 5 --write-tokens 1024" | tee "$OUT/matrix.txt"
fi

# ── the router ───────────────────────────────────────────────────────────────
step "2/2  prefix router: four subagents sharing one tool prefix"
SNAP="$OUT/slots"; mkdir -p "$SNAP"
RCFG="$OUT/router-configs.txt"
{
  echo "direct=$BASE -ub 512 -ctk f16 -ctv f16 $DRAFT --spec-draft-n-max 2"
  echo "router=python3 $REPO_DIR/lib/agent-router.py --listen 127.0.0.1:{port} --backend http://127.0.0.1:8111 --snap-dir $SNAP --ident small-mac-\$RANDOM & $(printf '%s' "$BASE" | sed 's/{port}/8111/') -ub 512 -ctk f16 -ctv f16 $DRAFT --spec-draft-n-max 2 --slot-save-path $SNAP"
} > "$RCFG"
python3 "$REPO_DIR/bench/matrix.py" --rounds "$ROUNDS" --configs-file "$RCFG" --port "$PORT_T" \
  --out "$OUT/router.json" \
  --replay-args "--prefix-tokens 8000 --turns 2 --phases prefix,turns,subagents --write-tokens 256" \
  | tee "$OUT/router.txt"

{
  echo "UpinelAIOS small-Mac check  $(date '+%Y-%m-%d %H:%M')"
  echo "chip: $(detected_chip)   RAM: $(total_ram_gb) GB   macOS $(macos_version)"
  echo "llama.cpp: $("$LLAMA" --version 2>&1 | sed -n 's/^version: //p')"
  echo "mtplx: $("$MTPLX" --version 2>/dev/null | head -1 || echo none)"
  echo
  [[ -f "$OUT/matrix.txt" ]] && sed -n '/best of rounds/,$p' "$OUT/matrix.txt"
  echo
  sed -n '/best of rounds/,$p' "$OUT/router.txt"
} > "$OUT/report.txt"

ok "Done. Send this file back:  $OUT/report.txt"
log ""
cat "$OUT/report.txt"
