"""``python -m vcs_executor`` — the production run entrypoint (compose CMD).

Serves ``create_app()`` (agent-api base from ``VEXA_AGENT_API_URL``, redis from ``REDIS_URL``,
GitHub App identity from ``VEXA_GITHUB_APP_ID`` + ``VEXA_GITHUB_APP_PRIVATE_KEY_PATH``, the
report-back secret from ``VEXA_EXECUTOR_RESULT_TOKEN``); HOST/PORT from env (default port 8021,
the compose-assigned executor port). The consumer thread rides the app lifespan.
"""
from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    from .app import create_app

    uvicorn.run(
        create_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8021")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
