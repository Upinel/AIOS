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
"""Serve a model that is already on disk through Splash (incoai/splash).

Run with Splash's own Python, which this script finds the rest of Splash
from. `splash serve` downloads its models from the Hub; UpinelAIOS already has
the uncensored weights in models/, so this uses Splash's local-files route
instead (install.local.prepare_local_model): the files are linked, never
copied, and only the DFlash2 draft is fetched, once, into the Hugging Face
cache.

    splash-serve.py --check  --target FILE_OR_DIR --draft-repo REPO [--language-only]
    splash-serve.py          --target FILE_OR_DIR --draft-repo REPO --model-id OWNER/REPO \\
                             [--language-only] -- <splash server options>

--check prepares the model and exits 0, or exits 3 with Splash's reason when
it cannot serve these files (a pack in tensor types it does not load, say) -
which is how the launcher decides to fall back to the model's own engine.
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def splash_root():
    # <root>/python/bin/python3 is the interpreter running this.
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(sys.executable))))
    if not os.path.isdir(os.path.join(root, "install")):
        sys.exit(f"error: run this with Splash's Python (no install/ under {root})")
    return root


def draft_dir(repo):
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(repo, local_files_only=True)
    except Exception:                                       # noqa: BLE001
        print(f"fetching the DFlash2 draft {repo} (first start only)...", file=sys.stderr)
        return snapshot_download(repo)


def main():
    if "--" in sys.argv:
        i = sys.argv.index("--")
        own, rest = sys.argv[1:i], sys.argv[i + 1:]
    else:
        own, rest = sys.argv[1:], []
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--draft-repo", required=True)
    ap.add_argument("--model-id", default="")
    ap.add_argument("--language-only", action="store_true")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(own)

    root = splash_root()
    sys.path.insert(0, root)
    from install.local import ModelError, prepare_local_model

    try:
        prepared = prepare_local_model(a.target, draft=draft_dir(a.draft_repo),
                                       language_only=a.language_only)
    except ModelError as e:
        print(f"splash cannot serve these files: {e}", file=sys.stderr)
        return 3
    if a.check:
        print(prepared)
        return 0

    env = dict(os.environ, PYTHONPATH=root, PYTHONDONTWRITEBYTECODE="1")
    argv = [sys.executable, "-P", "-m", "server.server", prepared,
            "--model", a.model_id, "--binary", os.path.join(root, "engine", "splash")] + rest
    os.execve(sys.executable, argv, env)


if __name__ == "__main__":
    sys.exit(main())
