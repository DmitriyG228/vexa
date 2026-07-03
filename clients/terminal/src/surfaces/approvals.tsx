"use client";
/** Approvals — the human gate's inbox (proposal.v1). The left "Approvals" item opens a center BOARD of
 *  pending groups (routine × level): an L2 group (comment/label/close/open_issue) is ONE batch-approve
 *  card ("Approve all (n)" → /decide); every L3 proposal (push_branch/open_pr) stands alone — its detail
 *  tab (kind "proposal") shows rationale + target + the patch payload as a unified diff, and is approved
 *  or rejected one deliberate act at a time (confirm step). L2 rows can also be decided from detail. */
import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { registerCommand, registerList, registerTab, type TabProps } from "../contributions";
import { Icon } from "../ui-kit";
import { usePreviewPinTab } from "./previewPinTab";
// Data-access lives in its own SoC module (scoped to the authed user — no client subject, P20),
// proven in isolation by approvalsApi.test.ts (incl. the client-side L3-never-in-a-batch gate).
import {
  listProposals, getProposal, decideBatch, approveOne, rejectOne,
  type Proposal, type ProposalGroup, type ProposalPatch,
} from "./approvalsApi";

const BOARD: TabDescriptor = { id: "board:approvals", title: "Approvals", kind: "approvals", params: {}, context: null };
const proposalTab = (p: Proposal): TabDescriptor =>
  ({ id: `proposal:${p.id}`, title: `${p.action} · ${p.target.repo.split("/").pop()}`, kind: "proposal", params: { id: p.id }, context: null });

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const groupKey = (g: ProposalGroup) => `${g.routine.id ?? g.routine.name ?? "?"}:${g.level}`;
const routineName = (g: ProposalGroup) => g.routine.name || g.routine.id || "(unnamed routine)";

/** The target as a PLAIN-TEXT GitHub URL — deliberately not an <a>: the approval surface never
 *  clicks through to the action's destination (the decision happens here, on the evidence shown). */
function targetUrl(p: Proposal): string {
  const base = `https://github.com/${p.target.repo}`;
  if (p.target.kind === "issue" && p.target.number != null) return `${base}/issues/${p.target.number}`;
  if (p.target.kind === "pr" && p.target.number != null) return `${base}/pull/${p.target.number}`;
  if (p.target.kind === "branch" && p.target.ref) return `${base}/tree/${p.target.ref}`;
  return base;
}

const levelBadge = (level: "L2" | "L3"): CSSProperties => ({
  fontFamily: "var(--mono)", fontSize: 10.5, borderRadius: 5, padding: "1px 6px", flex: "none",
  background: "var(--panel2)", color: level === "L3" ? "var(--live)" : "var(--accent)",
});
const chip: CSSProperties = { fontFamily: "var(--mono)", fontSize: 11, borderRadius: 999, padding: "1px 8px", background: "var(--panel2)", color: "var(--accent)" };
const btn = (danger?: boolean): CSSProperties => ({
  display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 12px", borderRadius: 7, fontSize: 12.5,
  border: `1px solid ${danger ? "var(--live)" : "var(--line2)"}`, background: "var(--panel)",
  color: danger ? "var(--live)" : "var(--t1)", cursor: "pointer", fontFamily: "inherit",
});
const errBox: CSSProperties = { fontSize: 12.5, color: "var(--live)", background: "var(--panel)", border: "1px solid var(--live)", borderRadius: 8, padding: "8px 11px", marginBottom: 14 };

// ── unified-diff rendering — a simple line classifier, NO diff dependency ─────────
type DiffKind = "add" | "del" | "hunk" | "meta" | "ctx";
function classifyDiffLine(line: string): DiffKind {
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+++") || line.startsWith("---")) return "meta";
  if (line.startsWith("diff ") || line.startsWith("index ")) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}
const DIFF_COLOR: Record<DiffKind, string> = { add: "var(--green)", del: "var(--live)", hunk: "var(--accent)", meta: "var(--t3)", ctx: "var(--t2)" };

