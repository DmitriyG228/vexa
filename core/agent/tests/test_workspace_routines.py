"""Workspace routine files — governed config compiled to durable schedule.v1 jobs."""
from __future__ import annotations

from pathlib import Path

from control_plane.workspace_routines import (
    load_routine_file,
    reconcile_workspace_routines,
    routine_cards_for_subject,
    set_routine_file_enabled,
)
from tests.test_routines import _FakeScheduler


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_load_routine_file_valid_appends_body(tmp_path):
    path = tmp_path / "routines" / "morning.md"
    _write(
        path,
        "---\n"
        "enabled: true\n"
        "cron: '0 8 * * *'\n"
        "prompt: Summarize overnight updates.\n"
        "---\n"
        "Focus on urgent follow-ups.\n",
    )

    routine = load_routine_file(path)

    assert routine is not None
    assert routine.name == "morning"
    assert routine.enabled is True
    assert routine.cron == "0 8 * * *"
    assert routine.prompt == "Summarize overnight updates.\n\nFocus on urgent follow-ups."


def test_load_routine_file_invalid_and_disabled(tmp_path, caplog):
    invalid_cron = tmp_path / "routines" / "bad-cron.md"
    missing_prompt = tmp_path / "routines" / "missing-prompt.md"
    disabled = tmp_path / "routines" / "off.md"
    _write(invalid_cron, "---\nenabled: true\ncron: not-a-cron\nprompt: do it\n---\n")
    _write(missing_prompt, "---\nenabled: true\ncron: '0 8 * * *'\n---\nbody only\n")
    _write(disabled, "---\nenabled: false\n---\n")

    assert load_routine_file(invalid_cron) is None
    assert load_routine_file(missing_prompt) is None
    off = load_routine_file(disabled)

    assert off is not None
    assert off.enabled is False
    assert "invalid cron" in caplog.text
    assert "missing prompt" in caplog.text


def test_set_routine_file_enabled_preserves_frontmatter_and_body(tmp_path):
    workspaces = tmp_path / "workspaces"
    path = workspaces / "u_jane" / "routines" / "brief.md"
    _write(
        path,
        "---\n"
        "cron: '0 9 * * *'\n"
        "enabled: true # user-visible toggle\n"
        "prompt: Do the brief.\n"
        "---\n"
        "Keep this body exactly.\n",
    )

    set_routine_file_enabled("u_jane", "brief", enabled=False, workspaces_dir=workspaces)

    assert path.read_text() == (
        "---\n"
        "cron: '0 9 * * *'\n"
        "enabled: false # user-visible toggle\n"
        "prompt: Do the brief.\n"
        "---\n"
        "Keep this body exactly.\n"
    )
    routine = load_routine_file(path)
    assert routine is not None
    assert routine.enabled is False


def test_reconcile_upserts_and_is_idempotent(tmp_path):
    workspaces = tmp_path / "workspaces"
    routine_path = workspaces / "u_jane" / "routines" / "brief.md"
    _write(
        routine_path,
        "---\nenabled: true\ncron: '0 9 * * *'\nprompt: Do the brief.\n---\n",
    )
    scheduler = _FakeScheduler()

    first = reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )
    second = reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )

    assert first.scheduled == 1
    assert second.scheduled == 0
    assert second.kept == 1
    assert len(scheduler.jobs) == 1
    job = scheduler.jobs[0]
    assert job["metadata"]["source"] == "workspace-routine"
    assert job["metadata"]["owner"] == "u_jane"
    assert job["metadata"]["name"] == "brief"
    assert job["request"]["body"]["start"]["entrypoint"]["inline"] == "Do the brief."

    _write(
        routine_path,
        "---\nenabled: true\ncron: '30 9 * * *'\nprompt: Do the updated brief.\n---\n",
    )
    updated = reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )

    assert updated.cancelled == 1
    assert updated.scheduled == 1
    assert len(scheduler.jobs) == 1
    assert scheduler.jobs[0]["cron"] == "30 9 * * *"
    assert scheduler.jobs[0]["metadata"]["name"] == "brief"


def test_reconcile_removed_or_disabled_file_cancels_job(tmp_path):
    workspaces = tmp_path / "workspaces"
    enabled = workspaces / "u_jane" / "routines" / "enabled.md"
    disabled = workspaces / "u_jane" / "routines" / "soon-disabled.md"
    body = "---\nenabled: true\ncron: '*/15 * * * *'\nprompt: Run this.\n---\n"
    _write(enabled, body)
    _write(disabled, body)
    scheduler = _FakeScheduler()

    reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )
    assert len(scheduler.jobs) == 2

    enabled.unlink()
    _write(disabled, "---\nenabled: false\n---\n")
    result = reconcile_workspace_routines(
        "u_jane",
        scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces,
    )

    assert result.cancelled == 2
    assert scheduler.jobs == []


