"use client";

import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, GitBranch, Link2, Unlink, RefreshCw } from "lucide-react";

type GitStatus =
  | { connected: false }
  | {
      connected: true;
      remote_url: string;
      branch: string;
      mode: string;
      has_token: boolean;
      head: string | null;
    };

/**
 * Connect the org workspace to a git remote (BYOR or provision). Once connected,
 * the remote is the source of truth: every agent/chat/upload commit is pushed.
 */
export function GitConnect({ onChanged }: { onChanged?: () => void }) {
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [remoteUrl, setRemoteUrl] = useState("");
  const [branch, setBranch] = useState("main");
  const [token, setToken] = useState("");
  const [mode, setMode] = useState<"byor" | "provision">("byor");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/workspace-ei/git");
      if (r.ok) setStatus(await r.json());
    } catch {}
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const connect = async () => {
    setErr("");
    setBusy(true);
    try {
      const r = await fetch("/api/workspace-ei/git/connect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ remote_url: remoteUrl.trim(), branch: branch.trim() || "main", token: token || null, mode }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      setStatus(d);
      setToken("");
      onChanged?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const act = async (path: string) => {
    setBusy(true);
    setErr("");
    try {
      const r = await fetch(`/api/workspace-ei/${path}`, { method: "POST" });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error((d as { detail?: string }).detail || `HTTP ${r.status}`);
      }
      await load();
      onChanged?.();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (status?.connected) {
    return (
      <div className="rounded-lg border p-4 text-sm space-y-2 max-w-md">
        <div className="flex items-center gap-2 font-medium">
          <GitBranch className="h-4 w-4" /> Connected to git
        </div>
        <div className="text-muted-foreground break-all">
          {status.remote_url} · branch <span className="font-mono">{status.branch}</span>
        </div>
        {status.head && (
          <div className="text-xs text-muted-foreground font-mono">HEAD {status.head.slice(0, 10)}</div>
        )}
        <p className="text-xs text-muted-foreground">
          Remote is the source of truth — every change is pushed automatically.
        </p>
        {err && <p className="text-xs text-destructive">{err}</p>}
        <div className="flex gap-2 pt-1">
          <Button size="sm" variant="outline" onClick={() => act("git/sync")} disabled={busy} className="gap-1">
            {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />} Sync
          </Button>
          <Button size="sm" variant="outline" onClick={() => act("git/disconnect")} disabled={busy} className="gap-1">
            <Unlink className="h-3 w-3" /> Disconnect
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border p-4 text-sm space-y-3 max-w-md">
      <div className="flex items-center gap-2 font-medium">
        <Link2 className="h-4 w-4" /> Connect this workspace to git
      </div>
      <p className="text-xs text-muted-foreground">
        Back the knowledge base with a git remote (GitHub, GitLab, Gitea, internal). Once connected
        the remote is authoritative and every change is pushed.
      </p>
      <div className="flex gap-2">
        <button
          onClick={() => setMode("byor")}
          className={`flex-1 rounded-md border px-2 py-1 text-xs ${mode === "byor" ? "bg-muted font-medium" : "text-muted-foreground"}`}
        >
          Use existing repo
        </button>
        <button
          onClick={() => setMode("provision")}
          className={`flex-1 rounded-md border px-2 py-1 text-xs ${mode === "provision" ? "bg-muted font-medium" : "text-muted-foreground"}`}
        >
          Provision (push seed)
        </button>
      </div>
      <Input placeholder="https://github.com/org/repo.git" value={remoteUrl} onChange={(e) => setRemoteUrl(e.target.value)} />
      <div className="flex gap-2">
        <Input placeholder="branch (main)" value={branch} onChange={(e) => setBranch(e.target.value)} className="w-32" />
        <Input placeholder="access token" type="password" value={token} onChange={(e) => setToken(e.target.value)} />
      </div>
      {err && <p className="text-xs text-destructive">{err}</p>}
      <Button size="sm" onClick={connect} disabled={busy || !remoteUrl.trim()} className="gap-1">
        {busy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Link2 className="h-3 w-3" />} Connect
      </Button>
    </div>
  );
}