function UnifiedDiff({ diff }: { diff: string }) {
  return (
    <pre style={{ margin: 0, padding: "10px 12px", overflowX: "auto", fontFamily: "var(--mono)", fontSize: 11.5, lineHeight: 1.55, background: "var(--panel2)", borderRadius: "0 0 8px 8px" }}>
      {diff.split("\n").map((line, i) => {
        const kind = classifyDiffLine(line);
        return <div key={i} style={{ color: DIFF_COLOR[kind], background: kind === "add" || kind === "del" ? "color-mix(in srgb, currentColor 8%, transparent)" : undefined }}>{line || " "}</div>;
      })}
    </pre>
  );
}

function PatchCard({ patch }: { patch: ProposalPatch }) {
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 8, marginTop: 10 }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--t1)", padding: "6px 12px", borderBottom: "1px solid var(--line)", display: "flex", gap: 8, alignItems: "center" }}>
        <Icon name="file" size={12} style={{ color: "var(--t3)" }} />{patch.path}
        {patch.diff == null && <span style={{ color: "var(--t3)" }}>(full file)</span>}
      </div>
      {patch.diff != null
        ? <UnifiedDiff diff={patch.diff} />
        : <pre style={{ margin: 0, padding: "10px 12px", overflowX: "auto", fontFamily: "var(--mono)", fontSize: 11.5, lineHeight: 1.55, color: "var(--t2)", background: "var(--panel2)", borderRadius: "0 0 8px 8px" }}>{patch.content ?? ""}</pre>}
    </div>
  );
}

// ── payload rendering, per action ──────────────────────────────────────────────────
function PayloadView({ p }: { p: Proposal }) {
  const { payload } = p;
  return (
    <div>
      {payload.title && <div style={{ fontSize: 14.5, color: "var(--t1)", fontWeight: 500, marginBottom: 6 }}>{payload.title}</div>}
      {payload.comment && (
        <blockquote style={{ margin: 0, padding: "8px 14px", borderLeft: "3px solid var(--accent)", background: "var(--panel2)", borderRadius: "0 8px 8px 0", fontSize: 13, color: "var(--t1)", lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
          {payload.comment}
        </blockquote>
      )}
      {payload.body && <div style={{ fontSize: 13, color: "var(--t2)", lineHeight: 1.6, whiteSpace: "pre-wrap", marginTop: 8 }}>{payload.body}</div>}
      {payload.labels && payload.labels.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 10 }}>
          {payload.labels.map((l) => <span key={l} style={chip}>{l}</span>)}
        </div>
      )}
      {payload.branch && (
        <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t2)", marginTop: 10 }}>
          <span style={{ color: "var(--accent)" }}>{payload.branch}</span>{payload.base && <span> ← {payload.base}</span>}
        </div>
      )}
      {(payload.patches ?? []).map((patch, i) => <PatchCard key={`${patch.path}:${i}`} patch={patch} />)}
    </div>
  );
}

// ── per-action Approve/Reject with a confirm step (the L3 deliberate act; L2 direct) ─
function DecideButtons({ proposal, onDecided, onError }: { proposal: Proposal; onDecided: (p: Proposal) => void; onError: (msg: string) => void }) {
  const [confirming, setConfirming] = useState<"approve" | "reject" | null>(null);
  const [busy, setBusy] = useState(false);
  const decide = async (verb: "approve" | "reject") => {
    setBusy(true);
    try { onDecided(await (verb === "approve" ? approveOne(proposal.id) : rejectOne(proposal.id))); }
    catch (e: unknown) { onError(errText(e)); }
    finally { setBusy(false); setConfirming(null); }
  };
  // L3 = a mutation: one deliberate act — click, then CONFIRM. L2 decides directly.
  const click = (verb: "approve" | "reject") => (proposal.level === "L3" && confirming !== verb ? setConfirming(verb) : void decide(verb));
  if (proposal.status !== "pending") return null;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 16 }}>
      <button disabled={busy} onClick={() => click("approve")} style={{ ...btn(), borderColor: "var(--green)", color: "var(--green)" }}>
        <Icon name="check" size={13} />{confirming === "approve" ? `Confirm: approve this ${proposal.action}?` : "Approve"}
      </button>
      <button disabled={busy} onClick={() => click("reject")} style={btn(true)}>
        <Icon name="x" size={13} />{confirming === "reject" ? "Confirm: reject?" : "Reject"}
      </button>
      {confirming && <button disabled={busy} onClick={() => setConfirming(null)} style={{ background: "none", border: "none", color: "var(--t3)", cursor: "pointer", fontSize: 12 }}>cancel</button>}
    </div>
  );
}

