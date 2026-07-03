/** approvalsApi — the Approvals surface's data-access (its clean SoC boundary), isolation-testable.
 *
 *  Every call goes to the ONE gateway edge under /api/proposals* (the Next proxy maps it to the
 *  gateway's /agent/proposals*, agent-api behind it) and carries NO `subject`: the gateway injects
 *  X-User-Id and agent-api derives the user's scope from it (P20). FAIL-LOUD (P18): a backend error
 *  or network failure THROWS (via apiClient) instead of being swallowed into an empty queue — the
 *  surface catches it and shows the error, so a failure is never hidden as "nothing to approve".
 *
 *  Shapes mirror proposal.v1 (core/agent/contracts/proposal.v1/proposal.schema.json): L2 annotate
 *  (comment|label|close|open_issue — batch-approvable), L3 mutate (push_branch|open_pr — per-action
 *  ONLY). Defense in depth: decideBatch() REFUSES any id whose last-seen level is L3 BEFORE hitting
 *  the network — the same structural gate agent-api enforces with a 403. */
import { getJson } from "./apiClient";

export type ProposalLevel = "L2" | "L3";
export type ProposalAction = "comment" | "label" | "close" | "open_issue" | "push_branch" | "open_pr";
export type ProposalStatus = "pending" | "approved" | "rejected" | "executed" | "failed" | "expired";

export interface ProposalRoutine { id?: string; name?: string; declared_access?: "L1" | "L2" | "L3" }
export interface ProposalTarget { provider: "github"; repo: string; kind?: "issue" | "pr" | "branch"; number?: number; ref?: string }
export interface ProposalPatch { path: string; diff?: string; content?: string }
export interface ProposalPayload {
  comment?: string; labels?: string[]; title?: string; body?: string;
  branch?: string; base?: string; patches?: ProposalPatch[];
}
export interface DecisionRecord { by: string; at: string; note?: string }

export interface Proposal {
  id: string;
  level: ProposalLevel;
  action: ProposalAction;
  target: ProposalTarget;
  payload: ProposalPayload;
  rationale: string;
  status: ProposalStatus;
  routine?: ProposalRoutine;
  decision?: DecisionRecord;
  created_at: string;
}

/** One (routine × level) group from GET /api/proposals — an L2 group is one batch-approve card. */
export interface ProposalGroup {
  routine: ProposalRoutine;
  level: ProposalLevel;
  batch_approvable: boolean;
  proposals: Proposal[];
}

// Last-seen level per proposal id (fed by every list/get) — the client half of the L3-never-in-a-batch
// gate. Unknown ids pass through: the server enforces the same rule authoritatively (403).
const levelCache = new Map<string, ProposalLevel>();
const remember = (p: Proposal): Proposal => { levelCache.set(p.id, p.level); return p; };

export async function listProposals(status: ProposalStatus = "pending"): Promise<ProposalGroup[]> {
  const data = await getJson<{ groups?: ProposalGroup[] }>(`/api/proposals?status=${encodeURIComponent(status)}`);
  return (data.groups ?? []).map((g) => ({ ...g, proposals: (g.proposals ?? []).map(remember) }));
}

export async function getProposal(id: string): Promise<Proposal> {
  return remember(await getJson<Proposal>(`/api/proposals/${encodeURIComponent(id)}`));
}

/** Batch decision — the L2 bulk-approve UX. REFUSES (throws, no request) any id last seen as L3:
 *  a mutation is approved one deliberate act at a time, never in bulk (mirrors agent-api's 403). */
export async function decideBatch(ids: string[], approve: boolean, note?: string): Promise<Proposal[]> {
  const l3 = ids.find((id) => levelCache.get(id) === "L3");
  if (l3) throw new Error(`${l3} is L3 — per-action approval only, never a batch`);
  const data = await getJson<{ decided?: Proposal[] }>(`/api/proposals/decide`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(note ? { ids, approve, note } : { ids, approve }),
  });
  return (data.decided ?? []).map(remember);
}

async function decideOne(id: string, verb: "approve" | "reject", note?: string): Promise<Proposal> {
  const p = await getJson<Proposal>(`/api/proposals/${encodeURIComponent(id)}/${verb}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(note ? { note } : {}),
  });
  return remember(p);
}

/** Per-action approval — the ONLY path an L3 (push_branch/open_pr) goes through (a single L2 works too). */
export const approveOne = (id: string, note?: string): Promise<Proposal> => decideOne(id, "approve", note);
export const rejectOne = (id: string, note?: string): Promise<Proposal> => decideOne(id, "reject", note);
