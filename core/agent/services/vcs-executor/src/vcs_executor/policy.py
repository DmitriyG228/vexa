"""policy.py — the fail-loud pre-credential gate. EVERYTHING here runs BEFORE a token exists.

The executor is the only holder of GitHub credentials, so the last line of defense is structural:
a proposal that violates policy is marked **failed** and reported — the mint function is never
called, no credential is ever created for it. Checks (defense in depth — the emission sink and the
decide surface already enforced most of these once):

- the level↔action binding (a self-contained mirror of proposal.v1's ``allOf`` — code, not config);
  an unknown action is a violation, never a fall-through;
- only ``status == "approved"`` proposals carrying a human ``decision.by`` execute — the stream is
  supposed to carry nothing else, but the stream is not the authority, the record is;
- the L3 head branch is CONSTRUCTED here as ``vexa/<proposal-id>-<slug>`` and validated against
  that exact shape, so a proposal can never name its own destination ref;
- a head that equals the base, sits on the protected list (env ``VEXA_PROTECTED_BRANCHES``,
  default ``main,master,0.12`` — the stand-in for the repo default branch, which policy cannot ask
  GitHub about before a credential exists), or misses the ``vexa/<id>-`` prefix is refused;
- ``--force`` never exists in this codebase: ``actions._git`` refuses the flag at the runner.
"""
from __future__ import annotations

import re

# The structural level ↔ action binding (mirrors proposal.v1's allOf and
# control_plane.proposals.LEVEL_ACTIONS — duplicated by design: this package is self-contained,
# it consumes the contract by schema path, never by importing agent-api code).
LEVEL_ACTIONS: dict[str, frozenset[str]] = {
    "L2": frozenset({"comment", "label", "close", "open_issue"}),
    "L3": frozenset({"push_branch", "open_pr"}),
}
_KNOWN_ACTIONS = frozenset().union(*LEVEL_ACTIONS.values())

# The default protected list — the branches an executor must never target as a push head.
# Overridden by env VEXA_PROTECTED_BRANCHES (comma-separated) at app assembly.
DEFAULT_PROTECTED = ("main", "master", "0.12")

_HEAD_RE = re.compile(r"^vexa/(?P<pid>prop_[0-9a-f]+)-[A-Za-z0-9][A-Za-z0-9._-]*$")
_SLUG_KEEP = re.compile(r"[^a-z0-9._-]+")


class PolicyViolation(Exception):
    """A proposal the executor refuses BEFORE any credential is minted — reported as failed."""


def slugify(text: str, *, max_len: int = 40) -> str:
    """A conservative branch-name slug: lowercase, ``[a-z0-9._-]`` only, trimmed."""
    slug = _SLUG_KEEP.sub("-", (text or "").lower()).strip("-.")[:max_len].strip("-.")
    return slug or "change"


def head_branch(proposal: dict) -> str:
    """The ONE place the L3 head ref comes from: ``vexa/<proposal-id>-<slug>``.

    The slug derives from the payload's requested branch (or title, or the action) — requested
    material is slug material only; the prefix is never negotiable.
    """
    payload = proposal.get("payload") or {}
    seed = payload.get("branch") or payload.get("title") or proposal.get("action") or "change"
    return f"vexa/{proposal.get('id', '')}-{slugify(seed)}"


def check_head(head: str, proposal_id: str, base: str, *, protected: tuple[str, ...]) -> None:
    """Refuse any head ref that is not this proposal's own ``vexa/<id>-*`` namespace."""
    if head in protected:
        raise PolicyViolation(f"head branch {head!r} is protected — the executor never targets it")
    if head == base:
        raise PolicyViolation(f"head branch {head!r} equals the base — refusing")
    m = _HEAD_RE.match(head)
    if not m:
        raise PolicyViolation(
            f"head branch {head!r} does not match the required shape vexa/<proposal-id>-<slug>"
        )
    if m.group("pid") != proposal_id:
        raise PolicyViolation(
            f"head branch {head!r} names a different proposal than {proposal_id!r} — refusing"
        )


def check(proposal: dict, *, protected: tuple[str, ...] = DEFAULT_PROTECTED) -> None:
    """The full pre-mint gate. Raises ``PolicyViolation``; a pass means a credential MAY be minted.

    Order matters only in that nothing here touches the network or a secret: pure record checks.
    """
    action = proposal.get("action")
    level = proposal.get("level")
    if action not in _KNOWN_ACTIONS:
        raise PolicyViolation(f"unknown proposal action {action!r} — refusing")
    if action not in LEVEL_ACTIONS.get(level or "", frozenset()):
        raise PolicyViolation(
            f"action {action!r} is not an {level!r} action — the level↔action binding is structural"
        )
    if proposal.get("status") != "approved":
        raise PolicyViolation(
            f"proposal {proposal.get('id')!r} is {proposal.get('status')!r}, not approved — "
            "the executor obeys only the human gate"
        )
    decided_by = (proposal.get("decision") or {}).get("by")
    if not decided_by:
        raise PolicyViolation(
            f"proposal {proposal.get('id')!r} carries no decision.by — an approval without an "
            "approver is not an approval"
        )
    if level == "L3":
        payload = proposal.get("payload") or {}
        base = payload.get("base")
        if not base:
            raise PolicyViolation("an L3 proposal must declare payload.base — refusing")
        check_head(head_branch(proposal), proposal.get("id", ""), base, protected=protected)


def protected_from_env(raw: str | None) -> tuple[str, ...]:
    """Parse ``VEXA_PROTECTED_BRANCHES`` (comma-separated); empty/None → the default list."""
    if not raw:
        return DEFAULT_PROTECTED
    names = tuple(x.strip() for x in raw.split(",") if x.strip())
    return names or DEFAULT_PROTECTED
