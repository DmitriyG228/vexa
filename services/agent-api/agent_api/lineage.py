"""Enterprise-Intelligence lineage — meeting.completed → agent proposal → human-signed merge.

Pack ei-lineage (issue #24, epic #21). The missing link between meeting-api's
POST_MEETING_HOOKS delivery seam and the agent runtime:

  meeting.completed envelope (consumed, never re-transported)
    → resolve user → org EI config (org-level enable flag, default OFF)
    → claim the meeting (duplicate-delivery-safe, #330 receiving-side lesson)
    → ensure the org's seeded knowledge git repo (workspace, one per org)
    → ensure the org's agent container (runtime-api)
    → agent runs with workspace clone + transcript
    → proposal branch  meeting/<meeting-id>  appears in the org repo
       ONLY after a fully successful run (crash → no partial branch)
    → sign API (frozen contract v1): list / diff / sign(merge) / reject

Frozen sign-API contract v1 (P2-signed on issue #24 — #25 builds against it):

  GET  /api/proposals?org=<org_id>       -> 200 [{id, meeting_id, meeting_title,
                                                  created_at, branch, summary, files_changed}]
  GET  /api/proposals/{id}/diff          -> 200 {proposal_id, files: [{path,
                                                  status: added|modified, before, after, patch}]}
  POST /api/proposals/{id}/sign          -> 200 {merged: true, merge_commit}
                                            (idempotent; 409 if already merged/rejected)
  POST /api/proposals/{id}/reject {note} -> 200 {closed: true}  (note required)

State:
  - Git (the org workspace repo on a volume) is the source of truth for content:
    main branch + proposal branches. Nothing reaches main except via /sign.
  - Redis carries run/claim status (`ei:claim:*`) and proposal metadata
    (`ei:proposal:*`, `ei:org:*:proposals`).

Provider-agnostic by construction: the agent invocation is built from env
(AGENT_CLI / EI_AGENT_CMD / DEFAULT_MODEL) with an optional per-org override in
the org's EI config (`user.data.ei.agent_cli`) — the same admin-controlled seam
that already injects per-user container env. No provider is hardcoded.
"""

import asyncio
import io
import json
import logging
import os
import re
import shlex
import tarfile
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent_api import config
from agent_api.auth import require_api_key

logger = logging.getLogger("agent_api.lineage")

router = APIRouter()

# Module singletons, set once from main.startup() via configure().
_redis: Any = None
_cm: Any = None

# Per-org asyncio locks serializing git mutations on the org repo.
_org_locks: dict[str, asyncio.Lock] = {}

# Background proposal tasks (kept referenced so they are not GC'd).
_tasks: set = set()

_GIT_IDENT = ["-c", "user.name=Vexa EI", "-c", "user.email=ei-agent@vexa.ai"]
_ORG_RE = re.compile(r"[^a-z0-9_-]+")

CLAIM_RUNNING = "running"
CLAIM_PROPOSED = "proposed"
CLAIM_FAILED = "failed"

# Atomic claim: take the claim if absent OR if the previous run failed
# (safe retry). Never re-claim a running/proposed meeting — exactly one
# proposal per meeting (#330 receiving-side lesson, claim BEFORE run).
_CLAIM_LUA = """
local cur = redis.call('GET', KEYS[1])
if cur then
  local ok, prev = pcall(cjson.decode, cur)
  if ok and prev['status'] == 'failed' then
    redis.call('SET', KEYS[1], ARGV[1])
    return 1
  end
  return 0
end
redis.call('SET', KEYS[1], ARGV[1])
return 1
"""


def configure(redis, cm) -> None:
    """Wire module singletons at app startup."""
    global _redis, _cm
    _redis = redis
    _cm = cm


def _org_lock(org_id: str) -> asyncio.Lock:
    if org_id not in _org_locks:
        _org_locks[org_id] = asyncio.Lock()
    return _org_locks[org_id]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize_org_id(raw: str) -> str:
    org = _ORG_RE.sub("-", str(raw).strip().lower()).strip("-")
    if not org:
        raise ValueError(f"Unusable org id: {raw!r}")
    return org


# ── Paths ──────────────────────────────────────────────────────────────────


def org_repo_path(org_id: str) -> str:
    return os.path.join(config.EI_WORKSPACES_PATH, org_id, "repo")


def _runs_dir(org_id: str) -> str:
    return os.path.join(config.EI_WORKSPACES_PATH, org_id, ".runs")


# ── Subprocess helpers ─────────────────────────────────────────────────────


