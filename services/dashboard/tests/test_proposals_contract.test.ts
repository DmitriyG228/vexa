/**
 * Pack ei-review-ui (issue #25) — API-driven contract checks.
 *
 * Runs the dashboard's proposals client against the COMMITTED mock of the
 * frozen sign-API contract v1 (tests/mock-sign-api.mjs) — fully standalone,
 * no agent-api required. Proves:
 *   - list-live:       open proposals appear; signed/rejected ones disappear
 *   - approve-merges:  sign returns {merged, merge_commit}; repeat sign -> 409
 *   - reject-closes:   reject requires a note; note is persisted; repeat -> 409
 *   - tenant-scoped:   org A cannot list, diff, sign, or reject org B proposals
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { createProposalsClient, ProposalsApiError } from "@/lib/proposals";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — committed contract mock, plain ESM
import { startMockSignApi } from "./mock-sign-api.mjs";

const API_KEY = "test-token";

interface Mock {
  baseUrl: string;
  state: { proposals: Map<string, Record<string, unknown>> };
  close: () => Promise<void>;
}

let mock: Mock;

function clientFor(org: string) {
  return createProposalsClient({
    baseUrl: mock.baseUrl,
    org,
    headers: { "X-API-Key": API_KEY },
  });
}

beforeEach(async () => {
  mock = (await startMockSignApi({ apiKey: API_KEY })) as unknown as Mock;
});

afterEach(async () => {
  await mock.close();
});

describe("auth (agent-api require_api_key parity)", () => {
  it("rejects a missing/wrong API key with 403", async () => {
    const bad = createProposalsClient({ baseUrl: mock.baseUrl, org: "org-a" });
    await expect(bad.list()).rejects.toMatchObject({ status: 403 });
  });
});

describe("prove: list-live", () => {
  it("lists only the org's open proposals in contract shape", async () => {
    const list = await clientFor("org-a").list();
    expect(list.map((p) => p.id).sort()).toEqual(["prop_a1", "prop_a2"]);
    for (const p of list) {
      expect(Object.keys(p).sort()).toEqual([
        "branch",
        "created_at",
        "files_changed",
        "id",
        "meeting_id",
        "meeting_title",
        "summary",
      ]);
    }
  });

  it("signed and rejected proposals disappear from the list", async () => {
    const a = clientFor("org-a");
    await a.sign("prop_a1");
    await a.reject("prop_a2", "not relevant");
    expect(await a.list()).toEqual([]);
  });
});

describe("prove: approve-merges", () => {
  it("sign merges and returns the merge commit", async () => {
    const res = await clientFor("org-a").sign("prop_a1");
    expect(res.merged).toBe(true);
    expect(res.merge_commit).toMatch(/^[0-9a-f]{40}$/);
  });

  it("repeat sign is a 409, and sign-after-reject is a 409 (idempotent in effect)", async () => {
    const a = clientFor("org-a");
    await a.sign("prop_a1");
    await expect(a.sign("prop_a1")).rejects.toMatchObject({ status: 409 });
    await a.reject("prop_a2", "no");
    await expect(a.sign("prop_a2")).rejects.toMatchObject({ status: 409 });
  });
});

describe("prove: reject-closes", () => {
  it("requires a non-empty note", async () => {
    const a = clientFor("org-a");
    await expect(a.reject("prop_a1", "")).rejects.toBeInstanceOf(ProposalsApiError);
    // still open afterwards
    expect((await a.list()).some((p) => p.id === "prop_a1")).toBe(true);
  });

  it("closes with the note persisted", async () => {
    const a = clientFor("org-a");
    const res = await a.reject("prop_a1", "duplicate of an existing entity page");
    expect(res.closed).toBe(true);
    // note persisted — inspect via the mock's internal surface
    const raw = await fetch(`${mock.baseUrl}/internal/proposals/prop_a1`, {
      headers: { "X-API-Key": API_KEY },
    }).then((r) => r.json());
    expect(raw.status).toBe("rejected");
    expect(raw.note).toBe("duplicate of an existing entity page");
  });
});

describe("prove: diff endpoint shape", () => {
  it("returns per-file before/after/patch with added|modified status", async () => {
    const diff = await clientFor("org-a").diff("prop_a1");
    expect(diff.proposal_id).toBe("prop_a1");
    expect(diff.files.length).toBeGreaterThanOrEqual(2);
    const added = diff.files.find((f) => f.status === "added");
    const modified = diff.files.find((f) => f.status === "modified");
    expect(added?.before).toBe("");
    expect(added?.after).toContain("# Pricing review with Acme Corp");
    expect(modified?.before).not.toBe(modified?.after);
    expect(added?.patch).toContain("+++");
  });
});

describe("prove: tenant-scoped", () => {
  it("org A cannot see org B proposals in the list", async () => {
    const list = await clientFor("org-a").list();
    expect(list.some((p) => p.id === "prop_b1")).toBe(false);
  });

  it("org A gets 404 (not 403) on org B diff/sign/reject — no existence oracle", async () => {
    const a = clientFor("org-a");
    await expect(a.diff("prop_b1")).rejects.toMatchObject({ status: 404 });
    await expect(a.sign("prop_b1")).rejects.toMatchObject({ status: 404 });
    await expect(a.reject("prop_b1", "sneaky")).rejects.toMatchObject({ status: 404 });
    // org B proposal untouched
    const b = await clientFor("org-b").list();
    expect(b.map((p) => p.id)).toEqual(["prop_b1"]);
  });
});
