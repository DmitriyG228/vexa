/**
 * Committed mock of the EI sign-API — frozen contract v1 (issues #24/#25).
 *
 * This is the pack's ONLY backend coupling, mocked so the ei-review-ui checks
 * run standalone (no agent-api, no git workspaces). Behavior mirrors the real
 * implementation in services/agent-api/agent_api/lineage.py:
 *
 *   GET  /api/proposals?org=<org_id>       -> 200 [contract shape] (open only)
 *   GET  /api/proposals/{id}/diff?org=...  -> 200 {proposal_id, files[]}; cross-org -> 404
 *   POST /api/proposals/{id}/sign?org=...  -> 200 {merged, merge_commit}; non-open -> 409
 *   POST /api/proposals/{id}/reject?org=.. -> 200 {closed: true}; note required -> 422
 *
 * Auth: X-API-Key (403 on mismatch, matching agent-api's require_api_key).
 * Cross-org access is a 404, never a 403 (no existence oracle).
 *
 * Usage:
 *   tests:      import { startMockSignApi, makeFixtureState } from "./mock-sign-api.mjs"
 *   dev server: node tests/mock-sign-api.mjs   (PORT=8100 API_KEY=token node ...)
 */

import http from "node:http";
import crypto from "node:crypto";

// ── Fixture data ────────────────────────────────────────────────────────────

const ENTITY_BEFORE = `# Acme Corp

Type: company

## Routine updates

<!-- region: routine-updates -->
- 2026-05-01 (confidence: high) — Initial contact established. [[meetings/41-intro-call]]
<!-- endregion -->
`;

const ENTITY_AFTER = `# Acme Corp

Type: company

## Routine updates

<!-- region: routine-updates -->
- 2026-05-01 (confidence: high) — Initial contact established. [[meetings/41-intro-call]]
- 2026-06-09 (confidence: medium) — Evaluating the enterprise tier; decision expected by end of Q2. [[meetings/42-pricing-review]]
<!-- endregion -->
`;

const MEETING_ARTIFACT = `# Pricing review with Acme Corp

Date: 2026-06-09
Participants: [[people/jane-doe]], [[companies/acme-corp]]

## Summary

Acme Corp is evaluating the enterprise tier. Jane raised questions about
seat-based pricing and SSO support.

## Decisions

- Send the enterprise pricing sheet by Friday.
`;

function patchFor(path, before, after) {
  // Simplified unified patch — shape-faithful (the UI treats it as opaque text).
  const minus = before
    ? before.split("\n").map((l) => `-${l}`).join("\n")
    : "";
  const plus = after.split("\n").map((l) => `+${l}`).join("\n");
  return `diff --git a/${path} b/${path}\n--- ${before ? `a/${path}` : "/dev/null"}\n+++ b/${path}\n${minus ? minus + "\n" : ""}${plus}`;
}

/** Two orgs; org-a has two open proposals, org-b has one — proves tenant scoping. */
export function makeFixtureState() {
  const mk = (id, org, meetingId, title, files) => ({
    id,
    org_id: org,
    meeting_id: meetingId,
    meeting_title: title,
    created_at: new Date(Date.now() - 3600_000).toISOString(),
    branch: `meeting/${meetingId}`,
    summary: `meeting artifact ${files[0].path} + ${files.length - 1} other change(s)`,
    files_changed: files.length,
    status: "open",
    files,
  });

  const meetingFile = {
    path: "graph/kg/entities/meetings/42-pricing-review.md",
    status: "added",
    before: "",
    after: MEETING_ARTIFACT,
    patch: patchFor("graph/kg/entities/meetings/42-pricing-review.md", "", MEETING_ARTIFACT),
  };
  const entityFile = {
    path: "graph/kg/entities/companies/acme-corp.md",
    status: "modified",
    before: ENTITY_BEFORE,
    after: ENTITY_AFTER,
    patch: patchFor("graph/kg/entities/companies/acme-corp.md", ENTITY_BEFORE, ENTITY_AFTER),
  };

  return {
    proposals: new Map(
      [
        mk("prop_a1", "org-a", 42, "Pricing review with Acme Corp", [meetingFile, entityFile]),
        mk("prop_a2", "org-a", 43, "Weekly sync", [meetingFile]),
        mk("prop_b1", "org-b", 99, "Org B private meeting", [meetingFile]),
      ].map((p) => [p.id, p])
    ),
  };
}

