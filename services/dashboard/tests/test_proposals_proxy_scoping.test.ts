/**
 * Pack ei-review-ui (issue #25) — proxy-layer tenant scoping (prove: tenant-scoped).
 *
 * The dashboard proxy (src/app/api/proposals/[[...path]]/route.ts) builds its
 * upstream URL with buildProposalsTarget(): the org is ALWAYS the one resolved
 * from the authenticated session, and any client-supplied `org` query param is
 * discarded. These tests pin that invariant plus the allowed-path whitelist.
 */
import { describe, it, expect } from "vitest";
import {
  buildProposalsTarget,
  parseProposalsPath,
  sanitizeOrgId,
} from "@/lib/proposals";

const API = "http://agent-api:8000";

describe("parseProposalsPath whitelist", () => {
  it("accepts list, diff, sign, reject only", () => {
    expect(parseProposalsPath([])).toEqual({ kind: "list" });
    expect(parseProposalsPath(["p1", "diff"])).toEqual({ kind: "diff", id: "p1" });
    expect(parseProposalsPath(["p1", "sign"])).toEqual({ kind: "sign", id: "p1" });
    expect(parseProposalsPath(["p1", "reject"])).toEqual({ kind: "reject", id: "p1" });
  });

  it("rejects anything else (no open proxy)", () => {
    expect(parseProposalsPath(["p1"])).toBeNull();
    expect(parseProposalsPath(["p1", "delete"])).toBeNull();
    expect(parseProposalsPath(["p1", "diff", "extra"])).toBeNull();
    expect(parseProposalsPath(["", "sign"])).toBeNull();
  });
});

describe("org injection (tenant-scoped at the proxy)", () => {
  it("injects the session-resolved org on list", () => {
    const t = buildProposalsTarget(API, [], "org-a");
    expect(t).toBe(`${API}/api/proposals?org=org-a`);
  });

  it("DISCARDS a client-supplied org — user of org A cannot ask for org B", () => {
    const t = buildProposalsTarget(API, [], "org-a", "?org=org-b");
    expect(t).toContain("org=org-a");
    expect(t).not.toContain("org-b");
  });

  it("discards the client org on actions too", () => {
    for (const action of ["diff", "sign", "reject"]) {
      const t = buildProposalsTarget(API, ["prop_b1", action], "org-a", "?org=org-b&x=1");
      expect(t).toBe(`${API}/api/proposals/prop_b1/${action}?x=1&org=org-a`);
    }
  });

  it("URL-encodes hostile proposal ids", () => {
    const t = buildProposalsTarget(API, ["../admin", "sign"], "org-a");
    expect(t).toBe(`${API}/api/proposals/..%2Fadmin/sign?org=org-a`);
  });
});

describe("sanitizeOrgId (parity with agent-api lineage.sanitize_org_id)", () => {
  it("matches the backend behavior", () => {
    expect(sanitizeOrgId("Acme Corp!")).toBe("acme-corp");
    expect(sanitizeOrgId("  org_42  ")).toBe("org_42");
    expect(sanitizeOrgId("user-7")).toBe("user-7");
    expect(sanitizeOrgId("///")).toBeNull();
    expect(sanitizeOrgId("")).toBeNull();
  });
});