async def _run(argv: list[str], cwd: Optional[str] = None, stdin: Optional[bytes] = None,
               timeout: int = 120) -> tuple[int, bytes]:
    """Run a local subprocess, return (rc, combined output)."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "true"},
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(input=stdin), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, b"timeout"
    return proc.returncode or 0, out or b""


async def _git(args: list[str], cwd: Optional[str] = None, check: bool = True,
               timeout: int = 60) -> str:
    rc, out = await _run(["git", *_GIT_IDENT, *args], cwd=cwd, timeout=timeout)
    text = out.decode(errors="replace").strip()
    if check and rc != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} failed (rc={rc}): {text[:500]}")
    return text


async def _dexec(container: str, argv: list[str], stdin: Optional[bytes] = None,
                 timeout: int = 120) -> tuple[int, bytes]:
    """docker exec into the agent container."""
    cmd = ["docker", "exec"]
    if stdin is not None:
        cmd.append("-i")
    cmd += [container, *argv]
    return await _run(cmd, stdin=stdin, timeout=timeout)


# ── Tar transfer (agent-api FS ⇄ agent container) ──────────────────────────


def _tar_dir(path: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for entry in sorted(os.listdir(path)):
            tf.add(os.path.join(path, entry), arcname=entry)
    return buf.getvalue()


def _untar_to(data: bytes, path: str) -> None:
    os.makedirs(path, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        tf.extractall(path)  # noqa: S202 — archive produced by our own container


# ── EI org config (fresh fetch — flag flips must take effect immediately) ──


async def resolve_ei_config(user_id) -> Optional[dict]:
    """Fetch user.data.ei from admin-api. Returns None when EI is not enabled.

    Never cached: the org-level enable flag and agent settings must be
    re-read on every event (default OFF — absent key means disabled).
    """
    try:
        int_id = int(user_id)
    except (TypeError, ValueError):
        logger.info(f"EI: non-numeric user_id {user_id!r} — treating as disabled")
        return None

    headers = {"X-Admin-API-Key": config.ADMIN_API_TOKEN} if config.ADMIN_API_TOKEN else {}
    async with httpx.AsyncClient(base_url=config.ADMIN_API_URL, timeout=10,
                                 headers=headers) as client:
        resp = await client.get(f"/admin/users/{int_id}")
    if resp.status_code == 404:
        logger.info(f"EI: user {int_id} not found — treating as disabled")
        return None
    if resp.status_code != 200:
        raise RuntimeError(f"admin-api returned {resp.status_code} for user {int_id}")

    data = resp.json().get("data") or {}
    ei = data.get("ei") or {}
    if not ei.get("enabled"):
        return None
    org_raw = ei.get("org_id") or f"user-{int_id}"
    ei = dict(ei)
    ei["org_id"] = sanitize_org_id(org_raw)
    return ei


# ── Claim / run-status (Redis) ─────────────────────────────────────────────


def _claim_key(org_id: str, meeting_id) -> str:
    return f"ei:claim:{org_id}:{meeting_id}"


async def claim_meeting(org_id: str, meeting_id, event_id: str) -> bool:
    """Atomically claim a meeting for an agent run. True = we own the run."""
    value = json.dumps({
        "status": CLAIM_RUNNING,
        "event_id": event_id,
        "updated_at": _now(),
    })
    res = await _redis.eval(_CLAIM_LUA, 1, _claim_key(org_id, meeting_id), value)
    return bool(res)


async def set_claim(org_id: str, meeting_id, status: str, **extra) -> None:
    raw = await _redis.get(_claim_key(org_id, meeting_id))
    record = json.loads(raw) if raw else {}
    record.update(status=status, updated_at=_now(), **extra)
    await _redis.set(_claim_key(org_id, meeting_id), json.dumps(record))


async def get_claim(org_id: str, meeting_id) -> Optional[dict]:
    raw = await _redis.get(_claim_key(org_id, meeting_id))
    return json.loads(raw) if raw else None


# ── Proposal store (Redis metadata; git is the content truth) ──────────────


async def _save_proposal(record: dict) -> None:
    pid = record["id"]
    await _redis.set(f"ei:proposal:{pid}", json.dumps(record))
    await _redis.sadd(f"ei:org:{record['org_id']}:proposals", pid)


async def get_proposal(pid: str) -> Optional[dict]:
    raw = await _redis.get(f"ei:proposal:{pid}")
    return json.loads(raw) if raw else None


async def list_org_proposals(org_id: str) -> list[dict]:
    pids = await _redis.smembers(f"ei:org:{org_id}:proposals")
    records = []
    for pid in pids:
        rec = await get_proposal(pid)
        if rec:
            records.append(rec)
    records.sort(key=lambda r: r.get("created_at", ""))
    return records


# ── Workspace (seeded git repo per org) ────────────────────────────────────


def _git_remote_cfg_path(org_id: str) -> str:
    return os.path.join(config.EI_WORKSPACES_PATH, org_id, ".git-remote.json")


def load_git_remote(org_id: str) -> Optional[dict]:
    """Org-keyed git remote config (remote_url, branch, token). Token on disk
    here is a demo seam — production resolves it from the secrets store."""
    path = _git_remote_cfg_path(sanitize_org_id(org_id))
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def save_git_remote(org_id: str, cfg: dict) -> None:
    path = _git_remote_cfg_path(sanitize_org_id(org_id))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(cfg, f)
    os.chmod(path, 0o600)


def clear_git_remote(org_id: str) -> None:
    path = _git_remote_cfg_path(sanitize_org_id(org_id))
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _auth_remote_url(remote_url: str, token: Optional[str]) -> str:
    """Embed a token into an https remote URL for fetch/push (demo seam)."""
    if not token or not remote_url.startswith("https://"):
        return remote_url
    rest = remote_url[len("https://"):]
    # token as the username works for GitHub/GitLab/Gitea PATs
    return f"https://{token}@{rest}"


def _branch_of(cfg: dict) -> str:
    return re.sub(r"[^a-zA-Z0-9._/-]", "", cfg.get("branch") or "main") or "main"


async def push_remote(org_id: str) -> Optional[str]:
    """Push the org repo's branch to its configured remote. No-op if unconfigured."""
    cfg = load_git_remote(org_id)
    if not cfg or not cfg.get("remote_url"):
        return None
    repo = org_repo_path(org_id)
    if not os.path.isdir(os.path.join(repo, ".git")):
        return None
    branch = _branch_of(cfg)
    auth = _auth_remote_url(cfg["remote_url"], cfg.get("token"))
    try:
        await _git(["push", auth, f"HEAD:{branch}"], cwd=repo, timeout=120)
        return await _git(["rev-parse", "HEAD"], cwd=repo)
    except RuntimeError as e:
        logger.error(f"EI: push failed for org {org_id}: {str(e)[:300]}")
        return None


async def ensure_workspace(org_id: str) -> str:
    """Ensure the org's knowledge repo exists; seed it on first use.

    Seed template ships in the agent-api image (services/agent-api/workspace-seed,
    synthetic content only). Returns the repo path.
    """
    repo = org_repo_path(org_id)
    cfg = load_git_remote(org_id)
    async with _org_lock(org_id):
        # Remote-connected workspace: remote is the source of truth.
        if cfg and cfg.get("remote_url"):
            branch = _branch_of(cfg)
            auth = _auth_remote_url(cfg["remote_url"], cfg.get("token"))
            if os.path.isdir(os.path.join(repo, ".git")):
                # Pull latest (single-writer: hard-sync to remote is safe).
                try:
                    await _git(["fetch", auth, branch], cwd=repo, timeout=120)
                    await _git(["reset", "--hard", "FETCH_HEAD"], cwd=repo)
                except RuntimeError as e:
                    logger.warning(f"EI: fetch failed org {org_id}: {str(e)[:200]}")
                return repo
            # First use of a connected workspace.
            os.makedirs(os.path.dirname(repo) or repo, exist_ok=True)
            if cfg.get("mode") == "provision":
                # init from seed, then push to the (empty) remote.
                os.makedirs(repo, exist_ok=True)
                seed = config.EI_WORKSPACE_SEED_PATH
                await _run(["cp", "-a", f"{seed}/.", repo])
                await _git(["init", "-b", branch], cwd=repo)
                await _git(["add", "-A"], cwd=repo)
                await _git(["commit", "-m", f"seed: workspace for {org_id}"], cwd=repo)
                try:
                    await _git(["push", auth, f"HEAD:{branch}"], cwd=repo, timeout=120)
                except RuntimeError as e:
                    logger.error(f"EI: provision push failed org {org_id}: {str(e)[:200]}")
            else:
                # BYOR: clone the existing remote.
                rc, out = await _run(["git", *_GIT_IDENT, "clone", "-b", branch,
                                      auth, repo], timeout=180)
                if rc != 0:
                    raise RuntimeError(f"clone failed: {out.decode(errors='replace')[:300]}")
            # normalize local branch name to `main` for internal ops if needed
            cur = await _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
            if cur != "main":
                await _git(["branch", "-M", "main"], cwd=repo)
            return repo

        if os.path.isdir(os.path.join(repo, ".git")):
            return repo
        seed = config.EI_WORKSPACE_SEED_PATH
        if not os.path.isdir(seed):
            raise RuntimeError(f"workspace seed missing at {seed}")
        os.makedirs(repo, exist_ok=True)
        rc, out = await _run(["cp", "-a", f"{seed}/.", repo])
        if rc != 0:
            raise RuntimeError(f"seed copy failed: {out.decode(errors='replace')[:300]}")
        await _git(["init", "-b", "main"], cwd=repo)
        await _git(["add", "-A"], cwd=repo)
        await _git(["commit", "-m", f"seed: org workspace for {org_id}"], cwd=repo)
        logger.info(f"EI: seeded workspace for org {org_id} at {repo}")
        return repo


