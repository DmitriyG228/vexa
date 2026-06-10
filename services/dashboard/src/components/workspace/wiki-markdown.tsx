"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Slug used to resolve [[Wikilinks]] against workspace file basenames. */
export function slugify(name: string): string {
  return name.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

/** Turn [[Entity Name]] / [[Target|alias]] into links on a wiki: protocol. */
export function linkifyWikilinks(text: string): string {
  return text.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, target, alias) => {
    const label = (alias || target).trim();
    return `[${label}](wiki:${encodeURIComponent(target.trim())})`;
  });
}

export type FileIndex = Record<string, string>;

export async function fetchFileIndex(): Promise<FileIndex> {
  try {
    const resp = await fetch("/api/workspace-ei/tree");
    if (!resp.ok) return {};
    const data = await resp.json();
    const idx: FileIndex = {};
    for (const f of data.files || []) {
      if (typeof f === "string" && f.endsWith(".md")) {
        const base = f.split("/").pop()!.replace(/\.md$/, "");
        idx[slugify(base)] = f;
      }
    }
    return idx;
  } catch {
    return {};
  }
}

const mdComponents = {
  h1: (p: React.ComponentProps<"h1">) => <h1 className="text-lg font-bold mt-3 mb-1" {...p} />,
  h2: (p: React.ComponentProps<"h2">) => <h2 className="text-base font-bold mt-3 mb-1" {...p} />,
  h3: (p: React.ComponentProps<"h3">) => <h3 className="text-sm font-semibold mt-2 mb-1" {...p} />,
  p: (p: React.ComponentProps<"p">) => <p className="my-1.5 leading-relaxed" {...p} />,
  ul: (p: React.ComponentProps<"ul">) => <ul className="list-disc pl-5 my-1.5 space-y-0.5" {...p} />,
  ol: (p: React.ComponentProps<"ol">) => <ol className="list-decimal pl-5 my-1.5 space-y-0.5" {...p} />,
  li: (p: React.ComponentProps<"li">) => <li className="leading-relaxed" {...p} />,
  strong: (p: React.ComponentProps<"strong">) => <strong className="font-semibold" {...p} />,
  blockquote: (p: React.ComponentProps<"blockquote">) => (
    <blockquote className="border-l-2 pl-3 my-2 italic opacity-80" {...p} />
  ),
  hr: () => <hr className="my-3 border-border" />,
  code: (p: React.ComponentProps<"code">) => (
    <code className="rounded bg-background/60 px-1 py-0.5 font-mono text-xs" {...p} />
  ),
  pre: (p: React.ComponentProps<"pre">) => (
    <pre className="rounded bg-background/60 p-2 my-2 overflow-x-auto font-mono text-xs" {...p} />
  ),
  table: (p: React.ComponentProps<"table">) => (
    <div className="overflow-x-auto my-2">
      <table className="text-xs border-collapse" {...p} />
    </div>
  ),
  th: (p: React.ComponentProps<"th">) => (
    <th className="border border-border px-2 py-1 text-left font-semibold bg-background/40" {...p} />
  ),
  td: (p: React.ComponentProps<"td">) => <td className="border border-border px-2 py-1 align-top" {...p} />,
};

/**
 * Markdown renderer with clickable [[wikilinks]].
 * `onOpenFile(path)` is called with the resolved workspace path.
 */
export function WikiMarkdown({
  text,
  fileIndex,
  onOpenFile,
}: {
  text: string;
  fileIndex: FileIndex;
  onOpenFile: (path: string) => void;
}) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      urlTransform={(u) => u}
      components={{
        ...mdComponents,
        a: (p: React.ComponentProps<"a">) => {
          const href = p.href || "";
          if (href.startsWith("wiki:")) {
            const name = decodeURIComponent(href.slice(5));
            const path = fileIndex[slugify(name)];
            return (
              <a
                role="link"
                className={
                  path
                    ? "underline underline-offset-2 cursor-pointer text-primary font-medium"
                    : "underline decoration-dotted underline-offset-2 opacity-70 cursor-default"
                }
                onClick={(e) => {
                  e.preventDefault();
                  if (path) onOpenFile(path);
                }}
              >
                {p.children}
              </a>
            );
          }
          return (
            <a className="underline underline-offset-2" target="_blank" rel="noreferrer" {...p} />
          );
        },
      }}
    >
      {linkifyWikilinks(text)}
    </ReactMarkdown>
  );
}
