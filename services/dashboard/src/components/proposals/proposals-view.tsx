"use client";

/**
 * Proposals view (pack ei-review-ui, issue #25).
 *
 * Lists the org's pending agent knowledge proposals from the sign API
 * (via the org-scoped dashboard proxy /api/proposals), renders the markdown
 * diff per proposal, and exposes the two actions:
 *   Approve -> POST /sign  (merge into the org workspace main)
 *   Reject  -> POST /reject with a REQUIRED note
 * Both update the list immediately on success.
 */

import { useCallback, useEffect, useState } from "react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  Check,
  FileDiff,
  GitPullRequest,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState } from "@/components/ui/empty-state";
import { ErrorState } from "@/components/ui/error-state";
import { Skeleton } from "@/components/ui/skeleton";
import { cn, parseUTCTimestamp } from "@/lib/utils";
import { withBasePath } from "@/lib/base-path";
import {
  createProposalsClient,
  ProposalsApiError,
  type ProposalDiff,
  type ProposalSummary,
} from "@/lib/proposals";
import { ProposalDiffView } from "./proposal-diff";

const client = createProposalsClient({ baseUrl: withBasePath("") });

export function proposalAge(createdAt: string): string {
  try {
    return formatDistanceToNow(parseUTCTimestamp(createdAt), { addSuffix: true });
  } catch {
    return createdAt;
  }
}

