"""O-AG-2 — the GitHub-per-user ``VcsPort`` over a BROKERED token (P15).

Proves: a push uses the token fetched from the identity ``SecretsPort`` (audited), the push lands
in the target repo, and the raw token NEVER appears in a log line or the persisted git remote.
The remote here is a bare LOCAL repo (no network); the token is brokered via a fake that mirrors
identity's redacting ``BrokeredSecret`` shape.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from shared.adapters import GitHubVcs, RealGitWorkspace
from shared.models import WorkspaceWrite

from .fakes import FakeSecretsBroker

TOKEN = "ghp_SUPERSECRETtoken1234567890"


@pytest.fixture
def origin_repo(tmp_path: Path) -> str:
    origin = tmp_path / "origin"
    origin.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=origin, check=True, capture_output=True)
    run("init", "-b", "main")
    run("config", "user.name", "seed")
    run("config", "user.email", "seed@example.com")
    (origin / "README.md").write_text("# memory\n")
    run("add", "-A")
    run("commit", "-m", "seed")
    return str(origin)


@pytest.fixture
def remote_repo(tmp_path: Path) -> str:
    """A bare local repo standing in for the user's GitHub remote (no network)."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    # use a real URL-like form so the adapter exercises the token-in-URL path
    return f"file://{remote}"


def test_push_uses_brokered_token_and_lands(tmp_path: Path, origin_repo: str, remote_repo: str):
    ws = RealGitWorkspace(tmp_path / "work")
    ws.clone(origin_repo, "main")
    ws.write(WorkspaceWrite(
        path="kg/entities/meeting/m1.md",
        frontmatter={"type": "meeting", "id": "m1", "title": "M1"},
        body="hi",
    ))
    ws.commit("add m1")

    broker = FakeSecretsBroker({"workspace_git.token": TOKEN})
    vcs = GitHubVcs(broker, subject="user-7")
    sha = vcs.push(str(tmp_path / "work"), remote_repo, "main")

    # the broker was consulted with the right subject/scope (audit records metadata, never the value)
    assert broker.audit == [("user-7", "workspace_git.token", "repo:push")]
    # the push landed: the bare remote now has our commit at main
    remote_path = remote_repo.replace("file://", "")
    remote_head = subprocess.run(
        ["git", "rev-parse", "main"], cwd=remote_path, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert remote_head == sha


def test_token_never_logged_or_persisted(tmp_path: Path, origin_repo: str, remote_repo: str, caplog):
    ws = RealGitWorkspace(tmp_path / "work")
    ws.clone(origin_repo, "main")
    ws.write(WorkspaceWrite(
        path="kg/entities/meeting/m1.md",
        frontmatter={"type": "meeting", "id": "m1", "title": "M1"},
        body="hi",
    ))
    ws.commit("add m1")

    broker = FakeSecretsBroker({"workspace_git.token": TOKEN})
    vcs = GitHubVcs(broker, subject="user-7")
    with caplog.at_level(logging.DEBUG):
        vcs.push(str(tmp_path / "work"), remote_repo, "main")

    # P15: the raw token appears NOWHERE in the captured logs
    assert TOKEN not in caplog.text
    assert "***REDACTED***" in caplog.text  # the redacted form is what got logged

    # ...and the token was stripped from the persisted remote (can't leak to the repo/object store)
    persisted = subprocess.run(
        ["git", "remote", "get-url", "vexa-vcs"],
        cwd=tmp_path / "work", check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert TOKEN not in persisted
    assert persisted == remote_repo


def test_open_pr_uses_brokered_token_and_returns_the_pr_url(monkeypatch, caplog):
    """The worker-side ``VcsPort.open_pr`` counterpart of the vcs-executor's PR call: one POST to
    /repos/{owner}/{repo}/pulls, the brokered token revealed ONLY into the Authorization header,
    logged only redacted."""
    import io
    import json as _json
    import logging
    import urllib.request

    from shared import adapters as adapters_mod

    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = _json.loads(req.data)

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp(_json.dumps({"html_url": "https://github.com/user-7/memory/pull/12"}).encode())

    monkeypatch.setattr(adapters_mod.urllib.request, "urlopen", fake_urlopen)
    broker = FakeSecretsBroker({"workspace_git.token": TOKEN})
    vcs = GitHubVcs(broker, subject="user-7")

    with caplog.at_level(logging.DEBUG):
        url = vcs.open_pr(
            "https://github.com/user-7/memory.git",
            base="main", head="vexa/prop_1-docs-fix", title="docs: fix", body="why",
        )

    assert url == "https://github.com/user-7/memory/pull/12"
    assert captured["url"] == "https://api.github.com/repos/user-7/memory/pulls"
    assert captured["auth"] == f"Bearer {TOKEN}"                 # revealed ONLY into the header
    assert captured["body"] == {"title": "docs: fix", "body": "why",
                                "head": "vexa/prop_1-docs-fix", "base": "main"}
    # the broker audited the PR scope; the raw token never rode a log line (P15)
    assert broker.audit == [("user-7", "workspace_git.token", "repo:pr")]
    assert TOKEN not in caplog.text
    assert "***REDACTED***" in caplog.text


def test_open_pr_refuses_an_underivable_remote():
    vcs = GitHubVcs(FakeSecretsBroker({"workspace_git.token": TOKEN}), subject="user-7")
    with pytest.raises(ValueError, match="owner/name"):
        vcs.open_pr("not-a-remote", base="main", head="vexa/prop_1-x", title="t", body="b")
