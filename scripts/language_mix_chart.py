#!/usr/bin/env python3
"""Generate a PNG bar chart for README language mix (GitHub-inspired colors)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Hex colors aligned with github/linguist (common entries + neutral fallback).
LINGUIST: dict[str, str] = {
    "Python": "#3572A5",
    "Jupyter Notebook": "#DA5B0B",
    "TypeScript": "#3178c6",
    "JavaScript": "#f1e05a",
    "Rust": "#dea584",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "SCSS": "#c6538c",
    "Ruby": "#701516",
    "Go": "#00ADD8",
    "Java": "#b07219",
    "Kotlin": "#A97BFF",
    "Swift": "#F05138",
    "C": "#555555",
    "C++": "#f34b7d",
    "C#": "#239120",
    "Shell": "#89e051",
    "Dockerfile": "#384d54",
    "PHP": "#4F5D95",
    "R": "#198CE7",
    "Move": "#4a137a",
    "PLpgSQL": "#336790",
    "AMPL": "#5c4d3a",
    "Boogie": "#c80fa0",
    "Cuda": "#3A4E3D",
    "MDX": "#1acfb8",
    "Makefile": "#427819",
    "Tree-sitter Query": "#8a6747",
    "Batchfile": "#C1F12E",
    "Vue": "#41b883",
    "Dart": "#00B4AB",
    "Haskell": "#5e5086",
    "Lua": "#000080",
    "Perl": "#39457e",
    "Scala": "#c22d40",
    "Jupyter": "#DA5B0B",
}
FALLBACK = "#8b949e"
OTHER_COLOR = "#6e7681"
BG = "#ffffff"
GRID = "#d8dee4"
TEXT = "#1f2328"
TEXT_MUTED = "#57606a"


def _bar_color(label: str) -> str:
    if label.startswith("Other"):
        return OTHER_COLOR
    return LINGUIST.get(label, FALLBACK)


def write_language_mix_png(
    path: Path,
    inclusion: dict[str, int],
    n_lang: int,
    *,
    top_n: int = 14,
) -> bool:
    """Write a horizontal bar chart. Returns False if matplotlib is unavailable."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    if not inclusion or n_lang <= 0:
        return False

    sorted_items = sorted(inclusion.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(sorted_items) > top_n:
        head = sorted_items[:top_n]
        tail_n = len(sorted_items) - top_n
        tail_count = sum(c for _, c in sorted_items[top_n:])
        rows = list(head) + [(f"Other ({tail_n} languages)", tail_count)]
    else:
        rows = list(sorted_items)

    labels = [name for name, _ in rows]
    counts = [int(c) for _, c in rows]
    colors = [_bar_color(lbl) for lbl in labels]

    # Largest bar at the top (barh reads from bottom — reverse).
    labels_r = list(reversed(labels))
    counts_r = list(reversed(counts))
    colors_r = list(reversed(colors))

    n_bars = len(labels_r)
    fig_h = max(3.4, 0.4 * n_bars + 1.55)
    fig, ax = plt.subplots(figsize=(9.2, fig_h), dpi=150, facecolor=BG)
    ax.set_facecolor(BG)

    y_pos = range(n_bars)
    bars = ax.barh(
        list(y_pos),
        counts_r,
        height=0.72,
        color=colors_r,
        edgecolor="none",
        linewidth=0,
        zorder=2,
    )

    ax.set_yticks(list(y_pos))
    ax.set_yticklabels(labels_r, fontsize=10, color=TEXT)
    ax.tick_params(axis="y", length=0, pad=8)
    ax.tick_params(axis="x", colors=TEXT_MUTED, labelsize=9)
    ax.set_xlabel("Repositories listing language", fontsize=10, color=TEXT_MUTED, labelpad=8)
    ax.set_xlim(0, max(counts_r) * 1.22)

    ax.set_title(
        "Language mix",
        fontsize=15,
        fontweight="600",
        color=TEXT,
        pad=28,
        loc="left",
    )
    ax.text(
        0.0,
        1.035,
        f"Across {n_lang:,} repositories (personal, org, collaborator)",
        transform=ax.transAxes,
        fontsize=9.5,
        color=TEXT_MUTED,
        ha="left",
        va="bottom",
    )

    ax.grid(axis="x", color=GRID, linestyle="-", linewidth=0.8, zorder=0, alpha=1.0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(GRID)
        ax.spines[spine].set_linewidth(0.8)

    xmax = max(counts_r) if counts_r else 1
    for i, (rect, val) in enumerate(zip(bars, counts_r)):
        pct = 100.0 * val / n_lang
        x = val + xmax * 0.015
        ax.text(
            x,
            rect.get_y() + rect.get_height() / 2,
            f"{val}  ({pct:.1f}%)",
            va="center",
            ha="left",
            fontsize=9,
            color=TEXT_MUTED,
            zorder=3,
        )

    plt.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=BG, edgecolor="none", bbox_inches="tight", pad_inches=0.22)
    plt.close(fig)
    return True


def write_language_mix_png_from_stats(data: dict[str, Any], path: Path) -> bool:
    n_public = int(data["public_repos"])
    n_lang = int(data.get("language_stats_repo_count") or n_public)
    inclusion = data.get("languages_by_repo_inclusion") or {}
    return write_language_mix_png(path, inclusion, n_lang)
