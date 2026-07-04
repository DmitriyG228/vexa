"""``create_app(...) -> FastAPI`` — the vcs executor's front door: /health + the lifecycle-managed
consumer.

The privileged half of the human gate: the ONLY holder of GitHub credentials. It consumes
human-APPROVED ``proposal.v1`` records off the ``proposal:approved`` redis stream and executes
them — nothing else, no interpretation (see ``consumer.py`` for the per-entry pipeline and
``policy.py`` for the pre-credential gate).

Assembly is fail-closed: the consumer thread starts ONLY when the service is fully configured
(redis + agent-api report token + GitHub App credentials). A partially-configured executor serves
/health, logs the gap loudly, and consumes nothing — approved entries stay on the stream, no
approval is ever burned by a misconfiguration (P18).

Every effect is injectable for the offline tests: ``redis_client`` (fakeredis),
``agent_api_transport`` / ``github_transport`` (httpx.MockTransport), ``clock``, and the whole
``consumer`` (a stub proves the lifespan wiring without threads touching real sockets).
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Optional

import httpx
from fastapi import FastAPI

from .consumer import Consumer, ResultReporter
from .github_app import GitHubApp
from .policy import protected_from_env

log = logging.getLogger(__name__)

_DEFAULT_AGENT_API_URL = "http://agent-api:8100"
_DEFAULT_REDIS_URL = "redis://redis:6379/0"


def _github_app(github_transport, clock) -> Optional[GitHubApp]:
    """The App-auth port from env — None (not a crash) when unconfigured, so the service can
    come up, answer /health, and report the gap instead of crash-looping."""
    app_id = os.getenv("VEXA_GITHUB_APP_ID", "")
    key_path = os.getenv("VEXA_GITHUB_APP_PRIVATE_KEY_PATH", "")
    if not app_id or not key_path:
        return None
    key_file = Path(key_path)
    if not key_file.exists():
        log.error("VEXA_GITHUB_APP_PRIVATE_KEY_PATH=%s does not exist — consumer stays down", key_path)
        return None
    kwargs = {"clock": clock} if clock is not None else {}
    return GitHubApp(app_id, key_file.read_text(), transport=github_transport, **kwargs)


def create_app(
    agent_api_url: Optional[str] = None,
    redis_url: Optional[str] = None,
    *,
    redis_client=None,
    agent_api_transport: Optional[httpx.BaseTransport] = None,
    github_transport: Optional[httpx.BaseTransport] = None,
    clock: Optional[Callable[[], float]] = None,
    consumer: Optional[Consumer] = None,
    consume: Optional[bool] = None,
) -> FastAPI:
    """Build the executor app.

    ``agent_api_url``       — the result-report sink base (env ``VEXA_AGENT_API_URL``).
    ``redis_url``           — the approved-feed backing (env ``REDIS_URL``); ignored when a
                              ``redis_client`` is injected (fakeredis in the tests).
    ``agent_api_transport`` / ``github_transport`` — httpx transport overrides (offline tests).
    ``clock``               — time source override (JWT claims provable without real time).
    ``consumer``            — a fully-built consumer (the tests' lifespan stub).
    ``consume``             — force the consumer thread on/off; default: on when fully configured
                              (env ``VEXA_EXECUTOR_CONSUME`` ≠ ``0``).
    """
    base_url = (agent_api_url or os.getenv("VEXA_AGENT_API_URL") or _DEFAULT_AGENT_API_URL).rstrip("/")
    report_token = os.getenv("VEXA_EXECUTOR_RESULT_TOKEN", "")
    protected = protected_from_env(os.getenv("VEXA_PROTECTED_BRANCHES"))

    if redis_client is None and consumer is None:
        import redis as _redis

        redis_client = _redis.from_url(
            redis_url or os.getenv("REDIS_URL") or _DEFAULT_REDIS_URL, decode_responses=True
        )

    built = consumer
    missing: list[str] = []
    if built is None:
        github = _github_app(github_transport, clock)
        if github is None:
            missing.append("VEXA_GITHUB_APP_ID/VEXA_GITHUB_APP_PRIVATE_KEY_PATH")
        if not report_token:
            missing.append("VEXA_EXECUTOR_RESULT_TOKEN")
        if not missing:
            reporter = ResultReporter(base_url, report_token, transport=agent_api_transport)
            built = Consumer(
                redis_client, reporter,
                mint=github.mint, protected=protected,
                api_base=os.getenv("VEXA_GITHUB_API_URL", "https://api.github.com"),
                transport=github_transport,
            )

    should_consume = consume if consume is not None else os.getenv("VEXA_EXECUTOR_CONSUME", "1") != "0"
    stop = threading.Event()
    state: dict = {"thread": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if built is not None and should_consume:
            thread = threading.Thread(target=built.run_forever, args=(stop,), daemon=True,
                                      name="vcs-executor-consumer")
            thread.start()
            state["thread"] = thread
            log.info("consumer started (group-owned XREADGROUP on proposal:approved)")
        elif should_consume:
            # Fail-closed, fail-loud: unconfigured ⇒ consume NOTHING (entries stay on the
            # stream — no approval is burned), and say so on every boot (P18).
            log.error("executor not fully configured (%s) — consumer stays down", ", ".join(missing))
        yield
        stop.set()
        if state["thread"] is not None:
            state["thread"].join(timeout=10)

    app = FastAPI(title="vexa-vcs-executor", version="0.12.0", lifespan=lifespan)
    app.state.consumer = built
    app.state.consumer_missing = missing

    # ── liveness probe (compose healthcheck) — no auth, no redis, no GitHub hop ────────────────
    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "service": "vcs-executor"}

    return app
