"""gh.py — the "operational" tier: live GitHub state, plus the ONE config-gated write.

Read path: open issues on the public tracker carrying BOTH triage labels
``status: accepted`` + ``type: bug`` (note the space — the repo's triage label shape), behind a
15-minute in-memory TTL cache (the clock is injectable, so the cache is provable offline).
Anonymous works (60 req/h GitHub allowance — the cache makes that plenty); an optional
``HELP_GITHUB_TOKEN`` lifts the limit. Every failure DEGRADES — an empty result plus a visible
note, never a crashed answer (P18).

Write path (config-gated): ``create_issue`` files the escalation issue — it exists only when the
operator set ``HELP_GITHUB_TOKEN``; without it the caller gets the fully-formed draft instead.
Everything returned here is ``provenance:"operational"`` — real, current tracker state that is
NOT yet documentation.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from .docs_index import tokenize

_API_BASE = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"

DEFAULT_REPO = "Vexa-ai/vexa"
# The triage gate: only maintainer-ACCEPTED bugs count as "known issues" (note the label spaces).
TRIAGE_LABELS = "status: accepted,type: bug"
CACHE_TTL_SEC = 15 * 60


class GitHubHelpError(RuntimeError):
    """The config-gated write failed — reported to the caller, who still gets the draft."""


class GitHubHelp:
    """The one GitHub port: TTL-cached triaged-bug reads + the config-gated escalation write."""

    def __init__(
        self,
        repo: str = DEFAULT_REPO,
        token: str = "",
        *,
        api_base: str = _API_BASE,
        transport: Optional[httpx.BaseTransport] = None,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = CACHE_TTL_SEC,
    ) -> None:
        self.repo = repo
        self._token = token or ""
        self._api_base = api_base.rstrip("/")
        self._transport = transport
        self._clock = clock
        self._ttl = ttl
        self._cache: Optional[Tuple[float, List[Dict]]] = None

    @property
    def authenticated(self) -> bool:
        return bool(self._token)

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": _ACCEPT}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def known_issues(self) -> Tuple[List[Dict], List[str]]:
        """``(issues, notes)`` — the cached triaged-bug list. Failures return ``([], [note])``
        and are never cached, so recovery is immediate."""
        now = self._clock()
        if self._cache is not None and now - self._cache[0] < self._ttl:
            return self._cache[1], []
        try:
            with httpx.Client(timeout=10, transport=self._transport) as client:
                resp = client.get(
                    f"{self._api_base}/repos/{self.repo}/issues",
                    params={"state": "open", "labels": TRIAGE_LABELS, "per_page": 100},
                    headers=self._headers(),
                )
            if resp.status_code in (403, 429):
                hint = "" if self._token else " (anonymous access — set HELP_GITHUB_TOKEN to lift it)"
                return [], [f"GitHub rate limit hit{hint}; live known-issue state is temporarily unavailable."]
            resp.raise_for_status()
            issues = [self._shape(i) for i in resp.json() if "pull_request" not in i]
        except httpx.HTTPError as exc:
            return [], [f"GitHub unreachable ({exc.__class__.__name__}); live known-issue state is temporarily unavailable."]
        self._cache = (now, issues)
        return issues, []

    @staticmethod
    def _shape(issue: Dict) -> Dict:
        return {
            "provenance": "operational",
            "title": issue.get("title", ""),
            "url": issue.get("html_url", ""),
            "labels": [l.get("name", "") for l in issue.get("labels", [])],
            "updated_at": issue.get("updated_at", ""),
        }

    def create_issue(self, title: str, body: str) -> str:
        """The config-gated write: file the escalation issue, return its html_url."""
        try:
            with httpx.Client(timeout=10, transport=self._transport) as client:
                resp = client.post(
                    f"{self._api_base}/repos/{self.repo}/issues",
                    json={"title": title, "body": body},
                    headers=self._headers(),
                )
            resp.raise_for_status()
            return resp.json().get("html_url", "")
        except httpx.HTTPError as exc:
            raise GitHubHelpError(f"GitHub issue creation failed: {exc.__class__.__name__}") from exc


def filter_issues(issues: List[Dict], query: str = "", area: str = "") -> List[Dict]:
    """Client-side narrowing over the cached fetch: ``area`` matches a label substring
    (e.g. ``bot`` → ``area: bot``); ``query`` is a lexical token overlap on title + labels."""
    out = issues
    if area:
        needle = area.strip().lower()
        out = [i for i in out if any(needle in label.lower() for label in i["labels"])]
    if query:
        q = set(tokenize(query))
        if q:
            out = [i for i in out if q & set(tokenize(i["title"] + " " + " ".join(i["labels"])))]
    return out


def issue_draft(question: str, environment_summary: str, docs_consulted: List[str]) -> Tuple[str, str]:
    """The structured escalation issue — the SAME shape whether filed directly (token mode)
    or returned as a draft for the user to file."""
    title = "[help] " + (re.sub(r"\s+", " ", question).strip()[:120] or "escalated question")
    consulted = "\n".join(f"- {d}" for d in docs_consulted) or "- (none)"
    body = (
        "## Question\n\n"
        f"{question.strip()}\n\n"
        "## Environment\n\n"
        f"{environment_summary.strip() or '(not provided)'}\n\n"
        "## Docs consulted\n\n"
        f"{consulted}\n\n"
        "---\n"
        "_filed via the vexa help companion_\n"
    )
    return title, body
