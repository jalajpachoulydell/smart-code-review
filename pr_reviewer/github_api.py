# github_api.py
from __future__ import annotations

import re
import fnmatch
from typing import List, Tuple, Dict, Any, Optional

import requests

from .tls import get_verify_path

# ---------------------------- PR URL parsing & basics ----------------------------
PR_URL_RE = re.compile(
    r"^https?://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)

def parse_pr_url(pr_url: str) -> Tuple[str, str, str, int]:
    m = PR_URL_RE.match((pr_url or "").strip())
    if not m:
        raise ValueError(
            "Invalid PR URL. Expected https://<host>/<owner>/<repo>/pull/<number>"
        )
    return m.group("host"), m.group("owner"), m.group("repo"), int(m.group("number"))


def github_api_base_from_host(host: str) -> str:
    return "https://api.github.com" if host.lower() == "github.com" else f"https://{host}/api/v3"


def _gh_headers(cfg: Dict[str, Any], accept: str) -> Dict[str, str]:
    tok = (cfg.get("github_token") or "").strip()
    if not tok:
        raise RuntimeError("Missing GitHub token in settings.")
    return {
        "Authorization": f"Bearer {tok}",
        "Accept": accept,
        "User-Agent": "pr-reviewer-ui",
        # Helps with compatibility; harmless on GH Enterprise (ignored if unknown)
        "X-GitHub-Api-Version": "2022-11-28",
    }

# ---------------------------- Pull Request content/meta ----------------------------
def fetch_pr_diff(cfg: Dict[str, Any], pr_url: str) -> str:
    """
    Raw unified diff for a PR (no filtering). Use fetch_pr_diff_filtered if
    you want generated files excluded and the skipped-file list.
    """
    host, owner, repo, number = parse_pr_url(pr_url)
    api_base = github_api_base_from_host(host)
    url = f"{api_base}/repos/{owner}/{repo}/pulls/{number}"
    r = requests.get(
        url,
        headers=_gh_headers(cfg, "application/vnd.github.v3.diff"),
        verify=get_verify_path(cfg),
        timeout=60,
    )
    if r.status_code == 401:
        raise RuntimeError(
            "GitHub 401 Unauthorized. Ensure the PAT has repo read access for this repository."
        )
    if r.status_code == 403 and "rate limit" in (r.text or "").lower():
        raise RuntimeError("GitHub rate limit/abuse detection hit (403). Try again later.")
    r.raise_for_status()
    return r.text


def fetch_pr_diff_filtered(cfg: dict, pr_url: str):
    """
    Always returns (filtered_diff_text: str, skipped_files: List[str]).
    Never returns 3+ items.
    """
    # Get raw diff
    raw = fetch_pr_diff(cfg, pr_url)  # existing function

    # Filter (helper may return 2 or 3 items in other versions)
    filtered, skipped = None, []
    try:
        res = filter_out_generated_diffs(raw, cfg)
        if isinstance(res, tuple):
            if len(res) >= 1:
                filtered = res[0]
            if len(res) >= 2 and isinstance(res[1], (list, tuple)):
                skipped = list(res[1])
        else:
            # Fall back (if someone changed the helper to return just a string)
            filtered = res
    except Exception:
        # If filtering fails, don't block reviews; fall back to raw
        filtered, skipped = raw, []

    # Final guardrails: types + shape
    filtered = filtered if isinstance(filtered, str) else (filtered or "")
    skipped = skipped if isinstance(skipped, list) else []
    return filtered, skipped


def fetch_pr_meta(cfg: Dict[str, Any], pr_url: str) -> Dict[str, Any]:
    host, owner, repo, number = parse_pr_url(pr_url)
    api_base = github_api_base_from_host(host)
    url = f"{api_base}/repos/{owner}/{repo}/pulls/{number}"
    r = requests.get(
        url,
        headers=_gh_headers(cfg, "application/vnd.github+json"),
        verify=get_verify_path(cfg),
        timeout=60,
    )
    return r.json() if r.ok else {}

# ---------------------------- PR pagination helpers ----------------------------
def fetch_all_prs(cfg: dict, host: str, owner: str, repo: str):
    token = (cfg.get("github_token") or "").strip()
    if not token:
        raise RuntimeError("Missing GitHub PAT in Configuration.")

    api_base = github_api_base_from_host(host)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-reviewer-ui",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    prs: List[Dict[str, Any]] = []
    page = 1
    per_page = 100  # max allowed by GitHub
    while True:
        url = f"{api_base}/repos/{owner}/{repo}/pulls?state=all&per_page={per_page}&page={page}"
        r = requests.get(url, headers=headers, verify=get_verify_path(cfg), timeout=60)
        if not r.ok:
            raise RuntimeError(f"Failed to fetch PRs: {r.status_code} {r.text}")
        batch = r.json() or []
        if not batch:
            break
        prs.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return prs

# ---------------------------- Generated-code filtering ----------------------------
FILE_HEADER_RE = re.compile(r"^diff --git a/(?P<a>.+) b/(?P<b>.+)$", re.MULTILINE)

def _is_generated_path(path: str, globs: List[str], file_regex: Optional[str]) -> bool:
    p = (path or "").strip()
    for g in globs or []:
        try:
            if fnmatch.fnmatch(p, g):
                return True
        except Exception:
            # ignore bad glob; keep going
            pass
    if file_regex:
        try:
            if re.search(file_regex, p, flags=re.IGNORECASE):
                return True
        except re.error:
            # ignore malformed regex
            pass
    return False


def _has_generated_markers(block_text: str, markers: List[str], head_lines: int = 120) -> bool:
    if not markers:
        return False
    head = "\n".join(block_text.splitlines()[:head_lines]).lower()
    for m in markers:
        mm = (m or "").lower()
        if mm and mm in head:
            return True
    return False


def filter_out_generated_diffs(diff_text: str, cfg: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    Returns (filtered_diff_text, skipped_files_list).
    Removes entire file blocks from a unified diff when they appear to be generated.
    Controlled by config keys:
      - skip_generated (bool, default True)
      - generated_path_globs (list[str])
      - generated_file_regex (str)
      - generated_header_markers (list[str])
    """
    if not diff_text:
        return diff_text, []
    if not cfg.get("skip_generated", True):
        return diff_text, []

    globs = cfg.get("generated_path_globs") or []
    file_rx = cfg.get("generated_file_regex") or ""
    markers = cfg.get("generated_header_markers") or []

    # Find the start of each file block by "diff --git a/... b/..."
    starts = [m.start() for m in FILE_HEADER_RE.finditer(diff_text)]
    if not starts:
        return diff_text, []
    starts.append(len(diff_text))  # sentinel
    blocks = [diff_text[starts[i]:starts[i + 1]] for i in range(len(starts) - 1)]

    kept: List[str] = []
    skipped: List[str] = []
    for blk in blocks:
        m = FILE_HEADER_RE.search(blk)
        b_path = (m.group("b") if m else "") or ""
        if b_path.startswith("b/"):
            b_path = b_path[2:]
        is_gen = _is_generated_path(b_path, globs, file_rx) or _has_generated_markers(blk, markers)
        if is_gen:
            skipped.append(b_path or "(unknown)")
        else:
            kept.append(blk)
    return "".join(kept), skipped

# ---------------------------- Symbol extraction heuristics ----------------------------
# Heuristic extraction of "class-like" symbols from ADDED lines across languages.
_CLASS_PATTERNS: List[re.Pattern] = [
    # Java / C# / TS / JS / Kotlin / Swift / PHP / C++
    re.compile(r'^\+\s*(?:public|protected|private|internal|abstract|final|static|sealed|data\s+)?\s*(?:class|interface|enum|struct)\s+([A-Za-z_]\w*)\b'),
    # C++ (with optional template)
    re.compile(r'^\+\s*(?:template<[^>]+>\s*)?(?:class|struct)\s+([A-Za-z_]\w*)\b'),
    # Go: type Name struct / interface
    re.compile(r'^\+\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b'),
    # Rust: struct / enum / trait
    re.compile(r'^\+\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)\b'),
    # Python: class Name:
    re.compile(r'^\+\s*class\s+([A-Za-z_]\w*)\b'),
]

def _extract_added_class_like_symbols(block_text: str) -> List[str]:
    names: List[str] = []
    for line in block_text.splitlines():
        # Only consider added lines in the diff
        if not line.startswith("+"):
            continue
        for rx in _CLASS_PATTERNS:
            m = rx.search(line)
            if m:
                names.append(m.group(1))
                break
    # de-dup, keep order
    seen = set()
    uniq = []
    for n in names:
        if n not in seen:
            uniq.append(n)
            seen.add(n)
    return uniq

# ---------------------------- Repo listing (FIXED) ----------------------------
def _page_json(url: str, headers: dict, verify) -> Optional[List[dict]]:
    """GETs all pages. Returns list of JSON items, None on 404."""
    items: List[dict] = []
    page, per_page = 1, 100  # GitHub’s max
    while True:
        sep = '&' if '?' in url else '?'
        paged_url = f"{url}{sep}per_page={per_page}&page={page}"
        r = requests.get(paged_url, headers=headers, verify=verify, timeout=60)
        if r.status_code == 404:
            return None
        if not r.ok:
            raise RuntimeError(f"Failed to fetch {paged_url}: {r.status_code} {r.text}")
        batch = r.json() or []
        if not batch:
            break
        items.extend(batch)
        if len(batch) < per_page:
            break
        page += 1
    return items


def fetch_all_repos_for_owner(cfg: dict, host: str, owner: str) -> list[str]:
    """
    Fetch ALL repositories for a given owner (user or org), across all pages.
    Uses /user/repos with visibility=all & affiliation to include private repos the
    token can access, then filters by owner. Falls back to /orgs/{owner}/repos and
    /users/{owner}/repos as needed.

    Notes:
      * /users/{username}/repos returns only public repos even when authenticated.
      * Do not mix `type` with `visibility`/`affiliation` on /user/repos (422 error).
    """
    token = (cfg.get("github_token") or "").strip()
    if not token:
        raise RuntimeError("Missing GitHub PAT in Configuration.")

    api_base = github_api_base_from_host(host)
    verify = get_verify_path(cfg)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "pr-reviewer-ui",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # 1) Most inclusive: /user/repos (visibility + affiliation). DO NOT add &type=...
    me_url = (
        f"{api_base}/user/repos"
        "?visibility=all"
        "&affiliation=owner,collaborator,organization_member"
        "&sort=full_name&direction=asc"
    )
    try:
        me_repos = _page_json(me_url, headers, verify) or []
    except Exception:
        me_repos = []

    filtered = [
        r for r in me_repos
        if ((r.get("owner") or {}).get("login", "").lower() == owner.lower())
    ]

    # 2) If nothing yet, try as ORG (type=all)
    if not filtered:
        org_url = f"{api_base}/orgs/{owner}/repos?type=all&sort=full_name&direction=asc"
        org_repos = _page_json(org_url, headers, verify)
        if org_repos:  # None means 404
            filtered = org_repos

    # 3) If still nothing, try as USER (public only)
    if not filtered:
        user_url = f"{api_base}/users/{owner}/repos?sort=full_name&direction=asc"
        user_repos = _page_json(user_url, headers, verify) or []
        filtered = user_repos

    names = {r.get("name", "") for r in filtered if r.get("name")}
    return sorted(names, key=str.lower)
