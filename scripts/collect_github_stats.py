#!/usr/bin/env python3
"""
Fetch public GitHub profile statistics for README generation.

Environment:
  GITHUB_LOGIN   GitHub username (default: stevewoz1234567890)
  GITHUB_TOKEN   PAT with read access — required for GraphQL (yearly contributions,
                 repositories contributed to). REST-only data works with lower limits.

Output:
  Writes JSON to scripts/data/github-stats.json (path relative to repo root).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "scripts" / "data" / "github-stats.json"
DEFAULT_LOGIN = "stevewoz1234567890"
USER_AGENT = "stevewoz1234567890-readme-stats/1.0"


@dataclass
class YearContributions:
    year: int
    calendar_total: int
    commits: int
    prs: int
    label: str = ""


@dataclass
class CollectedStats:
    collected_at_utc: str
    login: str
    name: str | None
    created_at: str
    joined_display: str
    years_on_platform_rounded: int
    calendar_years_one_decimal: str
    public_repos: int
    followers: int
    following: int
    stars_received: int
    prs_opened_lifetime: int
    issues_opened_lifetime: int
    pr_issue_counts_source: str
    repos_contributed_to_outside_owned: int | None
    total_language_bytes: int
    approx_codebase_mb: float
    languages_by_bytes: dict[str, int] = field(default_factory=dict)
    yearly_contributions: list[YearContributions] = field(default_factory=list)
    graphql_note: str = ""
    repos: list[dict[str, Any]] = field(default_factory=list)


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    token: str | None = None,
    accept: str = "application/vnd.github+json",
) -> tuple[int, dict[str, str], bytes]:
    headers = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read()
            rh = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, rh, body
    except urllib.error.HTTPError as e:
        body = e.read()
        rh = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        raise RuntimeError(f"HTTP {e.code} for {url}: {body[:500]!r}") from e


def _json_get(url: str, token: str | None) -> Any:
    status, headers, body = _request(url, token=token)
    if status != 200:
        raise RuntimeError(f"Unexpected status {status} for {url}")
    remaining = headers.get("x-ratelimit-remaining")
    if remaining is not None and int(remaining) < 5:
        reset = int(headers.get("x-ratelimit-reset", "0"))
        sleep_s = max(0, reset - int(time.time())) + 1
        if sleep_s > 0 and sleep_s < 4000:
            time.sleep(sleep_s)
    return json.loads(body.decode("utf-8"))


def _graphql(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    status, _, body = _request(
        "https://api.github.com/graphql",
        method="POST",
        data=payload,
        token=token,
    )
    if status != 200:
        raise RuntimeError(f"GraphQL HTTP {status}: {body[:800]!r}")
    parsed = json.loads(body.decode("utf-8"))
    if "errors" in parsed:
        raise RuntimeError(f"GraphQL errors: {parsed['errors']}")
    return parsed["data"]


def _paginate_repos(login: str, token: str | None) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    while True:
        q = urllib.parse.urlencode(
            {"per_page": per_page, "page": page, "type": "owner", "sort": "full_name"}
        )
        url = f"https://api.github.com/users/{urllib.parse.quote(login)}/repos?{q}"
        batch = _json_get(url, token)
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return [r for r in repos if not r.get("private")]


def _search_total_count(q: str, token: str | None) -> int:
    encoded = urllib.parse.quote(q, safe="")
    url = f"https://api.github.com/search/issues?q={encoded}&per_page=1"
    data = _json_get(url, token)
    return int(data.get("total_count", 0))


def _month_name(d: datetime) -> str:
    names = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    return names[d.month - 1]


def _format_joined(created_at: str) -> str:
    dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    return f"{dt.day} {_month_name(dt)} {dt.year}"


def _years_since_join(created_at: str) -> tuple[float, int]:
    joined = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    days = (now - joined).total_seconds() / 86400
    years = days / 365.25
    rounded = max(1, int(round(years)))
    return years, rounded


def _fetch_yearly_contributions(
    login: str, token: str, join_year: int, current_year: int
) -> list[YearContributions]:
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          totalPullRequestContributions
          contributionCalendar {
            totalContributions
          }
        }
      }
    }
    """
    out: list[YearContributions] = []
    for year in range(join_year, current_year + 1):
        from_iso = f"{year}-01-01T00:00:00Z"
        if year < current_year:
            to_iso = f"{year}-12-31T23:59:59Z"
            label = str(year)
        else:
            to_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            label = f"{year} (YTD)"
        data = _graphql(query, {"login": login, "from": from_iso, "to": to_iso}, token)
        user = data.get("user") or {}
        coll = user.get("contributionsCollection") or {}
        cal = coll.get("contributionCalendar") or {}
        out.append(
            YearContributions(
                year=year,
                calendar_total=int(cal.get("totalContributions") or 0),
                commits=int(coll.get("totalCommitContributions") or 0),
                prs=int(coll.get("totalPullRequestContributions") or 0),
                label=label,
            )
        )
    return out


