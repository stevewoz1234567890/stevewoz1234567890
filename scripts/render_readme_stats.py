#!/usr/bin/env python3
"""
Render README fragments from scripts/data/github-stats.json.

Used by patch_readme.py; can also print to stdout for inspection:
  python scripts/render_readme_stats.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
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


def _human_mb(total_bytes: int) -> str:
    if total_bytes <= 0:
        return "~0 MB"
    mb = total_bytes / (1024 * 1024)
    if mb >= 10:
        return f"~{mb:.0f} MB"
    return f"~{mb:.1f} MB"


def render_member_line(data: dict[str, Any]) -> str:
    joined = data["joined_display"]
    y = data["years_on_platform_rounded"]
    return f"**GitHub:** member since **{joined}** (~**{y} years** on the platform)."


def render_core_stats(data: dict[str, Any]) -> str:
    now = datetime.fromisoformat(data["collected_at_utc"].replace("Z", "+00:00"))
    month = now.strftime("%B")
    year = now.year
    last_updated = f"{month} {year}"

    langs = data["languages_by_bytes"]
    total = int(data["total_language_bytes"])
    contrib_note = data.get("graphql_note") or ""

    lines: list[str] = [
        f"*Language totals sum the `languages` API bytes per public repo (`.ipynb` → Jupyter Notebook). **Last updated:** {last_updated}.*",
        "",
    ]
    if contrib_note:
        lines.extend([f"*{contrib_note}*", ""])

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
            f"| **Public repositories** | **{_fmt_int(int(data['public_repos']))}** |",
            f"| **Followers · Following** | **{_fmt_int(int(data['followers']))}** · **{_fmt_int(int(data['following']))}** |",
            f"| **Stars received** | **{_fmt_int(int(data['stars_received']))}** |",
            f"| **Pull requests · Issues** {pr_issue_lbl} | **{_fmt_int(int(data['prs_opened_lifetime']))}** · **{_fmt_int(int(data['issues_opened_lifetime']))}** |",
        ]
    )

    rc = data.get("repos_contributed_to_outside_owned")
    if rc is None:
        lines.append('| **Repositories contributed to** (outside owned) | *Requires `GITHUB_TOKEN` (GraphQL).* |')
    else:
        lines.append(f"| **Repositories contributed to** (outside owned) | **{_fmt_int(int(rc))}** |")

    lines.extend(
        [
            f"| **Approx. codebase size** (public repos, sum of language bytes) | **{_human_mb(total)}** |",
            "",
        ]
    )

    if pr_src == "search":
        lines.extend(
            [
                "*PR/issue totals use the REST Search API (`author:login` with `is:pr` / `is:issue`). That index is visibility-sensitive and often **undercounts** dashboard totals when you work in private repositories. With `GITHUB_TOKEN`, this script prefers GraphQL `User` totals instead.*",
                "",
            ]
        )

    lines.extend(
        [
            "### Contributions by calendar year",
            "",
            f"*Calendar = GitHub contribution graph total for 1 Jan–31 Dec (UTC window from the API). **{now.year}** is year-to-date. Commit/PR sub-totals come from GitHub's contribution breakdown for the same period.*",
            "",
        ]
    )

    yearly = data.get("yearly_contributions") or []
    if not yearly:
        lines.extend(
            [
                "*Contribution table omitted — set `GITHUB_TOKEN` and re-run `collect_github_stats.py`.*",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "| Year | Calendar contributions | Commits | PRs |",
                "| --- | ---: | ---: | ---: |",
            ]
        )
        for y in yearly:
            lines.append(
                f"| {y['label']} | {_fmt_int(int(y['calendar_total']))} | "
                f"{_fmt_int(int(y['commits']))} | {_fmt_int(int(y['prs']))} |"
            )
        lines.extend(
            [
                "",
                "\\*Where calendar totals are **0**, GitHub reported no contribution-graph activity for that year in this query.",
                "",
            ]
        )

    lines.extend(
        [
            "### Languages by code volume (public repos)",
            "",
            "*Share of total bytes across **public** repositories (GitHub `languages` API).*",
            "",
            "| Language | Share | Approx. |",
            "| --- | ---: | ---: |",
        ]
    )

    if not langs or total <= 0:
        lines.append("| *No language data* | — | — |")
    else:
        sorted_items = sorted(langs.items(), key=lambda kv: (-kv[1], kv[0]))
        if len(sorted_items) > 15:
            head = sorted_items[:14]
            tail = sorted_items[14:]
        else:
            head = sorted_items
            tail = []

        for name, b in head:
            share = 100.0 * b / total
            approx = b / (1024 * 1024)
            approx_s = f"~{approx:.1f} MB" if approx >= 0.1 else f"~{approx * 1024:.1f} KB"
            lines.append(f"| {name} | {share:.1f}% | {approx_s} |")

        if tail:
            tail_bytes = sum(b for _, b in tail)
            share = 100.0 * tail_bytes / total
            approx = tail_bytes / (1024 * 1024)
            approx_s = f"~{approx:.1f} MB" if approx >= 0.1 else f"~{approx * 1024:.1f} KB"
            names = ", ".join(n for n, _ in sorted(tail, key=lambda kv: kv[0].lower()))
            lines.append(
                f"| *Long tail ({len(tail)} languages, combined)* | {share:.1f}% | {approx_s} |"
            )
            lines.extend(["", f"*Long tail: {names}.*", ""])
        else:
            lines.append("")

    lines.extend(
        [
            "### Language mix (visualization)",
            "",
            "```mermaid",
            "pie title Public repository language share (by bytes)",
        ]
    )
    if langs and total > 0:
        pie_items = sorted(langs.items(), key=lambda kv: -kv[1])[:12]
        other = total - sum(b for _, b in pie_items)
        for name, b in pie_items:
            pct = 100.0 * b / total
            lines.append(f'  "{name}" : {round(pct, 1)}')
        if other > 0 and len(langs) > len(pie_items):
            pct = 100.0 * other / total
            lines.append(f'  "Other ({len(langs) - len(pie_items)} langs)" : {round(pct, 1)}')
    else:
        lines.append('  "Unknown" : 100')
    lines.extend(["```", ""])

    return "\n".join(lines).rstrip() + "\n"


def render_repos_section(data: dict[str, Any]) -> str:
    collected = data["collected_at_utc"][:10]
    n = int(data["public_repos"])
    lines = [
        f"**{_fmt_int(n)}** public repositories (verified **{collected}** against the GitHub API; descriptions use the repo's GitHub field when set, otherwise a short summary).",
        "",
        "| Repository | Description |",
        "| --- | --- |",
    ]
    for r in data["repos"]:
        name = r["name"]
        url = r["html_url"]
        desc = r.get("description") or ""
        cell = desc if desc else "—"
        lines.append(f"| [{name}]({url}) | {cell} |")
    lines.extend(
        [
            "",
            f"*Last synced from the GitHub API: {collected} — public repository list, descriptions, and per-repo language bytes (token recommended for rate limits).*",
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
    print("--- repos ---")
    print(render_repos_section(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
