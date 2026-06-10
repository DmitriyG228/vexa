"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Loader2, Send, GitCommit, BookOpen } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function slugify(name: string): string {
  return name.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

/** Turn [[Entity Name]] into markdown links on a custom wiki: protocol. */
function linkifyWikilinks(text: string): string {
  return text.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, target, alias) => {
    const label = (alias || target).trim();
    return `[${label}](wiki:${encodeURIComponent(target.trim())})`;
  });
}

const mdComponents = {
  h1: (p: React.ComponentProps<"h1">) => <h1 className="text-lg font-bold mt-3 mb-1" {...p} />,
  h2: (p: React.ComponentProps<"h2">) => <h2 className="text-base font-bold mt-3 mb-1" {...p} />,
  h3: (p: React.ComponentProps<"h3">) => <h3 className="text-sm font-semibold mt-2 mb-1" {...p} />,
  p: (p: React.ComponentProps<"p">) => <p className="my-1.5 leading-relaxed" {...p} />,
  ul: (p: React.ComponentProps<"ul">) => <ul className="list-disc pl-5 my-1.5 space-y-0.5" {...p} />,
  ol: (p: React.ComponentProps<"ol">) => <ol className="list-decimal pl-5 my-1.5 space-y-0.5" {...p} />,
  li: (p: React.ComponentProps<"li">) => <li className="leading-relaxed" {...p} />,
  a: (p: React.ComponentProps<"a">) => <a className="underline underline-offset-2" {...p} />,
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

interface ChatMsg {
  role: "user" | "agent";
  text: string;
  commit?: string | null;
  filesChanged?: number;
}

/**
 * EI knowledge-agent chat: ask questions, request research, record knowledge.
 * The agent reads/writes the org workspace; every write is auto-committed to
 * git (revert any time) — no proposal gate on this surface.
 */
export function EiChat() {
  const router = useRouter();
  const [fileIndex, setFileIndex] = useState<Record<string, string>>({});
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const loadIndex = useCallback(async () => {
    try {
      const resp = await fetch("/api/workspace-ei/tree");
      if (!resp.ok) return;
      const data = await resp.json();
      const idx: Record<string, string> = {};
      for (const f of data.files || []) {
        if (typeof f === "string" && f.endsWith(".md")) {
          const base = f.split("/").pop()!.replace(/\.md$/, "");
          idx[slugify(base)] = f;
        }
      }
      setFileIndex(idx);
    } catch {}
  }, []);

  useEffect(() => {
    loadIndex();
  }, [loadIndex]);

  const openWikilink = useCallback(
    (name: string) => {
      const path = fileIndex[slugify(name)];
      if (path) router.push(`/workspace?file=${encodeURIComponent(path)}`);
    },
    [fileIndex, router]
  );

  const send = async () => {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: message }]);
    setBusy(true);
    try {
      const resp = await fetch("/api/workspace-ei/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);
      setMessages((m) => [
        ...m,
        {
          role: "agent",
          text: data.reply || "(no reply)",
          commit: data.commit,
          filesChanged: data.files_changed,
        },
      ]);
      if (data.commit) loadIndex();
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "agent", text: `Error: ${e instanceof Error ? e.message : String(e)}` },
      ]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-4 py-2 border-b">
        <BookOpen className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-medium">Knowledge Agent</span>
        <span className="text-xs text-muted-foreground">
          reads &amp; writes the workspace · every change is a git commit
        </span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-muted-foreground text-sm mt-12">
            <p>Ask about your meetings and knowledge base, or tell the agent</p>
            <p>to record, research, or restructure knowledge.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
            <div
              className={`max-w-[80%] rounded-lg px-3 py-2 text-sm ${
                m.role === "user"
                  ? "bg-primary text-primary-foreground whitespace-pre-wrap"
                  : "bg-muted"
              }`}
            >
              {m.role === "agent" ? (
                <div className="max-w-none">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    urlTransform={(u) => u}
                    components={{
                      ...mdComponents,
                      a: (p: React.ComponentProps<"a">) => {
                        const href = p.href || "";
                        if (href.startsWith("wiki:")) {
                          const name = decodeURIComponent(href.slice(5));
                          const known = !!fileIndex[slugify(name)];
                          return (
                            <a
                              role="link"
                              className={
                                known
                                  ? "underline underline-offset-2 cursor-pointer text-primary font-medium"
                                  : "underline decoration-dotted underline-offset-2 opacity-70 cursor-default"
                              }
                              onClick={(e) => {
                                e.preventDefault();
                                if (known) openWikilink(name);
                              }}
                            >
                              {p.children}
                            </a>
                          );
                        }
                        return (
                          <a
                            className="underline underline-offset-2"
                            target="_blank"
                            rel="noreferrer"
                            {...p}
                          />
                        );
                      },
                    }}
                  >
                    {linkifyWikilinks(m.text)}
                  </ReactMarkdown>
                </div>
              ) : (
                m.text
              )}
              {m.commit && (
                <div className="mt-2 flex items-center gap-1 text-xs opacity-70">
                  <GitCommit className="h-3 w-3" />
                  <span>
                    {m.filesChanged} file{m.filesChanged === 1 ? "" : "s"} committed (
                    {m.commit.slice(0, 10)})
                  </span>
                </div>
              )}
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>The agent is working in the workspace…</span>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t p-3 flex gap-2">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask the knowledge agent… (Enter to send, Shift+Enter for newline)"
          rows={2}
          className="flex-1 resize-none rounded-md border bg-background p-2 text-sm focus:outline-none"
          disabled={busy}
        />
        <Button onClick={send} disabled={busy || !input.trim()} className="self-end gap-1">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          Send
        </Button>
      </div>
    </div>
  );
}
