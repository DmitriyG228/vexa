"""``python -m vcs_ingress`` — the production run entrypoint (compose CMD).

Serves ``create_app()`` (agent-api base from ``VEXA_AGENT_API_URL``, redis from ``REDIS_URL``,
secret from ``VEXA_GITHUB_WEBHOOK_SECRET``); HOST/PORT from env (default port 8020, the
compose-assigned ingress port).
"""
from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    from .app import create_app

    uvicorn.run(
        create_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8020")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
