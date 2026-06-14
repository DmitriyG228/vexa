"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Loader2, Send, GitCommit, BookOpen, Paperclip, X } from "lucide-react";
import { useWorkspaceUpload } from "./use-upload";
import { WikiMarkdown, FileIndex, fetchFileIndex } from "./wiki-markdown";
import { FilePanel } from "./file-panel";

interface ChatMsg {
  role: "user" | "agent";
  text: string;
  commit?: string | null;
  filesChanged?: number;
}

/**
 * EI knowledge-agent chat: ask questions, request research, record knowledge.
 * Three-pane behavior: chat in the center; clicking a [[wikilink]] opens the
 * file in a collapsible right panel (full-screen overlay on mobile). The agent
 * reads/writes the org workspace; every write is auto-committed to git.
 */
export function EiChat() {
  const searchParams = useSearchParams();
  const [sessionId, setSessionIdState] = useState<string | null>(null);
  const setSessionId = useCallback((sid: string | null) => {
    setSessionIdState(sid);
    try {
      if (sid) sessionStorage.setItem("ei-chat-session", sid);
      else sessionStorage.removeItem("ei-chat-session");
    } catch {}
  }, []);

  useEffect(() => {
    try {
      const saved = sessionStorage.getItem("ei-chat-session");
      if (saved) setSessionIdState(saved);
    } catch {}
  }, []);
  const [fileIndex, setFileIndex] = useState<FileIndex>({});
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [activity, setActivity] = useState<string[]>([]);
  const [preview, setPreview] = useState("");
  const [panelFile, setPanelFile] = useState<string | null>(null);
  const [attachments, setAttachments] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { upload, uploading } = useWorkspaceUpload("uploads");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, busy]);

  const loadIndex = useCallback(async () => {
    setFileIndex(await fetchFileIndex());
  }, []);

  useEffect(() => {
    loadIndex();
  }, [loadIndex]);

  useEffect(() => {
    const reset = () => {
      setMessages([]);
      setPanelFile(null);
      setSessionId(null);
    };
    window.addEventListener("ei-chat-new", reset);
    return () => window.removeEventListener("ei-chat-new", reset);
  }, []);

  // Restore a past session: /chat?session=<id> loads chats/<id>.md from the workspace.
  useEffect(() => {
    const sid = searchParams.get("session");
    if (!sid || sid === sessionId) return;
    fetch(`/api/workspace-ei/file?path=${encodeURIComponent(`chats/${sid}.md`)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => {
        const text: string = d.content || "";
        const parts = text.split(/^## (You|Agent) — .*$/m);
        const msgs: ChatMsg[] = [];
        for (let i = 1; i < parts.length; i += 2) {
          const role = parts[i] === "You" ? "user" : "agent";
          const body = (parts[i + 1] || "").trim();
          if (body) msgs.push({ role: role as ChatMsg["role"], text: body });
        }
        setMessages(msgs);
        setSessionId(sid);
        setPanelFile(null);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const handleFiles = async (files: FileList | File[]) => {
    const res = await upload(files);
    if (res?.uploaded?.length) {
      setAttachments((a) => [...a, ...res.uploaded]);
      loadIndex();
    }
  };

  const send = async () => {
    const baseMessage = input.trim();
    if ((!baseMessage && attachments.length === 0) || busy) return;
    const attachLine = attachments.length
      ? `\n\nAttached files in the workspace (read them if relevant): ${attachments
          .map((p) => `[[${p}]]`)
          .join(", ")}`
      : "";
    const message = (baseMessage || "(see attached files)") + attachLine;
    const display = baseMessage + (attachments.length ? `\n📎 ${attachments.length} file(s)` : "");
    setInput("");
    setAttachments([]);
    setMessages((m) => [...m, { role: "user", text: display }]);
    setBusy(true);
    setActivity([]);
    setPreview("");
    try {
      const resp = await fetch("/api/workspace-ei/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId }),
      });
      if (!resp.ok || !resp.body) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(
          (data as { detail?: string }).detail || `HTTP ${resp.status}`
        );
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let finished = false;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const frames = buf.split("\n\n");
        buf = frames.pop() || "";
        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          let ev: Record<string, unknown>;
          try {
            ev = JSON.parse(line.slice(6));
          } catch {
            continue;
          }
          if (ev.type === "status") {
            setActivity((a) => [...a, `· ${String(ev.text || "")}`]);
          } else if (ev.type === "tools") {
            const calls = (ev.calls as { name: string; args: string }[]) || [];
            setActivity((a) => [
              ...a,
              ...calls.map(
                (c) => `⚙ ${c.name}${c.args ? ` — ${c.args.slice(0, 80)}` : ""}`
              ),
            ]);
          } else if (ev.type === "tool_result") {
            const sn = String(ev.snippet || "").slice(0, 80);
            if (sn) setActivity((a) => [...a, `→ ${sn}`]);
          } else if (ev.type === "assistant") {
            setPreview(String(ev.text || ""));
          } else if (ev.type === "error") {
            throw new Error(String(ev.message || "agent error"));
          } else if (ev.type === "done") {
            finished = true;
            setMessages((m) => [
              ...m,
              {
                role: "agent",
                text: String(ev.reply || "(no reply)"),
                commit: (ev.commit as string) || null,
                filesChanged: (ev.files_changed as number) || 0,
              },
            ]);
            if (ev.session_id && ev.session_id !== sessionId)
              setSessionId(String(ev.session_id));
            if (ev.commit) loadIndex();
          }
        }
      }
      if (!finished) throw new Error("stream ended unexpectedly");
    } catch (e) {
      setMessages((m) => [
        ...m,
        { role: "agent", text: `Error: ${e instanceof Error ? e.message : String(e)}` },
      ]);
    } finally {
      setBusy(false);
      setActivity([]);
      setPreview("");
    }
  };

  return (
    <div className="flex h-full min-h-0 relative overflow-hidden">
      {/* Center: chat */}
      <div
        className={`flex flex-col flex-1 min-w-0 min-h-0 relative ${dragOver ? "ring-2 ring-primary ring-inset" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          if (e.dataTransfer.files?.length) handleFiles(e.dataTransfer.files);
        }}
      >
        {dragOver && (
          <div className="absolute inset-0 z-30 flex items-center justify-center bg-background/70 pointer-events-none">
            <div className="rounded-lg border-2 border-dashed border-primary px-6 py-4 text-sm font-medium">
              Drop files to attach
            </div>
          </div>
        )}
        <div className="flex items-center gap-2 px-4 py-2 border-b">
          <BookOpen className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">Knowledge Agent</span>
          <span className="text-xs text-muted-foreground hidden sm:inline">
            reads &amp; writes the workspace · every change is a git commit
          </span>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
          {messages.length === 0 && (
            <div className="text-center text-muted-foreground text-sm mt-12">
              <p>Ask about your meetings and knowledge base, or tell the agent</p>
              <p>to record, research, or restructure knowledge.</p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                  m.role === "user"
                    ? "bg-primary text-primary-foreground whitespace-pre-wrap"
                    : "bg-muted"
                }`}
              >
                {m.role === "agent" ? (
                  <WikiMarkdown text={m.text} fileIndex={fileIndex} onOpenFile={setPanelFile} />
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
            <div className="flex justify-start">
              <div className="max-w-[85%] rounded-lg bg-muted px-3 py-2 text-sm space-y-1">
                <div className="flex items-center gap-2 text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>The agent is working in the workspace…</span>
                </div>
                {activity.slice(-8).map((a, i) => (
                  <div key={i} className="font-mono text-xs text-muted-foreground truncate">
                    {a}
                  </div>
                ))}
                {preview && (
                  <div className="text-xs opacity-80 border-t pt-1 mt-1 whitespace-pre-wrap">
                    {preview.slice(0, 400)}
                  </div>
                )}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-3 pt-2">
            {attachments.map((a, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs"
              >
                <Paperclip className="h-3 w-3" />
                {a.split("/").pop()}
                <button
                  onClick={() => setAttachments((x) => x.filter((_, j) => j !== i))}
                  aria-label="Remove attachment"
                >
                  <X className="h-3 w-3 opacity-60 hover:opacity-100" />
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="border-t p-3 flex gap-2 items-end">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            className="hidden"
            onChange={(e) => {
              if (e.target.files?.length) handleFiles(e.target.files);
              e.target.value = "";
            }}
          />
          <Button
            variant="ghost"
            size="icon"
            className="self-end shrink-0"
            disabled={uploading}
            onClick={() => fileInputRef.current?.click()}
            aria-label="Attach files"
          >
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Paperclip className="h-4 w-4" />}
          </Button>
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
          <Button onClick={send} disabled={busy || (!input.trim() && attachments.length === 0)} className="self-end gap-1">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
            Send
          </Button>
        </div>
      </div>

      {/* Right: contextual file panel (overlay on mobile, column on desktop) */}
      {panelFile && (
        <div className="absolute inset-0 z-20 md:static md:z-auto md:w-[26rem] lg:w-[30rem] md:flex-shrink-0 h-full min-h-0 overflow-hidden border-l bg-background">
          <FilePanel
            path={panelFile}
            fileIndex={fileIndex}
            onNavigate={setPanelFile}
            onClose={() => setPanelFile(null)}
          />
        </div>
      )}
    </div>
  );
}
