/**
 * Pack ei-review-ui (issue #25) — block-level markdown diff unit tests
 * (underpins prove: diff-renders).
 */
import { describe, it, expect } from "vitest";
import { diffMarkdown, diffStats, splitMarkdownBlocks } from "@/lib/markdown-diff";

describe("splitMarkdownBlocks", () => {
  it("splits on blank lines and drops empties", () => {
    expect(splitMarkdownBlocks("# A\n\npara one\n\n\n- li1\n- li2\n")).toEqual([
      "# A",
      "para one",
      "- li1\n- li2",
    ]);
  });

  it("handles empty and CRLF input", () => {
    expect(splitMarkdownBlocks("")).toEqual([]);
    expect(splitMarkdownBlocks("a\r\n\r\nb")).toEqual(["a", "b"]);
  });
});

describe("diffMarkdown", () => {
  it("marks every block added for a new file", () => {
    const blocks = diffMarkdown("", "# Title\n\nBody.");
    expect(blocks).toEqual([
      { type: "added", text: "# Title" },
      { type: "added", text: "Body." },
    ]);
  });

  it("detects an appended block (the EI routine-update append shape)", () => {
    const before = "# Acme\n\n- 2026-05-01 first contact";
    const after = "# Acme\n\n- 2026-05-01 first contact\n\n- 2026-06-09 pricing review";
    expect(diffMarkdown(before, after)).toEqual([
      { type: "unchanged", text: "# Acme" },
      { type: "unchanged", text: "- 2026-05-01 first contact" },
      { type: "added", text: "- 2026-06-09 pricing review" },
    ]);
  });

  it("represents an edited block as removed + added", () => {
    const blocks = diffMarkdown("# A\n\nold text", "# A\n\nnew text");
    expect(blocks).toEqual([
      { type: "unchanged", text: "# A" },
      { type: "removed", text: "old text" },
      { type: "added", text: "new text" },
    ]);
  });

  it("marks every block removed for a deleted document", () => {
    expect(diffMarkdown("a\n\nb", "")).toEqual([
      { type: "removed", text: "a" },
      { type: "removed", text: "b" },
    ]);
  });

  it("identical documents diff to all-unchanged", () => {
    const blocks = diffMarkdown("x\n\ny", "x\n\ny");
    expect(blocks.every((b) => b.type === "unchanged")).toBe(true);
    expect(diffStats(blocks)).toEqual({ added: 0, removed: 0 });
  });
});
