"""guide.py — ``get_deploy_guide``'s target → docs-section map.

Each target resolves an ORDERED list of ``(path, heading-prefix)`` selectors against the live
docs index: matched sections ride out WHOLE, each labeled ``provenance:"doc"`` with its
path+heading citation. What the docs do NOT yet cover rides as an explicit
``provenance:"operational"`` note (live repo state — verify against the tree), never silently
blended into doc text. A selector that stops resolving (a doc heading renamed) degrades to a
visible ``docs gap`` note — the guide never invents content — and the test suite pins every
selector against the shipped corpus so that drift is caught at gate time, not by a user.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .docs_index import DocsIndex

DEPLOY_TARGETS = ("compose", "lite", "helm", "overview")

# target → {sections: [(docs-root-relative path, heading prefix)], notes: [operational note]}
TARGETS: Dict[str, Dict] = {
    "overview": {
        "sections": [
            ("deployment.mdx", "Deployment"),                    # the preamble: what self-hosting means
            ("deployment.mdx", "The stack"),
            ("quickstart.mdx", "1. Install and start"),
        ],
        "notes": [
            "Deploy targets shipped in the repo: deploy/compose (per-service stack — the documented "
            "path), deploy/lite (single container), deploy/helm (Kubernetes chart). Only compose is "
            "fully covered by the shipped docs corpus today.",
        ],
    },
    "compose": {
        "sections": [
            ("deployment.mdx", "Quick start (Docker Compose)"),
            ("deployment.mdx", "The stack"),
            ("deployment.mdx", "Configuration"),
            ("configuration.mdx", "Transcription (STT)"),
            ("configuration.mdx", "Ports"),
            ("deployment.mdx", "Publishing behind a reverse proxy"),
            ("deployment.mdx", "Air-gapped"),
        ],
        "notes": [],
    },
    "lite": {
        "sections": [],
        "notes": [
            "The Lite (single-container) variant ships in the repo at deploy/lite — `make lite` from "
            "the repo root: one app container (process-backend runtime; bots and agent workers run as "
            "child processes) plus PostgreSQL/MinIO sidecars — but it is NOT yet covered by the shipped "
            "docs corpus. Treat deploy/lite/README.md in your checkout as the source and verify there.",
        ],
    },
    "helm": {
        "sections": [
            ("core/runtime.mdx", "On Kubernetes"),
        ],
        "notes": [
            "A Kubernetes chart ships in the repo at deploy/helm (charts/vexa; RUNTIME_BACKEND=k8s — "
            "the runtime spawns Pods instead of Docker containers) but the chart itself is NOT yet "
            "covered by the shipped docs corpus. Treat deploy/helm/README.md in your checkout as the "
            "source and verify there.",
        ],
    },
}


def deploy_guide(index: DocsIndex, target: str) -> Tuple[List[Dict], List[Dict]]:
    """``(sections, notes)`` for one target — sections are doc-cited; notes are operational."""
    spec = TARGETS[target]
    sections: List[Dict] = []
    notes: List[Dict] = []
    for path, heading in spec["sections"]:
        s = index.get(path, heading)
        if s is None:
            notes.append({
                "provenance": "operational",
                "note": f"docs gap: expected section '{heading}' in {path} was not found in this docs build",
            })
            continue
        sections.append({
            "provenance": "doc",
            "path": s.path,
            "heading": s.heading,
            "content": s.text,
        })
    notes.extend({"provenance": "operational", "note": n} for n in spec["notes"])
    return sections, notes
