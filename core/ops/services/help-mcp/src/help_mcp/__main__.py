"""``python -m help_mcp`` — the production run entrypoint (compose CMD).

Serves ``create_app()`` (docs corpus from ``DOCS_ROOT``, agent-api base from
``VEXA_AGENT_API_URL``, redis from ``REDIS_URL``, the optional ``HELP_GITHUB_TOKEN``);
HOST/PORT from env (default port 8011, the compose-assigned help-mcp port).
"""
from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    from .app import create_app

    uvicorn.run(
        create_app(),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8011")),
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
