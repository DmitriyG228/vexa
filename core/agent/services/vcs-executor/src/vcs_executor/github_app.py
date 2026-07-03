"""github_app.py — per-proposal GitHub App installation tokens, minted fresh, scoped twice.

The trust design (P15): this service is the ONLY credential holder, and even it never holds a
long-lived repo credential. Per approved proposal it:

1. signs a short-lived (~10 min) RS256 **app JWT** from ``VEXA_GITHUB_APP_ID`` + the private key
   at ``VEXA_GITHUB_APP_PRIVATE_KEY_PATH`` (mounted secret file, never env-inlined);
2. resolves the installation for the TARGET repo (``GET /repos/{owner}/{repo}/installation``);
3. mints an installation token scoped to exactly that repository AND the proposal's level:
   L2 → ``{issues: write, pull_requests: write}`` (annotate),
   L3 → the same + ``{contents: write}`` (mutate).

The minted token is wrapped in ``InstallationToken`` — the same redaction contract identity's
``BrokeredSecret`` documents (value reachable ONLY via ``reveal()``; repr/str/format are
redacted), so an accidental log line or f-string leaks nothing.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

import httpx
import jwt

_API_BASE = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"

# The level → token-permission binding (the credential-side twin of policy.LEVEL_ACTIONS):
# an L2 token structurally cannot push, whatever the executor code does with it.
PERMISSIONS: dict[str, dict[str, str]] = {
    "L2": {"issues": "write", "pull_requests": "write"},
    "L3": {"issues": "write", "pull_requests": "write", "contents": "write"},
}

JWT_TTL_SEC = 9 * 60          # < GitHub's 10-minute app-JWT maximum
JWT_CLOCK_DRIFT_SEC = 60      # iat backdated against clock drift (GitHub's documented advice)


class GitHubAppError(RuntimeError):
    """App auth / token minting failed — the proposal is reported failed, fail-loud (P18)."""


class InstallationToken:
    """A minted installation token that redacts itself (the ``BrokeredSecret`` contract).

    The raw value is reachable ONLY via ``reveal()`` — repr/str/format all print the redacted
    form, so the token cannot ride a log record or an exception message by accident.
    """

    __slots__ = ("_value", "repo", "level", "expires_at")
    _REDACTED = "***REDACTED***"

    def __init__(self, value: str, *, repo: str, level: str, expires_at: str = "") -> None:
        self._value = value
        self.repo = repo
        self.level = level
        self.expires_at = expires_at

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"InstallationToken(repo={self.repo!r}, level={self.level!r}, value={self._REDACTED})"

    __str__ = __repr__

    def __format__(self, _spec: str) -> str:
        return self._REDACTED


class GitHubApp:
    """The App-auth port: app JWT → installation lookup → scoped installation token.

    ``transport`` is injectable (``httpx.MockTransport`` in the offline tests); ``clock`` too, so
    JWT claims are provable without real time.
    """

    def __init__(
        self,
        app_id: str,
        private_key_pem: str,
        *,
        api_base: str = _API_BASE,
        transport: Optional[httpx.BaseTransport] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not app_id:
            raise GitHubAppError("VEXA_GITHUB_APP_ID is required")
        if not private_key_pem:
            raise GitHubAppError("the GitHub App private key is required")
        self._app_id = app_id
        self._key = private_key_pem
        self._base = api_base.rstrip("/")
        self._transport = transport
        self._clock = clock

    def app_jwt(self) -> str:
        """The short-lived RS256 app JWT (iss = the App id)."""
        now = int(self._clock())
        claims = {"iat": now - JWT_CLOCK_DRIFT_SEC, "exp": now + JWT_TTL_SEC, "iss": self._app_id}
        return jwt.encode(claims, self._key, algorithm="RS256")

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base,
            headers={"Authorization": f"Bearer {self.app_jwt()}", "Accept": _ACCEPT},
            timeout=15,
            transport=self._transport,
        )

    def installation_id(self, repo: str) -> int:
        """The App installation covering ``repo`` (``owner/name``)."""
        with self._client() as client:
            resp = client.get(f"/repos/{repo}/installation")
        if resp.status_code != 200:
            raise GitHubAppError(
                f"no App installation for {repo!r} (GET /repos/{repo}/installation → {resp.status_code})"
            )
        return int(resp.json()["id"])

    def mint(self, repo: str, level: str) -> InstallationToken:
        """A fresh installation token scoped to ``repo`` + the ``level``'s permission set."""
        permissions = PERMISSIONS.get(level)
        if permissions is None:
            raise GitHubAppError(f"no permission set for level {level!r} — refusing to mint")
        name = repo.split("/", 1)[-1]
        body = {"repositories": [name], "permissions": permissions}
        with self._client() as client:
            resp = client.post(f"/app/installations/{self.installation_id(repo)}/access_tokens", json=body)
        if resp.status_code != 201:
            raise GitHubAppError(
                f"installation token mint for {repo!r} failed "
                f"(POST access_tokens → {resp.status_code})"
            )
        data = resp.json()
        return InstallationToken(
            data["token"], repo=repo, level=level, expires_at=data.get("expires_at", "")
        )
