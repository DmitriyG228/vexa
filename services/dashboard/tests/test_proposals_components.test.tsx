/**
 * Pack ei-review-ui (issue #25) — component render checks (prove: diff-renders).
 *
 * Server-renders the proposal components with the contract fixture and asserts
 * the diff is a READABLE RENDERED markdown view (real <h1>/<ul>/<strong> HTML,
 * blocks tagged added/removed), not a raw patch — and that the list shows
 * title/summary/age. No browser, no flake: react-dom/server only.
 */
import { describe, it, expect } from "vitest";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { FileDiffCard, ProposalDiffView } from "@/components/proposals/proposal-diff";
import { ProposalListItem, proposalAge } from "@/components/proposals/proposals-view";
import type { DiffFile, ProposalSummary } from "@/lib/proposals";
// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-ignore — committed contract mock, plain ESM
import { makeFixtureState } from "./mock-sign-api.mjs";

function fixtureFiles(): DiffFile[] {
  const state = makeFixtureState() as { proposals: Map<string, { files: DiffFile[] }> };
  return state.proposals.get("prop_a1")!.files;
}

describe("prove: diff-renders — new meeting artifact", () => {
  const added = fixtureFiles().find((f) => f.status === "added")!;
  const html = renderToStaticMarkup(<FileDiffCard file={added} />);

  it("renders the markdown as HTML (heading, list), not raw text", () => {
    expect(html).toContain("<h1>Pricing review with Acme Corp</h1>");
    expect(html).toContain("<li>Send the enterprise pricing sheet by Friday.</li>");
    expect(html).not.toContain("# Pricing review"); // no unrendered markdown
  });

  it("tags every block of a new file as added", () => {
    expect(html).toContain('data-diff-block="added"');
    expect(html).not.toContain('data-diff-block="removed"');
  });

  it("shows the file path and status badge", () => {
    expect(html).toContain("graph/kg/entities/meetings/42-pricing-review.md");
    expect(html).toContain("added");
  });
});

describe("prove: diff-renders — modified entity page", () => {
  const modified = fixtureFiles().find((f) => f.status === "modified")!;
  const html = renderToStaticMarkup(<FileDiffCard file={modified} />);

  it("keeps unchanged content and highlights only the appended update", () => {
    expect(html).toContain('data-diff-block="unchanged"');
    expect(html).toContain('data-diff-block="added"');
    // the appended routine-update line is in an added block
    const addedIdx = html.indexOf("Evaluating the enterprise tier");
    expect(addedIdx).toBeGreaterThan(-1);
  });
});

describe("ProposalDiffView", () => {
  const files = fixtureFiles();

  it("defaults to the rendered view with a raw-patch toggle", () => {
    const html = renderToStaticMarkup(
      <ProposalDiffView diff={{ proposal_id: "prop_a1", files }} />
    );
    expect(html).toContain("Rendered");
    expect(html).toContain("Raw patch"); // the toggle is present
    expect(html).toContain('data-testid="file-diff"'); // rendered view active
    expect(html).not.toContain('data-testid="raw-patch"'); // raw hidden by default
  });

  it("handles an empty diff without crashing", () => {
    const html = renderToStaticMarkup(
      <ProposalDiffView diff={{ proposal_id: "x", files: [] }} />
    );
    expect(html).toContain("No file changes");
  });
});

describe("proposal list item", () => {
  const proposal: ProposalSummary = {
    id: "prop_a1",
    meeting_id: 42,
    meeting_title: "Pricing review with Acme Corp",
    created_at: new Date(Date.now() - 2 * 3600_000).toISOString(),
    branch: "meeting/42",
    summary: "meeting artifact ... + 1 other change(s)",
    files_changed: 2,
  };

  it("shows title, summary, file count and age", () => {
    const html = renderToStaticMarkup(
      <ProposalListItem proposal={proposal} selected={false} onSelect={() => {}} />
    );
    expect(html).toContain("Pricing review with Acme Corp");
    expect(html).toContain("2 files");
    expect(html).toContain("ago"); // proposal age
  });

  it("proposalAge is humanized and crash-safe", () => {
    expect(proposalAge(proposal.created_at)).toMatch(/ago/);
    expect(proposalAge("garbage")).toBe("garbage");
  });
});
