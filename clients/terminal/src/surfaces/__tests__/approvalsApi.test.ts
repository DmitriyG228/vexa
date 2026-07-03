/** Isolation harness — Approvals data-access. Asserts every call hits the ONE gateway edge under
 *  /api/proposals* with NO `subject` (scope is server-derived — P20), that a backend error is
 *  FAIL-LOUD (throws, never a silent empty queue — P18), and that the client half of the structural
 *  gate holds: decideBatch REFUSES an id last seen as L3 BEFORE any request (defense in depth —
 *  agent-api answers the same batch with a 403). */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { listProposals, getProposal, decideBatch, approveOne, rejectOne } from "../approvalsApi";
import { ApiError } from "../apiClient";

const l2 = (id: string) => ({
  id, subject: "u_jane", level: "L2", action: "comment", status: "pending",
  routine: { id: "rt_triage", name: "Issue triage", declared_access: "L2" },
  target: { provider: "github", repo: "vexa-ai/vexa", kind: "issue", number: 512 },
  payload: { comment: "Duplicate of #498." }, rationale: "same stack trace", created_at: "2026-06-30T08:12:00Z",
});
const l3 = (id: string) => ({
  ...l2(id), level: "L3", action: "open_pr",
  routine: { id: "rt_docs", name: "Docs drift fixer", declared_access: "L3" },
  target: { provider: "github", repo: "vexa-ai/vexa", kind: "branch", ref: "docs/fix" },
  payload: { branch: "docs/fix", base: "main", patches: [{ path: "docs/q.md", diff: "@@ -1 +1 @@\n-a\n+b" }] },
});

let fetchMock: ReturnType<typeof vi.fn>;
const lastUrl = () => String(fetchMock.mock.calls.at(-1)![0]);
const lastInit = () => (fetchMock.mock.calls.at(-1)![1] ?? {}) as RequestInit;
const lastBody = () => JSON.parse(String(lastInit().body));
const respond = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as unknown as Response;

beforeEach(() => {
  fetchMock = vi.fn(async () => respond({}));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => vi.restoreAllMocks());

describe("approvalsApi — scoped (no subject) + fail-loud", () => {
  it("listProposals GETs /api/proposals?status=pending with no subject, returns the groups", async () => {
    fetchMock.mockResolvedValueOnce(respond({ status: "pending", groups: [
      { routine: { id: "rt_triage", name: "Issue triage" }, level: "L2", batch_approvable: true, proposals: [l2("prop_aa01")] },
    ] }));
    const groups = await listProposals();
    expect(groups).toHaveLength(1);
    expect(groups[0].batch_approvable).toBe(true);
    expect(groups[0].proposals[0].id).toBe("prop_aa01");
    expect(lastUrl()).toBe("/api/proposals?status=pending");
    expect(lastUrl()).not.toContain("subject");
  });
  it("getProposal GETs /api/proposals/{id}, no subject", async () => {
    fetchMock.mockResolvedValueOnce(respond(l2("prop_aa02")));
    expect((await getProposal("prop_aa02")).action).toBe("comment");
    expect(lastUrl()).toBe("/api/proposals/prop_aa02");
    expect(lastUrl()).not.toContain("subject");
  });
  it("decideBatch POSTs /api/proposals/decide with exactly {ids, approve, note} — no subject", async () => {
    fetchMock.mockResolvedValueOnce(respond({ approve: true, decided: [l2("prop_aa03"), l2("prop_aa04")] }));
    const decided = await decideBatch(["prop_aa03", "prop_aa04"], true, "triage batch looks right");
    expect(decided).toHaveLength(2);
    expect(lastUrl()).toBe("/api/proposals/decide");
    expect(lastInit().method).toBe("POST");
    expect(lastBody()).toEqual({ ids: ["prop_aa03", "prop_aa04"], approve: true, note: "triage batch looks right" });
  });
  it("decideBatch without a note sends no note key (the contract's optional field stays absent)", async () => {
    fetchMock.mockResolvedValueOnce(respond({ approve: false, decided: [l2("prop_aa05")] }));
    await decideBatch(["prop_aa05"], false);
    expect(lastBody()).toEqual({ ids: ["prop_aa05"], approve: false });
  });
  it("approveOne POSTs /api/proposals/{id}/approve with {note}", async () => {
    fetchMock.mockResolvedValueOnce(respond({ ...l2("prop_aa06"), status: "approved" }));
    expect((await approveOne("prop_aa06", "ok")).status).toBe("approved");
    expect(lastUrl()).toBe("/api/proposals/prop_aa06/approve");
    expect(lastInit().method).toBe("POST");
    expect(lastBody()).toEqual({ note: "ok" });
  });
  it("rejectOne POSTs /api/proposals/{id}/reject (empty body object when no note)", async () => {
    fetchMock.mockResolvedValueOnce(respond({ ...l2("prop_aa07"), status: "rejected" }));
    expect((await rejectOne("prop_aa07")).status).toBe("rejected");
    expect(lastUrl()).toBe("/api/proposals/prop_aa07/reject");
    expect(lastBody()).toEqual({});
  });
  it("FAIL-LOUD: a backend error throws ApiError (never a silent empty queue)", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 502, json: async () => ({ detail: "upstream" }) } as unknown as Response);
    await expect(listProposals()).rejects.toBeInstanceOf(ApiError);
  });
  it("FAIL-LOUD: a network failure throws (not [])", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("offline"));
    await expect(listProposals()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("approvalsApi — the L3-never-in-a-batch gate (client half, defense in depth)", () => {
  it("decideBatch refuses an id listProposals saw as L3 — BEFORE any request", async () => {
    fetchMock.mockResolvedValueOnce(respond({ status: "pending", groups: [
      { routine: { id: "rt_docs" }, level: "L3", batch_approvable: false, proposals: [l3("prop_bb01")] },
    ] }));
    await listProposals();
    const calls = fetchMock.mock.calls.length;
    await expect(decideBatch(["prop_bb01"], true)).rejects.toThrow(/L3.*per-action/);
    expect(fetchMock.mock.calls.length).toBe(calls);  // the refusal never hit the network
  });
  it("decideBatch refuses a MIXED batch when getProposal cached the L3 level", async () => {
    fetchMock.mockResolvedValueOnce(respond(l3("prop_bb02")));
    await getProposal("prop_bb02");
    const calls = fetchMock.mock.calls.length;
    await expect(decideBatch(["prop_aa03", "prop_bb02"], true)).rejects.toThrow("prop_bb02 is L3");
    expect(fetchMock.mock.calls.length).toBe(calls);
  });
  it("an id never seen passes through — the server's 403 is the authoritative gate", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 403, json: async () => ({ detail: "prop_cc01 is L3 (open_pr) — per-action approval only, never a batch" }) } as unknown as Response);
    await expect(decideBatch(["prop_cc01"], true)).rejects.toBeInstanceOf(ApiError);
    expect(lastUrl()).toBe("/api/proposals/decide");
  });
});