async def _branch_exists(repo: str, branch: str) -> bool:
    rc, _ = await _run(["git", "-C", repo, "rev-parse", "--verify", "--quiet",
                        f"refs/heads/{branch}"])
    return rc == 0


# ── Transcript fetch ───────────────────────────────────────────────────────


async def fetch_transcript(meeting_id) -> str:
    """Best-effort transcript fetch from the collector's internal endpoint."""
    url = f"{config.TRANSCRIPTION_COLLECTOR_URL}/internal/transcripts/{meeting_id}"
    headers = ({"X-Internal-Secret": config.INTERNAL_API_SECRET}
               if config.INTERNAL_API_SECRET else {})
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.warning(f"EI: transcript fetch for meeting {meeting_id} -> "
                           f"{resp.status_code}; continuing with empty transcript")
            return ""
        segments = resp.json() or []
    except Exception as e:
        logger.warning(f"EI: transcript fetch failed for meeting {meeting_id}: {e}")
        return ""
    lines = []
    for seg in segments:
        speaker = (seg.get("speaker") or "Unknown").strip()
        text = (seg.get("text") or "").strip()
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n".join(lines)


# ── Agent invocation ───────────────────────────────────────────────────────


def _agent_command(ei: dict) -> str:
    """Build the agent CLI invocation prefix. The prompt is appended as ONE
    shell-quoted argument. Resolution order (most specific wins, no provider
    hardcoded): org EI config `agent_cli` → env EI_AGENT_CMD → env AGENT_CLI
    (+ AGENT_ALLOWED_TOOLS / model from org config or DEFAULT_MODEL)."""
    if ei.get("agent_cli"):
        return str(ei["agent_cli"])
    if config.EI_AGENT_CMD:
        return config.EI_AGENT_CMD
    parts = [config.AGENT_CLI, "--allowedTools", shlex.quote(config.AGENT_ALLOWED_TOOLS)]
    model = ei.get("model") or config.DEFAULT_MODEL
    if model:
        parts += ["--model", shlex.quote(str(model))]
    parts.append("-p")
    return " ".join(parts)


def _build_prompt(meeting_id, meeting: dict) -> str:
    title = meeting.get("title") or f"{meeting.get('platform', 'meeting')} {meeting_id}"
    return f"""A meeting has completed and its transcript must be folded into this org's knowledge graph.

Meeting: {title} (id {meeting_id})
Transcript: ../input/transcript.md  (read-only input — do NOT copy it into the repo)
Metadata:   ../input/meeting.json

You are in the org knowledge repository working tree. First read AGENT.md at the
repository root and follow its conventions exactly (dated confidence-scored appends,
single-write-path regions, [[wikilinks]]). Then:

1. Create the meeting artifact graph/kg/entities/meetings/{meeting_id}-<slug>.md
   from templates/meeting._template.md.
2. For every person/company that matters in the meeting, update (or create from the
   matching template) their entity file under graph/kg/entities/, appending a dated,
   confidence-scored entry inside the routine-updates region only.
3. Link artifacts with [[wikilinks]].

Modify files only inside this repository working tree. Do not run git commands —
branching and commits happen outside the agent.
"""


async def _touch_loop(container: str) -> None:
    """Keep runtime-api's idle reaper away while a long agent run is active."""
    while True:
        await asyncio.sleep(60)
        try:
            await _cm._touch(container)
        except Exception:
            pass


# ── The proposal pipeline ──────────────────────────────────────────────────


async def run_proposal(org_id: str, meeting_id, ei: dict, envelope: dict) -> None:
    """Background pipeline: workspace → container → agent → proposal branch.

    Crash-safety invariant: the proposal branch is fetched into the org repo
    only after the agent run succeeded AND produced a schema-conforming commit
    in an isolated working clone. Any failure before that leaves the org repo
    untouched; the claim flips to `failed` (visible status, safe retry).
    """
    meeting = (envelope.get("data") or {}).get("meeting") or {}
    branch = f"meeting/{meeting_id}"
    run_id = f"{meeting_id}-{int(time.time())}"
    run_dir = os.path.join(_runs_dir(org_id), run_id)
    container_run_root = f"/tmp/ei-run-{org_id}-{meeting_id}"
    container = None
    touch_task = None
    try:
        repo = await ensure_workspace(org_id)

        # Idempotency belt-and-braces: proposal branch already in the repo.
        if await _branch_exists(repo, branch):
            logger.info(f"EI: branch {branch} already exists for org {org_id} — skipping run")
            await set_claim(org_id, meeting_id, CLAIM_PROPOSED)
            return

        transcript = await fetch_transcript(meeting_id)

        # Working clone — the agent never touches the org repo directly.
        os.makedirs(run_dir, exist_ok=True)
        async with _org_lock(org_id):
            await _git(["clone", "--branch", "main", "--single-branch", repo,
                        os.path.join(run_dir, "repo")])
        input_dir = os.path.join(run_dir, "input")
        os.makedirs(input_dir, exist_ok=True)
        with open(os.path.join(input_dir, "transcript.md"), "w") as f:
            f.write(transcript or "(no transcript available)\n")
        with open(os.path.join(input_dir, "meeting.json"), "w") as f:
            json.dump(meeting, f, indent=2)

        # Org agent container via runtime-api (one per org — tenant isolation).
        org_env = ei.get("env") or {}
        container = await _cm.ensure_container(
            f"ei-{org_id}", session_id="ei",
            config={"env": org_env} if org_env else {},
        )

        # Ship the working clone + inputs into the container.
        payload = _tar_dir(run_dir)
        rc, out = await _dexec(
            container,
            ["sh", "-c",
             f"rm -rf {container_run_root} && mkdir -p {container_run_root} "
             f"&& tar -xz -C {container_run_root}"],
            stdin=payload, timeout=120,
        )
        if rc != 0:
            raise RuntimeError(f"workspace transfer failed: {out.decode(errors='replace')[:300]}")

        # Run the agent.
        prompt = _build_prompt(meeting_id, meeting)
        agent_cmd = _agent_command(ei)
        shell_cmd = (
            f"cd {container_run_root}/repo && "
            f"EI_MEETING_ID={shlex.quote(str(meeting_id))} EI_ORG_ID={shlex.quote(org_id)} "
            f"{agent_cmd} {shlex.quote(prompt)}"
        )
        logger.info(f"EI: agent run starting for org={org_id} meeting={meeting_id} "
                    f"container={container}")
        touch_task = asyncio.create_task(_touch_loop(container))
        rc, out = await _dexec(container, ["bash", "-c", shell_cmd],
                               timeout=config.EI_AGENT_TIMEOUT)
        touch_task.cancel()
        touch_task = None
        tail = out.decode(errors="replace")[-2000:]
        if rc != 0:
            raise RuntimeError(f"agent run failed (rc={rc}): {tail[:500]}")

        # Pull the (possibly modified) clone back out.
        rc, data = await _dexec(container, ["tar", "-cz", "-C", container_run_root, "repo"],
                                timeout=120)
        if rc != 0:
            raise RuntimeError("workspace retrieval failed")
        out_dir = os.path.join(run_dir, "out")
        _untar_to(data, out_dir)
        out_repo = os.path.join(out_dir, "repo")

        # Validate + commit the proposal in the isolated clone.
        await _git(["checkout", "-b", branch], cwd=out_repo)
        await _git(["add", "-A"], cwd=out_repo)
        changed = await _git(["diff", "--cached", "--name-status"], cwd=out_repo)
        if not changed.strip():
            raise RuntimeError("agent run produced no changes")
        new_meeting_artifacts = [
            line.split("\t", 1)[1] for line in changed.splitlines()
            if line.startswith("A") and "graph/kg/entities/meetings/" in line
        ]
        if not new_meeting_artifacts:
            raise RuntimeError(
                "agent run produced changes but no new meeting artifact under "
                "graph/kg/entities/meetings/ — rejecting non-conforming proposal")
        await _git(["commit", "-m", f"proposal: meeting {meeting_id}"], cwd=out_repo)

        # Atomic publication: the branch lands in the org repo in one fetch.
        async with _org_lock(org_id):
            if await _branch_exists(repo, branch):
                raise RuntimeError(f"branch {branch} appeared concurrently")
            await _git(["fetch", out_repo, f"{branch}:{branch}"], cwd=repo)

        files = [ln for ln in changed.splitlines() if ln.strip()]
        record = {
            "id": f"prop_{uuid.uuid4().hex[:12]}",
            "org_id": org_id,
            "meeting_id": meeting_id,
            "meeting_title": meeting.get("title")
                             or f"{meeting.get('platform', 'meeting')} {meeting_id}",
            "created_at": _now(),
            "branch": branch,
            "summary": f"meeting artifact {new_meeting_artifacts[0]}"
                       f" + {len(files) - 1} other change(s)",
            "files_changed": len(files),
            "status": "open",
        }
        await _save_proposal(record)
        await set_claim(org_id, meeting_id, CLAIM_PROPOSED, proposal_id=record["id"])
        logger.info(f"EI: proposal {record['id']} ({branch}) created for org {org_id}")

        # Auto-merge mode: org opts out of the human sign gate; git history is
        # the safety net (every change is a commit on main — revert any time).
        if ei.get("auto_merge"):
            async with _org_lock(org_id):
                await _git(["merge", "--no-ff", "-m",
                            f"auto: merge proposal {record['id']} ({branch})", branch],
                           cwd=repo)
                merge_commit = await _git(["rev-parse", "HEAD"], cwd=repo)
                record["status"] = "signed"
                record["merge_commit"] = merge_commit
                record["signed_at"] = _now()
                record["note"] = "auto-merged (org auto_merge)"
                await _save_proposal(record)
            await push_remote(org_id)
            logger.info(f"EI: proposal {record['id']} auto-merged ({merge_commit[:12]})")

    except Exception as e:
        logger.error(f"EI: proposal run failed for org={org_id} meeting={meeting_id}: {e}",
                     exc_info=True)
        try:
            await set_claim(org_id, meeting_id, CLAIM_FAILED, error=str(e)[:500])
        except Exception:
            logger.exception("EI: could not record failed claim")
    finally:
        if touch_task:
            touch_task.cancel()
        # No partial state anywhere: temp run dir + container scratch removed.
        await _run(["rm", "-rf", run_dir])
        if container:
            try:
                await _dexec(container, ["rm", "-rf", container_run_root], timeout=30)
            except Exception:
                pass