// ── Contract behavior ───────────────────────────────────────────────────────

function contractShape(p) {
  const { id, meeting_id, meeting_title, created_at, branch, summary, files_changed } = p;
  return { id, meeting_id, meeting_title, created_at, branch, summary, files_changed };
}

function loadScoped(state, pid, org) {
  const record = state.proposals.get(pid);
  if (!record) return null;
  if (org != null && record.org_id !== org) return null; // cross-org -> 404
  return record;
}

export function handleRequest(state, apiKey, req, res) {
  const url = new URL(req.url, "http://mock");
  const send = (status, body) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  };

  if (apiKey && req.headers["x-api-key"] !== apiKey) {
    return send(403, { detail: "Invalid or missing API key" });
  }

  const org = url.searchParams.get("org");
  const parts = url.pathname.split("/").filter(Boolean);

  // GET /api/proposals
  if (req.method === "GET" && url.pathname === "/api/proposals") {
    if (!org) return send(422, { detail: "org query param required" });
    const open = [...state.proposals.values()]
      .filter((p) => p.org_id === org && p.status === "open")
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    return send(200, open.map(contractShape));
  }

  // /api/proposals/{id}/{action}
  if (parts.length === 4 && parts[0] === "api" && parts[1] === "proposals") {
    const [, , pid, action] = parts;
    const record = loadScoped(state, pid, org);
    if (!record) return send(404, { detail: "Proposal not found" });

    if (req.method === "GET" && action === "diff") {
      if (record.status === "rejected") return send(200, { proposal_id: pid, files: [] });
      return send(200, { proposal_id: pid, files: record.files });
    }

    if (req.method === "POST" && action === "sign") {
      if (record.status !== "open") {
        return send(409, {
          detail:
            `Proposal already ${record.status}` +
            (record.merge_commit ? ` (merge_commit ${record.merge_commit})` : ""),
        });
      }
      record.status = "signed";
      record.merge_commit = crypto.randomBytes(20).toString("hex");
      record.signed_at = new Date().toISOString();
      return send(200, { merged: true, merge_commit: record.merge_commit });
    }

    if (req.method === "POST" && action === "reject") {
      let chunks = [];
      req.on("data", (c) => chunks.push(c));
      req.on("end", () => {
        let note;
        try {
          note = JSON.parse(Buffer.concat(chunks).toString() || "{}").note;
        } catch {
          note = undefined;
        }
        if (typeof note !== "string" || note.length < 1) {
          return send(422, { detail: "note is required" }); // pydantic min_length=1
        }
        if (record.status !== "open") {
          return send(409, { detail: `Proposal already ${record.status}` });
        }
        record.status = "rejected";
        record.note = note;
        record.rejected_at = new Date().toISOString();
        return send(200, { closed: true });
      });
      return;
    }
  }

  // Internal inspection surface (mirrors agent-api's /internal/ei/status role):
  // lets checks assert persisted state (e.g. the reject note) without an oracle
  // in the public contract.
  if (req.method === "GET" && parts.length === 3 && parts[0] === "internal" && parts[1] === "proposals") {
    const record = state.proposals.get(parts[2]);
    if (!record) return send(404, { detail: "Proposal not found" });
    return send(200, record);
  }

  return send(404, { detail: "Not found" });
}

export function startMockSignApi({ apiKey = "", port = 0, state = makeFixtureState() } = {}) {
  const server = http.createServer((req, res) => handleRequest(state, apiKey, req, res));
  return new Promise((resolve) => {
    server.listen(port, "127.0.0.1", () => {
      resolve({
        server,
        state,
        port: server.address().port,
        baseUrl: `http://127.0.0.1:${server.address().port}`,
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

// Standalone dev server: node tests/mock-sign-api.mjs
if (process.argv[1] && import.meta.url.endsWith(process.argv[1].split("/").pop())) {
  const port = Number(process.env.PORT || 8100);
  const apiKey = process.env.API_KEY || "";
  startMockSignApi({ apiKey, port }).then(({ baseUrl }) => {
    console.log(`mock sign-api (contract v1) listening at ${baseUrl} (orgs: org-a, org-b)`);
  });
}
