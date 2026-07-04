"""L2 unit — the fail-loud policy gate: everything it refuses, it refuses BEFORE a credential.

Proves: the head branch is executor-constructed (``vexa/<proposal-id>-<slug>``) and validated
against exactly that shape; a protected/default-branch head, a wrong prefix, a foreign proposal
id, and head==base are all refused; the level↔action binding holds against the code table alone
(schema deliberately bypassed); non-approved / approver-less proposals never execute; unknown
actions fail; and ``--force`` structurally cannot reach git.
"""
from __future__ import annotations

import pytest

from conftest import make_proposal
from vcs_executor import actions, policy
from vcs_executor.policy import PolicyViolation


# ── head construction ─────────────────────────────────────────────────────────────────────────

def test_head_branch_is_namespaced_to_the_proposal():
    p = make_proposal("open_pr", pid="prop_abc123")
    assert policy.head_branch(p) == "vexa/prop_abc123-fix-port"     # payload.branch "fix/port"


def test_head_branch_slug_falls_back_to_title_then_action():
    p = make_proposal("push_branch", pid="prop_1")
    p["payload"].pop("branch")
    p["payload"]["title"] = "Docs: Fix THE Port!!"
    assert policy.head_branch(p) == "vexa/prop_1-docs-fix-the-port"
    p["payload"].pop("title")
    assert policy.head_branch(p) == "vexa/prop_1-push_branch" or \
        policy.head_branch(p) == "vexa/prop_1-push-branch"


def test_slugify_is_conservative():
    assert policy.slugify("Ünsafe//name --") == "nsafe-name"
    assert policy.slugify("") == "change"
    assert len(policy.slugify("x" * 100)) <= 40


# ── head refusal: default branch, configured list, wrong prefix, head==base ───────────────────

@pytest.mark.parametrize("head", ["main", "master", "0.12"])
def test_default_protected_branches_are_refused(head):
    with pytest.raises(PolicyViolation):
        policy.check_head(head, "prop_1", "main", protected=policy.DEFAULT_PROTECTED)


def test_configured_protected_list_is_refused():
    with pytest.raises(PolicyViolation):
        policy.check_head("release", "prop_1", "main", protected=("release",))


def test_wrong_prefix_is_refused():
    for head in ("fix/port", "vexa-prop_1-x", "feature/vexa/prop_1-x"):
        with pytest.raises(PolicyViolation):
            policy.check_head(head, "prop_1", "main", protected=policy.DEFAULT_PROTECTED)


def test_foreign_proposal_id_in_head_is_refused():
    with pytest.raises(PolicyViolation):
        policy.check_head("vexa/prop_2-x", "prop_1", "main", protected=policy.DEFAULT_PROTECTED)


def test_head_equal_to_base_is_refused():
    with pytest.raises(PolicyViolation):
        policy.check_head("vexa/prop_1-x", "prop_1", "vexa/prop_1-x", protected=())


def test_own_namespace_passes():
    policy.check_head("vexa/prop_1-docs-fix", "prop_1", "main", protected=policy.DEFAULT_PROTECTED)


# ── the full pre-mint gate ────────────────────────────────────────────────────────────────────

def test_unknown_action_is_refused():
    p = make_proposal("comment")
    p["action"] = "force_push"
    with pytest.raises(PolicyViolation, match="unknown proposal action"):
        policy.check(p)


def test_level_action_binding_is_recheked_in_code_alone():
    # Defense in depth: the schema is NOT consulted here — the code table refuses on its own.
    mislabeled = make_proposal("push_branch")
    mislabeled["level"] = "L2"
    with pytest.raises(PolicyViolation, match="structural"):
        policy.check(mislabeled)


@pytest.mark.parametrize("status", ["pending", "rejected", "executed", "failed", "expired"])
def test_non_approved_status_is_refused(status):
    with pytest.raises(PolicyViolation, match="not approved"):
        policy.check(make_proposal("comment", status=status))


def test_approval_without_an_approver_is_refused():
    p = make_proposal("open_pr")
    del p["decision"]
    with pytest.raises(PolicyViolation, match="decision.by"):
        policy.check(p)
    p["decision"] = {"at": "2026-06-30T09:03:00Z"}
    with pytest.raises(PolicyViolation, match="decision.by"):
        policy.check(p)


def test_l3_without_base_is_refused():
    p = make_proposal("push_branch")
    del p["payload"]["base"]
    with pytest.raises(PolicyViolation, match="payload.base"):
        policy.check(p)


def test_approved_l2_and_l3_pass():
    policy.check(make_proposal("comment"))
    policy.check(make_proposal("open_pr"))


def test_protected_from_env():
    assert policy.protected_from_env(None) == policy.DEFAULT_PROTECTED
    assert policy.protected_from_env("") == policy.DEFAULT_PROTECTED
    assert policy.protected_from_env("main, release/1.0") == ("main", "release/1.0")


# ── --force structurally never reaches git ────────────────────────────────────────────────────

@pytest.mark.parametrize("flag", ["--force", "-f", "--force-with-lease"])
def test_git_runner_refuses_force(tmp_path, flag):
    with pytest.raises(actions.ExecutionError, match="never used"):
        actions._git(tmp_path, "push", flag, "origin", "HEAD:refs/heads/vexa/prop_1-x")