export function ProposalListItem({
  proposal,
  selected,
  onSelect,
}: {
  proposal: ProposalSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      data-testid="proposal-item"
      onClick={onSelect}
      className={cn(
        "w-full text-left rounded-lg border p-3 transition-colors",
        selected ? "border-primary bg-accent/50" : "hover:bg-accent/30"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-medium text-sm truncate">{proposal.meeting_title}</span>
        <Badge variant="outline" className="shrink-0 text-xs">
          {proposal.files_changed} file{proposal.files_changed === 1 ? "" : "s"}
        </Badge>
      </div>
      <p className="text-xs text-muted-foreground mt-1 truncate">{proposal.summary}</p>
      <p className="text-xs text-muted-foreground mt-1">
        {proposalAge(proposal.created_at)}
      </p>
    </button>
  );
}

function RejectDialog({
  open,
  proposal,
  busy,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  proposal: ProposalSummary | null;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (note: string) => void;
}) {
  const [note, setNote] = useState("");

  const close = () => {
    setNote("");
    onCancel();
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && !busy && close()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Reject proposal</DialogTitle>
          <DialogDescription>
            {proposal
              ? `Reject the knowledge proposal for "${proposal.meeting_title}". A short note explaining why is required.`
              : ""}
          </DialogDescription>
        </DialogHeader>
        <Textarea
          data-testid="reject-note"
          placeholder="Why is this proposal being rejected? (required)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
        />
        <DialogFooter>
          <Button variant="outline" onClick={close} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={!note.trim() || busy}
            onClick={() => {
              const n = note.trim();
              setNote("");
              onConfirm(n);
            }}
          >
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />}
            Reject
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

type ViewState =
  | { phase: "loading" }
  | { phase: "unauthenticated" }
  | { phase: "disabled" }
  | { phase: "error"; message: string }
  | { phase: "ready" };

export function ProposalsView() {
  const [state, setState] = useState<ViewState>({ phase: "loading" });
  const [proposals, setProposals] = useState<ProposalSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [diff, setDiff] = useState<ProposalDiff | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);

  const selected = proposals.find((p) => p.id === selectedId) ?? null;

  const refresh = useCallback(async () => {
    try {
      const list = await client.list();
      setProposals(list);
      setState({ phase: "ready" });
      setSelectedId((cur) => (cur && list.some((p) => p.id === cur) ? cur : null));
    } catch (e) {
      if (e instanceof ProposalsApiError && e.status === 401) {
        setState({ phase: "unauthenticated" });
      } else if (e instanceof ProposalsApiError && e.status === 404) {
        setState({ phase: "disabled" });
      } else {
        setState({ phase: "error", message: e instanceof Error ? e.message : String(e) });
      }
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    if (!selectedId) {
      setDiff(null);
      return;
    }
    let cancelled = false;
    setDiffLoading(true);
    client
      .diff(selectedId)
      .then((d) => {
        if (!cancelled) setDiff(d);
      })
      .catch((e) => {
        if (!cancelled) {
          toast.error(`Could not load diff: ${e instanceof Error ? e.message : e}`);
          setDiff(null);
        }
      })
      .finally(() => {
        if (!cancelled) setDiffLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const removeFromList = useCallback((id: string) => {
    setProposals((prev) => prev.filter((p) => p.id !== id));
    setSelectedId((cur) => (cur === id ? null : cur));
  }, []);

  const handleApprove = useCallback(async () => {
    if (!selected) return;
    setActionBusy(true);
    try {
      const res = await client.sign(selected.id);
      toast.success(
        `Approved — merged into the knowledge base (${res.merge_commit.slice(0, 7)})`
      );
      removeFromList(selected.id);
    } catch (e) {
      if (e instanceof ProposalsApiError && e.status === 409) {
        toast.info("This proposal was already resolved elsewhere — refreshing.");
        removeFromList(selected.id);
        refresh();
      } else {
        toast.error(`Approve failed: ${e instanceof Error ? e.message : e}`);
      }
    } finally {
      setActionBusy(false);
    }
  }, [selected, removeFromList, refresh]);

  const handleReject = useCallback(
    async (note: string) => {
      if (!selected) return;
      setActionBusy(true);
      try {
        await client.reject(selected.id, note);
        toast.success("Proposal rejected.");
        setRejectOpen(false);
        removeFromList(selected.id);
      } catch (e) {
        if (e instanceof ProposalsApiError && e.status === 409) {
          toast.info("This proposal was already resolved elsewhere — refreshing.");
          setRejectOpen(false);
          removeFromList(selected.id);
          refresh();
        } else {
          toast.error(`Reject failed: ${e instanceof Error ? e.message : e}`);
        }
      } finally {
        setActionBusy(false);
      }
    },
    [selected, removeFromList, refresh]
  );

  if (state.phase === "loading") {
    return (
      <div className="space-y-3">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }
  if (state.phase === "unauthenticated") {
    return (
      <ErrorState
        title="Sign in required"
        message="Sign in to review your organization's knowledge proposals."
      />
    );
  }
  if (state.phase === "disabled") {
    return (
      <EmptyState
        title="Enterprise Intelligence is not enabled"
        message="Knowledge proposals appear here once Enterprise Intelligence is enabled for your organization."
      />
    );
  }
  if (state.phase === "error") {
    return (
      <ErrorState
        title="Could not load proposals"
        message={state.message}
        onRetry={() => {
          setState({ phase: "loading" });
          refresh();
        }}
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold flex items-center gap-2">
            <GitPullRequest className="h-5 w-5" />
            Knowledge Proposals
          </h1>
          <p className="text-sm text-muted-foreground">
            Review what the agent wants to add to your organization&apos;s knowledge base.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh}>
          <RefreshCw className="h-4 w-4" />
          Refresh
        </Button>
      </div>

      {proposals.length === 0 ? (
        <EmptyState
          title="No pending proposals"
          message="When a meeting completes, the agent's proposed knowledge updates will appear here for review."
        />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[320px_1fr]">
          <div className="space-y-2" data-testid="proposal-list">
            {proposals.map((p) => (
              <ProposalListItem
                key={p.id}
                proposal={p}
                selected={p.id === selectedId}
                onSelect={() => setSelectedId(p.id)}
              />
            ))}
          </div>

          <Card className="p-4 min-h-[200px]">
            {!selected ? (
              <div className="flex flex-col items-center justify-center h-full py-10 text-muted-foreground">
                <FileDiff className="h-8 w-8 mb-2" />
                <p className="text-sm">Select a proposal to review its changes.</p>
              </div>
            ) : (
              <div className="space-y-4">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                  <div className="min-w-0">
                    <h2 className="font-medium truncate">{selected.meeting_title}</h2>
                    <p className="text-xs text-muted-foreground">
                      {selected.branch} · {proposalAge(selected.created_at)}
                    </p>
                  </div>
                  <div className="flex gap-2 shrink-0">
                    <Button
                      data-testid="approve-button"
                      size="sm"
                      disabled={actionBusy}
                      onClick={handleApprove}
                    >
                      {actionBusy ? (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      ) : (
                        <Check className="h-4 w-4" />
                      )}
                      Approve
                    </Button>
                    <Button
                      data-testid="reject-button"
                      size="sm"
                      variant="outline"
                      disabled={actionBusy}
                      onClick={() => setRejectOpen(true)}
                    >
                      <X className="h-4 w-4" />
                      Reject
                    </Button>
                  </div>
                </div>

                {diffLoading ? (
                  <div className="space-y-2">
                    <Skeleton className="h-24 w-full" />
                    <Skeleton className="h-24 w-full" />
                  </div>
                ) : diff ? (
                  <ProposalDiffView diff={diff} />
                ) : null}
              </div>
            )}
          </Card>
        </div>
      )}

      <RejectDialog
        open={rejectOpen}
        proposal={selected}
        busy={actionBusy}
        onCancel={() => setRejectOpen(false)}
        onConfirm={handleReject}
      />
    </div>
  );
}
