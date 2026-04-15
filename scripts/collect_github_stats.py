#!/usr/bin/env python3
"""
Fetch GitHub profile statistics for README generation.

Environment:
  GITHUB_LOGIN   GitHub username (default: stevewoz1234567890). Must match the token
                 owner when using private repository access.
  GITHUB_TOKEN   PAT with read access — with `repo` scope (or fine-grained equivalent),
                 owned **private** repositories are included in language aggregates and
                 skills data. The README repository table still lists **public** repos only.
                 Also read from repo-root `.env` if present (`GITHUB_TOKEN` or `GH_TOKEN`);
                 existing environment variables take precedence.

  Repository table descriptions are taken from each repo’s **README** when possible (REST ``/repos/{owner}/{repo}/readme``), then the GitHub description field, then a short fallback.

Output:
  Writes JSON to scripts/data/github-stats.json (path relative to repo root).
"""

from __future__ import annotations

import base64
import json
import os
import re
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
    language_stats_repo_count: int
    owned_public_repo_count: int
    owned_private_repo_count: int
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
    languages_by_primary_repo: dict[str, int] = field(default_factory=dict)
    languages_by_repo_inclusion: dict[str, int] = field(default_factory=dict)
    yearly_contributions: list[YearContributions] = field(default_factory=list)
    graphql_note: str = ""
    owned_repo_names: list[str] = field(default_factory=list)
    repos: list[dict[str, Any]] = field(default_factory=list)


def _github_headers(token: str | None, accept: str = "application/vnd.github+json") -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    token: str | None = None,
    accept: str = "application/vnd.github+json",
) -> tuple[int, dict[str, str], bytes]:
    headers = _github_headers(token, accept=accept)
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