# ── the event-binding path (`on: vcs.*` → a vcs:subs subscription, routine.v1 kind=event) ─────────

import json

import fakeredis

from control_plane.vcs_subscriptions import VCS_SUBS_HASH, RedisVcsSubscriptionStore
from control_plane.workspace_routines import routine_id_for_workspace_file

EVENT_ROUTINE = (
    "---\n"
    "enabled: true\n"
    "on: vcs.issue.opened\n"
    "repo: vexa-ai/vexa\n"
    "access: L2\n"
    "prompt: Triage the issue at the event ref.\n"
    "---\n"
    "Check kg/ for duplicates first.\n"
)


def _subs_store() -> RedisVcsSubscriptionStore:
    return RedisVcsSubscriptionStore(fakeredis.FakeRedis(decode_responses=True))


def _records(store: RedisVcsSubscriptionStore, repo: str = "vexa-ai/vexa") -> list[dict]:
    raw = store._r.hget(VCS_SUBS_HASH, repo)
    return json.loads(raw) if raw else []


def test_load_routine_file_event_binding(tmp_path):
    path = tmp_path / "routines" / "issue-triage.md"
    _write(path, EVENT_ROUTINE)

    routine = load_routine_file(path)

    assert routine is not None
    assert routine.enabled is True
    assert routine.on == "vcs.issue.opened"
    assert routine.repo == "vexa-ai/vexa"
    assert routine.access == "L2"
    assert routine.cron == ""
    assert routine.prompt == "Triage the issue at the event ref.\n\nCheck kg/ for duplicates first."


def test_load_routine_file_event_binding_invalid_variants(tmp_path, caplog):
    both = tmp_path / "routines" / "both.md"
    no_repo = tmp_path / "routines" / "no-repo.md"
    bad_access = tmp_path / "routines" / "bad-access.md"
    non_vcs = tmp_path / "routines" / "non-vcs.md"
    _write(both, "---\non: vcs.issue.opened\nrepo: a/b\ncron: '0 8 * * *'\nprompt: x\n---\n")
    _write(no_repo, "---\non: vcs.issue.opened\nprompt: x\n---\n")
    _write(bad_access, "---\non: vcs.issue.opened\nrepo: a/b\naccess: L9\nprompt: x\n---\n")
    _write(non_vcs, "---\non: email.received\nprompt: x\n---\n")

    assert load_routine_file(both) is None
    assert load_routine_file(no_repo) is None
    assert load_routine_file(bad_access) is None
    assert load_routine_file(non_vcs) is None
    assert "ambiguous trigger" in caplog.text
    assert "requires `repo: owner/name`" in caplog.text
    assert "invalid access" in caplog.text
    assert "unsupported event" in caplog.text


def test_event_routine_access_defaults_to_l1(tmp_path):
    path = tmp_path / "routines" / "watch.md"
    _write(path, "---\non: vcs.pr.opened\nrepo: vexa-ai/vexa\nprompt: Watch PRs.\n---\n")

    routine = load_routine_file(path)

    assert routine is not None and routine.access == "L1"  # fail-closed default


def test_reconcile_compiles_event_routine_to_subscription_not_job(tmp_path):
    workspaces = tmp_path / "workspaces"
    _write(workspaces / "u_jane" / "routines" / "issue-triage.md", EVENT_ROUTINE)
    scheduler = _FakeScheduler()
    store = _subs_store()

    first = reconcile_workspace_routines(
        "u_jane", scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces, subscriptions=store,
    )
    second = reconcile_workspace_routines(
        "u_jane", scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces, subscriptions=store,
    )

    assert scheduler.jobs == []                      # an event routine is NOT a cron job
    assert (first.subscribed, first.sub_kept) == (1, 0)
    assert (second.subscribed, second.sub_kept) == (0, 1)   # idempotent
    records = _records(store)
    assert len(records) == 1
    rec = records[0]
    assert rec["routine_id"] == routine_id_for_workspace_file("u_jane", "issue-triage")
    assert rec["subject"] == "u_jane"
    assert rec["event"] == "vcs.issue.opened"
    assert rec["repo"] == "vexa-ai/vexa"
    assert rec["access"] == "L2"
    assert rec["plan"]["prompt"].startswith("Triage the issue")


