/**
 * Dashboard proxy for the EI sign API (frozen contract v1 — issue #25).
 *
 * Forwards to agent-api with the service token, but the org query param is
 * ALWAYS resolved server-side from the authenticated dashboard session
 * (src/lib/ei-org.ts) — any client-supplied `org` is discarded, which is what
 * enforces the tenant-scoped prove: a user of org A can neither list nor act
 * on org B proposals through this surface.
 *
 * Routes (mirroring the contract):
 *   GET  /api/proposals               -> list pending proposals for MY org
 *   GET  /api/proposals/{id}/diff     -> per-file diff
 *   POST /api/proposals/{id}/sign     -> approve (merge)
 *   POST /api/proposals/{id}/reject   -> reject with required note
 */

import { NextRequest } from "next/server";
import { getAuthenticatedEiOrg } from "@/lib/ei-org";
import { buildProposalsTarget, parseProposalsPath } from "@/lib/proposals";

const AGENT_API_URL = process.env.AGENT_API_URL || "http://localhost:8100";
// Service-to-service token — must match the agent-api container's API key
const AGENT_API_TOKEN = process.env.AGENT_API_TOKEN || "";

type Ctx = { params: Promise<{ path?: string[] }> };

async function resolveOrg(): Promise<{ org: string } | { error: Response }> {
  const result = await getAuthenticatedEiOrg();
  if (result.status === "unauthenticated") {
    return { error: Response.json({ detail: "Not authenticated" }, { status: 401 }) };
  }
  if (result.status === "disabled") {
    return {
      error: Response.json(
        { detail: "Enterprise Intelligence is not enabled for this account" },
        { status: 404 }
      ),
    };
  }
  return { org: result.org };
}

async function safeJsonResponse(resp: globalThis.Response): Promise<Response> {
  const text = await resp.text();
  try {
    return Response.json(JSON.parse(text), { status: resp.status });
  } catch {
    return new Response(text, {
      status: resp.status,
      headers: { "Content-Type": resp.headers.get("content-type") || "text/plain" },
    });
  }
}

async function forward(
  req: NextRequest,
  ctx: Ctx,
  method: "GET" | "POST"
): Promise<Response> {
  const { path = [] } = await ctx.params;
  const parsed = parseProposalsPath(path);
  if (!parsed) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  // Method discipline per contract: list/diff are GET, sign/reject are POST.
  const wantPost = parsed.kind === "sign" || parsed.kind === "reject";
  if (wantPost !== (method === "POST")) {
    return Response.json({ detail: "Method not allowed" }, { status: 405 });
  }

  const resolved = await resolveOrg();
  if ("error" in resolved) return resolved.error;

  const url = new URL(req.url);
  const target = buildProposalsTarget(AGENT_API_URL, path, resolved.org, url.search);
  if (!target) return Response.json({ detail: "Not found" }, { status: 404 });

  const init: RequestInit = {
    method,
    headers: { "Content-Type": "application/json", "X-API-Key": AGENT_API_TOKEN },
  };
  if (method === "POST") {
    const body = await req.text();
    if (body) init.body = body;
  }
  const resp = await fetch(target, init);
  return safeJsonResponse(resp);
}

export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, ctx, "GET");
}

export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, ctx, "POST");
}
