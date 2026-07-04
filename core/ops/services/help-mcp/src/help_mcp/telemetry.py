"""telemetry.py — the ``help:questions`` stream: the question log IS the feedback loop.

Every tool call lands ONE entry ``{ts, tool, question, top_paths, answered_from_docs}`` on the
``help:questions`` redis stream (MAXLEN ~5000, approximate) — recurring friction is the doc-gap
signal the ops workspace reviews via ``review_question_log``. Best-effort BY DESIGN: a redis
outage is logged and swallowed — telemetry must never fail a user's answer (P18). Single writer:
help-mcp (the log freezes as a published ``help-log.v1`` contract the day a second writer appears).

Privacy stance (disclosed in every tool docstring, which IS the MCP tool description): the
question text is retained to improve the docs — callers must send no code, secrets, or personal
data; the stream is bounded (~5000 entries) so retention is finite by construction.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

QUESTIONS_STREAM = "help:questions"
QUESTIONS_MAXLEN = 5_000
_QUESTION_CLIP = 2_000  # defensive bound — a pasted log dump must not become the stream


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class QuestionLog:
    """XADD on every tool call; XREVRANGE for the maintainer read-back."""

    def __init__(self, redis_client) -> None:
        self._r = redis_client

    def record(self, tool: str, question: str, top_paths: Sequence[str], answered_from_docs: bool) -> bool:
        """Best-effort append — ``False`` (and a log line) on any redis failure, never a raise."""
        entry = {
            "ts": _utcnow(),
            "tool": tool,
            "question": (question or "")[:_QUESTION_CLIP],
            "top_paths": json.dumps(list(top_paths)),
            "answered_from_docs": "true" if answered_from_docs else "false",
        }
        try:
            self._r.xadd(QUESTIONS_STREAM, entry, maxlen=QUESTIONS_MAXLEN, approximate=True)
            return True
        except Exception as exc:  # noqa: BLE001 — ANY redis failure degrades, never propagates
            log.warning("question-log XADD failed (%s: %s) — the answer is unaffected",
                        exc.__class__.__name__, exc)
            return False

    def read(self, since: Optional[str], limit: int) -> Tuple[List[Dict], List[str]]:
        """``(entries, notes)`` oldest→newest. ``since`` is ISO-8601 (mapped to a stream id);
        a malformed ``since`` raises ``ValueError`` (the route's 422); redis absence degrades
        to ``([], [note])``."""
        start = "-"
        if since:
            dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            start = f"{int(dt.timestamp() * 1000)}-0"
        try:
            raw = self._r.xrevrange(QUESTIONS_STREAM, max="+", min=start, count=limit)
        except Exception as exc:  # noqa: BLE001
            return [], [f"question log unavailable (redis: {exc.__class__.__name__}) — telemetry is best-effort."]
        entries: List[Dict] = []
        for entry_id, fields in reversed(raw):
            try:
                top_paths = json.loads(fields.get("top_paths", "[]"))
            except ValueError:
                top_paths = []
            entries.append({
                "provenance": "operational",
                "id": entry_id,
                "ts": fields.get("ts", ""),
                "tool": fields.get("tool", ""),
                "question": fields.get("question", ""),
                "top_paths": top_paths,
                "answered_from_docs": fields.get("answered_from_docs") == "true",
            })
        return entries, []