def _rest_get(url: str, token: str | None) -> tuple[int, dict[str, str], bytes]:
    """GET returning status even on HTTP errors (for rate-limit retries)."""
    req = urllib.request.Request(url, headers=_github_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            rh = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, rh, resp.read()
    except urllib.error.HTTPError as e:
        rh = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        return e.code, rh, e.read()


def _sleep_for_rate_limit(headers: dict[str, str], *, cap_s: int) -> None:
    ra = headers.get("retry-after")
    if ra and ra.isdigit():
        sleep_s = min(int(ra), cap_s)
    else:
        reset = int(headers.get("x-ratelimit-reset", "0"))
        sleep_s = max(0, reset - int(time.time())) + 1
        sleep_s = min(sleep_s, cap_s)
    if sleep_s > 0:
        print(f"github API: waiting {sleep_s}s (rate limit)...", file=sys.stderr)
        time.sleep(sleep_s)


def _json_get(url: str, token: str | None) -> Any:
    cap = 120 if not token else 60
    max_attempts = 40
    for attempt in range(max_attempts):
        status, headers, body = _rest_get(url, token)
        if status == 200:
            remaining = headers.get("x-ratelimit-remaining")
            if remaining is not None and int(remaining) < 3:
                _sleep_for_rate_limit(headers, cap_s=cap)
            return json.loads(body.decode("utf-8"))
        if status in (403, 429) and attempt + 1 < max_attempts:
            _sleep_for_rate_limit(headers, cap_s=cap)
            continue
        raise RuntimeError(f"Unexpected status {status} for {url}: {body[:500]!r}")
    raise RuntimeError(f"Exceeded retries for {url}")


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


def _paginate_repos_for_stats(login: str, token: str | None) -> list[dict[str, Any]]:
    """Repositories to include in language/skills stats.

    Without a token: public repos for `login` (REST ``/users/{login}/repos``).

    With a token: all repos the account can access via ``/user/repos`` — personal,
    organization, and collaborator — with ``visibility=all`` so **private** repos
    are included (subject to token scopes).
    """
    repos: list[dict[str, Any]] = []
    page = 1
    per_page = 100
    while True:
        if token:
            params = {
                "per_page": per_page,
                "page": page,
                "sort": "full_name",
                "visibility": "all",
                "affiliation": "owner,organization_member,collaborator",
            }
            url = f"https://api.github.com/user/repos?{urllib.parse.urlencode(params)}"
        else:
            params = {
                "per_page": per_page,
                "page": page,
                "type": "owner",
                "sort": "full_name",
            }
            url = (
                f"https://api.github.com/users/{urllib.parse.quote(login)}/repos?"
                f"{urllib.parse.urlencode(params)}"
            )
        batch = _json_get(url, token)
        if not batch:
            break
        repos.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    if token:
        seen: set[int] = set()
        out: list[dict[str, Any]] = []
        for r in repos:
            rid = r.get("id")
            if isinstance(rid, int):
                if rid in seen:
                    continue
                seen.add(rid)
            out.append(r)
        return out
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


_WEAK_DESC_TOKENS = frozenset(
    {
        "python",
        "nltk",
        "java",
        "rust",
        "go",
        "ruby",
        "php",
        "html",
        "css",
        "r",
        "c",
        "swift",
        "kotlin",
        "dart",
        "scala",
        "perl",
        "lua",
        "ts",
        "js",
        "notebook",
        "jupyter",
    }
)


def _is_weak_repo_description(text: str) -> bool:
    t = (text or "").strip()
    if not t or t in {"—", "-", "N/A", "n/a", "TODO", "todo", "TBD", "tbd"}:
        return True
    tl = t.lower()
    if len(t) < 12 and tl in _WEAK_DESC_TOKENS:
        return True
    if " " not in t and len(t) <= 12 and tl in _WEAK_DESC_TOKENS:
        return True
    return False


def _readable_repo_title(name: str) -> str:
    """Turn a repo slug into a short title (best-effort)."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    s = s.replace("_", " ").replace("-", " ")
    words = [w for w in s.split() if w]
    acronyms = {
        "ai": "AI",
        "ml": "ML",
        "ui": "UI",
        "api": "API",
        "ton": "TON",
        "sql": "SQL",
        "mtg": "MTG",
        "swe": "SWE",
        "llm": "LLM",
        "lp": "LP",
        "ds": "DS",
        "js": "JS",
        "ts": "TS",
        "gpu": "GPU",
        "nft": "NFT",
        "qft": "QFT",
        "gan": "GAN",
        "brats": "BRATS",
        "coco": "COCO",
        "mnist": "MNIST",
        "gpt4o": "GPT-4o",
        "cs": "CS",
        "sci": "SCI",
        "ode": "ODE",
        "odes": "ODEs",
        "xray": "X-ray",
        "x": "X",
    }
    out: list[str] = []
    for w in words:
        wl = w.lower()
        if wl in acronyms:
            out.append(acronyms[wl])
        elif w.isupper() and len(w) <= 5:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:] if len(w) > 1 else w.upper())
    return " ".join(out)


def _slugish_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _desc_matches_repo_name(repo_name: str, text: str) -> bool:
    """True if the text is only a restatement of the repo slug (not a real description)."""
    if not repo_name or not text:
        return False
    name_key = _slugish_key(repo_name)
    if len(name_key) < 4:
        return False
    if _slugish_key(text) == name_key:
        return True
    readable = _readable_repo_title(repo_name)
    if _slugish_key(text) == _slugish_key(readable):
        return True
    return False


def _finalize_description(s: str, max_len: int = 220) -> str:
    """Trim, fix casual README phrasing, cap length for the profile table."""
    s = (s or "").strip()
    if not s:
        return ""
    low = s.lower()
    if low.startswith("i want "):
        s = s[7:].strip()
        if s and s[0].islower():
            s = s[0].upper() + s[1:]
    return _truncate_blurb(s, max_len)


def _normalize_repo_description(name: str, raw: str | None, primary: str | None) -> str:
    raw_s = (raw or "").strip()
    if (
        not _is_weak_repo_description(raw_s)
        and not _desc_matches_repo_name(name, raw_s)
    ):
        return _finalize_description(raw_s)
    title = _readable_repo_title(name)
    lang = (primary or "").strip()
    if lang and lang != "Other":
        return _finalize_description(
            f"{title} — {lang}: utilities, experiments, and small production-minded projects."
        )
    return _finalize_description(
        f"{title} — scripts, prototypes, and reference code from hands-on work."
    )


def _fetch_repo_readme_text(owner: str, name: str, token: str | None) -> str | None:
    url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/readme"
    try:
        data = _json_get(url, token)
    except RuntimeError:
        return None
    content = data.get("content")
    if not content or (data.get("encoding") or "").lower() != "base64":
        return None
    try:
        raw = base64.standard_b64decode(content.replace("\n", "")).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, OSError):
        return None
    return raw


def _truncate_blurb(s: str, max_len: int) -> str:
    s = re.sub(r"\s+", " ", s.strip())
    if len(s) <= max_len:
        return s
    cut = s[: max_len - 1]
    sp = cut.rfind(" ")
    if sp > max_len // 2:
        cut = cut[:sp].rstrip(",;:")
    return cut + "…"


def _clean_markdown_inline(s: str) -> str:
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"__([^_]+)__", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"\[([^\]]+)\]\[[^\]]*\]", r"\1", s)
    return s.strip()


def _blurb_from_readme(
    md: str, max_len: int = 220, repo_name: str | None = None
) -> str | None:
    """Best short line for a portfolio table: prefer a real summary over a bare title."""
    text = md.replace("\r\n", "\n").strip()
    if text.startswith("\ufeff"):
        text = text[1:].lstrip()
    if not text:
        return None
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            text = text[end + 5 :].lstrip()

    lines = text.split("\n")
    heading: str | None = None
    prose_candidates: list[str] = []
    i = 0
    max_scan = min(len(lines), 120)
    while i < max_scan:
        raw = lines[i]
        line = raw.strip()
        i += 1
        if not line:
            continue
        if line.startswith("<!--"):
            continue
        if line in ("---", "***", "* * *"):
            continue
        if line.startswith("```"):
            while i < len(lines) and lines[i].strip() != "```":
                i += 1
            if i < len(lines):
                i += 1
            continue
        if line.startswith("!["):
            continue
        if line.startswith("[!["):
            continue
        if re.match(r"^<[a-zA-Z]", line):
            continue
        if re.match(r"^<p\s+align", line, re.I):
            continue

        m = re.match(r"^(#{1,6})\s+(.+)$", line)
        if m:
            t = _clean_markdown_inline(m.group(2).strip())
            if t and len(t) > 1 and heading is None:
                heading = t
            continue

        if line.startswith(("#", ">", "|")):
            continue
        if line.startswith(("- ", "* ", "+ ")):
            continue
        if re.match(r"^[-*+]\s+\[[ xX]\]\s", line):
            continue
        if re.match(r"^\d+\.\s", line):
            continue

        prose = _clean_markdown_inline(line)
        if prose and len(prose) > 12 and not prose.startswith("http"):
            prose_candidates.append(prose)

    substantial = [p for p in prose_candidates if len(p) >= 32]
    best_prose = substantial[0] if substantial else (
        prose_candidates[0] if prose_candidates else None
    )
    heading_is_slug = bool(
        repo_name and heading and _desc_matches_repo_name(repo_name, heading)
    )

    if best_prose:
        if heading_is_slug:
            return _finalize_description(best_prose, max_len)
        if not heading:
            return _finalize_description(best_prose, max_len)
        if len(best_prose) >= max(36, len(heading) + 10):
            return _finalize_description(best_prose, max_len)

    if heading and not heading_is_slug:
        return _finalize_description(heading, max_len)
    if best_prose:
        return _finalize_description(best_prose, max_len)
    if heading:
        return _finalize_description(heading, max_len)
    return None


def _compose_repo_description(
    name: str,
    github_desc: str | None,
    primary: str | None,
    readme_md: str | None,
) -> str:
    blurb: str | None = None
    if readme_md:
        blurb = _blurb_from_readme(readme_md, repo_name=name)
    if blurb and _desc_matches_repo_name(name, blurb):
        blurb = None
    if blurb:
        return blurb
    return _normalize_repo_description(name, github_desc, primary)


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

    repos = _paginate_repos_for_stats(login, token)
    profile_public_repos = int(user.get("public_repos") or 0)
    langs: dict[str, int] = defaultdict(int)
    primary_repo_counts: dict[str, int] = defaultdict(int)
    inclusion_repo_counts: dict[str, int] = defaultdict(int)
    stars = 0
    repo_rows: list[dict[str, Any]] = []
    owned_names: list[str] = []
    for r in repos:
        if not r.get("private"):
            stars += int(r.get("stargazers_count") or 0)
        owner = (r.get("owner") or {}).get("login")
        name = r.get("name")
        if not owner or not name:
            continue
        owned_names.append(name)
        lang_url = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/languages"
        try:
            blob = _json_get(lang_url, token)
        except RuntimeError:
            blob = {}
        for lang, n in blob.items():
            langs[lang] += int(n)
        for lang in blob:
            inclusion_repo_counts[lang] += 1

        primary = (r.get("language") or "").strip() or None
        if not primary and blob:
            primary = max(blob.items(), key=lambda kv: kv[1])[0]
        if not primary:
            primary = "Other"
        primary_repo_counts[primary] += 1

        if not r.get("private"):
            readme_text = _fetch_repo_readme_text(owner, name, token)
            desc = _compose_repo_description(name, r.get("description"), primary, readme_text)
            repo_rows.append(
                {
                    "name": name,
                    "html_url": r.get("html_url"),
                    "description": desc,
                }
            )

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

    repo_rows.sort(key=lambda x: (x.get("name") or "").lower())
    owned_names_sorted = sorted(set(owned_names), key=str.lower)
    primary_sorted = dict(sorted(primary_repo_counts.items(), key=lambda kv: (-kv[1], kv[0])))
    inclusion_sorted = dict(
        sorted(inclusion_repo_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    )
    owned_public_repo_count = 0
    owned_private_repo_count = 0
    for r in repos:
        if not (r.get("owner") or {}).get("login") or not r.get("name"):
            continue
        if r.get("private"):
            owned_private_repo_count += 1
        else:
            owned_public_repo_count += 1

    return CollectedStats(
        collected_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        login=login,
        name=user.get("name"),
        created_at=created_at,
        joined_display=_format_joined(created_at),
        years_on_platform_rounded=years_rounded,
        calendar_years_one_decimal=cal_one,
        public_repos=profile_public_repos,
        language_stats_repo_count=owned_public_repo_count + owned_private_repo_count,
        owned_public_repo_count=owned_public_repo_count,
        owned_private_repo_count=owned_private_repo_count,
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
        languages_by_primary_repo=primary_sorted,
        languages_by_repo_inclusion=inclusion_sorted,
        yearly_contributions=yearly,
        graphql_note=gql_note.strip(),
        owned_repo_names=owned_names_sorted,
        repos=repo_rows,
    )


def _stats_to_jsonable(s: CollectedStats) -> dict[str, Any]:
    d = asdict(s)
    d["yearly_contributions"] = [asdict(y) for y in s.yearly_contributions]
    return d


def _load_dotenv(repo_root: Path) -> None:
    """Populate os.environ from repo-root `.env` without overriding existing vars."""
    path = repo_root / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key or key in os.environ:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ[key] = val


def main() -> int:
    _load_dotenv(REPO_ROOT)
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
