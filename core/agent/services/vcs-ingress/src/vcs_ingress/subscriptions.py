"""subscriptions.py — the READ side of the ``vcs:subs`` hash (agent-api is the only writer).

A workspace routine authored with ``on: vcs.* / repo: owner/name`` compiles (in agent-api's
workspace-routine reconciler) to a subscription record stored under ``HGET vcs:subs <owner/name>``
as a JSON array. This service never writes that hash — it only resolves, per verified delivery,
which ``(subject, plan, access)`` records want the event, and stamps one event.v1 envelope each.
A malformed field is treated as empty (logged), never a crash: an ingress must stay up.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

VCS_SUBS_HASH = "vcs:subs"


def subscriptions_for(client, repo: str, event_name: str) -> list[dict]:
    """The subscription records matching ``(repo, event_name)`` — [] when none (the caller
    persists the delivery anyway and just skips dispatch)."""
    if not repo:
        return []
    raw = client.hget(VCS_SUBS_HASH, repo)
    if not raw:
        return []
    try:
        records = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("vcs:subs[%s]: malformed JSON; treating as no subscriptions", repo)
        return []
    out: list[dict] = []
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict) or rec.get("event") != event_name:
            continue
        if not rec.get("subject") or not isinstance(rec.get("plan"), dict):
            log.warning("vcs:subs[%s]: record %r lacks subject/plan; skipping", repo, rec.get("routine_id"))
            continue
        out.append(rec)
    return out
