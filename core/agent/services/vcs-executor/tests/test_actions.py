"""L3 seam — the actions, end-to-end against a LOCAL bare "GitHub" (no network).

The git flow: branch ``vexa/<proposal-id>-<slug>`` created from the declared base; the ``git am``
path preserves Author / AuthorDate / Signed-off-by VERBATIM from a format-patch mail; the Origin
provenance trailer lands exactly once (idempotent under ``--if-exists doNothing`` even when the
mail already carries it); the ``content`` path commits with the payload's title/body; the push
lands in the remote; ``open_pr`` opens into the declared base with the proposal id + approver as
the audit cross-link. The L2 REST actions hit exactly their endpoint with exactly their payload.
"""
from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from conftest import make_proposal, remote_log
from vcs_executor.actions import ExecutionError, execute

ORIGIN_SHA = "0123456789abcdef0123456789abcdef01234567"
ORIGIN_REF = f"vexa-ai/vexa-workspace@{ORIGIN_SHA}"

AUTHOR_MAIL = """From 1234567890abcdef Mon Sep 17 00:00:00 2001
From: Jane Author <jane@example.com>
Date: Tue, 30 Jun 2026 07:45:00 +0000
Subject: [PATCH] docs: agent-api listens on 8100

The quickstart still names the pre-0.12 port.

Signed-off-by: Jane Author <jane@example.com>
---
--- a/docs/q.md
+++ b/docs/q.md
@@ -1 +1 @@
-8000
+8100
"""


def _execute(proposal, token, github, bare_remote, **over):
    kwargs = dict(
        api_base="https://github.test",
        transport=httpx.MockTransport(github.handler),
        remote_url_for=lambda repo: str(bare_remote),
        clock=lambda: 1_780_000_000.0,
    )
    kwargs.update(over)
    return execute(proposal, token, **kwargs)


# ── the git-am path: Author/AuthorDate/Signed-off-by verbatim + Origin exactly once ───────────

def _am_proposal(**over) -> dict:
    p = make_proposal("open_pr")
    p["payload"]["patches"] = [{"path": "docs/q.md", "diff": AUTHOR_MAIL}]
    p["origin"]["event_ref"] = ORIGIN_REF
    p.update(over)
    return p


def test_am_path_preserves_author_metadata_verbatim(token, github, bare_remote):
    result = _execute(_am_proposal(), token, github, bare_remote)

    head = "vexa/prop_7b20de55-fix-port"                      # constructed, never payload-named
    assert remote_log(bare_remote, head, "%an <%ae>") == "Jane Author <jane@example.com>"
    assert remote_log(bare_remote, head, "%aI") == "2026-06-30T07:45:00+00:00"
    body = remote_log(bare_remote, head, "%B")
    assert "Signed-off-by: Jane Author <jane@example.com>" in body
    assert body.count(f"Origin: {ORIGIN_REF}") == 1           # the provenance trailer, once
    # the branch forked from the declared base
    base_sha = remote_log(bare_remote, "main", "%H")
    assert remote_log(bare_remote, f"{head}~1", "%H") == base_sha
    # ...and the pushed sha is what the record reports
    assert result["sha"] == remote_log(bare_remote, head, "%H")


def test_origin_trailer_is_idempotent_when_already_present(token, github, bare_remote):
    """A mail that ALREADY carries the Origin trailer (a re-emitted patch) gains no duplicate —
    ``--if-exists doNothing`` is the exactly-once guarantee."""
    mail = AUTHOR_MAIL.replace(
        "Signed-off-by: Jane Author <jane@example.com>",
        f"Signed-off-by: Jane Author <jane@example.com>\nOrigin: {ORIGIN_REF}",
    )
    p = _am_proposal()
    p["payload"]["patches"] = [{"path": "docs/q.md", "diff": mail}]
    _execute(p, token, github, bare_remote)
    body = remote_log(bare_remote, "vexa/prop_7b20de55-fix-port", "%B")
    assert body.count("Origin:") == 1


def test_no_origin_info_means_no_trailer(token, github, bare_remote):
    p = _am_proposal()
    del p["origin"]["event_ref"]                              # opaque refs only when present
    _execute(p, token, github, bare_remote)
    assert "Origin:" not in remote_log(bare_remote, "vexa/prop_7b20de55-fix-port", "%B")


def test_bare_unified_diff_is_wrapped_and_applied(token, github, bare_remote):
    """The golden's shape: a bare hunk + a separate path — file headers are synthesized, the
    executor identity authors the commit, the payload title is the subject."""
    p = make_proposal("open_pr")                              # golden-style bare diff
    p["payload"]["patches"] = [{"path": "docs/q.md", "diff": "@@ -1 +1 @@\n-8000\n+8100"}]
    _execute(p, token, github, bare_remote)
    head = "vexa/prop_7b20de55-fix-port"
    assert remote_log(bare_remote, head, "%s") == "docs: fix port"
    assert remote_log(bare_remote, head, "%an") == "vexa-executor"
    # the patch really applied
    blob = subprocess.run(["git", "-C", str(bare_remote), "show", f"{head}:docs/q.md"],
                          check=True, capture_output=True, text=True).stdout
    assert blob == "8100\n"


