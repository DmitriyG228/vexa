"use client";

import { useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Loader2, Send, GitCommit, BookOpen } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

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
                <div className="prose prose-sm dark:prose-invert max-w-none [&_p]:my-1 [&_ul]:my-1 [&_ol]:my-1 [&_table]:my-2">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
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