# ── Event consumer ─────────────────────────────────────────────────────────


async def handle_meeting_completed(envelope: dict) -> dict:
    """Consume one meeting.completed envelope (POST_MEETING_HOOKS target).

    Returns a status dict; raises HTTPException(503) on infrastructure errors
    so meeting-api's outbound ledger keeps the event retryable.
    """
    event_type = envelope.get("event_type", "")
    if event_type != "meeting.completed":
        return {"status": "ignored", "detail": f"event_type {event_type!r} not consumed"}

    meeting = (envelope.get("data") or {}).get("meeting") or {}
    meeting_id = meeting.get("id")
    user_id = meeting.get("user_id")
    if meeting_id is None or user_id is None:
        return {"status": "ignored", "detail": "missing meeting id/user_id"}

    try:
        ei = await resolve_ei_config(user_id)
    except Exception as e:
        logger.error(f"EI: cannot resolve EI config for user {user_id}: {e}")
        raise HTTPException(503, "EI config resolution failed; retry later")

    if not ei:
        # Org-level enable flag is OFF (default) — fully inert, no state created.
        return {"status": "inert", "detail": "EI disabled for this user/org"}

    org_id = ei["org_id"]
    event_id = envelope.get("event_id", "")
    claimed = await claim_meeting(org_id, meeting_id, event_id)
    if not claimed:
        claim = await get_claim(org_id, meeting_id)
        return {
            "status": "duplicate",
            "detail": f"meeting already {((claim or {}).get('status')) or 'claimed'}",
            "org_id": org_id,
            "meeting_id": meeting_id,
        }

    task = asyncio.create_task(run_proposal(org_id, meeting_id, ei, envelope))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"status": "accepted", "org_id": org_id, "meeting_id": meeting_id}


# ── Sign API (frozen contract v1) ──────────────────────────────────────────


class RejectRequest(BaseModel):
    note: str = Field(..., min_length=1)


async def _load_scoped(pid: str, org: Optional[str]) -> dict:
    """Load a proposal; org-scoped — cross-org access is a 404, not a 403."""
    record = await get_proposal(pid)
    if not record:
        raise HTTPException(404, "Proposal not found")
    if org is not None and record["org_id"] != org:
        raise HTTPException(404, "Proposal not found")
    return record


def _contract_shape(record: dict) -> dict:
    return {
        "id": record["id"],
        "meeting_id": record["meeting_id"],
        "meeting_title": record["meeting_title"],
        "created_at": record["created_at"],
        "branch": record["branch"],
        "summary": record["summary"],
        "files_changed": record["files_changed"],
    }


@router.get("/api/proposals", dependencies=[Depends(require_api_key)])
async def list_proposals(org: str = Query(...)):
    """List pending (open) proposals for an org."""
    org = sanitize_org_id(org)
    records = await list_org_proposals(org)
    return [_contract_shape(r) for r in records if r.get("status") == "open"]


@router.get("/api/proposals/{pid}/diff", dependencies=[Depends(require_api_key)])
async def proposal_diff(pid: str, org: Optional[str] = Query(None)):
    record = await _load_scoped(pid, sanitize_org_id(org) if org else None)
    repo = org_repo_path(record["org_id"])
    branch = record["branch"]
    if record.get("status") == "rejected" or not await _branch_exists(repo, branch):
        # Rejected proposals have no branch left to diff.
        return {"proposal_id": pid, "files": []}

    name_status = await _git(["diff", "--name-status", f"main...{branch}"], cwd=repo)
    files = []
    for line in name_status.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        code, path = parts[0], parts[-1]
        status = "added" if code.startswith("A") else "modified"
        before = ""
        if not code.startswith("A"):
            rc, out = await _run(["git", "-C", repo, "show", f"main:{path}"])
            before = out.decode(errors="replace") if rc == 0 else ""
        rc, out = await _run(["git", "-C", repo, "show", f"{branch}:{path}"])
        after = out.decode(errors="replace") if rc == 0 else ""
        patch = await _git(["diff", f"main...{branch}", "--", path], cwd=repo)
        files.append({"path": path, "status": status,
                      "before": before, "after": after, "patch": patch})
    return {"proposal_id": pid, "files": files}


