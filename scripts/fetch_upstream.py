#!/usr/bin/env python
"""Fetch the upstream reference implementation used for cross-validation.

The checkout is *not* vendored: it is pinned to the last upstream commit and
cloned into ``upstream-ref/`` (git-ignored), so the validation suite always
compares against a known state of the original project.

    python scripts/fetch_upstream.py [--force]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

UPSTREAM = "https://github.com/AngelKitty/stegosaurus.git"
PINNED = "8d5868b1bd4aa37946a80224619133e9d7116ea2"  # last commit on master (2019-10-07)
DESTINATION = Path(__file__).resolve().parents[1] / "upstream-ref"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="replace an existing checkout")
    args = parser.parse_args()

    if DESTINATION.exists():
        if not args.force:
            print(f"{DESTINATION} already exists (use --force to replace)")
            return 0
        shutil.rmtree(DESTINATION)

    if shutil.which("git") is None:
        print("git is required to fetch the reference implementation", file=sys.stderr)
        return 1

    print(f"Cloning {UPSTREAM} at {PINNED[:12]} into {DESTINATION}")
    for attempt in (1, 2):
        completed = subprocess.run(
            ["git", "clone", "--quiet", UPSTREAM, str(DESTINATION)], capture_output=True, text=True
        )
        if completed.returncode == 0:
            break
        if DESTINATION.exists():  # a half-finished clone cannot be reused
            shutil.rmtree(DESTINATION)
        if attempt == 2:
            print(completed.stderr.strip(), file=sys.stderr)
            return completed.returncode

    checkout = subprocess.run(
        ["git", "-C", str(DESTINATION), "checkout", "--quiet", PINNED],
        capture_output=True,
        text=True,
    )
    if checkout.returncode != 0:
        print(checkout.stderr.strip(), file=sys.stderr)
        return checkout.returncode
    print("Done:", DESTINATION / "stegosaurus.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
