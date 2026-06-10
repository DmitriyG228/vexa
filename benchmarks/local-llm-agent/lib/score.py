"""Scoring for the local-llm-agent benchmark tasks (pack ei-agent).

All scores are 0.0–1.0 fractions of deterministic checks. Keyword specs support
`a|b` alternation; matching is case-insensitive substring.
"""
import json
import re
from pathlib import Path


def _has(text: str, keyword_spec: str) -> bool:
    return any(alt.strip().lower() in text.lower() for alt in keyword_spec.split("|"))


def _has_all(text: str, specs: list) -> bool:
    return all(_has(text, s) for s in specs)


# ── T1: transcript → schema-compliant per-meeting markdown ────────────────────

FRONT_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def score_t1(wd: Path, fixtures: Path) -> dict:
    transcript = json.loads((fixtures / "transcripts" / "meeting-alpha.json").read_text())
    ref = json.loads((fixtures / "reference" / "meeting-alpha.expected.json").read_text())
    note_path = wd / "notes" / "meeting-alpha.md"
    if not note_path.exists():
        return {"schema_compliance": 0.0, "content_accuracy": 0.0, "unassisted_completion": 0.0,
                "detail": "notes/meeting-alpha.md not produced"}
    text = note_path.read_text()

    rules = {}
    fm = FRONT_RE.match(text)
    rules["front_matter_present"] = fm is not None
    body = text[fm.end():] if fm else text
    fm_text = fm.group(1) if fm else ""
    rules["front_matter_meeting_id"] = bool(re.search(r"^meeting_id:\s*['\"]?meeting-alpha['\"]?\s*$", fm_text, re.M))
    rules["front_matter_date"] = transcript["date"] in fm_text
    rules["front_matter_participants"] = all(p in fm_text for p in transcript["participants"])
    rules["h1_title"] = bool(re.search(r"^# \S", body, re.M))

    headings = re.findall(r"^(#{1,2}) (.+)$", body, re.M)
    h2 = [h[1].strip() for h in headings if h[0] == "##"]
    rules["sections_order"] = h2 == ["Summary", "Decisions", "Action Items"]

    def section(name: str) -> str:
        m = re.search(rf"^## {name}\s*\n(.*?)(?=^## |\Z)", body, re.S | re.M)
        return m.group(1) if m else ""

    decisions = [ln for ln in section("Decisions").splitlines() if ln.strip().startswith("- ")]
    rules["decisions_bullets"] = len(decisions) >= 1
    actions = [ln.strip() for ln in section("Action Items").splitlines() if ln.strip().startswith("- ")]
    action_re = re.compile(r"^- \[[ xX]\] .+\s[—–-]\s*.+$")
    rules["action_items_format"] = len(actions) >= 1 and all(action_re.match(a) for a in actions)

    schema = sum(rules.values()) / len(rules)

    facts = 0
    hits = 0
    dec_text = section("Decisions")
    act_text = section("Action Items")
    for d in ref["decisions"]:
        facts += 1
        hits += _has_all(dec_text or text, d["keywords"])
    for a in ref["action_items"]:
        facts += 1
        hits += (_has_all(act_text or text, a["keywords"]) and _has(act_text or text, a["owner"]))
    accuracy = hits / facts if facts else 0.0

    return {"schema_compliance": round(schema, 3), "content_accuracy": round(accuracy, 3),
            "unassisted_completion": 1.0,
            "detail": {k: bool(v) for k, v in rules.items()}}


# ── T2: cited Q&A over a multi-meeting workspace ──────────────────────────────

def score_t2(wd: Path, fixtures: Path) -> dict:
    qs = json.loads((fixtures / "questions" / "t2-questions.json").read_text())["questions"]
    ans_path = wd / "answers.md"
    if not ans_path.exists():
        return {"correctness": 0.0, "citation_validity": 0.0, "unassisted_completion": 0.0,
                "detail": "answers.md not produced"}
    text = ans_path.read_text()
    blocks = {}
    for m in re.finditer(r"^##\s*(q\d+)\b(.*?)(?=^##\s*q\d+\b|\Z)", text, re.S | re.M | re.I):
        blocks[m.group(1).lower()] = m.group(2)

    correct = 0
    cited = 0
    detail = {}
    for q in qs:
        block = blocks.get(q["id"].lower(), "")
        ok = bool(block) and _has_all(block, q["expected_keywords"])
        src_m = re.search(r"Source:\s*(.+)$", block, re.M | re.I) if block else None
        cite_ok = False
        if src_m:
            cited_path = src_m.group(1).strip().strip("`")
            cite_ok = Path(q["expected_source"]).name in cited_path and (wd / cited_path.lstrip("./")).exists()
        correct += ok
        cited += cite_ok
        detail[q["id"]] = {"correct": bool(ok), "citation_valid": bool(cite_ok)}
    n = len(qs)
    return {"correctness": round(correct / n, 3), "citation_validity": round(cited / n, 3),
            "unassisted_completion": 1.0, "detail": detail}


# ── T3: tool-loop fidelity micro-task ─────────────────────────────────────────

def score_t3(wd: Path, fixtures: Path) -> dict:
    expected_down = sorted(
        ln.split()[0] for ln in (fixtures / "t3" / "services.txt").read_text().splitlines()
        if ln.strip() and not ln.startswith("#") and ln.split()[-1] == "DOWN")
    checks = {}

    plan = wd / "ops" / "restart-plan.md"
    checks["plan_exists"] = plan.exists()
    if plan.exists():
        ptext = plan.read_text()
        checks["plan_h1"] = bool(re.search(r"^# Restart plan\s*$", ptext, re.M))
        bullets = [re.sub(r"^[-*]\s+", "", ln.strip()).strip("`* ")
                   for ln in ptext.splitlines() if ln.strip().startswith(("- ", "* "))]
        checks["plan_services_sorted"] = bullets == expected_down
    else:
        checks["plan_h1"] = checks["plan_services_sorted"] = False

    ack = wd / "ops" / "ack.txt"
    checks["ack_exact"] = ack.exists() and ack.read_text().strip() == f"ACK {len(expected_down)}"
    checks["source_unmodified"] = (
        (wd / "data" / "services.txt").read_text() == (fixtures / "t3" / "services.txt").read_text()
        if (wd / "data" / "services.txt").exists() else False)

    return {"task_success": round(sum(checks.values()) / len(checks), 3),
            "unassisted_completion": 1.0,
            "detail": {k: bool(v) for k, v in checks.items()}}


def score_task(task: str, wd: Path, fixtures: Path) -> dict:
    return {"t1": score_t1, "t2": score_t2, "t3": score_t3}[task](wd, fixtures)
