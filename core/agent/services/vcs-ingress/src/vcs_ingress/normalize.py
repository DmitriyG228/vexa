"""normalize.py — GitHub payload → ingress.v1 Delivery record → event.v1 envelope.

The whole point of this module is what it DOES NOT carry: the returned record holds the delivery
identity (id, event, action, repo, sender), the sha256 digest of the raw body (the audit anchor),
and an envelope whose ``source.uri`` is an OPAQUE ref (``github://<owner>/<repo>/issues/<n>`` or
``.../pull/<n>``) — never title/body text. The agent unit re-fetches content via read-only tools;
no payload bytes cross the seam. ``subject`` + ``plan`` are stamped per subscription at dispatch.
"""
from __future__ import annotations

from typing import Optional

# The GitHub event types this ingress accepts (ping is the handshake, never persisted).
ALLOWED_EVENTS = frozenset({
    "ping",
    "issues",
    "issue_comment",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
})


def _issue(payload: dict) -> dict:
    return payload.get("issue") or {}


def _pr_number(payload: dict) -> Optional[int]:
    return (payload.get("pull_request") or {}).get("number")


def normalize(event: str, payload: dict) -> Optional[tuple[str, str]]:
    """Derive ``(event.v1 name, opaque source uri)`` from a GitHub ``(event, action)`` pair.

    Mapped names: vcs.issue.opened · vcs.issue.commented · vcs.pr.opened · vcs.pr.commented ·
    vcs.pr.review. A comment on an issue that IS a pull request (``issue.pull_request`` present)
    is a PR comment. Anything unmapped (issues.labeled, pull_request.synchronize, …) → None.
    """
    repo = ((payload.get("repository") or {}).get("full_name") or "").strip()
    if not repo:
        return None
    action = payload.get("action")

    if event == "issues" and action == "opened":
        n = _issue(payload).get("number")
        return ("vcs.issue.opened", f"github://{repo}/issues/{n}") if n else None
    if event == "issue_comment" and action == "created":
        issue = _issue(payload)
        n = issue.get("number")
        if not n:
            return None
        if issue.get("pull_request"):
            return ("vcs.pr.commented", f"github://{repo}/pull/{n}")
        return ("vcs.issue.commented", f"github://{repo}/issues/{n}")
    if event == "pull_request" and action == "opened":
        n = _pr_number(payload)
        return ("vcs.pr.opened", f"github://{repo}/pull/{n}") if n else None
    if event == "pull_request_review" and action == "submitted":
        n = _pr_number(payload)
        return ("vcs.pr.review", f"github://{repo}/pull/{n}") if n else None
    if event == "pull_request_review_comment" and action == "created":
        n = _pr_number(payload)
        return ("vcs.pr.commented", f"github://{repo}/pull/{n}") if n else None
    return None


def delivery_record(
    *,
    event: str,
    payload: dict,
    delivery_id: str,
    payload_digest: str,
    received_at: str,
) -> Optional[dict]:
    """Build the ingress.v1 Delivery for a verified webhook, or None when (event, action) is
    unmapped noise. The stored envelope carries the shared fields only — ``subject`` + ``plan``
    are stamped from each matching subscription at dispatch time."""
    mapped = normalize(event, payload)
    if mapped is None:
        return None
    name, uri = mapped
    record = {
        "delivery_id": delivery_id,
        "provider": "github",
        "event": event,
        "action": payload.get("action"),
        "repo": (payload.get("repository") or {}).get("full_name"),
        "sender": (payload.get("sender") or {}).get("login"),
        "received_at": received_at,
        "signature_ok": True,
        "payload_digest": payload_digest,
        "envelope": {
            "name": name,
            "occurred_at": received_at,
            "source": {"uri": uri},
        },
    }
    return {k: v for k, v in record.items() if v is not None}
