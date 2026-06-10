#!/usr/bin/env python3
"""Aggregate runs/*.jsonl into the scorecard tables of docs/benchmarks/local-llm-agent.md.

Regenerates only the region between the GENERATED markers; prose around it is
hand-written. For repeated (arm, model, task) records the LATEST one wins.

Usage: python3 lib/scorecard.py [--write]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
RUNS = HERE / "runs"
DOC = HERE.parent.parent / "docs" / "benchmarks" / "local-llm-agent.md"
BEGIN = "<!-- GENERATED:scorecard:begin (lib/scorecard.py — do not edit by hand) -->"
END = "<!-- GENERATED:scorecard:end -->"

# Indicative per-1M-token list prices (USD) used ONLY when the CLI did not report
# cost itself. Mistral La Plateforme / Anthropic list prices as of 2026-06.
PRICES = {
    "mistral-small-latest": (0.10, 0.30),
    "devstral-small-latest": (0.10, 0.30),
    "mistral-medium-latest": (0.40, 2.00),
}


def fmt(v, suffix="", nd=2):
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{round(v, nd)}{suffix}"
    return f"{v}{suffix}"


def cost_of(rec):
    m = rec.get("metrics") or {}
    if m.get("cost_usd"):
        return m["cost_usd"]
    p = PRICES.get(rec["model"])
    if p and m.get("tokens_in") is not None and m.get("tokens_out") is not None:
        return m["tokens_in"] / 1e6 * p[0] + m["tokens_out"] / 1e6 * p[1]
    return None


def primary_score(rec):
    s = rec.get("scores") or {}
    parts = [v for k, v in s.items()
             if k in ("schema_compliance", "content_accuracy", "correctness",
                      "citation_validity", "task_success")]
    return sum(parts) / len(parts) if parts else 0.0


def load():
    latest = {}
    for f in sorted(RUNS.glob("*.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            latest[(rec["arm"], rec["model"], rec["task"])] = rec
    return latest


def tables(latest):
    out = []
    arms = sorted({(r["arm"], r["model"]) for r in latest.values()})
    out.append("### Per-arm × per-task scores\n")
    out.append("| Arm | CLI | Model | Task | Score | Detail | Completed | Wall clock | Tokens in/out | Cost (USD) |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for arm, model in arms:
        for task in ("t1", "t2", "t3"):
            rec = latest.get((arm, model, task))
            if not rec:
                continue
            s = rec.get("scores") or {}
            if isinstance(s.get("detail"), dict) or s.get("detail") is None:
                detail = ", ".join(f"{k}={fmt(v)}" for k, v in s.items() if k != "detail" and not isinstance(v, dict))
            else:
                detail = str(s.get("detail"))
            m = rec.get("metrics") or {}
            tok = "—" if m.get("tokens_in") is None else f"{m['tokens_in']}/{fmt(m.get('tokens_out'))}"
            note = rec.get("notes") or ""
            blocked = note.startswith("BLOCKED")
            out.append(
                f"| {arm} | {rec['cli']} | {model} | {task.upper()} | "
                f"{'**blocked**' if blocked else fmt(primary_score(rec))} | {detail or note} | "
                f"{'—' if blocked else ('yes' if rec['completed'] else 'no')} | "
                f"{fmt(rec['wall_clock_s'], 's', 1)} | {tok} | {fmt(cost_of(rec), '', 4)} |")
    out.append("")
    out.append("### Tool-loop fidelity (T3 task + loop metrics across all tasks)\n")
    out.append("| Arm | Model | Task | Turns | Tool calls | Tool errors | Valid-tool-call rate |")
    out.append("|---|---|---|---|---|---|---|")
    for arm, model in arms:
        for task in ("t1", "t2", "t3"):
            rec = latest.get((arm, model, task))
            if not rec or (rec.get("notes") or "").startswith("BLOCKED"):
                continue
            m = rec.get("metrics") or {}
            calls, errs = m.get("tool_calls"), m.get("tool_errors")
            rate = None
            if calls:
                rate = (calls - (errs or 0)) / calls
            out.append(f"| {arm} | {model} | {task.upper()} | {fmt(m.get('turns'))} | "
                       f"{fmt(calls)} | {fmt(errs)} | {fmt(rate)} |")
    out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    body = tables(load())
    if not args.write:
        print(body)
        return 0
    doc = DOC.read_text()
    if BEGIN not in doc or END not in doc:
        print(f"markers missing in {DOC}", file=sys.stderr)
        return 1
    pre, rest = doc.split(BEGIN, 1)
    _, post = rest.split(END, 1)
    DOC.write_text(pre + BEGIN + "\n" + body + END + post)
    print(f"scorecard tables refreshed in {DOC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
