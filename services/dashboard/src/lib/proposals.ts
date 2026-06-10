/**
 * Sign-API contract v1 client (pack ei-review-ui, issue #25).
 *
 * The frozen contract (P2-signed on issue #24) is the ONLY backend coupling:
 *
 *   GET  /api/proposals?org=<org_id>       -> 200 [{id, meeting_id, meeting_title,
 *                                                   created_at, branch, summary, files_changed}]
 *   GET  /api/proposals/{id}/diff          -> 200 {proposal_id, files: [{path,
 *                                                   status: added|modified, before, after, patch}]}
 *   POST /api/proposals/{id}/sign          -> 200 {merged: true, merge_commit}
 *                                             (idempotent; 409 if already merged/rejected)
 *   POST /api/proposals/{id}/reject {note} -> 200 {closed: true}  (note required)
 *
 * This module is pure (no Next.js imports) so it is shared by the browser UI,
 * the server-side proxy route, and the standalone contract tests that run
 * against the committed mock (tests/mock-sign-api.mjs).
 */

export interface ProposalSummary {
  id: string;
  meeting_id: number | string;
  meeting_title: string;
  created_at: string;
  branch: string;
  summary: string;
  files_changed: number;
}

export interface DiffFile {
  path: string;
  status: "added" | "modified";
  before: string;
  after: string;
  patch: string;
}

export interface ProposalDiff {
  proposal_id: string;
  files: DiffFile[];
}

export interface SignResult {
  merged: boolean;
  merge_commit: string;
}

export interface RejectResult {
  closed: boolean;
}

export class ProposalsApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public details?: unknown
  ) {
    super(message);
    this.name = "ProposalsApiError";
  }
}

async function asJson<T>(resp: Response): Promise<T> {
  const text = await resp.text();
  let parsed: unknown;
  try {
    parsed = text ? JSON.parse(text) : undefined;
  } catch {
    parsed = text;
  }
  if (!resp.ok) {
    let message = `Request failed (${resp.status})`;
    if (parsed && typeof parsed === "object") {
      const obj = parsed as Record<string, unknown>;
      if (typeof obj.detail === "string") message = obj.detail;
      else if (typeof obj.error === "string") message = obj.error;
    } else if (typeof parsed === "string" && parsed) {
      message = parsed;
    }
    throw new ProposalsApiError(message, resp.status, parsed);
  }
  return parsed as T;
}

export interface ProposalsClientOptions {
  /** Base URL up to (not including) `/api/proposals`, e.g. "" for same-origin. */
  baseUrl: string;
  /** Extra headers (e.g. X-API-Key when talking to agent-api directly). */
  headers?: Record<string, string>;
  /** Org query param — only used when talking to agent-api directly.
   *  The dashboard proxy injects the org server-side and ignores client input. */
  org?: string;
  fetchImpl?: typeof fetch;
}

export interface ProposalsClient {
  list(): Promise<ProposalSummary[]>;
  diff(id: string): Promise<ProposalDiff>;
  sign(id: string): Promise<SignResult>;
  reject(id: string, note: string): Promise<RejectResult>;
}

export function createProposalsClient(opts: ProposalsClientOptions): ProposalsClient {
  const f = opts.fetchImpl ?? fetch;
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const orgQs = opts.org ? `?org=${encodeURIComponent(opts.org)}` : "";
  const base = `${opts.baseUrl}/api/proposals`;

  return {
    async list() {
      return asJson<ProposalSummary[]>(await f(`${base}${orgQs}`, { headers }));
    },
    async diff(id: string) {
      return asJson<ProposalDiff>(
        await f(`${base}/${encodeURIComponent(id)}/diff${orgQs}`, { headers })
      );
    },
    async sign(id: string) {
      return asJson<SignResult>(
        await f(`${base}/${encodeURIComponent(id)}/sign${orgQs}`, {
          method: "POST",
          headers,
        })
      );
    },
    async reject(id: string, note: string) {
      return asJson<RejectResult>(
        await f(`${base}/${encodeURIComponent(id)}/reject${orgQs}`, {
          method: "POST",
          headers,
          body: JSON.stringify({ note }),
        })
      );
    },
  };
}

/** Same sanitization as agent-api lineage.sanitize_org_id (lowercased,
 *  non [a-z0-9_-] runs collapsed to "-", trimmed; unusable -> null). */
export function sanitizeOrgId(raw: string): string | null {
  const org = String(raw)
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return org || null;
}

/** Allowed sub-paths the dashboard proxy will forward: list, {id}/diff, {id}/sign, {id}/reject. */
export function parseProposalsPath(
  segments: string[]
): { kind: "list" } | { kind: "diff" | "sign" | "reject"; id: string } | null {
  if (segments.length === 0) return { kind: "list" };
  if (segments.length === 2) {
    const [id, action] = segments;
    if (!id) return null;
    if (action === "diff" || action === "sign" || action === "reject") {
      return { kind: action, id };
    }
  }
  return null;
}

/**
 * Build the upstream agent-api URL for a proxied proposals request.
 *
 * Tenant-scoping invariant: any client-supplied `org` query param is DISCARDED;
 * the org resolved from the authenticated session is the only one forwarded.
 */
export function buildProposalsTarget(
  agentApiUrl: string,
  segments: string[],
  resolvedOrg: string,
  clientSearch?: string
): string | null {
  const parsed = parseProposalsPath(segments);
  if (!parsed) return null;
  const params = new URLSearchParams(clientSearch || "");
  params.delete("org"); // never trust the client's org
  params.set("org", resolvedOrg);
  const path =
    parsed.kind === "list" ? "" : `/${encodeURIComponent(parsed.id)}/${parsed.kind}`;
  return `${agentApiUrl}/api/proposals${path}?${params.toString()}`;
}
