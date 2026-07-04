"""L2 unit — GitHub App auth: the RS256 app JWT and the doubly-scoped installation token.

Proves (offline, MockTransport + a test RSA keypair): the app JWT verifies under the public key
with the documented claims (iss = app id, ~10-minute life, drift-backdated iat); the token mint
resolves the installation per target repo and requests EXACTLY ``repositories: [<repo>]`` + the
level's permission set (L2 never gets ``contents``); and the minted token redacts itself
everywhere except ``reveal()`` (the BrokeredSecret contract).
"""
from __future__ import annotations

import json

import jwt
import pytest

from conftest import APP_ID, GITHUB_TOKEN_VALUE, INSTALLATION_ID
from vcs_executor.github_app import PERMISSIONS, GitHubApp, GitHubAppError, InstallationToken


def test_app_jwt_claims_verify_under_the_public_key(github_app: GitHubApp, rsa_keypair):
    claims = jwt.decode(github_app.app_jwt(), rsa_keypair[1], algorithms=["RS256"],
                        options={"verify_exp": False})
    assert claims["iss"] == APP_ID
    assert claims["exp"] - claims["iat"] == 10 * 60          # 9 min TTL + 60 s drift backdate
    assert claims["iat"] == 1_780_000_000 - 60               # the injected clock, backdated


def test_installation_resolved_per_repo(github_app: GitHubApp, github):
    assert github_app.installation_id("vexa-ai/vexa") == INSTALLATION_ID
    assert github.requests[0].url.path == "/repos/vexa-ai/vexa/installation"
    # the lookup authenticates as the APP (the JWT), not as any installation
    assert github.requests[0].headers["Authorization"].startswith("Bearer ey")


@pytest.mark.parametrize("level", ["L2", "L3"])
def test_mint_scopes_token_to_repo_and_level(github_app: GitHubApp, github, level, rsa_keypair):
    token = github_app.mint("vexa-ai/vexa", level)
    mint_request = github.requests[-1]
    assert mint_request.url.path == f"/app/installations/{INSTALLATION_ID}/access_tokens"
    body = json.loads(mint_request.content)
    assert body["repositories"] == ["vexa"]                  # the TARGET repo only, never org-wide
    assert body["permissions"] == PERMISSIONS[level]
    # the mint call itself is app-JWT-authenticated and the JWT verifies
    presented = mint_request.headers["Authorization"].removeprefix("Bearer ")
    assert jwt.decode(presented, rsa_keypair[1], algorithms=["RS256"],
                      options={"verify_exp": False})["iss"] == APP_ID
    assert token.reveal() == GITHUB_TOKEN_VALUE


def test_l2_permissions_never_include_contents():
    assert "contents" not in PERMISSIONS["L2"]
    assert PERMISSIONS["L3"]["contents"] == "write"
    assert PERMISSIONS["L2"] == {"issues": "write", "pull_requests": "write"}


def test_unknown_level_refuses_to_mint(github_app: GitHubApp):
    with pytest.raises(GitHubAppError, match="no permission set"):
        github_app.mint("vexa-ai/vexa", "L4")


def test_token_redacts_itself_everywhere_but_reveal():
    token = InstallationToken("ghs_raw_value", repo="o/r", level="L2")
    assert token.reveal() == "ghs_raw_value"
    for rendered in (repr(token), str(token), f"{token}", "{}".format(token)):
        assert "ghs_raw_value" not in rendered
        assert "REDACTED" in rendered


def test_missing_app_identity_fails_loud(rsa_keypair):
    with pytest.raises(GitHubAppError):
        GitHubApp("", rsa_keypair[0])
    with pytest.raises(GitHubAppError):
        GitHubApp(APP_ID, "")
