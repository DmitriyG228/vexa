"""vcs_subscriptions.py — the repo→subscription view the vcs ingress dispatches from.

A workspace routine with ``on: vcs.*`` frontmatter compiles to a SUBSCRIPTION record instead of a
schedule.v1 cron job (routine.v1 ``kind: event`` made real). The records live in ONE redis hash
``vcs:subs`` — field = the ``owner/name`` repo, value = a JSON array of records — and **agent-api is
the only writer** (P23 single-writer): this module, driven by the workspace-routine reconciler,
mutates it; the vcs-ingress service only READS it to fan a verified GitHub delivery out to event.v1
envelopes. A record carries everything the ingress needs to stamp an envelope (subject + plan) plus
the routine's authored ``access`` ceiling (proposal.v1 ``declared_access`` — rides the record so the
emission gate can enforce it downstream).
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

VCS_SUBS_HASH = "vcs:subs"


def subscription_record(
    *,
    routine_id: str,
    subject: str,
    name: str,
    event: str,
    repo: str,
    access: str = "L1",
    plan: dict,
) -> dict:
    """One vcs:subs record — the compiled form of an event-routine (pure, deterministic)."""
    return {
        "routine_id": routine_id,
        "subject": subject,
        "name": name,
        "event": event,
        "repo": repo,
        "access": access,
        "plan": dict(plan),
    }


class RedisVcsSubscriptionStore:
    """The ``vcs:subs`` hash behind a small port. agent-api is the ONE writer; the ingress reads.

    Layout: ``HGET vcs:subs <owner/name>`` → JSON array of subscription records, kept sorted by
    ``routine_id`` so re-serialization is deterministic (an unchanged desired set writes the same
    bytes and the reconcile diff stays honest).
    """

    def __init__(self, client) -> None:
        self._r = client

    # ── read ──────────────────────────────────────────────────────────────────────────────────
    def _parsed(self) -> dict[str, list[dict]]:
        parsed: dict[str, list[dict]] = {}
        for repo, raw in (self._r.hgetall(VCS_SUBS_HASH) or {}).items():
            try:
                records = json.loads(raw)
            except (TypeError, ValueError):
                log.warning("vcs:subs[%s]: malformed JSON; treating as empty", repo)
                records = []
            parsed[repo] = [r for r in records if isinstance(r, dict)]
        return parsed

    def list(self, subject: str | None = None) -> list[dict]:
        """Every subscription record (optionally one subject's), across all repos."""
        out: list[dict] = []
        for records in self._parsed().values():
            out.extend(r for r in records if subject is None or r.get("subject") == subject)
        return out

    def records_for(self, subject: str) -> dict[str, dict]:
        """The subject's current records keyed by routine_id (the reconcile diff base)."""
        return {r["routine_id"]: r for r in self.list(subject) if r.get("routine_id")}

    # ── write (agent-api only — the reconciler) ──────────────────────────────────────────────
    def sync_subject(self, subject: str, desired: dict[str, dict]) -> tuple[int, int, int]:
        """Reconcile ONE subject's records to ``desired`` (routine_id → record), leaving every
        other subject's records untouched. Returns ``(written, kept, removed)`` — written counts
        new + changed records, kept counts unchanged ones, removed counts records dropped."""
        parsed = self._parsed()
        current = {
            r["routine_id"]: r
            for records in parsed.values()
            for r in records
            if r.get("subject") == subject and r.get("routine_id")
        }

        written = sum(1 for rid, rec in desired.items() if current.get(rid) != rec)
        kept = len(desired) - written
        removed = sum(1 for rid in current if rid not in desired)

        # Rebuild the per-repo arrays: other subjects' records survive verbatim; this subject's
        # records are replaced wholesale by the desired set.
        repos: dict[str, list[dict]] = {}
        for repo, records in parsed.items():
            keepers = [r for r in records if r.get("subject") != subject]
            if keepers:
                repos[repo] = keepers
        for rec in desired.values():
            repos.setdefault(rec["repo"], []).append(rec)

        for repo in set(parsed) | set(repos):
            if repo in repos:
                records = sorted(repos[repo], key=lambda r: (str(r.get("subject")), str(r.get("routine_id"))))
                self._r.hset(VCS_SUBS_HASH, repo, json.dumps(records, sort_keys=True))
            else:
                self._r.hdel(VCS_SUBS_HASH, repo)
        return written, kept, removed
