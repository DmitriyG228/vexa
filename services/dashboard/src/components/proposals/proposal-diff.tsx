"use client";

/**
 * Rendered markdown diff for one proposal (issue #25, prove: diff-renders).
 *
 * Default view: per-file block-level markdown diff — every changed chunk is
 * rendered as markdown (legible), tinted green (added) / red (removed).
 * Toggle: the unified raw patch straight from the sign API.
 */

import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { FilePlus2, FilePen } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { diffMarkdown, diffStats, type DiffBlock } from "@/lib/markdown-diff";
import type { DiffFile, ProposalDiff } from "@/lib/proposals";

function MarkdownBlock({ block }: { block: DiffBlock }) {
  return (
    <div
      data-diff-block={block.type}
      className={cn(
        "px-3 py-1.5 border-l-2",
        block.type === "added" &&
          "border-green-500 bg-green-500/10 dark:bg-green-500/15",
        block.type === "removed" &&
          "border-red-500 bg-red-500/10 dark:bg-red-500/15 opacity-75 [&_*]:line-through",
        block.type === "unchanged" && "border-transparent"
      )}
    >
      <div className="prose prose-sm dark:prose-invert max-w-none [&_h1]:text-lg [&_h1]:font-semibold [&_h2]:text-base [&_h2]:font-semibold [&_h3]:text-sm [&_h3]:font-semibold [&_p]:my-1 [&_ul]:my-1 [&_li]:my-0">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.text}</ReactMarkdown>
      </div>
    </div>
  );
}

export function FileDiffCard({ file }: { file: DiffFile }) {
  const blocks = useMemo(
    () => diffMarkdown(file.status === "added" ? "" : file.before, file.after),
    [file]
  );
  const stats = useMemo(() => diffStats(blocks), [blocks]);
  const Icon = file.status === "added" ? FilePlus2 : FilePen;

  return (
    <div data-testid="file-diff" className="rounded-lg border overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 bg-muted/50 border-b text-sm">
        <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-mono text-xs truncate flex-1">{file.path}</span>
        <Badge
          variant="outline"
          className={cn(
            file.status === "added"
              ? "text-green-600 border-green-600/40"
              : "text-amber-600 border-amber-600/40"
          )}
        >
          {file.status}
        </Badge>
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          +{stats.added} −{stats.removed}
        </span>
      </div>
      <div className="divide-y divide-border/40">
        {blocks.map((block, i) => (
          <MarkdownBlock key={i} block={block} />
        ))}
      </div>
    </div>
  );
}

function RawPatch({ files }: { files: DiffFile[] }) {
  return (
    <pre
      data-testid="raw-patch"
      className="rounded-lg border bg-muted/30 p-3 text-xs font-mono overflow-x-auto whitespace-pre-wrap"
    >
      {files.map((f) => f.patch).join("\n") || "(empty patch)"}
    </pre>
  );
}

export function ProposalDiffView({ diff }: { diff: ProposalDiff }) {
  const [view, setView] = useState<"rendered" | "raw">("rendered");

  if (!diff.files.length) {
    return (
      <p className="text-sm text-muted-foreground py-4">
        No file changes to display for this proposal.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      <Tabs value={view} onValueChange={(v) => setView(v as "rendered" | "raw")}>
        <TabsList>
          <TabsTrigger value="rendered">Rendered</TabsTrigger>
          <TabsTrigger value="raw">Raw patch</TabsTrigger>
        </TabsList>
      </Tabs>
      {view === "rendered" ? (
        <div className="space-y-3">
          {diff.files.map((file) => (
            <FileDiffCard key={file.path} file={file} />
          ))}
        </div>
      ) : (
        <RawPatch files={diff.files} />
      )}
    </div>
  );
}
