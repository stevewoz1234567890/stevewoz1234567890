#!/usr/bin/env python3
"""
Replace marked regions in README.md with rendered fragments from github-stats.json.

Markers (HTML comments):
  <!-- github-stats:auto:start member-line --> ... <!-- github-stats:auto:end member-line -->
  <!-- github-stats:auto:start core-stats --> ... <!-- github-stats:auto:end core-stats -->
  <!-- github-stats:auto:start repos --> ... <!-- github-stats:auto:end repos -->

Run after collect_github_stats.py:
  python scripts/collect_github_stats.py && python scripts/patch_readme.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from render_readme_stats import (  # noqa: E402
    render_core_stats,
    render_member_line,
    render_repos_section,
)
from render_readme_stats import _load as load_stats  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
README_PATH = REPO_ROOT / "README.md"


def _replace_region(text: str, name: str, new_inner: str) -> str:
    start_tag = f"<!-- github-stats:auto:start {name} -->"
    end_tag = f"<!-- github-stats:auto:end {name} -->"
    si = text.find(start_tag)
    ei = text.find(end_tag)
    if si == -1 or ei == -1 or ei < si:
        raise ValueError(f"README.md: missing or invalid markers for {name!r}")
    after_start = si + len(start_tag)
    if text[after_start : after_start + 1] == "\n":
        after_start += 1
    inner = new_inner.rstrip() + "\n"
    return text[:after_start] + inner + text[ei:]


def main() -> int:
    try:
        data = load_stats()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1

    text = README_PATH.read_text(encoding="utf-8")
    try:
        text = _replace_region(text, "member-line", render_member_line(data))
        text = _replace_region(text, "core-stats", render_core_stats(data))
        text = _replace_region(text, "repos", render_repos_section(data))
    except ValueError as e:
        print(f"patch_readme: {e}", file=sys.stderr)
        return 1

    README_PATH.write_text(text, encoding="utf-8", newline="\n")
    print(f"Updated {README_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
