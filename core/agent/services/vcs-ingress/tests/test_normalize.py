"""L1 contract — normalization output conforms to BOTH sealed schemas BY PATH (P4/P8).

The (event, action) → vcs.* name table, the opaque-uri shapes, and the no-payload rule: a
Delivery validates against ingress.v1 #/$defs/Delivery, and the per-subscription stamped envelope
validates against event.v1 #/$defs/Event — without importing any agent code.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from vcs_ingress import delivery_record, normalize
from conftest import fixture_payload, validate_delivery, validate_event

RECEIVED_AT = "2026-07-01T09:14:03Z"


@pytest.mark.parametrize(
    ("fixture", "event", "name", "uri"),
    [
        ("issues.opened.json", "issues", "vcs.issue.opened", "github://vexa-ai/vexa/issues/512"),
        ("issue_comment.created.json", "issue_comment", "vcs.issue.commented", "github://vexa-ai/vexa/issues/512"),
        ("pr_comment.created.json", "issue_comment", "vcs.pr.commented", "github://vexa-ai/vexa/pull/731"),
        ("pull_request.opened.json", "pull_request", "vcs.pr.opened", "github://vexa-ai/vexa/pull/731"),
        ("pull_request_review.submitted.json", "pull_request_review", "vcs.pr.review", "github://vexa-ai/vexa/pull/731"),
    ],
)
def test_event_name_and_opaque_uri(fixture, event, name, uri):
    assert normalize(event, fixture_payload(fixture)) == (name, uri)


def test_review_comment_maps_to_pr_commented():
    payload = fixture_payload("pull_request.opened.json") | {"action": "created"}
    assert normalize("pull_request_review_comment", payload) == (
        "vcs.pr.commented", "github://vexa-ai/vexa/pull/731",
    )


@pytest.mark.parametrize(
    ("event", "action"),
    [("issues", "labeled"), ("issues", "closed"), ("pull_request", "synchronize"), ("issue_comment", "deleted")],
)
def test_unmapped_action_is_none(event, action):
    payload = fixture_payload("issues.opened.json") | {"action": action}
    assert normalize(event, payload) is None


def test_missing_repo_is_none():
    payload = {"action": "opened", "issue": {"number": 1}}
    assert normalize("issues", payload) is None


def _record(fixture: str, event: str) -> dict:
    payload = fixture_payload(fixture)
    raw = json.dumps(payload).encode()
    record = delivery_record(
        event=event,
        payload=payload,
        delivery_id="72d3162e-cc78-11e3-81ab-4c9367dc0958",
        payload_digest=hashlib.sha256(raw).hexdigest(),
        received_at=RECEIVED_AT,
    )
    assert record is not None
    return record


def test_delivery_conforms_to_ingress_v1_by_path():
    record = _record("issues.opened.json", "issues")
    validate_delivery(record)
    assert record["provider"] == "github"
    assert record["signature_ok"] is True
    assert record["repo"] == "vexa-ai/vexa"
    assert record["sender"] == "mallory"


def test_stamped_envelope_conforms_to_event_v1_by_path():
    record = _record("pull_request.opened.json", "pull_request")
    envelope = dict(record["envelope"])
    envelope["subject"] = "u_jane"                       # stamped from the subscription
    envelope["plan"] = {"prompt": "triage the PR at the ref"}
    validate_event(envelope)
    assert envelope["source"]["uri"] == "github://vexa-ai/vexa/pull/731"


def test_no_payload_bytes_cross_the_seam():
    """The injection canary: attacker-authored title/body text never rides the record."""
    record = _record("issues.opened.json", "issues")
    dumped = json.dumps(record)
    assert "ignore previous instructions" not in dumped.lower()
    assert "SYSTEM OVERRIDE" not in dumped
    assert "git push" not in dumped
    # What DOES cross: the opaque ref and the digest anchor.
    assert record["envelope"]["source"]["uri"] == "github://vexa-ai/vexa/issues/512"
    assert len(record["payload_digest"]) == 64
