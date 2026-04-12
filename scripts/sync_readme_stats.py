#!/usr/bin/env python3
"""
Collect GitHub statistics and patch README.md in one step.

Usage (from repository root):
  python scripts/sync_readme_stats.py

Environment:
  GITHUB_LOGIN   optional override (default stevewoz1234567890)
  GITHUB_TOKEN   or GH_TOKEN — recommended (GraphQL + higher REST limits)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    collect = REPO_ROOT / "scripts" / "collect_github_stats.py"
    patch = REPO_ROOT / "scripts" / "patch_readme.py"
    r1 = subprocess.run([sys.executable, str(collect)], cwd=REPO_ROOT)
    if r1.returncode != 0:
        return r1.returncode
    r2 = subprocess.run([sys.executable, str(patch)], cwd=REPO_ROOT)
    return r2.returncode


if __name__ == "__main__":
    raise SystemExit(main())