// ── center DETAIL tab (kind "proposal", params { id }) ─────────────────────────────
function ProposalTab({ params }: TabProps) {
  const proposalId = String(params.id ?? "");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [error, setError] = useState<string | null>(null);  // fail-loud (P18)
  useEffect(() => {
    setProposal(null);
    void getProposal(proposalId)
      .then((p) => { setProposal(p); setError(null); })
      .catch((e: unknown) => setError(errText(e)));
  }, [proposalId]);
  return (
    <div style={{ height: "100%", overflowY: "auto", background: "var(--bg)" }}>
      <div style={{ maxWidth: 760, margin: "0 auto", padding: "24px" }}>
        {error && <div role="alert" style={errBox}>⚠ Couldn’t load the proposal — {error}</div>}
        {proposal && (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
              <span style={{ fontSize: 18, color: "var(--t1)", fontWeight: 500 }}>{proposal.action.replace("_", " ")}</span>
              <span style={levelBadge(proposal.level)}>{proposal.level}</span>
              <span style={{ fontSize: 12, color: proposal.status === "pending" ? "var(--t3)" : proposal.status === "rejected" ? "var(--live)" : "var(--green)" }}>{proposal.status}</span>
            </div>
            {proposal.routine && <div style={{ fontSize: 12.5, color: "var(--t3)", marginBottom: 14 }}>proposed by <span style={{ color: "var(--t2)" }}>{proposal.routine.name || proposal.routine.id}</span></div>}

            <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 4 }}>target</div>
            {/* plain text on purpose — never a click-through to the action's destination */}
            <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--t2)", marginBottom: 16, userSelect: "text" }}>{targetUrl(proposal)}</div>

            <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 4 }}>rationale</div>
            <div style={{ fontSize: 13, color: "var(--t1)", lineHeight: 1.6, marginBottom: 16 }}>{proposal.rationale}</div>

            <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 6 }}>payload</div>
            <PayloadView p={proposal} />

            {proposal.decision && (
              <div style={{ fontSize: 12.5, color: "var(--t3)", marginTop: 16 }}>
                decided by {proposal.decision.by} at {proposal.decision.at}{proposal.decision.note ? ` — “${proposal.decision.note}”` : ""}
              </div>
            )}
            <DecideButtons proposal={proposal} onDecided={setProposal} onError={setError} />
          </>
        )}
      </div>
    </div>
  );
}

// ── one (routine × level) group card on the board ──────────────────────────────────
function GroupCard({ group, onDecided, onError }: { group: ProposalGroup; onDecided: () => void; onError: (msg: string) => void }) {
  const layout = useService(LayoutServiceId);
  const [busy, setBusy] = useState(false);
  const approveAll = async () => {
    setBusy(true);
    try { await decideBatch(group.proposals.map((p) => p.id), true); onDecided(); }
    catch (e: unknown) { onError(errText(e)); }
    finally { setBusy(false); }
  };
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 12, background: "var(--panel)", padding: "14px 16px", marginBottom: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ fontSize: 14.5, color: "var(--t1)", fontWeight: 500, flex: 1 }}>{routineName(group)}</span>
        <span style={levelBadge(group.level)}>{group.level}</span>
        <span style={{ fontSize: 12, color: "var(--t3)" }}>{group.proposals.length} pending</span>
        {group.batch_approvable && (
          <button disabled={busy} onClick={() => void approveAll()} style={{ ...btn(), borderColor: "var(--green)", color: "var(--green)" }}>
            <Icon name="check" size={13} />Approve all ({group.proposals.length})
          </button>
        )}
      </div>
      {!group.batch_approvable && <div style={{ fontSize: 11.5, color: "var(--t3)", marginTop: 4 }}>mutations — each approved on its own, from its detail</div>}
      <div style={{ marginTop: 8 }}>
        {group.proposals.map((p) => (
          <div key={p.id} onClick={() => layout.openPreview(proposalTab(p))} onDoubleClick={() => layout.openTab(proposalTab(p))}
            style={{ display: "flex", alignItems: "baseline", gap: 8, padding: "6px 8px", borderRadius: 6, cursor: "pointer", fontSize: 12.5 }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--accent)", flex: "none" }}>{p.action}</span>
            <span style={{ color: "var(--t2)", flex: "none" }}>{p.target.repo}{p.target.number != null ? `#${p.target.number}` : p.target.ref ? `@${p.target.ref}` : ""}</span>
            <span style={{ color: "var(--t3)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.rationale}</span>
            <Icon name="chevR" size={12} style={{ color: "var(--t3)", marginLeft: "auto" }} />
          </div>
        ))}
      </div>
    </div>
  );
}

