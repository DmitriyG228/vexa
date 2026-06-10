"""Unit tests for the EI lineage module (pack ei-lineage, issue #24).

Pure-function coverage: org sanitization, provider-agnostic agent command
resolution, frozen-contract response shaping, prompt invariants. The live
behavior (claim/run/sign against a real stack) is covered by the compose
regression tests3/tests/ei-lineage.sh.
"""

import pytest

from agent_api import config, lineage


# ── sanitize_org_id ────────────────────────────────────────────────────────

def test_sanitize_org_id_passthrough():
    assert lineage.sanitize_org_id("acme-corp_1") == "acme-corp_1"


def test_sanitize_org_id_normalizes():
    assert lineage.sanitize_org_id("Acme Corp / EU!") == "acme-corp-eu"


def test_sanitize_org_id_rejects_empty():
    with pytest.raises(ValueError):
        lineage.sanitize_org_id("///")


def test_sanitize_org_id_blocks_traversal():
    # Path separators must never survive into workspace paths.
    assert "/" not in lineage.sanitize_org_id("../../etc")
    assert ".." not in lineage.sanitize_org_id("../../etc")


# ── _agent_command (provider via env/org config only — never hardcoded) ────

def test_agent_command_org_override_wins(monkeypatch):
    monkeypatch.setattr(config, "EI_AGENT_CMD", "env-cmd -p")
    assert lineage._agent_command({"agent_cli": "vibe run"}) == "vibe run"


def test_agent_command_env_override(monkeypatch):
    monkeypatch.setattr(config, "EI_AGENT_CMD", "opencode --quiet -p")
    assert lineage._agent_command({}) == "opencode --quiet -p"


def test_agent_command_default_built_from_agent_cli(monkeypatch):
    monkeypatch.setattr(config, "EI_AGENT_CMD", "")
    monkeypatch.setattr(config, "AGENT_CLI", "claude")
    monkeypatch.setattr(config, "AGENT_ALLOWED_TOOLS", "Read,Write")
    monkeypatch.setattr(config, "DEFAULT_MODEL", "")
    cmd = lineage._agent_command({})
    assert cmd.startswith("claude ")
    assert cmd.endswith(" -p")
    assert "Read,Write" in cmd
    assert "--model" not in cmd


def test_agent_command_org_model(monkeypatch):
    monkeypatch.setattr(config, "EI_AGENT_CMD", "")
    monkeypatch.setattr(config, "AGENT_CLI", "claude")
    monkeypatch.setattr(config, "DEFAULT_MODEL", "")
    cmd = lineage._agent_command({"model": "some-local-model"})
    assert "--model some-local-model" in cmd


# ── Frozen sign-API contract v1 shape ──────────────────────────────────────

def test_contract_shape_exact_keys():
    record = {
        "id": "prop_abc", "org_id": "acme", "meeting_id": 7,
        "meeting_title": "t", "created_at": "2026-06-10T00:00:00Z",
        "branch": "meeting/7", "summary": "s", "files_changed": 2,
        "status": "open", "internal_field": "must-not-leak",
    }
    shaped = lineage._contract_shape(record)
    assert set(shaped) == {"id", "meeting_id", "meeting_title", "created_at",
                           "branch", "summary", "files_changed"}
    assert shaped["branch"] == "meeting/7"


# ── Prompt invariants ──────────────────────────────────────────────────────

def test_prompt_mentions_conventions_and_inputs():
    prompt = lineage._build_prompt(42, {"platform": "google_meet"})
    assert "AGENT.md" in prompt
    assert "../input/transcript.md" in prompt
    assert "graph/kg/entities/meetings/42-" in prompt
    assert "Do not run git commands" in prompt
