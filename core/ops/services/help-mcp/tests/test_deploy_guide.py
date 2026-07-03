"""L3 — get_deploy_guide against the REAL shipped docs corpus (offline: files in this repo).

Pins every target's selectors to the corpus: a renamed doc heading breaks HERE at gate time,
not in a user's answer. Also proves the provenance split: sections are always "doc" (cited),
undocumented targets (lite/helm) degrade to explicit "operational" notes, never invented text.
"""
import pytest
from fastapi.testclient import TestClient

from help_mcp.guide import TARGETS

from conftest import repo_docs_root


@pytest.fixture
def real_client(app_factory) -> TestClient:
    return TestClient(app_factory(docs_root=repo_docs_root()))


@pytest.mark.parametrize("target", sorted(TARGETS))
def test_every_selector_resolves_against_shipped_corpus(real_client, target):
    """Anti-drift: no target may report a 'docs gap' note on the corpus this repo ships."""
    body = real_client.get("/deploy-guide", params={"target": target}).json()
    gaps = [n for n in body["notes"] if "docs gap" in n["note"]]
    assert gaps == [], f"{target}: {gaps}"


def test_compose_target_is_fully_documented(real_client):
    body = real_client.get("/deploy-guide", params={"target": "compose"}).json()
    assert body["target"] == "compose"
    assert all(s["provenance"] == "doc" for s in body["results"])
    cited = {(s["path"], s["heading"]) for s in body["results"]}
    assert ("deployment.mdx", "Quick start (Docker Compose)") in cited
    assert ("deployment.mdx", "Publishing behind a reverse proxy") in cited
    # sections ride out whole, with content
    assert all(s["content"].strip() for s in body["results"])


def test_helm_target_mixes_doc_section_and_operational_note(real_client):
    body = real_client.get("/deploy-guide", params={"target": "helm"}).json()
    assert any(s["path"] == "core/runtime.mdx" and s["heading"] == "On Kubernetes"
               for s in body["results"])
    assert any("deploy/helm" in n["note"] and n["provenance"] == "operational"
               for n in body["notes"])


def test_lite_target_is_an_honest_gap(real_client):
    """No lite doc exists yet — the guide says so operationally instead of inventing one."""
    body = real_client.get("/deploy-guide", params={"target": "lite"}).json()
    assert body["results"] == []
    assert any("deploy/lite" in n["note"] and n["provenance"] == "operational"
               for n in body["notes"])


def test_overview_default_and_unknown_target(real_client):
    assert real_client.get("/deploy-guide").json()["target"] == "overview"
    assert real_client.get("/deploy-guide", params={"target": "kustomize"}).status_code == 422


def test_guide_calls_are_telemetered(real_client, redis_client):
    real_client.get("/deploy-guide", params={"target": "compose"})
    assert any(args[0] == "help:questions" and args[1]["tool"] == "get_deploy_guide"
               for args, _ in redis_client.xadd_calls)