@router.post("/api/proposals/{pid}/sign", dependencies=[Depends(require_api_key)])
async def sign_proposal(pid: str, org: Optional[str] = Query(None)):
    """Merge the proposal branch into the workspace main branch.

    The ONLY path by which content reaches main. Idempotent in effect: a
    repeat sign (or a sign after reject) returns 409 and never re-merges.
    """
    record = await _load_scoped(pid, sanitize_org_id(org) if org else None)
    if record.get("status") != "open":
        raise HTTPException(
            409, f"Proposal already {record.get('status')}"
                 + (f" (merge_commit {record['merge_commit']})"
                    if record.get("merge_commit") else ""))

    repo = org_repo_path(record["org_id"])
    branch = record["branch"]
    async with _org_lock(record["org_id"]):
        if not await _branch_exists(repo, branch):
            raise HTTPException(409, "Proposal branch is gone")
        # The org repo's checkout stays on main and is never dirtied (runs use
        # isolated clones), so a plain --no-ff merge is safe here.
        await _git(["merge", "--no-ff", "-m",
                    f"sign: merge proposal {pid} ({branch})", branch], cwd=repo)
        merge_commit = await _git(["rev-parse", "HEAD"], cwd=repo)
        record["status"] = "signed"
        record["merge_commit"] = merge_commit
        record["signed_at"] = _now()
        await _save_proposal(record)
    await push_remote(record["org_id"])
    return {"merged": True, "merge_commit": merge_commit}


@router.post("/api/proposals/{pid}/reject", dependencies=[Depends(require_api_key)])
async def reject_proposal(pid: str, body: RejectRequest, org: Optional[str] = Query(None)):
    """Close a proposal without merging: delete the branch, keep the note."""
    record = await _load_scoped(pid, sanitize_org_id(org) if org else None)
    if record.get("status") != "open":
        raise HTTPException(409, f"Proposal already {record.get('status')}")

    repo = org_repo_path(record["org_id"])
    async with _org_lock(record["org_id"]):
        if await _branch_exists(repo, record["branch"]):
            await _git(["branch", "-D", record["branch"]], cwd=repo)
        record["status"] = "rejected"
        record["note"] = body.note
        record["rejected_at"] = _now()
        await _save_proposal(record)
    return {"closed": True}


# ── Agent chat on the org workspace (reads + auto-committed writes) ────────


class ChatRequest(BaseModel):
    message: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None


def _extract_agent_reply(stdout: str) -> Optional[str]:
    """Best-effort: pull the last assistant message out of an agent CLI's JSON
    output (e.g. Vibe --output json prints the conversation log)."""
    text = stdout.strip()
    if not text:
        return None
    start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
    if start < 0:
        return None
    try:
        data = json.loads(text[start:])
    except Exception:
        return None
    msgs = data if isinstance(data, list) else data.get("messages") if isinstance(data, dict) else None
    if not isinstance(msgs, list):
        return None
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "assistant":
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                return c.strip()
            if isinstance(c, list):
                parts = [b.get("text", "") for b in c if isinstance(b, dict)]
                joined = "\n".join(p for p in parts if p).strip()
                if joined:
                    return joined
    return None


def _build_chat_prompt(message: str, history: str = "") -> str:
    history_block = (
        "CONVERSATION SO FAR (from the session log; continue it):\n" + history + "\n\n"
        if history.strip() else ""
    )
    return (
        "You are the organization's knowledge agent, working inside its git "
        "knowledge workspace (conventions: AGENT.md; entity graph under "
        "graph/kg/, strategy graph under graph/sg/, templates under templates/).\n\n"
        + history_block +
        "USER MESSAGE:\n" + message + "\n\n"
        "Instructions:\n"
        "1. Answer the user using the workspace content; cite files with "
        "[[wikilinks]] or paths where relevant.\n"
        "2. If the user asks you to record, update, research-and-store, or "
        "restructure knowledge, edit/create files following the workspace "
        "conventions (dated confidence-scored appends in routine-updates "
        "regions; templates for new entities; sg/ nodes for strategy).\n"
        "3. ALWAYS write your final reply for the user as markdown to "
        ".ei/reply.md — even for greetings or questions needing no file "
        "changes (create the .ei directory; it is never committed).\n"
        "4. Do not run git commands; the platform commits your changes."
    )


