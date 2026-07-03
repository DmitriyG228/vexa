"""actions.py — execute exactly the approved action, nothing else, no interpretation.

L2 (annotate) is plain GitHub REST: comment on an issue/PR, add labels, close (with an optional
closing note), open an issue. L3 (mutate) is the git flow:

  shallow-clone the target repo (token-in-remote-URL, the ``GitHubVcs`` idiom — the authenticated
  URL is EPHEMERAL argv, never a persisted remote) → branch ``vexa/<proposal-id>-<slug>`` from the
  declared base → apply the payload's patches:

  * a patch carrying ``diff`` rides an mbox through ``git am`` — a full ``format-patch``-style
    mail passes VERBATIM (Author / AuthorDate / Signed-off-by preserved exactly); a bare unified
    diff is wrapped in a minimal mail authored by the executor identity;
  * a patch carrying ``content`` is written + committed with the payload's title/body as message;

  → when the proposal carries origin provenance (``origin.event_ref`` shaped ``<repo>@<sha>``),
  every new commit's message runs through ``git interpret-trailers --if-exists doNothing
  --trailer "Origin: <repo>@<sha>"`` (idempotent — a re-run or a mail already carrying the
  trailer never duplicates it) → push (NEVER ``--force`` — the git runner refuses the flag
  outright) → ``open_pr`` opens the PR into the declared base, its body carrying the proposal id
  + approver as the audit cross-link.

Every effect is injectable: the REST transport (``httpx.MockTransport``), the remote URL mapping
(local bare repos in the tests), the working directory, and the clock.
"""
from __future__ import annotations

import email.utils
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from .github_app import InstallationToken
from .policy import head_branch

_ACCEPT = "application/vnd.github+json"
_ORIGIN_REF_RE = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{7,40}$")

# The executor's own commit identity — used ONLY where the payload carries no author of its own
# (bare diffs, content patches). A format-patch mail's author always wins verbatim.
COMMIT_NAME = os.getenv("VEXA_EXECUTOR_COMMIT_NAME", "vexa-executor")
COMMIT_EMAIL = os.getenv("VEXA_EXECUTOR_COMMIT_EMAIL", "executor@vexa.ai")


class ExecutionError(RuntimeError):
    """The action could not be performed — reported to agent-api as ``{error}`` → ``failed``."""


# ── the git runner — fail-loud, --force-refusing, token-redacting ─────────────────────────────

def _git(cwd: Path, *args: str, token: Optional[str] = None, stdin: Optional[str] = None) -> str:
    """Run git in ``cwd``; return trimmed stdout. REFUSES ``--force``/``-f`` structurally, and
    redacts the token from any error text (a failed push must not leak the authenticated URL)."""
    if any(a in ("--force", "-f", "--force-with-lease") or a.startswith("--force=") for a in args):
        raise ExecutionError("git --force is never used by the executor")
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true",
           "FILTER_BRANCH_SQUELCH_WARNING": "1"}
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), env=env, input=stdin, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        if token:
            detail = detail.replace(token, "***REDACTED***")
        safe_args = " ".join(a if not (token and token in a) else "<redacted-url>" for a in args)
        raise ExecutionError(f"git {safe_args} failed: {detail}")
    return proc.stdout.strip()


def _authed_url(remote_url: str, token: InstallationToken) -> str:
    """Token-in-remote-URL (the ``GitHubVcs`` idiom) — installation tokens authenticate as
    ``x-access-token``. A local path (no scheme) passes through untouched (the offline tests)."""
    if "://" not in remote_url:
        return remote_url
    proto, rest = remote_url.split("://", 1)
    return f"{proto}://x-access-token:{token.reveal()}@{rest}"


# ── mbox construction for diff patches ────────────────────────────────────────────────────────

def _looks_like_mail(diff: str) -> bool:
    head = diff.lstrip().splitlines()[:6]
    return bool(head) and (head[0].startswith("From ") or head[0].startswith("From:"))


def _ensure_file_headers(diff: str, path: str) -> str:
    """A bare hunk (``@@ …``) gets ``--- a/<path> / +++ b/<path>`` headers so ``git am -p1`` binds
    it to the patch's declared path."""
    if any(line.startswith("--- ") for line in diff.splitlines()):
        return diff
    return f"--- a/{path}\n+++ b/{path}\n{diff}"


