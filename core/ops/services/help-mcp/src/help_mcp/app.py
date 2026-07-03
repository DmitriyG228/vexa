"""``create_app(...) -> FastAPI`` — the vexa help companion (v0.12): the DYNAMIC help surface.

A PUBLIC-FACING MCP service users' coding agents connect to while deploying/integrating vexa.
It answers from two knowledge tiers and ALWAYS labels which (the provenance rule, carried on
every response envelope):

- ``doc``         — cited from the shipped docs corpus (path + heading): documented behavior;
- ``operational`` — live state (GitHub triage, the question log) NOT yet documented.

Read-only v1: the ONLY things it mutates are its own question-log telemetry stream
(``help:questions``) and — config-gated behind ``HELP_GITHUB_TOKEN`` — escalation issues on the
public tracker. The question stream is the product's feedback loop: recurring friction becomes
the doc-gap signal the ops workspace reviews (``review_question_log``).

Privacy: every tool docstring (= the MCP tool description the caller's agent reads) discloses
that the question is retained for doc improvement and that no code/secrets should be sent.

Pattern: the repo's MCP idiom (core/meetings/services/mcp) — each tool is a thin FastAPI route
(``operation_id`` = tool name, docstring = tool description); ``FastApiMCP`` mounts the
streamable-HTTP transport at ``/mcp``. Every effect port is injectable for the offline tests:
``github_transport`` / ``agent_transport`` (httpx.MockTransport), ``redis_client`` (fakeredis),
``clock`` (the TTL cache proven without real time).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Literal, Optional
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi_mcp import FastApiMCP
from pydantic import BaseModel, Field

from .docs_index import DocsIndex
from .gh import DEFAULT_REPO, GitHubHelp, GitHubHelpError, filter_issues, issue_draft
from .guide import deploy_guide
from .telemetry import QUESTIONS_STREAM, QuestionLog

log = logging.getLogger(__name__)

_DEFAULT_DOCS_ROOT = "/app/docs"
_DEFAULT_AGENT_API_URL = "http://agent-api:8100"
_DEFAULT_REDIS_URL = "redis://redis:6379/0"
_DEFAULT_OPS_SUBJECT = "ops"

PROVENANCE_RULE = (
    "Every result is labeled with its knowledge tier: 'doc' = cited from the shipped docs corpus "
    "(path + heading — documented, supported behavior); 'operational' = live operational state "
    "(GitHub triage, the question log) that is NOT yet documented — verify before relying on it."
)
RETENTION_NOTE = (
    "Questions sent to this service are retained in a bounded internal question log so recurring "
    "friction becomes documentation. Send no code, secrets, or personal data."
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


# ---------------------------
# Request models
# ---------------------------
class SearchDocsRequest(BaseModel):
    query: str = Field(..., min_length=1, description="One focused question or phrase (e.g. 'reverse proxy websocket').")
    max_results: int = Field(5, ge=1, le=20, description="Max doc sections to return (default 5).")


class EscalateRequest(BaseModel):
    question: str = Field(..., min_length=1, description="The question the docs + known issues could not answer.")
    environment_summary: str = Field(
        "",
        description="Deploy target, versions, relevant config — FACTS ONLY, never secrets/tokens/keys.",
    )
    docs_consulted: List[str] = Field(
        default_factory=list,
        description='Doc citations already tried, e.g. "deployment.mdx#Air-gapped" (shows maintainers the gap).',
    )


def create_app(
    docs_root: Optional[str | Path] = None,
    agent_api_url: Optional[str] = None,
    *,
    github_repo: Optional[str] = None,
    github_token: Optional[str] = None,
    ops_subject: Optional[str] = None,
    redis_url: Optional[str] = None,
    redis_client=None,
    github_transport: Optional[httpx.BaseTransport] = None,
    agent_transport: Optional[httpx.BaseTransport] = None,
    clock: Optional[Callable[[], float]] = None,
) -> FastAPI:
    """Build the help companion app.

    ``docs_root``     — the "doc" tier corpus (env ``DOCS_ROOT``; the image vendors ``/app/docs``).
    ``agent_api_url`` — the help.escalated event sink base (env ``VEXA_AGENT_API_URL``).
    ``github_repo`` / ``github_token`` — the tracker + the OPTIONAL credential (env
                        ``HELP_GITHUB_REPO`` / ``HELP_GITHUB_TOKEN``); no token ⇒ anonymous reads
                        (rate-limit degrade) and draft-mode escalation.
    ``ops_subject``   — the event.v1 subject escalations are attributed to (env ``VEXA_OPS_SUBJECT``).
    ``redis_url`` / ``redis_client`` — the question-log backing (env ``REDIS_URL``); absence
                        degrades (answers still return), fakeredis in the tests.
    ``github_transport`` / ``agent_transport`` / ``clock`` — injectable effect ports (offline tests).
    """
    root = Path(docs_root or os.getenv("DOCS_ROOT") or _DEFAULT_DOCS_ROOT)
    index = DocsIndex.load(root)
    if index.files == 0:
        log.warning("docs corpus empty at DOCS_ROOT=%s — 'doc'-tier answers unavailable", root)

    repo = github_repo or os.getenv("HELP_GITHUB_REPO") or DEFAULT_REPO
    token = github_token if github_token is not None else os.getenv("HELP_GITHUB_TOKEN", "")
    subject = ops_subject or os.getenv("VEXA_OPS_SUBJECT") or _DEFAULT_OPS_SUBJECT
    base_url = (agent_api_url or os.getenv("VEXA_AGENT_API_URL") or _DEFAULT_AGENT_API_URL).rstrip("/")

    if redis_client is None:
        import redis as _redis

        redis_client = _redis.from_url(
            redis_url or os.getenv("REDIS_URL") or _DEFAULT_REDIS_URL, decode_responses=True
        )
    qlog = QuestionLog(redis_client)

    gh_kwargs = {"clock": clock} if clock is not None else {}
    gh = GitHubHelp(repo, token, transport=github_transport, **gh_kwargs)

    _vexa_env = os.getenv("VEXA_ENV", "development")
    _public_docs = _vexa_env != "production"
    app = FastAPI(
        title="Vexa Help Companion (v0.12)",
        docs_url="/docs" if _public_docs else None,
        redoc_url="/redoc" if _public_docs else None,
        openapi_url="/openapi.json" if _public_docs else None,
    )

    def _envelope(results: list, notes: list, **extra) -> dict:
        return {
            "provenance_rule": PROVENANCE_RULE,
            "retention": RETENTION_NOTE,
            "results": results,
            "notes": notes,
            **extra,
        }

    # --- liveness probe (compose healthcheck) — no auth, no downstream hop, no telemetry.
    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok", "service": "help-mcp"}

    # ---------------------------
    # Tools (each a FastAPI route; operation_id = MCP tool name)
    # ---------------------------
    @app.post("/search-docs", operation_id="search_vexa_docs")
    async def search_vexa_docs(data: SearchDocsRequest):
        """
        Search the shipped Vexa docs corpus — the 'doc' knowledge tier.

        Results are doc SECTIONS cited by path + heading with a snippet; every result carries
        provenance:"doc" (documented behavior you can rely on). An empty result usually means a
        documentation gap — try check_known_issues next, or escalate.

        Retention disclosure: your query is retained in Vexa's internal question log to find
        documentation gaps — send no code, secrets, or personal data.
        """
        results = index.search(data.query, data.max_results)
        notes: List[str] = []
        if index.files == 0:
            notes.append("docs corpus is empty at DOCS_ROOT — 'doc'-tier answers are unavailable")
        elif not results:
            notes.append("no doc section matched — likely a documentation gap; try check_known_issues or escalate")
        qlog.record("search_vexa_docs", data.query, [r["path"] for r in results], bool(results))
        return _envelope(results, notes, corpus_files=index.files)

    @app.get("/deploy-guide", operation_id="get_deploy_guide")
    async def get_deploy_guide(
        target: Literal["compose", "lite", "helm", "overview"] = Query(
            "overview", description="Deploy path: compose (documented default) | lite | helm | overview."
        ),
    ):
        """
        Assembled deployment guide for one target (compose | lite | helm | overview).

        Sections come WHOLE from the shipped docs, each labeled provenance:"doc" with its
        path + heading citation; anything the docs do not yet cover rides as an explicit
        provenance:"operational" note (live repo state — verify against your checkout).

        Retention disclosure: the requested target is retained in Vexa's internal question log
        to find documentation gaps — send no code, secrets, or personal data.
        """
        sections, notes = deploy_guide(index, target)
        qlog.record("get_deploy_guide", target, [s["path"] for s in sections], bool(sections))
        return _envelope(sections, notes, target=target)

    @app.get("/known-issues", operation_id="check_known_issues")
    async def check_known_issues(
        query: Optional[str] = Query(None, description="Optional lexical filter over issue titles + labels."),
        area: Optional[str] = Query(None, description="Optional label substring filter, e.g. 'bot' → 'area: bot'."),
    ):
        """
        Live open bugs the maintainers have ACCEPTED (labels 'status: accepted' + 'type: bug' on
        the public tracker). Every result carries provenance:"operational" — real, current state
        that is NOT yet documentation; verify before relying on it. Served from a 15-minute
        cache; if GitHub is rate-limited or unreachable you get an empty list plus a note, never
        stale certainty.

        Retention disclosure: your query is retained in Vexa's internal question log to find
        documentation gaps — send no code, secrets, or personal data.
        """
        issues, notes = gh.known_issues()
        results = filter_issues(issues, query or "", area or "")
        qlog.record("check_known_issues", query or area or "", [r["url"] for r in results], False)
        return _envelope(results, notes, repo=repo)

    @app.post("/escalate", operation_id="escalate")
    async def escalate(data: EscalateRequest):
        """
        Escalate a question the docs and known issues could not answer.

        Config-gated dual mode: when the operator set HELP_GITHUB_TOKEN this FILES a structured
        issue on the public tracker and returns its URL; without a token it returns the
        fully-formed issue draft (title + body) for you to file. Either way the escalation is
        reported to the vexa ops workspace as a help.escalated event (best-effort — an
        unreachable ops plane never blocks your escalation). Include environment FACTS only —
        the issue lands on a PUBLIC tracker: never paste secrets, tokens, or private code.

        Retention disclosure: your question is retained in Vexa's internal question log to find
        documentation gaps — send no code, secrets, or personal data.
        """
        title, body = issue_draft(data.question, data.environment_summary, data.docs_consulted)
        notes: List[str] = []
        issue_url: Optional[str] = None
        if gh.authenticated:
            try:
                issue_url = gh.create_issue(title, body) or None
            except GitHubHelpError as exc:
                notes.append(f"could not file the issue ({exc}) — returning the draft instead")
        source_uri = issue_url or f"help://draft/{uuid4()}"

        # The event.v1 hop — the vcs-ingress dispatch idiom: a plain POST of ONE envelope to
        # agent-api /events; delivery failure degrades (the escalation answer is unaffected).
        event = {
            "name": "help.escalated",
            "subject": subject,
            "occurred_at": _utcnow(),
            "source": {"uri": source_uri},
        }
        delivered = False
        try:
            with httpx.Client(timeout=10, transport=agent_transport) as client:
                resp = client.post(f"{base_url}/events", json=event)
            resp.raise_for_status()
            delivered = True
        except httpx.HTTPError as exc:
            notes.append("ops-event delivery failed (agent-api unreachable) — your escalation itself is unaffected")
            log.warning("help.escalated dispatch to %s failed: %s", base_url, exc)

        result = {
            "provenance": "operational",
            "mode": "filed" if issue_url else "draft",
            "issue_url": issue_url,
            "draft": None if issue_url else {"title": title, "body": body},
            "event": {"name": "help.escalated", "source_uri": source_uri, "delivered": delivered},
        }
        qlog.record("escalate", data.question, list(data.docs_consulted), False)
        return _envelope([result], notes)

    @app.get("/question-log", operation_id="review_question_log")
    async def review_question_log(
        since: Optional[str] = Query(None, description="ISO-8601 lower bound, e.g. 2026-07-01T00:00:00Z."),
        limit: int = Query(100, ge=1, le=1000, description="Max entries (default 100, newest kept)."),
    ):
        """
        MAINTAINER-FACING — not an end-user answer surface. Reads the help:questions telemetry
        stream back (oldest→newest) so the ops workspace's doc-gap routine can turn recurring
        friction into documentation. Entries are exactly the retained question log the other
        tools disclose: {ts, tool, question, top_paths, answered_from_docs}, each labeled
        provenance:"operational".

        Retention disclosure: this IS the retained question log (bounded, ~5000 entries) —
        reviewing it is also recorded.
        """
        try:
            entries, notes = qlog.read(since, limit)
        except ValueError:
            raise HTTPException(status_code=422, detail="since must be ISO-8601, e.g. 2026-07-01T00:00:00Z")
        qlog.record("review_question_log", since or "", [], False)
        return _envelope(entries, notes, stream=QUESTIONS_STREAM)

    # ---------------------------
    # MCP mount
    # ---------------------------
    mcp = FastApiMCP(app)
    mcp.mount_http()
    app.state.mcp = mcp
    app.state.index = index
    return app