// ── center BOARD (kind "approvals") ────────────────────────────────────────────────
function ApprovalsBoard() {
  const [groups, setGroups] = useState<ProposalGroup[]>([]);
  const [error, setError] = useState<string | null>(null);  // fail-loud (P18): a load/mutation error is shown, never swallowed
  const reload = useCallback(() => {
    void listProposals().then((gs) => { setGroups(gs); setError(null); }).catch((e: unknown) => setError(errText(e)));
  }, []);
  useEffect(() => reload(), [reload]);
  return (
    <div style={{ height: "100%", overflowY: "auto", background: "var(--bg)" }}>
      <div style={{ maxWidth: 760, margin: "0 auto", padding: "24px" }}>
        <div style={{ fontSize: 18, color: "var(--t1)", fontWeight: 500, marginBottom: 4 }}>Approvals</div>
        <div style={{ fontSize: 13, color: "var(--t3)", marginBottom: 20 }}>Proposed VCS actions awaiting your decision. L2 (annotate) approves in batch; L3 (mutate) one act at a time.</div>
        {error && <div role="alert" style={errBox}>⚠ Couldn’t load proposals — {error}</div>}
        {groups.map((g) => <GroupCard key={groupKey(g)} group={g} onDecided={reload} onError={setError} />)}
        {groups.length === 0 && !error && <div style={{ color: "var(--t3)", fontSize: 13, padding: "20px 0" }}>Nothing pending — routines will file proposals here when they want to act.</div>}
      </div>
    </div>
  );
}

// ── left launcher (opens the board, shows the pending groups) ──────────────────────
function ApprovalsBoardNav() {
  const nav = usePreviewPinTab<HTMLButtonElement>(BOARD);
  return (
    <button onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} style={{ display: "flex", alignItems: "center", gap: 8, width: "100%", padding: "8px 9px", borderRadius: 7, border: "1px solid var(--line2)", background: "var(--panel)", color: "var(--t1)", fontSize: 13, cursor: "pointer", marginBottom: 8 }}>
      <Icon name="check" size={14} />Approvals inbox
    </button>
  );
}

function GroupNavRow({ group }: { group: ProposalGroup }) {
  const nav = usePreviewPinTab<HTMLDivElement>(BOARD);
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 9px", borderRadius: 6, cursor: "pointer", fontSize: 12.5, color: "var(--t2)" }}>
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{routineName(group)}</span>
      <span style={levelBadge(group.level)}>{group.level}</span>
      <span style={{ fontSize: 11, color: "var(--t3)" }}>{group.proposals.length}</span>
    </div>
  );
}

function ApprovalsLeft() {
  const layout = useService(LayoutServiceId);
  const [groups, setGroups] = useState<ProposalGroup[]>([]);
  useEffect(() => { layout.openTab(BOARD); void listProposals().then(setGroups).catch(() => {/* the board view surfaces the error loudly */}); }, [layout]);
  return (
    <div style={{ padding: "8px" }}>
      <ApprovalsBoardNav />
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 4px" }}>pending · routine × level</div>
      {groups.map((g) => <GroupNavRow key={groupKey(g)} group={g} />)}
      {groups.length === 0 && <div style={{ padding: "8px 4px", color: "var(--t3)", fontSize: 12 }}>Nothing pending.</div>}
    </div>
  );
}

// Agent surface — absent in meetings-only mode (NEXT_PUBLIC_TERMINAL_MODE=meetings).
if (process.env.NEXT_PUBLIC_TERMINAL_MODE !== "meetings") {
  registerTab("approvals", ApprovalsBoard);
  registerTab("proposal", ProposalTab);
  registerList({ id: "approvals", label: "Approvals", icon: "check", order: 45, component: ApprovalsLeft });
  registerCommand({ id: "approvals.open", title: "Open Approvals Inbox", run: ({ container }) => container.get(LayoutServiceId).openTab(BOARD) });
}