def _user_pr_issue_counts_graphql(login: str, token: str) -> tuple[int, int]:
    """Counts PRs and issues authored by the user (GraphQL; respects token visibility)."""
    query = """
    query($login: String!) {
      user(login: $login) {
        pullRequests {
          totalCount
        }
        issues {
          totalCount
        }
      }
    }
    """
    data = _graphql(query, {"login": login}, token)
    u = data.get("user") or {}
    prs = int((u.get("pullRequests") or {}).get("totalCount") or 0)
    issues = int((u.get("issues") or {}).get("totalCount") or 0)
    return prs, issues


def _repos_contributed_total_count(login: str, token: str) -> int:
    query = """
    query($login: String!) {
      user(login: $login) {
        repositoriesContributedTo {
          totalCount
        }
      }
    }
    """
    data = _graphql(query, {"login": login}, token)
    user = data.get("user") or {}
    conn = user.get("repositoriesContributedTo") or {}
    return int(conn.get("totalCount") or 0)


def collect(login: str, token: str | None) -> CollectedStats:
    user_url = f"https://api.github.com/users/{urllib.parse.quote(login)}"
    user = _json_get(user_url, token)

    created_at = user["created_at"]
    joined = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    join_year = joined.year
    current_year = datetime.now(timezone.utc).year

    cal_years, years_rounded = _years_since_join(created_at)
    cal_one = f"{cal_years:.1f}"

    repos = _paginate_repos(login, token)
    langs: dict[str, int] = defaultdict(int)
    stars = 0
    for r in repos:
        stars += int(r.get("stargazers_count") or 0)
        owner = (r.get("owner") or {}).get("login")
        name = r.get("name")
        if not owner or not name:
            continue
        lang_url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/languages"
        try:
            blob = _json_get(lang_url, token)
        except RuntimeError:
            continue
        for lang, n in blob.items():
            langs[lang] += int(n)

    total_bytes = sum(langs.values())
    approx_mb = round(total_bytes / (1024 * 1024), 1) if total_bytes else 0.0

    pr_q = f"author:{login} is:pr"
    issue_q = f"author:{login} is:issue"
    pr_issue_source = "search"
    prs = 0
    issues = 0
    if token:
        try:
            prs, issues = _user_pr_issue_counts_graphql(login, token)
            pr_issue_source = "graphql"
        except Exception:
            prs = _search_total_count(pr_q, token)
            issues = _search_total_count(issue_q, token)
            pr_issue_source = "search"
    else:
        prs = _search_total_count(pr_q, token)
        issues = _search_total_count(issue_q, token)

    yearly: list[YearContributions] = []
    contributed: int | None = None
    gql_note = ""

    if token:
        try:
            yearly = _fetch_yearly_contributions(login, token, join_year, current_year)
        except Exception as e:
            gql_note = f"Yearly contributions unavailable ({e}). "
        try:
            contributed = _repos_contributed_total_count(login, token)
        except Exception as e:
            gql_note += f"Repositories-contributed count unavailable ({e})."
            contributed = None
    else:
        gql_note = (
            "Set GITHUB_TOKEN for GraphQL: yearly contribution breakdown and "
            '"Repositories contributed to" count.'
        )

    repo_rows = []
    for r in sorted(repos, key=lambda x: (x.get("name") or "").lower()):
        desc = (r.get("description") or "").strip()
        repo_rows.append(
            {
                "name": r.get("name"),
                "html_url": r.get("html_url"),
                "description": desc,
            }
        )

    return CollectedStats(
        collected_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        login=login,
        name=user.get("name"),
        created_at=created_at,
        joined_display=_format_joined(created_at),
        years_on_platform_rounded=years_rounded,
        calendar_years_one_decimal=cal_one,
        public_repos=len(repos),
        followers=int(user.get("followers") or 0),
        following=int(user.get("following") or 0),
        stars_received=stars,
        prs_opened_lifetime=prs,
        issues_opened_lifetime=issues,
        pr_issue_counts_source=pr_issue_source,
        repos_contributed_to_outside_owned=contributed,
        total_language_bytes=total_bytes,
        approx_codebase_mb=approx_mb,
        languages_by_bytes=dict(sorted(langs.items(), key=lambda kv: (-kv[1], kv[0]))),
        yearly_contributions=yearly,
        graphql_note=gql_note.strip(),
        repos=repo_rows,
    )


def _stats_to_jsonable(s: CollectedStats) -> dict[str, Any]:
    d = asdict(s)
    d["yearly_contributions"] = [asdict(y) for y in s.yearly_contributions]
    return d


def main() -> int:
    login = os.environ.get("GITHUB_LOGIN", DEFAULT_LOGIN).strip()
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    token = token.strip() if token else None

    try:
        stats = collect(login, token)
    except Exception as e:
        print(f"collect_github_stats: failed: {e}", file=sys.stderr)
        return 1

    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATA_PATH.write_text(json.dumps(_stats_to_jsonable(stats), indent=2), encoding="utf-8")
    print(f"Wrote {DATA_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