@router.post("/api/ei/chat", dependencies=[Depends(require_api_key)])
async def ei_chat(body: ChatRequest, org: str = Query(...)):
    """One chat turn with the org knowledge agent.

    The agent runs in the org container on a working clone of workspace main;
    any file changes are committed straight onto main (auto-commit — git
    history is the audit/rollback mechanism). Returns the agent's reply.
    """
    org = sanitize_org_id(org)
    ei = None
    if body.user_id:
        ei = await resolve_ei_config(body.user_id)
    if not ei or sanitize_org_id(ei.get("org_id") or "") != org:
        raise HTTPException(status_code=404, detail="EI not enabled for this org/user")

    session_id = re.sub(r"[^a-zA-Z0-9_-]", "", body.session_id or "") or \
        f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    chat_rel = f"chats/{session_id}.md"
    run_id = f"chat-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    run_dir = os.path.join(_runs_dir(org), run_id)
    container_run_root = f"/tmp/ei-chat-{org}-{run_id}"
    container = None
    touch_task = None
    try:
        repo = await ensure_workspace(org)
        os.makedirs(run_dir, exist_ok=True)
        async with _org_lock(org):
            await _git(["clone", "--branch", "main", "--single-branch", repo,
                        os.path.join(run_dir, "repo")])

        org_env = ei.get("env") or {}
        container = await _cm.ensure_container(
            f"ei-{org}", session_id="ei",
            config={"env": org_env} if org_env else {},
        )
        payload = _tar_dir(run_dir)
        rc, out = await _dexec(
            container,
            ["sh", "-c",
             f"rm -rf {container_run_root} && mkdir -p {container_run_root} "
             f"&& tar -xz -C {container_run_root}"],
            stdin=payload, timeout=120,
        )
        if rc != 0:
            raise RuntimeError(f"workspace transfer failed: {out.decode(errors='replace')[:300]}")

        history = ""
        hist_path = os.path.join(run_dir, "repo", chat_rel)
        if os.path.isfile(hist_path):
            with open(hist_path) as f:
                history = f.read()[-6000:]
        prompt = _build_chat_prompt(body.message, history)
        agent_cmd = _agent_command(ei)
        shell_cmd = (
            f"cd {container_run_root}/repo && EI_ORG_ID={shlex.quote(org)} "
            f"{agent_cmd} {shlex.quote(prompt)}"
        )
        touch_task = asyncio.create_task(_touch_loop(container))
        rc, out = await _dexec(container, ["bash", "-c", shell_cmd],
                               timeout=config.EI_AGENT_TIMEOUT)
        touch_task.cancel()
        touch_task = None
        if rc != 0:
            raise RuntimeError(f"agent chat run failed (rc={rc}): "
                               f"{out.decode(errors='replace')[-500:]}")

        rc, data = await _dexec(container, ["tar", "-cz", "-C", container_run_root, "repo"],
                                timeout=120)
        if rc != 0:
            raise RuntimeError("workspace retrieval failed")
        out_dir = os.path.join(run_dir, "out")
        _untar_to(data, out_dir)
        out_repo = os.path.join(out_dir, "repo")

        reply_path = os.path.join(out_repo, ".ei", "reply.md")
        reply = ""
        if os.path.isfile(reply_path):
            with open(reply_path) as f:
                reply = f.read().strip()
        await _run(["rm", "-rf", os.path.join(out_repo, ".ei")])

        if not reply:
            stdout_text = out.decode(errors="replace")
            reply = _extract_agent_reply(stdout_text) or ""
        if not reply:
            reply = "(the agent returned no reply)"

        # Append this turn to the session log — chats are workspace files too.
        now = _now()
        chat_abs = os.path.join(out_repo, chat_rel)
        os.makedirs(os.path.dirname(chat_abs), exist_ok=True)
        new_file = not os.path.isfile(chat_abs)
        with open(chat_abs, "a") as f:
            if new_file:
                title = " ".join(body.message.split())[:48] or f"Chat {session_id}"
                f.write(f"# {title}\n<!-- ei-chat v1 id:{session_id} -->\n")
            f.write(f"\n## You — {now}\n\n{body.message.strip()}\n")
            f.write(f"\n## Agent — {now}\n\n{reply.strip()}\n")

        await _git(["add", "-A"], cwd=out_repo)
        changed = await _git(["diff", "--cached", "--name-status"], cwd=out_repo)
        commit_sha = None
        files = [ln for ln in changed.splitlines() if ln.strip()]
        kb_files = [ln for ln in files if chat_rel not in ln]
        if files:
            snippet = " ".join(body.message.split())[:60]
            await _git(["commit", "-m", f"chat: {snippet}"], cwd=out_repo)
            async with _org_lock(org):
                await _git(["fetch", out_repo, "main"], cwd=repo)
                await _git(["merge", "--ff-only", "FETCH_HEAD"], cwd=repo)
                commit_sha = await _git(["rev-parse", "HEAD"], cwd=repo)
            await push_remote(org)

        return {"reply": reply, "commit": commit_sha,
                "files_changed": len(kb_files), "session_id": session_id}
    finally:
        if touch_task:
            touch_task.cancel()
        await _run(["rm", "-rf", run_dir])
        if container:
            try:
                await _dexec(container, ["rm", "-rf", container_run_root], timeout=30)
            except Exception:
                pass


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _streaming_agent_cmd(ei: dict) -> str:
    cmd = _agent_command(ei)
    if "--output streaming" in cmd:
        return cmd
    if "--output json" in cmd:
        return cmd.replace("--output json", "--output streaming")
    if cmd.rstrip().endswith("-p"):
        return cmd.rstrip()[:-2] + "--output streaming -p"
    return cmd


def _vibe_event(line: str) -> Optional[dict]:
    """Map one NDJSON line from the agent CLI to a UI event."""
    line = line.strip()
    if not line:
        return None
    try:
        m = json.loads(line)
    except Exception:
        return {"type": "log", "text": line[:200]}
    if not isinstance(m, dict):
        return None
    role = m.get("role")
    if m.get("tool_calls"):
        calls = []
        for tc in m["tool_calls"]:
            fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
            name = fn.get("name") or tc.get("name") or "tool"
            args = fn.get("arguments") or ""
            if not isinstance(args, str):
                args = json.dumps(args)
            calls.append({"name": name, "args": args[:160]})
        return {"type": "tools", "calls": calls}
    if role == "tool":
        c = m.get("content")
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
        return {"type": "tool_result", "snippet": str(c or "")[:160]}
    if role == "assistant":
        c = m.get("content")
        if isinstance(c, list):
            c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict))
        if c and str(c).strip():
            return {"type": "assistant", "text": str(c)}
    return None


# In-flight chat turns, keyed by f"{org}/{session}". A turn runs to completion
# (agent → persist → commit → push) in a DETACHED task, so a client reload or
# pane switch never aborts it: the answer is always written to chats/<id>.md.
# The SSE response merely tails the turn's event buffer; disconnect stops the
# tailing, not the work. A reconnecting client replays the buffer from the top.
_inflight_turns: dict = {}


class _ChatTurn:
    def __init__(self, key: str):
        self.key = key
        self.events: list = []
        self.done = False
        self._updated = asyncio.Event()
        self.task = None

    def emit(self, ev: dict) -> None:
        self.events.append(ev)
        self._updated.set()

    def finish(self) -> None:
        self.done = True
        self._updated.set()

    async def subscribe(self):
        i = 0
        while True:
            while i < len(self.events):
                yield self.events[i]
                i += 1
            if self.done:
                return
            await self._updated.wait()
            self._updated.clear()


