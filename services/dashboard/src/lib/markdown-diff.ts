/**
 * Block-level markdown diff (pack ei-review-ui, issue #25).
 *
 * Turns a (before, after) pair of markdown documents into a sequence of
 * markdown BLOCKS tagged unchanged/added/removed. Diffing at block level
 * (paragraphs, headings, list runs — split on blank lines) keeps every
 * chunk independently renderable as markdown, which is what makes the
 * "legible rendered view, not raw patch" possible.
 *
 * Pure module — unit-tested standalone (tests/test_markdown_diff.test.ts).
 */

export type DiffBlockType = "unchanged" | "added" | "removed";

export interface DiffBlock {
  type: DiffBlockType;
  /** Renderable markdown chunk. */
  text: string;
}

/** Split a markdown document into blocks on blank-line boundaries. */
export function splitMarkdownBlocks(text: string): string[] {
  if (!text) return [];
  const normalized = text.replace(/\r\n/g, "\n");
  return normalized
    .split(/\n{2,}/)
    .map((b) => b.replace(/^\n+|\n+$/g, ""))
    .filter((b) => b.trim().length > 0);
}

/** Longest-common-subsequence table over two block arrays. */
function lcsMatrix(a: string[], b: string[]): number[][] {
  const m = a.length;
  const n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  return dp;
}

/**
 * Diff two markdown documents into tagged renderable blocks.
 * An added file is simply diffMarkdown("", after) — every block "added".
 */
export function diffMarkdown(before: string, after: string): DiffBlock[] {
  const a = splitMarkdownBlocks(before);
  const b = splitMarkdownBlocks(after);
  const dp = lcsMatrix(a, b);
  const out: DiffBlock[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ type: "unchanged", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      out.push({ type: "removed", text: a[i] });
      i++;
    } else {
      out.push({ type: "added", text: b[j] });
      j++;
    }
  }
  while (i < a.length) out.push({ type: "removed", text: a[i++] });
  while (j < b.length) out.push({ type: "added", text: b[j++] });
  return out;
}

/** Summary counts for a diff — used for per-file badges. */
export function diffStats(blocks: DiffBlock[]): { added: number; removed: number } {
  let added = 0;
  let removed = 0;
  for (const blk of blocks) {
    if (blk.type === "added") added++;
    else if (blk.type === "removed") removed++;
  }
  return { added, removed };
}
