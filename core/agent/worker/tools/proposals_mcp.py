"""proposals_mcp.py — the ``propose_vcs_action`` worker tool (a minimal stdio MCP server).

The worker's ONLY voice toward GitHub is a PROPOSAL. This server exposes one tool that assembles a
``proposal.v1`` Proposal and POSTs it to the control plane's emission sink
(``$VEXA_AGENT_API_URL/internal/proposals``), authenticated by the per-dispatch identity token
(``$VEXA_AGENT_IDENTITY_TOKEN``) the runtime injected. No GitHub credential exists in this
container — the human gate and the (future) credentialed executor sit on the OTHER side of the
seam (P15), so a prompt-injected unit can at worst *ask*.

The tool takes the ACTION (never a level): the level is DERIVED structurally from the action here
(mirroring the contract's binding), and the sink re-derives + re-enforces it — the model cannot
label its way past the gate. ``subject`` / ``origin`` default from the dispatch env (VEXA_OWNER /
VEXA_UNIT_ID / VEXA_UNIT_TRIGGER), and the token's ``sub`` must match the subject at the sink, so
impersonation dies at the boundary even if a prompt overrides the default.

Deliberately minimal: newline-delimited JSON-RPC 2.0 over stdio (initialize / tools/list /
tools/call) and one ``httpx`` POST — no MCP SDK dep (the worker image ships base deps only).
"""
from __future__ import annotations

import json
import os
import sys

# Mirrors control_plane.proposals.LEVEL_ACTIONS / proposal.v1's allOf. Kept LOCAL (not imported)
# because the worker image ships no control_plane package (D1) — the sink is the enforcing copy;
# this one only saves the model a rejected round-trip.
_ACTION_LEVEL = {
    "comment": "L2", "label": "L2", "close": "L2", "open_issue": "L2",
    "push_branch": "L3", "open_pr": "L3",
}

TOOL = {
    "name": "propose_vcs_action",
    "description": (
        "Propose an external GitHub action for HUMAN approval — nothing is executed now. "
        "L2 actions (comment, label, close, open_issue) can be batch-approved; L3 actions "
        "(push_branch, open_pr) each need an individual approval. Give a sharp `rationale`: "
        "it is what the approving human reads."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["action", "target", "payload", "rationale"],
        "properties": {
            "action": {"enum": sorted(_ACTION_LEVEL)},
            "target": {
                "type": "object",
                "required": ["repo"],
                "properties": {
                    "repo": {"type": "string", "description": "owner/name"},
                    "kind": {"enum": ["issue", "pr", "branch"]},
                    "number": {"type": "integer"},
                    "ref": {"type": "string"},
                },
            },
            "payload": {
                "type": "object",
                "description": (
                    "per action: comment→{comment}; label→{labels}; close→{comment?}; "
                    "open_issue→{title,body,labels?}; push_branch→{branch,base,patches}; "
                    "open_pr→{branch,base,patches,title,body}"
                ),
            },
            "rationale": {"type": "string"},
            "routine": {
                "type": "object",
                "description": "the emitting routine ({id, name, declared_access}) from your plan/routine context",
                "properties": {
                    "id": {"type": "string"},
                    "name": {"type": "string"},
                    "declared_access": {"enum": ["L1", "L2", "L3"]},
                },
            },
            "event_ref": {"type": "string", "description": "opaque ref to the triggering event/source, if any"},
        },
    },
}


def build_proposal(args: dict, env: dict) -> dict:
    """Assemble the proposal.v1 Proposal from the tool args + the dispatch env (pure — testable).

    ``level`` is DERIVED from the action (never taken from the model); ``subject`` and ``origin``
    come from the dispatch env. id/status/created_at are the sink's to default."""
    action = args["action"]
    level = _ACTION_LEVEL.get(action)
    if level is None:
        raise ValueError(f"unknown action {action!r} (one of: {', '.join(sorted(_ACTION_LEVEL))})")
    target = dict(args.get("target") or {})
    target.setdefault("provider", "github")
    proposal: dict = {
        "subject": env.get("VEXA_OWNER", ""),
        "level": level,
        "action": action,
        "target": target,
        "payload": dict(args.get("payload") or {}),
        "rationale": args.get("rationale", ""),
        "status": "pending",
    }
    if args.get("routine"):
        proposal["routine"] = dict(args["routine"])
    origin = {k: v for k, v in {
        "unit_id": env.get("VEXA_UNIT_ID", ""),
        "trigger": env.get("VEXA_UNIT_TRIGGER", ""),
        "event_ref": args.get("event_ref", ""),
    }.items() if v}
    if origin.get("unit_id"):
        proposal["origin"] = origin
    return proposal


def post_proposal(args: dict, env: dict | None = None) -> dict:
    """Build + POST the proposal to the control plane's sink; return the tool result payload."""
    import httpx

    env = dict(env if env is not None else os.environ)
    base = (env.get("VEXA_AGENT_API_URL") or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "VEXA_AGENT_API_URL is not set — the worker has no control-plane sink"}
    token = env.get("VEXA_AGENT_IDENTITY_TOKEN", "")
    try:
        proposal = build_proposal(args, env)
    except (KeyError, ValueError) as e:
        return {"ok": False, "error": str(e)}
    try:
        r = httpx.post(
            f"{base}/internal/proposals", json=proposal,
            headers={"Authorization": f"Bearer {token}"}, timeout=15.0,
        )
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"could not reach the proposal sink: {e}"}
    if r.status_code != 201:
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except Exception:  # noqa: BLE001 — a non-JSON error body is still an error
            detail = r.text[:300]
        return {"ok": False, "status_code": r.status_code, "error": detail}
    return {"ok": True, "id": r.json().get("id"), "level": proposal["level"],
            "note": "proposed — awaiting human approval; nothing was executed"}


# ── the stdio MCP loop (newline-delimited JSON-RPC 2.0) ───────────────────────────────────────────

def handle(req: dict) -> dict | None:
    """One JSON-RPC request → its response dict (None for notifications)."""
    method, req_id = req.get("method"), req.get("id")
    if req_id is None:  # a notification (e.g. notifications/initialized) — no response
        return None
    if method == "initialize":
        result = {
            "protocolVersion": req.get("params", {}).get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "vexa-proposals", "version": "0.12.0"},
        }
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = req.get("params", {})
        if params.get("name") != TOOL["name"]:
            return {"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": -32602, "message": f"unknown tool {params.get('name')!r}"}}
        outcome = post_proposal(params.get("arguments") or {})
        result = {"content": [{"type": "text", "text": json.dumps(outcome)}],
                  "isError": not outcome.get("ok", False)}
    else:
        return {"jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"method {method!r} not found"}}
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def main() -> None:  # pragma: no cover — the stdio shell around the tested handle()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    main()
