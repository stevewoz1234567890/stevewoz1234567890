#!/usr/bin/env python3
"""
Render README fragments from scripts/data/github-stats.json.

Used by patch_readme.py; can also print to stdout for inspection:
  python scripts/render_readme_stats.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "scripts" / "data" / "github-stats.json"


def _load() -> dict[str, Any]:
    if not DATA_PATH.is_file():
        raise FileNotFoundError(f"Missing {DATA_PATH}; run collect_github_stats.py first.")
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def _mermaid_slice_label(name: str) -> str:
    """Mermaid pie labels are quoted; avoid raw double quotes."""
    return name.replace('"', "'")


def render_member_line(data: dict[str, Any]) -> str:
    joined = data["joined_display"]
    y = data["years_on_platform_rounded"]
    return f"**GitHub:** member since **{joined}** (~**{y} years** on the platform)."


def render_core_stats(data: dict[str, Any]) -> str:
    n_public = int(data["public_repos"])
    n_private = int(data.get("owned_private_repo_count", 0))
    n_lang = int(data.get("language_stats_repo_count") or (n_public + n_private))
    inclusion = data.get("languages_by_repo_inclusion") or {}

    lines: list[str] = []

    pr_src = data.get("pr_issue_counts_source") or "search"
    pr_issue_lbl = (
        "(opened, account lifetime)"
        if pr_src == "graphql"
        else "(opened; GitHub search on visible/indexed items)"
    )

    lines.extend(
        [
            "### Account snapshot",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| **Joined** | {data['joined_display']} (~{data['calendar_years_one_decimal']} calendar years; **~{data['years_on_platform_rounded']} years** rounded) |",
            f"| **Public repositories** | **{_fmt_int(n_public)}** |",
            f"| **Private repositories** | **{_fmt_int(n_private)}** |",
            f"| **Followers · Following** | **{_fmt_int(int(data['followers']))}** · **{_fmt_int(int(data['following']))}** |",
            f"| **Stars received** | **{_fmt_int(int(data['stars_received']))}** |",
            f"| **Pull requests · Issues** {pr_issue_lbl} | **{_fmt_int(int(data['prs_opened_lifetime']))}** · **{_fmt_int(int(data['issues_opened_lifetime']))}** |",
            "",
        ]
    )

    lines.extend(
        [
            "### Languages by code volume",
            "",
            "| Language | Repositories | % of repos |",
            "| --- | ---: | ---: |",
        ]
    )

    if not inclusion or n_lang <= 0:
        lines.extend(["| *No language data* | — | — |", ""])
    else:
        for name, count in sorted(inclusion.items(), key=lambda kv: (-kv[1], kv[0])):
            share = 100.0 * int(count) / n_lang
            lines.append(f"| {name} | {_fmt_int(int(count))} | {share:.1f}% |")
        lines.append("")

    lines.extend(
        [
            "### Language mix (visualization)",
            "",
            "```mermaid",
            "pie title Repository language share (personal, org, collaborator)",
        ]
    )
    if inclusion and n_lang > 0:
        total_inc = sum(int(v) for v in inclusion.values())
        if total_inc <= 0:
            lines.append('  "Unknown" : 100')
        else:
            for name, count in sorted(inclusion.items(), key=lambda kv: (-kv[1], kv[0])):
                pct = 100.0 * int(count) / total_inc
                label = _mermaid_slice_label(name)
                lines.append(f'  "{label}" : {round(pct, 1)}')
    else:
        lines.append('  "Unknown" : 100')
    lines.extend(["```", ""])

    return "\n".join(lines).rstrip() + "\n"


def _skill_lang_label(lang: str) -> str:
    if lang == "Jupyter Notebook":
        return "Jupyter (notebooks)"
    return lang


def render_skills_languages_line(data: dict[str, Any]) -> str:
    """Single markdown line: **Languages:** … — from languages observed across owned repos (incl. private with token)."""
    raw_langs = list((data.get("languages_by_bytes") or {}).keys())
    labels = [_skill_lang_label(x) for x in raw_langs]
    bag: dict[str, str] = {}
    for L in labels:
        key = L.lower()
        if key not in bag:
            bag[key] = L
    ordered = sorted(bag.values(), key=str.lower)

    owned_names = data.get("owned_repo_names") or [r.get("name") or "" for r in data.get("repos", [])]
    if any("tact" in n.lower() for n in owned_names if n):
        if "tact (ton)" not in {x.lower() for x in ordered}:
            ordered.append("Tact (TON)")

    def take(name: str) -> str | None:
        for x in list(ordered):
            if x.lower() == name.lower():
                ordered.remove(x)
                return x
        return None

    pieces: list[str] = []
    js = take("JavaScript")
    ts = take("TypeScript")
    if js and ts:
        pieces.append("JavaScript / TypeScript")
    elif js:
        pieces.append(js)
    elif ts:
        pieces.append(ts)

    c = take("C")
    cpp = take("C++")
    if c and cpp:
        pieces.append("C / C++")
    elif c:
        pieces.append(c)
    elif cpp:
        pieces.append(cpp)

    pieces.extend(sorted(ordered, key=str.lower))

    for extra in ("Matlab", "SQL"):
        if not any(p.strip().lower() == extra.lower() for p in pieces):
            pieces.append(extra)

    return f"**Languages:** {', '.join(pieces)}"


def render_repos_section(data: dict[str, Any]) -> str:
    collected = data["collected_at_utc"][:10]
    n = int(data["public_repos"])
    lines = [
        f"**{_fmt_int(n)}** public repositories (verified **{collected}** against the GitHub API; "
        "each row includes the GitHub description when it is informative, otherwise a short generated summary).",
        "",
        "| Repository | Description |",
        "| --- | --- |",
    ]
    for r in data["repos"]:
        name = r["name"]
        url = r["html_url"]
        desc = (r.get("description") or "").strip()
        lines.append(f"| [{name}]({url}) | {desc} |")
    lines.extend(
        [
            "",
            f"*Last synced from the GitHub API: {collected} — public repository list and descriptions (token recommended for rate limits).*",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    try:
        data = _load()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    print("--- member-line ---")
    print(render_member_line(data))
    print("--- core-stats ---")
    print(render_core_stats(data))
    print("--- skills-languages ---")
    print(render_skills_languages_line(data))
    print("--- repos ---")
    print(render_repos_section(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
