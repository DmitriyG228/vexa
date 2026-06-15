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
import { cookies } from "next/headers";
import { getAuthenticatedEiOrg } from "@/lib/ei-org";
import { getAuthCookieName } from "@/lib/auth-cookies";

const AGENT_API_URL = process.env.AGENT_API_URL || "http://localhost:8100";
const AGENT_API_TOKEN = process.env.AGENT_API_TOKEN || "";

type Ctx = { params: Promise<{ path?: string[] }> };

type MeetingRef = { id: number | string; title?: string; status?: string };

/**
 * Keep only the meeting refs the authenticated user actually owns. The agent-api
 * fetches transcripts by internal id with no per-user check, so an unverified id
 * would be an IDOR. We verify each id against the gateway's user-scoped
 * GET /bots/id/{id} using the caller's own token (cookie). id is the security
 * boundary; title/status are display-only and pass through.
 */
async function verifyMeetingRefs(refs: unknown): Promise<MeetingRef[]> {
  if (!Array.isArray(refs) || refs.length === 0) return [];
  const VEXA_API_URL = process.env.VEXA_API_URL;
  if (!VEXA_API_URL) return [];
  const token = (await cookies()).get(getAuthCookieName())?.value;
  if (!token) return [];
  const candidates = refs
    .filter((r): r is MeetingRef => !!r && typeof r === "object" && "id" in r)
    .slice(0, 8); // bound the per-turn verification fan-out
  const checked = await Promise.all(
    candidates.map(async (r): Promise<MeetingRef | null> => {
      try {
        const resp = await fetch(`${VEXA_API_URL}/bots/id/${encodeURIComponent(String(r.id))}`, {
          headers: { "X-API-Key": token },
          signal: AbortSignal.timeout(5000),
        });
        return resp.ok ? { id: r.id, title: r.title, status: r.status } : null;
      } catch {
        return null;
      }
    })
  );
  const verified: MeetingRef[] = [];
  for (const r of checked) if (r) verified.push(r);
  return verified;
}

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
  if (
    leaf !== "tree" &&
    leaf !== "file" &&
    leaf !== "sessions" &&
    leaf !== "git" &&
    leaf !== "attach"
  ) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }

  const target =
    leaf === "sessions"
      ? new URL(`${AGENT_API_URL}/api/ei/chat/sessions`)
      : leaf === "attach"
      ? new URL(`${AGENT_API_URL}/api/ei/chat/attach`)
      : leaf === "git"
      ? new URL(`${AGENT_API_URL}/api/ei/workspace/git`)
      : new URL(`${AGENT_API_URL}/api/ei/workspace/${leaf}`);
  target.searchParams.set("org", auth.org);
  if (leaf === "file") {
    const filePath = req.nextUrl.searchParams.get("path") || "";
    target.searchParams.set("path", filePath);
  }
  if (leaf === "attach") {
    target.searchParams.set("session", req.nextUrl.searchParams.get("session") || "");
  }

  const resp = await fetch(target.toString(), {
    headers: AGENT_API_TOKEN ? { "X-API-Key": AGENT_API_TOKEN } : {},
    cache: "no-store",
  });
  if (leaf === "attach") {
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
  if (leaf === "git/connect" || leaf === "git/disconnect" || leaf === "git/sync") {
    const target = new URL(`${AGENT_API_URL}/api/ei/workspace/${leaf}`);
    target.searchParams.set("org", auth.org);
    let payload: unknown = {};
    try { payload = await req.json(); } catch {}
    const resp = await fetch(target.toString(), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(AGENT_API_TOKEN ? { "X-API-Key": AGENT_API_TOKEN } : {}),
      },
      body: JSON.stringify(payload),
      cache: "no-store",
    });
    const t = await resp.text();
    try { return Response.json(JSON.parse(t), { status: resp.status }); }
    catch { return new Response(t, { status: resp.status }); }
  }
  if (leaf === "upload") {
    const target = new URL(`${AGENT_API_URL}/api/ei/workspace/upload`);
    target.searchParams.set("org", auth.org);
    const dir = req.nextUrl.searchParams.get("dir");
    if (dir) target.searchParams.set("dir", dir);
    const resp = await fetch(target.toString(), {
      method: "POST",
      headers: {
        ...(AGENT_API_TOKEN ? { "X-API-Key": AGENT_API_TOKEN } : {}),
        ...(req.headers.get("content-type")
          ? { "content-type": req.headers.get("content-type") as string }
          : {}),
      },
      body: await req.arrayBuffer(),
      cache: "no-store",
    });
    const t = await resp.text();
    try {
      return Response.json(JSON.parse(t), { status: resp.status });
    } catch {
      return new Response(t, { status: resp.status });
    }
  }
  if (leaf !== "chat" && leaf !== "chat/stream" && leaf !== "sessions/rename") {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  let body: { message?: string; session_id?: string; title?: string; meeting_refs?: unknown };
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
    // user_id resolved server-side from the session — never client-supplied;
    // meeting_refs verified against the user's own meetings (drops unowned ids).
    const meeting_refs = await verifyMeetingRefs(body.meeting_refs);
    payload = { message, user_id: auth.userId, session_id: body.session_id || null, meeting_refs };
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