def test_content_patch_commits_with_payload_metadata(token, github, bare_remote):
    p = make_proposal("push_branch", pid="prop_c0ffee")
    p["payload"] = {"branch": "add-doc", "base": "main", "title": "docs: add the runbook",
                    "body": "Requested in #512.",
                    "patches": [{"path": "docs/runbook.md", "content": "# Runbook\n"}]}
    result = _execute(p, token, github, bare_remote)
    head = "vexa/prop_c0ffee-add-doc"
    assert remote_log(bare_remote, head, "%s") == "docs: add the runbook"
    assert "Requested in #512." in remote_log(bare_remote, head, "%B")
    blob = subprocess.run(["git", "-C", str(bare_remote), "show", f"{head}:docs/runbook.md"],
                          check=True, capture_output=True, text=True).stdout
    assert blob == "# Runbook\n"
    assert result["sha"] == remote_log(bare_remote, head, "%H")
    assert result["result_url"].endswith(f"/tree/{head}")


def test_push_branch_never_opens_a_pr(token, github, bare_remote):
    _execute(make_proposal("push_branch"), token, github, bare_remote)
    assert all(not r.url.path.endswith("/pulls") for r in github.requests)


def test_open_pr_carries_the_audit_cross_link(token, github, bare_remote):
    result = _execute(_am_proposal(), token, github, bare_remote)
    pr_requests = [r for r in github.requests if r.url.path.endswith("/pulls")]
    assert len(pr_requests) == 1
    body = json.loads(pr_requests[0].content)
    assert body["head"] == "vexa/prop_7b20de55-fix-port"
    assert body["base"] == "main"
    assert body["title"] == "docs: fix port"
    assert "prop_7b20de55" in body["body"] and "u_jane" in body["body"]   # id + approver
    assert result["result_url"] == "https://github.com/vexa-ai/vexa/pull/900"


def test_empty_patchset_fails_loud(token, github, bare_remote):
    p = make_proposal("push_branch")
    p["payload"]["patches"] = []
    with pytest.raises(ExecutionError, match="no commits"):
        _execute(p, token, github, bare_remote)


def test_content_path_escape_is_refused(token, github, bare_remote):
    p = make_proposal("push_branch")
    p["payload"]["patches"] = [{"path": "../evil.md", "content": "boo"}]
    with pytest.raises(ExecutionError, match="escapes"):
        _execute(p, token, github, bare_remote)


def test_git_failure_never_leaks_the_token(token, github, tmp_path):
    """A failing clone (missing remote) reports a redacted error — the authenticated URL never
    rides the exception text."""
    p = make_proposal("push_branch")
    with pytest.raises(ExecutionError) as err:
        _execute(p, token, github, tmp_path / "absent.git",
                 remote_url_for=lambda repo: f"file://{tmp_path}/absent.git")
    assert token.reveal() not in str(err.value)


# ── the L2 REST actions ───────────────────────────────────────────────────────────────────────

def test_comment_hits_the_issue_comments_endpoint(token, github, bare_remote):
    result = _execute(make_proposal("comment"), token, github, bare_remote)
    assert github.requests[-1].url.path == "/repos/vexa-ai/vexa/issues/512/comments"
    assert json.loads(github.requests[-1].content) == {"body": "Duplicate of #498 — fixed in v0.11.2."}
    assert result == {"result_url": "https://github.com/vexa-ai/vexa/issues/512#c1"}


def test_label_adds_the_payload_labels(token, github, bare_remote):
    result = _execute(make_proposal("label"), token, github, bare_remote)
    assert github.requests[-1].url.path == "/repos/vexa-ai/vexa/issues/512/labels"
    assert json.loads(github.requests[-1].content) == {"labels": ["bug", "duplicate"]}
    assert result["result_url"].endswith("/issues/512")


def test_close_posts_the_note_then_closes(token, github, bare_remote):
    _execute(make_proposal("close"), token, github, bare_remote)
    paths = [(r.method, r.url.path) for r in github.requests]
    assert paths == [
        ("POST", "/repos/vexa-ai/vexa/issues/512/comments"),
        ("PATCH", "/repos/vexa-ai/vexa/issues/512"),
    ]
    assert json.loads(github.requests[-1].content) == {"state": "closed"}


def test_open_issue_creates_with_title_body_labels(token, github, bare_remote):
    result = _execute(make_proposal("open_issue"), token, github, bare_remote)
    assert github.requests[-1].url.path == "/repos/vexa-ai/vexa/issues"
    body = json.loads(github.requests[-1].content)
    assert body == {"title": "Flaky reconnect", "body": "Seen 3× this week.", "labels": ["bug"]}
    assert result["result_url"].endswith("/issues/900")


def test_github_error_maps_to_execution_error(token, github, bare_remote):
    github.fail_with = 422
    with pytest.raises(ExecutionError, match="422"):
        _execute(make_proposal("comment"), token, github, bare_remote)
