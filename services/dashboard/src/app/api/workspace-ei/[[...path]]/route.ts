/**
 * Dashboard proxy for the EI org-workspace READ API (workspace viewer).
 *
 * GET /api/workspace-ei/tree              -> file list on workspace main
 * GET /api/workspace-ei/file?path=...     -> one file's content
 *
 * Same tenancy rule as the proposals proxy: the org is ALWAYS resolved
 * server-side from the authenticated dashboard session; any client-supplied
 * `org` is discarded. Read-only — no mutating verbs are forwarded.
 */

import { NextRequest } from "next/server";
import { getAuthenticatedEiOrg } from "@/lib/ei-org";

const AGENT_API_URL = process.env.AGENT_API_URL || "http://localhost:8100";
const AGENT_API_TOKEN = process.env.AGENT_API_TOKEN || "";

type Ctx = { params: Promise<{ path?: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx): Promise<Response> {
  const auth = await getAuthenticatedEiOrg();
  if (auth.status === "unauthenticated") {
    return Response.json({ detail: "Not authenticated" }, { status: 401 });
  }
  if (auth.status === "disabled") {
    return Response.json(
      { detail: "Enterprise Intelligence is not enabled for this account" },
      { status: 404 }
    );
  }

  const { path = [] } = await ctx.params;
  const leaf = path.join("/");
  if (leaf !== "tree" && leaf !== "file") {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }

  const target = new URL(`${AGENT_API_URL}/api/ei/workspace/${leaf}`);
  target.searchParams.set("org", auth.org);
  if (leaf === "file") {
    const filePath = req.nextUrl.searchParams.get("path") || "";
    target.searchParams.set("path", filePath);
  }

  const resp = await fetch(target.toString(), {
    headers: AGENT_API_TOKEN ? { "X-API-Key": AGENT_API_TOKEN } : {},
    cache: "no-store",
  });
  const text = await resp.text();
  try {
    return Response.json(JSON.parse(text), { status: resp.status });
  } catch {
    return new Response(text, { status: resp.status });
  }
}
