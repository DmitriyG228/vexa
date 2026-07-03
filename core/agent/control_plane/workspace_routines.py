"""Workspace-authored routines reconciled onto the durable runtime scheduler.

The authoring surface is a visible workspace file: ``routines/<name>.md`` with YAML
frontmatter plus optional natural-language body. A ``cron:`` routine compiles to a durable
schedule.v1 job (``routines.make_routine`` / ``compile_to_job``); an ``on: vcs.*`` routine
(routine.v1 ``kind: event`` made real) compiles to a ``vcs:subs`` subscription record instead
(``vcs_subscriptions``) — agent-api stays the ONE writer of that hash, the vcs ingress only
reads it. Enable/disable/change/remove reconcile with the same care on both paths.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

import contracts
from control_plane import routines as routines_mod
from control_plane import vcs_subscriptions as vcs_subs_mod
from shared.ports import SchedulerPort

log = logging.getLogger(__name__)

ROUTINES_DIR = "routines"
WORKSPACE_ROUTINE_SOURCE = "workspace-routine"

_FRONTMATTER = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_FRONTMATTER_BLOCK = re.compile(r"^(\s*---[^\S\n]*\n)(.*?)(\n---[^\S\n]*(?:\n|$))(.*)\Z", re.DOTALL)
_ENABLED_LINE = re.compile(
    r"(?m)^(?P<prefix>[ \t]*enabled[ \t]*:[ \t]*)(?P<value>[^#\n]*?)(?P<suffix>[ \t]*(?:#.*)?$)"
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
_DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


_ACCESS_LEVELS = ("L1", "L2", "L3")
_REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


@dataclass(frozen=True)
class RoutineFile:
    """The resolved workspace routine file."""

    name: str
    enabled: bool
    cron: str
    prompt: str
    path: Path
    # Event-binding (routine.v1 kind=event): `on:` the event name, `repo:` the owner/name the
    # subscription binds to, `access:` the authored proposal ceiling (default L1, fail-closed).
    on: str = ""
    repo: str = ""
    access: str = "L1"


@dataclass(frozen=True)
class ReconcileResult:
    """Counts from one reconcile pass, useful for logs and tests."""

    subject: str
    scanned: int = 0
    scheduled: int = 0
    kept: int = 0
    cancelled: int = 0
    skipped: int = 0
    # Event-subscription counters (the vcs:subs path) — mirror scheduled/kept/cancelled.
    subscribed: int = 0
    sub_kept: int = 0
    unsubscribed: int = 0


@dataclass(frozen=True)
class RoutineReconcilerHandle:
    """Background reconciler control handle."""

    thread: threading.Thread
    stop_event: threading.Event

    def stop(self) -> None:
        self.stop_event.set()


def routine_id_for_workspace_file(subject: str, name: str) -> str:
    """Stable routine id for ``routines/<name>.md``."""
    digest = hashlib.sha1(f"{subject}|{name}".encode()).hexdigest()[:10]
    return f"rt_{digest}"


def _safe_routine_path(workspaces_dir: str | Path, subject: str, name: str) -> Path:
    if not name or "/" in name or "\\" in name or name in {".", ".."}:
        raise ValueError("invalid routine name")
    return _safe_workspace_dir(workspaces_dir, subject) / ROUTINES_DIR / f"{name}.md"


def _split_frontmatter(text: str, *, label: str) -> tuple[dict, str]:
    m = _FRONTMATTER.match(text)
    if not m:
        return {}, text.strip()
    raw_fm, body = m.group(1), m.group(2)
    try:
        data = yaml.safe_load(raw_fm)
    except yaml.YAMLError:
        log.warning("%s: malformed YAML frontmatter; skipping routine", label)
        return {}, body.strip()
    if not isinstance(data, dict):
        return {}, body.strip()
    return data, body.strip()


def _as_bool(value: object, default: bool, *, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    log.warning("%s: enabled must be a boolean; using %s", label, default)
    return default


def _string_value(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _on_value(fm: dict) -> str:
    """The ``on:`` frontmatter key. PyYAML speaks YAML 1.1, where a bare ``on`` key parses as
    boolean ``True`` — accept both that and an explicitly quoted ``"on"`` key."""
    return _string_value(fm.get("on", fm.get(True)))


def _routine_card_from_file(path: Path, *, subject: str, job_card: Optional[dict] = None) -> dict:
    label = path.as_posix()
    try:
        text = path.read_text()
    except OSError:
        text = ""
    fm, body = _split_frontmatter(text, label=label)
    enabled = _as_bool(fm.get("enabled"), True, label=label)
    cron = _string_value(fm.get("cron"))
    on = _on_value(fm)
    prompt = _string_value(fm.get("prompt"))
    plan_parts = [prompt] if prompt else []
    if body:
        plan_parts.append(body)

    name = path.stem
    card = dict(job_card or {})
    card.update({
        "id": routine_id_for_workspace_file(subject, name),
        "owner": subject,
        "name": name,
        "routine_name": name,
        "enabled": enabled,
    })
    if on:
        card["kind"] = "event"
        card["on"] = on
        repo = _string_value(fm.get("repo"))
        if repo:
            card["repo"] = repo
        card["access"] = _string_value(fm.get("access")) or "L1"
    else:
        card.setdefault("kind", "scheduled")
    card.setdefault("lifecycle", "oneshot")
    card["cron"] = cron or card.get("cron")
    if plan_parts or "plan_summary" not in card:
        card["plan_summary"] = "\n\n".join(plan_parts)
    card["plan_kind"] = "prompt" if card.get("plan_summary") else card.get("plan_kind")
    card.setdefault("job_id", None)
    card.setdefault("next_run", None)
    if not enabled:
        card["status"] = "disabled"
    else:
        card.setdefault("status", None)
    return card


def set_routine_file_enabled(
    subject: str,
    name: str,
    *,
    enabled: bool,
    workspaces_dir: str | Path = "/workspaces",
) -> Path:
    """Rewrite only the ``enabled`` frontmatter field for ``routines/<name>.md``."""
    path = _safe_routine_path(workspaces_dir, subject, name)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)

    text = path.read_text()
    m = _FRONTMATTER_BLOCK.match(text)
    if not m:
        raise ValueError("routine file missing YAML frontmatter")

    open_marker, raw_fm, close_marker, body = m.groups()
    value = "true" if enabled else "false"
    line = _ENABLED_LINE.search(raw_fm)
    if line:
        raw_fm = (
            raw_fm[: line.start("value")]
            + value
            + raw_fm[line.end("value") :]
        )
    else:
        raw_fm = f"enabled: {value}\n{raw_fm}" if raw_fm else f"enabled: {value}"
    path.write_text(open_marker + raw_fm + close_marker + body)
    return path


def routine_cards_for_subject(
    subject: str,
    *,
    jobs: list[dict],
    workspaces_dir: str | Path = "/workspaces",
) -> list[dict]:
    """Routine cards for the API list, with workspace files as the enabled-state source."""
    job_by_rid: dict[str, dict] = {}
    legacy_cards: list[dict] = []
    for job in jobs:
        card = routines_mod.routine_card_from_job(job)
        if not card or card.get("owner") != subject:
            continue
        meta = job.get("metadata") or {}
        if meta.get("source") == WORKSPACE_ROUTINE_SOURCE:
            job_by_rid[card["id"]] = card
            continue
        card = dict(card)
        card["enabled"] = True
        card["routine_name"] = card.get("name")
        legacy_cards.append(card)

    ws = _safe_workspace_dir(workspaces_dir, subject)
    routines_dir = ws / ROUTINES_DIR
    cards: list[dict] = []
    for path in sorted(routines_dir.glob("*.md")) if routines_dir.exists() else []:
        rid = routine_id_for_workspace_file(subject, path.stem)
        job_card = job_by_rid.get(rid)
        cards.append(_routine_card_from_file(path, subject=subject, job_card=job_card))

    cards.extend(legacy_cards)
    return cards


def _cron_value(value: str, names: dict[str, int], minimum: int, maximum: int) -> Optional[int]:
    candidate = value.lower()
    if candidate in names:
        return names[candidate]
    try:
        parsed = int(candidate)
    except ValueError:
        return None
    return parsed if minimum <= parsed <= maximum else None


def _valid_cron_atom(atom: str, names: dict[str, int], minimum: int, maximum: int) -> bool:
    if atom == "*":
        return True
    if "-" in atom:
        start, end = atom.split("-", 1)
        start_v = _cron_value(start, names, minimum, maximum)
        end_v = _cron_value(end, names, minimum, maximum)
        return start_v is not None and end_v is not None and start_v <= end_v
    return _cron_value(atom, names, minimum, maximum) is not None


def _valid_cron_field(field: str, names: dict[str, int], minimum: int, maximum: int) -> bool:
    for part in field.split(","):
        if not part:
            return False
        base, sep, step = part.partition("/")
        if sep:
            try:
                if int(step) < 1:
                    return False
            except ValueError:
                return False
        if not _valid_cron_atom(base, names, minimum, maximum):
            return False
    return True


def _valid_cron(expr: str) -> bool:
    fields = expr.split()
    if len(fields) != 5:
        return False
    checks = (
        ({}, 0, 59),
        ({}, 0, 23),
        ({}, 1, 31),
        (_MONTHS, 1, 12),
        (_DAYS, 0, 7),
    )
    return all(_valid_cron_field(field, *check) for field, check in zip(fields, checks))


def load_routine_file(path: str | Path) -> Optional[RoutineFile]:
    """Parse ``routines/<name>.md`` into a ``RoutineFile``.

    Enabled files require a non-empty frontmatter ``prompt`` plus EITHER a valid 5-field ``cron``
    (a scheduled routine) OR an ``on:`` event name (an event routine — ``on: vcs.*`` also requires
    a ``repo: owner/name``); carrying both is ambiguous and skipped. Disabled files are returned
    even without cron/on/prompt so reconcile can cancel their existing jobs/subscriptions.
    Invalid enabled files return ``None`` and log the exact skipped key.
    """
    p = Path(path)
    label = p.as_posix()
    try:
        text = p.read_text()
    except OSError as exc:
        log.warning("%s: could not read routine file: %s", label, exc)
        return None

    fm, body = _split_frontmatter(text, label=label)
    enabled = _as_bool(fm.get("enabled"), True, label=label)
    if not enabled:
        return RoutineFile(name=p.stem, enabled=False, cron="", prompt="", path=p)

    on = _on_value(fm)
    cron = fm.get("cron")
    has_cron = isinstance(cron, str) and cron.strip()
    if on and has_cron:
        log.warning("%s: both `on` and `cron` set — ambiguous trigger; skipping routine", label)
        return None

    repo = ""
    access = "L1"
    if on:
        if not on.startswith("vcs."):
            log.warning("%s: unsupported event %r (only vcs.* binds today); skipping routine", label, on)
            return None
        repo = _string_value(fm.get("repo"))
        if not _REPO_RE.match(repo):
            log.warning("%s: `on: vcs.*` requires `repo: owner/name`; skipping routine", label)
            return None
        raw_access = _string_value(fm.get("access")) or "L1"
        if raw_access not in _ACCESS_LEVELS:
            log.warning("%s: invalid access %r (want L1|L2|L3); skipping routine", label, raw_access)
            return None
        access = raw_access
    else:
        if not has_cron or not _valid_cron(cron.strip()):
            log.warning("%s: invalid cron; skipping routine", label)
            return None

    prompt = fm.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        log.warning("%s: missing prompt; skipping routine", label)
        return None

    parts = [prompt.strip()]
    if body:
        parts.append(body)
    return RoutineFile(
        name=p.stem,
        enabled=True,
        cron=cron.strip() if has_cron else "",
        prompt="\n\n".join(parts),
        path=p,
        on=on,
        repo=repo,
        access=access,
    )


def _safe_workspace_dir(workspaces_dir: str | Path, subject: str) -> Path:
    root = Path(workspaces_dir).resolve()
    ws = (root / subject).resolve()
    if ws != root and root not in ws.parents:
        raise ValueError("invalid subject")
    return ws


def _job_fingerprint(job_spec: dict) -> str:
    meta = dict(job_spec.get("metadata") or {})
    meta.pop("routine_hash", None)
    payload = {
        "cron": job_spec.get("cron"),
        "request": job_spec.get("request"),
        "metadata": meta,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode()).hexdigest()


def _compile_workspace_job(routine: RoutineFile, *, subject: str, invocations_url: str) -> dict:
    routine_id = routine_id_for_workspace_file(subject, routine.name)
    authored = routines_mod.make_routine(
        subject=subject,
        name=routine.name,
        cron=routine.cron,
        prompt=routine.prompt,
        routine_id=routine_id,
    )
    job_spec = routines_mod.compile_to_job(authored, invocations_url=invocations_url)
    job_spec.pop("idempotency_key", None)
    job_spec["metadata"].update({
        "source": WORKSPACE_ROUTINE_SOURCE,
        "workspace_subject": subject,
        "workspace_path": f"{ROUTINES_DIR}/{routine.path.name}",
    })
    job_spec["metadata"]["routine_hash"] = _job_fingerprint(job_spec)
    return job_spec


def _compile_subscription(routine: RoutineFile, *, subject: str) -> dict:
    """Compile an ``on: vcs.*`` routine into its ``vcs:subs`` record. The authored routine.v1
    Routine (kind=event trigger + access ceiling) is validated at the seam (P8) before the record
    is derived, so a non-conformant authoring fails loud here — never half-lands in redis."""
    routine_id = routine_id_for_workspace_file(subject, routine.name)
    authored = {
        "id": routine_id,
        "owner": subject,
        "name": routine.name,
        "trigger": {"kind": "event", "event": routine.on, "repo": routine.repo},
        "plan": {"prompt": routine.prompt},
        "access": routine.access,
        "lifecycle": "oneshot",
        "enabled": True,
    }
    contracts.validate_routine(authored)
    return vcs_subs_mod.subscription_record(
        routine_id=routine_id,
        subject=subject,
        name=routine.name,
        event=routine.on,
        repo=routine.repo,
        access=routine.access,
        plan={"prompt": routine.prompt},
    )


def _workspace_jobs(scheduler: SchedulerPort, subject: str) -> dict[str, list[dict]]:
    jobs: dict[str, list[dict]] = defaultdict(list)
    for job in scheduler.list_jobs(limit=1000):
        meta = job.get("metadata") or {}
        if meta.get("source") != WORKSPACE_ROUTINE_SOURCE or meta.get("owner") != subject:
            continue
        rid = meta.get("routine_id")
        if isinstance(rid, str) and rid:
            jobs[rid].append(job)
    return jobs


def _cancel_job(scheduler: SchedulerPort, job: dict) -> int:
    job_id = job.get("job_id")
    if not job_id:
        return 0
    scheduler.cancel_job(job_id)
    return 1


def reconcile_workspace_routines(
    subject: str,
    *,
    scheduler: SchedulerPort,
    invocations_url: str,
    workspaces_dir: str | Path = "/workspaces",
    subscriptions: Optional[vcs_subs_mod.RedisVcsSubscriptionStore] = None,
) -> ReconcileResult:
    """Reconcile ``/workspaces/<subject>/routines/*.md`` onto schedule.v1 jobs (``cron:``) and
    ``vcs:subs`` subscription records (``on:``). One routine id spans both planes, so a routine
    edited from cron to event (or back) has its stale job/subscription removed in the same pass."""
    ws = _safe_workspace_dir(workspaces_dir, subject)
    routines_dir = ws / ROUTINES_DIR
    paths = sorted(routines_dir.glob("*.md")) if routines_dir.exists() else []

    desired: dict[str, dict] = {}
    desired_subs: dict[str, dict] = {}
    skipped = 0
    for path in paths:
        parsed = load_routine_file(path)
        if parsed is None:
            skipped += 1
            continue
        if not parsed.enabled:
            continue
        rid = routine_id_for_workspace_file(subject, path.stem)
        if parsed.on:
            if subscriptions is None:
                log.warning("%s: event routine but no subscription store wired; skipping", path.as_posix())
                skipped += 1
                continue
            desired_subs[rid] = _compile_subscription(parsed, subject=subject)
        else:
            desired[rid] = _compile_workspace_job(parsed, subject=subject, invocations_url=invocations_url)

    current = _workspace_jobs(scheduler, subject)
    scheduled = kept = cancelled = 0

    for rid, jobs in current.items():
        wanted = desired.get(rid)
        if wanted is None:
            for job in jobs:
                cancelled += _cancel_job(scheduler, job)
            continue

        wanted_hash = wanted["metadata"]["routine_hash"]
        matches = [j for j in jobs if (j.get("metadata") or {}).get("routine_hash") == wanted_hash]
        if matches:
            kept += 1
            for duplicate in matches[1:] + [j for j in jobs if j not in matches]:
                cancelled += _cancel_job(scheduler, duplicate)
            desired.pop(rid, None)
            continue

        for job in jobs:
            cancelled += _cancel_job(scheduler, job)

    for job_spec in desired.values():
        scheduler.schedule(job_spec)
        scheduled += 1

    subscribed = sub_kept = unsubscribed = 0
    if subscriptions is not None:
        # The event plane: agent-api (this reconciler) is the ONE writer of vcs:subs — the store
        # diffs this subject's records against the desired set (add/change/remove, P23).
        subscribed, sub_kept, unsubscribed = subscriptions.sync_subject(subject, desired_subs)

    return ReconcileResult(
        subject=subject,
        scanned=len(paths),
        scheduled=scheduled,
        kept=kept,
        cancelled=cancelled,
        skipped=skipped,
        subscribed=subscribed,
        sub_kept=sub_kept,
        unsubscribed=unsubscribed,
    )


def scan_workspace_subjects(workspaces_dir: str | Path = "/workspaces") -> list[str]:
    root = Path(workspaces_dir)
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("."))


def reconcile_all_workspace_routines(
    *,
    scheduler: SchedulerPort,
    invocations_url: str,
    workspaces_dir: str | Path = "/workspaces",
    subscriptions: Optional[vcs_subs_mod.RedisVcsSubscriptionStore] = None,
) -> list[ReconcileResult]:
    results: list[ReconcileResult] = []
    for subject in scan_workspace_subjects(workspaces_dir):
        results.append(
            reconcile_workspace_routines(
                subject,
                scheduler=scheduler,
                invocations_url=invocations_url,
                workspaces_dir=workspaces_dir,
                subscriptions=subscriptions,
            )
        )
    return results


def start_workspace_routine_reconciler(
    *,
    scheduler: SchedulerPort,
    invocations_url: str,
    workspaces_dir: str | Path = "/workspaces",
    interval_sec: float = 60.0,
    subscriptions: Optional[vcs_subs_mod.RedisVcsSubscriptionStore] = None,
) -> Optional[RoutineReconcilerHandle]:
    """Run one reconcile pass now, then keep scanning mounted workspaces in a daemon thread."""
    if interval_sec <= 0:
        return None

    def reconcile_once() -> None:
        try:
            results = reconcile_all_workspace_routines(
                scheduler=scheduler,
                invocations_url=invocations_url,
                workspaces_dir=workspaces_dir,
                subscriptions=subscriptions,
            )
            for result in results:
                log.info("workspace routines reconciled: %s", result)
        except Exception:
            log.exception("workspace routine reconcile failed")

    reconcile_once()
    stop_event = threading.Event()

    def loop() -> None:
        while not stop_event.wait(interval_sec):
            reconcile_once()

    thread = threading.Thread(target=loop, name="workspace-routine-reconciler", daemon=True)
    thread.start()
    return RoutineReconcilerHandle(thread=thread, stop_event=stop_event)