def _wrap_mail(diff: str, path: str, proposal: dict, clock: Callable[[], float]) -> str:
    """A minimal ``git am``-consumable mail around a bare unified diff, authored by the executor
    (the payload carries no author of its own on this path)."""
    payload = proposal.get("payload") or {}
    subject = payload.get("title") or f"vexa: apply proposal {proposal.get('id', '')} ({path})"
    date = email.utils.formatdate(clock(), usegmt=True)
    body = _ensure_file_headers(diff, path)
    if not body.endswith("\n"):
        body += "\n"
    return (
        "From vexa-executor Mon Sep 17 00:00:00 2001\n"
        f"From: {COMMIT_NAME} <{COMMIT_EMAIL}>\n"
        f"Date: {date}\n"
        f"Subject: [PATCH] {subject}\n"
        "\n"
        "---\n"
        f"{body}"
    )


def _mbox(proposal: dict, clock: Callable[[], float]) -> str:
    """One mail per ``diff`` patch, payload order. Full format-patch mails pass VERBATIM (their
    Author/AuthorDate/Signed-off-by are exactly what ``git am`` commits)."""
    mails: list[str] = []
    for patch in (proposal.get("payload") or {}).get("patches") or []:
        diff = patch.get("diff")
        if not diff:
            continue
        if _looks_like_mail(diff):
            mail = diff if diff.lstrip().startswith("From ") else \
                "From vexa-executor Mon Sep 17 00:00:00 2001\n" + diff.lstrip()
        else:
            mail = _wrap_mail(diff, patch.get("path", ""), proposal, clock)
        if not mail.endswith("\n"):
            mail += "\n"
        mails.append(mail)
    return "".join(mails)


def _origin_ref(proposal: dict) -> Optional[str]:
    """The provenance trailer value — ``origin.event_ref`` when it is shaped ``<repo>@<sha>``."""
    ref = (proposal.get("origin") or {}).get("event_ref") or ""
    return ref if _ORIGIN_REF_RE.match(ref) else None


# ── the L3 git flow ───────────────────────────────────────────────────────────────────────────

def _git_flow(
    proposal: dict,
    token: InstallationToken,
    remote_url: str,
    workdir: Path,
    clock: Callable[[], float],
) -> tuple[str, str]:
    """clone → branch → am/commit → Origin trailer → push. Returns ``(head_branch, sha)``."""
    payload = proposal.get("payload") or {}
    base = payload["base"]
    head = head_branch(proposal)
    auth_url = _authed_url(remote_url, token)
    raw = token.reveal()

    work = workdir / "repo"
    # Shallow clone of the declared base only — the executor needs the tip, never the history.
    # The authenticated URL is ephemeral argv; the persisted `origin` remote is reset to the
    # token-free URL immediately (the GitHubVcs discipline — the credential never lands on disk).
    _git(workdir, "clone", "--depth", "1", "--branch", base, auth_url, str(work), token=raw)
    _git(work, "remote", "set-url", "origin", remote_url, token=raw)
    _git(work, "config", "user.name", COMMIT_NAME)
    _git(work, "config", "user.email", COMMIT_EMAIL)
    base_sha = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", "-b", head)

    # 1) diff patches → one mbox → `git am` (Author/AuthorDate/Signed-off-by verbatim).
    mbox = _mbox(proposal, clock)
    if mbox:
        mbox_file = workdir / "patches.mbox"
        mbox_file.write_text(mbox)
        _git(work, "am", str(mbox_file))

    # 2) content patches → write + one commit with the metadata the payload specifies.
    content_paths: list[str] = []
    for patch in payload.get("patches") or []:
        if patch.get("content") is None:
            continue
        target = (work / patch["path"]).resolve()
        if work.resolve() not in target.parents:
            raise ExecutionError(f"patch path {patch['path']!r} escapes the repository — refusing")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(patch["content"])
        _git(work, "add", "--", patch["path"])
        content_paths.append(patch["path"])
    if content_paths:
        title = payload.get("title") or f"vexa: apply proposal {proposal.get('id', '')}"
        body = payload.get("body") or ""
        message = f"{title}\n\n{body}".rstrip() + "\n"
        _git(work, "commit", "-m", message)

    if _git(work, "rev-parse", "HEAD") == base_sha:
        raise ExecutionError("the proposal's patches produced no commits — nothing to push")

    # 3) provenance: every new commit's message through interpret-trailers (idempotent).
    origin_ref = _origin_ref(proposal)
    if origin_ref:
        _git(
            work, "filter-branch", "--msg-filter",
            f'git interpret-trailers --if-exists doNothing --trailer "Origin: {origin_ref}"',
            f"{base_sha}..HEAD",
        )

    # 4) push the head — plain refspec, no --force (the runner refuses the flag anyway).
    _git(work, "push", auth_url, f"HEAD:refs/heads/{head}", token=raw)
    return head, _git(work, "rev-parse", "HEAD")


