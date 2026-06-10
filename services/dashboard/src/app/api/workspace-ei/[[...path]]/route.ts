/**
 * Dashboard proxy for the EI org-workspace API.
 *
 * GET  /api/workspace-ei/tree             -> file list on workspace main
 * GET  /api/workspace-ei/file?path=...    -> one file's content
 * POST /api/workspace-ei/chat {message}   -> one agent chat turn (auto-commit)
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
  if (leaf !== "tree" && leaf !== "file" && leaf !== "sessions") {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }

  const target =
    leaf === "sessions"
      ? new URL(`${AGENT_API_URL}/api/ei/chat/sessions`)
      : new URL(`${AGENT_API_URL}/api/ei/workspace/${leaf}`);
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

export async function POST(req: NextRequest, ctx: Ctx): Promise<Response> {
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
  if (leaf !== "chat" && leaf !== "chat/stream" && leaf !== "sessions/rename") {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  let body: { message?: string; session_id?: string; title?: string };
  try {
    body = await req.json();
  } catch {
    return Response.json({ detail: "Invalid JSON" }, { status: 400 });
  }
  let target: URL;
  let payload: Record<string, unknown>;
  if (leaf === "chat" || leaf === "chat/stream") {
    const message = (body.message || "").trim();
    if (!message) {
      return Response.json({ detail: "message required" }, { status: 400 });
    }
    target = new URL(`${AGENT_API_URL}/api/ei/${leaf === "chat/stream" ? "chat/stream" : "chat"}`);
    // user_id resolved server-side from the session — never client-supplied
    payload = { message, user_id: auth.userId, session_id: body.session_id || null };
  } else {
    target = new URL(`${AGENT_API_URL}/api/ei/chat/sessions/rename`);
    payload = { session_id: body.session_id || "", title: body.title || "" };
  }
  target.searchParams.set("org", auth.org);
  const resp = await fetch(target.toString(), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(AGENT_API_TOKEN ? { "X-API-Key": AGENT_API_TOKEN } : {}),
    },
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (leaf === "chat/stream") {
    return new Response(resp.body, {
      status: resp.status,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
      },
    });
  }
  const text = await resp.text();
  try {
    return Response.json(JSON.parse(text), { status: resp.status });
  } catch {
    return new Response(text, { status: resp.status });
  }
}