async def _run_chat_turn(turn, org_s, body, session_id, chat_rel,
                         run_dir, container_run_root, ei):
    """The durable chat turn: runs the agent, persists chats/<id>.md, commits
    and pushes. Runs detached from the request so a client disconnect cannot
    cancel it. All progress goes to `turn` for any (re)connected SSE listener."""
    container = None
    touch_task = None
    proc = None
    try:
        turn.emit({"type": "status", "text": "preparing workspace"})
        repo = await ensure_workspace(org_s)
        os.makedirs(run_dir, exist_ok=True)
        async with _org_lock(org_s):
            await _git(["clone", "--branch", "main", "--single-branch", repo,
                        os.path.join(run_dir, "repo")])
        history = ""
        hist_path = os.path.join(run_dir, "repo", chat_rel)
        if os.path.isfile(hist_path):
            with open(hist_path) as f:
                history = f.read()[-6000:]

        org_env = ei.get("env") or {}
        container = await _cm.ensure_container(
            f"ei-{org_s}", session_id="ei",
            config={"env": org_env} if org_env else {},
        )
        payload = _tar_dir(run_dir)
        rc, out = await _dexec(
            container,
            ["sh", "-c",
             f"rm -rf {container_run_root} && mkdir -p {container_run_root} "
             f"&& tar -xz -C {container_run_root}"],
            stdin=payload, timeout=120,
        )
        if rc != 0:
            raise RuntimeError("workspace transfer failed")

        prompt = _build_chat_prompt(body.message, history)
        agent_cmd = _streaming_agent_cmd(ei)
        shell_cmd = (
            f"cd {container_run_root}/repo && EI_ORG_ID={shlex.quote(org_s)} "
            f"{agent_cmd} {shlex.quote(prompt)}"
        )
        turn.emit({"type": "status", "text": "agent running"})
        last_assistant = ""
        touch_task = asyncio.create_task(_touch_loop(container))
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container, "bash", "-c", shell_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        deadline = time.monotonic() + config.EI_AGENT_TIMEOUT
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.kill()
                raise RuntimeError("agent run timed out")
            try:
                line = await asyncio.wait_for(proc.stdout.readline(),
                                              timeout=min(remaining, 30))
            except asyncio.TimeoutError:
                turn.emit({"type": "ping"})
                continue
            if not line:
                break
            ev = _vibe_event(line.decode(errors="replace"))
            if ev:
                if ev.get("type") == "assistant" and ev.get("text"):
                    last_assistant = str(ev["text"])
                turn.emit(ev)
        rc = await proc.wait()
        touch_task.cancel()
        touch_task = None
        if rc != 0:
            raise RuntimeError(f"agent run failed (rc={rc})")

        rc, data = await _dexec(container,
                                ["tar", "-cz", "-C", container_run_root, "repo"],
                                timeout=120)
        if rc != 0:
            raise RuntimeError("workspace retrieval failed")
        out_dir = os.path.join(run_dir, "out")
        _untar_to(data, out_dir)
        out_repo = os.path.join(out_dir, "repo")

        reply_path = os.path.join(out_repo, ".ei", "reply.md")
        reply = ""
        if os.path.isfile(reply_path):
            with open(reply_path) as f:
                reply = f.read().strip()
        await _run(["rm", "-rf", os.path.join(out_repo, ".ei")])
        if not reply and last_assistant.strip():
            reply = last_assistant.strip()
        if not reply:
            reply = "(the agent returned no reply)"

        now = _now()
        chat_abs = os.path.join(out_repo, chat_rel)
        os.makedirs(os.path.dirname(chat_abs), exist_ok=True)
        new_file = not os.path.isfile(chat_abs)
        with open(chat_abs, "a") as f:
            if new_file:
                title = " ".join(body.message.split())[:48] or f"Chat {session_id}"
                f.write(f"# {title}\n<!-- ei-chat v1 id:{session_id} -->\n")
            f.write(f"\n## You — {now}\n\n{body.message.strip()}\n")
            f.write(f"\n## Agent — {now}\n\n{reply.strip()}\n")

        await _git(["add", "-A"], cwd=out_repo)
        changed = await _git(["diff", "--cached", "--name-status"], cwd=out_repo)
        commit_sha = None
        files = [ln for ln in changed.splitlines() if ln.strip()]
        kb_files = [ln for ln in files if chat_rel not in ln]
        if files:
            snippet = " ".join(body.message.split())[:60]
            await _git(["commit", "-m", f"chat: {snippet}"], cwd=out_repo)
            async with _org_lock(org_s):
                await _git(["fetch", out_repo, "main"], cwd=repo)
                await _git(["merge", "--ff-only", "FETCH_HEAD"], cwd=repo)
                commit_sha = await _git(["rev-parse", "HEAD"], cwd=repo)
            await push_remote(org_s)

        turn.emit({"type": "done", "reply": reply, "commit": commit_sha,
                   "files_changed": len(kb_files), "session_id": session_id})
    except Exception as e:
        logger.error(f"EI chat turn failed org={org_s}: {e}", exc_info=True)
        turn.emit({"type": "error", "message": str(e)[:300]})
    finally:
        if touch_task:
            touch_task.cancel()
        if proc and proc.returncode is None:
            proc.kill()
        await _run(["rm", "-rf", run_dir])
        if container:
            try:
                await _dexec(container, ["rm", "-rf", container_run_root], timeout=30)
            except Exception:
                pass
        turn.finish()
        _inflight_turns.pop(turn.key, None)


@router.post("/api/ei/chat/stream", dependencies=[Depends(require_api_key)])
async def ei_chat_stream(body: ChatRequest, org: str = Query(...)):
    """Streaming variant of /api/ei/chat: SSE of agent activity (tool calls,
    intermediate messages) followed by a final `done` event with the reply
    and commit info. Same workspace/auto-commit semantics. The turn itself
    runs detached, so a reload/disconnect never loses the answer."""
    org_s = sanitize_org_id(org)
    ei = await resolve_ei_config(body.user_id) if body.user_id else None
    if not ei or sanitize_org_id(ei.get("org_id") or "") != org_s:
        raise HTTPException(status_code=404, detail="EI not enabled for this org/user")

    session_id = re.sub(r"[^a-zA-Z0-9_-]", "", body.session_id or "") or \
        f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    chat_rel = f"chats/{session_id}.md"
    run_id = f"chat-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    run_dir = os.path.join(_runs_dir(org_s), run_id)
    container_run_root = f"/tmp/ei-chat-{org_s}-{run_id}"

    key = f"{org_s}/{session_id}"
    turn = _inflight_turns.get(key)
    if turn is None or turn.done:
        turn = _ChatTurn(key)
        _inflight_turns[key] = turn
        turn.task = asyncio.create_task(_run_chat_turn(
            turn, org_s, body, session_id, chat_rel,
            run_dir, container_run_root, ei))

    async def gen():
        async for ev in turn.subscribe():
            yield _sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@router.get("/api/ei/chat/attach", dependencies=[Depends(require_api_key)])
async def ei_chat_attach(org: str = Query(...), session: str = Query(...)):
    """Reattach to an in-flight chat turn (after a reload / pane switch): replays
    the turn's buffered events from the top, then streams the rest live until
    `done`. If no turn is in flight for this session, emits a single `idle` event
    so the client knows to restore from the persisted chats/<id>.md instead."""
    org_s = sanitize_org_id(org)
    sid = re.sub(r"[^a-zA-Z0-9_-]", "", session or "")
    turn = _inflight_turns.get(f"{org_s}/{sid}")

    async def gen():
        if turn is None:
            yield _sse({"type": "idle"})
            return
        async for ev in turn.subscribe():
            yield _sse(ev)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


class RenameChatRequest(BaseModel):
    session_id: str
    title: str


@router.get("/api/ei/chat/sessions", dependencies=[Depends(require_api_key)])
async def ei_chat_sessions(org: str = Query(...)):
    """List chat sessions (id + title) from chats/ on workspace main."""
    org = sanitize_org_id(org)
    repo = org_repo_path(org)
    if not os.path.isdir(repo):
        return []
    try:
        out = await _git(["ls-tree", "--name-only", "main", "chats/"], cwd=repo)
    except RuntimeError:
        return []
    sessions = []
    for f in sorted(out.splitlines(), reverse=True):
        if not f.endswith(".md"):
            continue
        sid = os.path.basename(f)[:-3]
        title = sid
        try:
            head = await _git(["show", f"main:{f}"], cwd=repo)
            first = head.splitlines()[0] if head else ""
            if first.startswith("# "):
                title = first[2:].strip() or sid
        except RuntimeError:
            pass
        sessions.append({"id": sid, "title": title})
    return sessions