# ── the REST client (L2 + the PR call) ────────────────────────────────────────────────────────

def _rest(token: InstallationToken, api_base: str, transport) -> httpx.Client:
    return httpx.Client(
        base_url=api_base.rstrip("/"),
        headers={"Authorization": f"Bearer {token.reveal()}", "Accept": _ACCEPT},
        timeout=30,
        transport=transport,
    )


def _checked(resp: httpx.Response, what: str) -> dict:
    if resp.status_code not in (200, 201):
        raise ExecutionError(f"{what} failed: GitHub answered {resp.status_code}")
    data = resp.json()
    return data if isinstance(data, dict) else {}


def execute(
    proposal: dict,
    token: InstallationToken,
    *,
    api_base: str = "https://api.github.com",
    transport: Optional[httpx.BaseTransport] = None,
    remote_url_for: Optional[Callable[[str], str]] = None,
    workdir: Optional[Path] = None,
    clock: Callable[[], float] = time.time,
) -> dict:
    """Perform the approved action; return the ``ExecutionRecord`` fields (``result_url``/``sha``).

    The caller (the consumer) has already validated the proposal against proposal.v1 AND run the
    policy gate — this function assumes an executable record and raises ``ExecutionError`` on any
    provider-side failure (mapped to ``failed`` by the report-back).
    """
    action = proposal["action"]
    target = proposal["target"]
    payload = proposal.get("payload") or {}
    repo = target["repo"]
    number = target.get("number")
    url_for = remote_url_for or (lambda r: f"https://github.com/{r}.git")

    def issue_url() -> str:
        return f"/repos/{repo}/issues/{number}"

    if action == "comment":
        with _rest(token, api_base, transport) as client:
            data = _checked(
                client.post(f"{issue_url()}/comments", json={"body": payload.get("comment", "")}),
                "comment",
            )
        return {"result_url": data.get("html_url", "")}

    if action == "label":
        with _rest(token, api_base, transport) as client:
            resp = client.post(f"{issue_url()}/labels", json={"labels": payload.get("labels") or []})
            if resp.status_code not in (200, 201):
                raise ExecutionError(f"label failed: GitHub answered {resp.status_code}")
        return {"result_url": f"https://github.com/{repo}/issues/{number}"}

    if action == "close":
        with _rest(token, api_base, transport) as client:
            if payload.get("comment"):
                _checked(
                    client.post(f"{issue_url()}/comments", json={"body": payload["comment"]}),
                    "closing comment",
                )
            data = _checked(client.patch(issue_url(), json={"state": "closed"}), "close")
        return {"result_url": data.get("html_url", "")}

    if action == "open_issue":
        body = {"title": payload.get("title", ""), "body": payload.get("body", "")}
        if payload.get("labels"):
            body["labels"] = payload["labels"]
        with _rest(token, api_base, transport) as client:
            data = _checked(client.post(f"/repos/{repo}/issues", json=body), "open_issue")
        return {"result_url": data.get("html_url", "")}

    if action in ("push_branch", "open_pr"):
        if workdir is not None:
            head, sha = _git_flow(proposal, token, url_for(repo), Path(workdir), clock)
        else:
            with tempfile.TemporaryDirectory(prefix="vcs-executor-") as tmp:
                head, sha = _git_flow(proposal, token, url_for(repo), Path(tmp), clock)
        if action == "push_branch":
            return {"sha": sha, "result_url": f"https://github.com/{repo}/tree/{head}"}
        decision = proposal.get("decision") or {}
        audit = (
            f"\n\n---\nProposal `{proposal.get('id', '')}` — approved by "
            f"`{decision.get('by', '')}` at {decision.get('at', '')}."
        )
        pr_body = {
            "title": payload.get("title") or f"vexa: proposal {proposal.get('id', '')}",
            "body": (payload.get("body") or "") + audit,
            "head": head,
            "base": payload["base"],
        }
        with _rest(token, api_base, transport) as client:
            data = _checked(client.post(f"/repos/{repo}/pulls", json=pr_body), "open_pr")
        return {"sha": sha, "result_url": data.get("html_url", "")}

    # policy.check refuses unknown actions before this point — belt and braces (P18).
    raise ExecutionError(f"unknown action {action!r}")