def test_reconcile_event_routine_change_disable_remove(tmp_path):
    workspaces = tmp_path / "workspaces"
    path = workspaces / "u_jane" / "routines" / "issue-triage.md"
    _write(path, EVENT_ROUTINE)
    scheduler = _FakeScheduler()
    store = _subs_store()
    kwargs = dict(
        scheduler=scheduler, invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces, subscriptions=store,
    )
    reconcile_workspace_routines("u_jane", **kwargs)

    # change: a new repo/prompt replaces the record in place (written, not duplicated)
    _write(path, EVENT_ROUTINE.replace("vexa-ai/vexa", "vexa-ai/vexa-core"))
    changed = reconcile_workspace_routines("u_jane", **kwargs)
    assert (changed.subscribed, changed.unsubscribed) == (1, 0)  # same routine_id → rewritten
    assert _records(store, "vexa-ai/vexa") == []                 # the old repo field is gone
    assert len(_records(store, "vexa-ai/vexa-core")) == 1

    # disable: the subscription is removed (like a cancelled job)
    set_routine_file_enabled("u_jane", "issue-triage", enabled=False, workspaces_dir=workspaces)
    disabled = reconcile_workspace_routines("u_jane", **kwargs)
    assert disabled.unsubscribed == 1
    assert store.list("u_jane") == []

    # re-enable + remove the file: enable restores, deletion unsubscribes
    set_routine_file_enabled("u_jane", "issue-triage", enabled=True, workspaces_dir=workspaces)
    assert reconcile_workspace_routines("u_jane", **kwargs).subscribed == 1
    path.unlink()
    removed = reconcile_workspace_routines("u_jane", **kwargs)
    assert removed.unsubscribed == 1
    assert store.list() == []


def test_reconcile_routine_flipped_between_cron_and_event(tmp_path):
    workspaces = tmp_path / "workspaces"
    path = workspaces / "u_jane" / "routines" / "triage.md"
    _write(path, "---\nenabled: true\ncron: '0 9 * * *'\nprompt: Do it on a clock.\n---\n")
    scheduler = _FakeScheduler()
    store = _subs_store()
    kwargs = dict(
        scheduler=scheduler, invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces, subscriptions=store,
    )
    assert reconcile_workspace_routines("u_jane", **kwargs).scheduled == 1

    # cron → event: the stale job is cancelled and the subscription lands, same routine id
    _write(path, EVENT_ROUTINE)
    flipped = reconcile_workspace_routines("u_jane", **kwargs)
    assert flipped.cancelled == 1 and flipped.subscribed == 1
    assert scheduler.jobs == [] and len(store.list("u_jane")) == 1

    # event → cron: the subscription is removed and the job returns
    _write(path, "---\nenabled: true\ncron: '0 9 * * *'\nprompt: Do it on a clock.\n---\n")
    back = reconcile_workspace_routines("u_jane", **kwargs)
    assert back.unsubscribed == 1 and back.scheduled == 1
    assert len(scheduler.jobs) == 1 and store.list() == []


def test_sync_subject_leaves_other_subjects_untouched(tmp_path):
    store = _subs_store()
    other = {
        "routine_id": "rt_other", "subject": "u_bob", "name": "bob-triage",
        "event": "vcs.issue.opened", "repo": "vexa-ai/vexa", "access": "L1",
        "plan": {"prompt": "Bob's plan."},
    }
    store._r.hset(VCS_SUBS_HASH, "vexa-ai/vexa", json.dumps([other]))

    workspaces = tmp_path / "workspaces"
    _write(workspaces / "u_jane" / "routines" / "issue-triage.md", EVENT_ROUTINE)
    scheduler = _FakeScheduler()
    reconcile_workspace_routines(
        "u_jane", scheduler=scheduler,
        invocations_url="http://agent-api:8100/invocations",
        workspaces_dir=workspaces, subscriptions=store,
    )

    records = _records(store)
    assert {r["subject"] for r in records} == {"u_bob", "u_jane"}
    assert [r for r in records if r["subject"] == "u_bob"] == [other]  # verbatim


def test_routine_card_reflects_event_binding(tmp_path):
    workspaces = tmp_path / "workspaces"
    _write(workspaces / "u_jane" / "routines" / "issue-triage.md", EVENT_ROUTINE)

    cards = routine_cards_for_subject("u_jane", jobs=[], workspaces_dir=workspaces)

    assert len(cards) == 1
    card = cards[0]
    assert card["kind"] == "event"
    assert card["on"] == "vcs.issue.opened"
    assert card["repo"] == "vexa-ai/vexa"
    assert card["access"] == "L2"