@router.post("/api/ei/chat/sessions/rename", dependencies=[Depends(require_api_key)])
async def ei_chat_rename(body: RenameChatRequest, org: str = Query(...)):
    """Rename a chat session: rewrite the H1 of its workspace file (committed)."""
    org = sanitize_org_id(org)
    sid = re.sub(r"[^a-zA-Z0-9_-]", "", body.session_id)
    title = " ".join(body.title.split())[:80]
    if not sid or not title:
        raise HTTPException(status_code=400, detail="session_id and title required")
    repo = org_repo_path(org)
    rel = f"chats/{sid}.md"
    path = os.path.join(repo, rel)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="session not found")
    async with _org_lock(org):
        with open(path) as f:
            lines = f.read().splitlines()
        if lines and lines[0].startswith("# "):
            lines[0] = f"# {title}"
        else:
            lines.insert(0, f"# {title}")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        await _git(["add", rel], cwd=repo)
        await _git(["commit", "-m", f"chat: rename {sid} -> {title[:40]}"], cwd=repo)
    return {"renamed": True, "id": sid, "title": title}


# ── Workspace file upload (drop to add files; committed) ───────────────────


@router.post("/api/ei/workspace/upload", dependencies=[Depends(require_api_key)])
async def ei_workspace_upload(
    org: str = Query(...),
    dir: str = Query("uploads"),
    files: list[UploadFile] = File(...),
):
    """Write one or more uploaded files into the org workspace under <dir> and
    commit (auto-commit model). Returns the committed relative paths."""
    org = sanitize_org_id(org)
    safe_dir = re.sub(r"[^a-zA-Z0-9_/-]", "", dir).strip("/") or "uploads"
    if ".." in safe_dir.split("/"):
        raise HTTPException(status_code=400, detail="invalid dir")
    repo = await ensure_workspace(org)
    written: list[str] = []
    async with _org_lock(org):
        for uf in files:
            name = os.path.basename(uf.filename or "file")
            name = re.sub(r"[^a-zA-Z0-9._ -]", "_", name).strip() or "file"
            rel = f"{safe_dir}/{name}"
            abspath = os.path.join(repo, rel)
            os.makedirs(os.path.dirname(abspath), exist_ok=True)
            data = await uf.read()
            with open(abspath, "wb") as fh:
                fh.write(data)
            written.append(rel)
        await _git(["add", "-A"], cwd=repo)
        status = await _git(["status", "--porcelain"], cwd=repo)
        commit = None
        if status.strip():
            label = ", ".join(os.path.basename(w) for w in written)[:60]
            await _git(["commit", "-m", f"upload: {label}"], cwd=repo)
            commit = await _git(["rev-parse", "HEAD"], cwd=repo)
    await push_remote(org)
    return {"uploaded": written, "commit": commit}


# ── Workspace git connection (connect a remote; remote is source of truth) ─


class GitConnectRequest(BaseModel):
    remote_url: str
    branch: Optional[str] = "main"
    token: Optional[str] = None
    mode: Optional[str] = "byor"  # byor | provision


@router.get("/api/ei/workspace/git", dependencies=[Depends(require_api_key)])
async def ei_git_status(org: str = Query(...)):
    org = sanitize_org_id(org)
    cfg = load_git_remote(org)
    if not cfg or not cfg.get("remote_url"):
        return {"connected": False}
    repo = org_repo_path(org)
    head = None
    if os.path.isdir(os.path.join(repo, ".git")):
        try:
            head = await _git(["rev-parse", "HEAD"], cwd=repo)
        except RuntimeError:
            head = None
    return {
        "connected": True,
        "remote_url": cfg["remote_url"],
        "branch": _branch_of(cfg),
        "mode": cfg.get("mode", "byor"),
        "has_token": bool(cfg.get("token")),
        "head": head,
    }


@router.post("/api/ei/workspace/git/connect", dependencies=[Depends(require_api_key)])
async def ei_git_connect(body: GitConnectRequest, org: str = Query(...)):
    org = sanitize_org_id(org)
    url = (body.remote_url or "").strip()
    if not (url.startswith("https://") or url.startswith("http://")
            or url.startswith("git@")):
        raise HTTPException(status_code=400, detail="remote_url must be http(s) or ssh")
    mode = body.mode if body.mode in ("byor", "provision") else "byor"
    cfg = {"remote_url": url, "branch": (body.branch or "main"),
           "token": body.token or None, "mode": mode}
    # Connecting replaces any existing local repo with the remote's content.
    repo = org_repo_path(org)
    async with _org_lock(org):
        await _run(["rm", "-rf", repo])
    save_git_remote(org, cfg)
    try:
        await ensure_workspace(org)  # clones (byor) or provisions+pushes
    except Exception as e:
        clear_git_remote(org)
        msg = str(e)
        if "could not read Username" in msg or "Authentication failed" in msg or "terminal prompts disabled" in msg:
            msg = "authentication required — provide an access token with repo read/write access"
        raise HTTPException(status_code=400, detail=f"connect failed: {msg[:200]}")
    return await ei_git_status(org=org)


@router.post("/api/ei/workspace/git/disconnect", dependencies=[Depends(require_api_key)])
async def ei_git_disconnect(org: str = Query(...)):
    org = sanitize_org_id(org)
    clear_git_remote(org)
    return {"connected": False}


@router.post("/api/ei/workspace/git/sync", dependencies=[Depends(require_api_key)])
async def ei_git_sync(org: str = Query(...)):
    org = sanitize_org_id(org)
    await ensure_workspace(org)   # fetch+reset to remote
    head = await push_remote(org)  # then push any local-ahead (no-op normally)
    return {"synced": True, "head": head}


# ── Org workspace read API (workspace viewer — read-only) ──────────────────


@router.get("/api/ei/workspace/tree", dependencies=[Depends(require_api_key)])
async def ei_workspace_tree(org: str = Query(...)):
    """List all files on the org workspace main branch (read-only viewer)."""
    org = sanitize_org_id(org)
    repo = org_repo_path(org)
    if not os.path.isdir(repo):
        return {"org_id": org, "files": []}
    out = await _git(["ls-tree", "-r", "--name-only", "main"], cwd=repo)
    return {"org_id": org, "files": [p for p in out.splitlines() if p]}


@router.get("/api/ei/workspace/file", dependencies=[Depends(require_api_key)])
async def ei_workspace_file(org: str = Query(...), path: str = Query(...)):
    """Read one file from the org workspace main branch (read-only viewer)."""
    org = sanitize_org_id(org)
    repo = org_repo_path(org)
    if path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="invalid path")
    if not os.path.isdir(repo):
        raise HTTPException(status_code=404, detail="workspace not found")
    try:
        content = await _git(["show", f"main:{path}"], cwd=repo)
    except RuntimeError:
        raise HTTPException(status_code=404, detail="file not found")
    return {"org_id": org, "path": path, "content": content}


# ── Internal status (visible run state — ops + regression checks) ──────────


@router.get("/internal/ei/status")
async def ei_status(org_id: str, meeting_id: str):
    """Run/claim status for one meeting in one org. Internal surface."""
    org_id = sanitize_org_id(org_id)
    claim = await get_claim(org_id, meeting_id)
    proposal = None
    if claim and claim.get("proposal_id"):
        proposal = await get_proposal(claim["proposal_id"])
    return {"org_id": org_id, "meeting_id": meeting_id,
            "claim": claim, "proposal": proposal}
