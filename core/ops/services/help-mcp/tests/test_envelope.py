"""L1 contract — the shared response envelope: EVERY tool response carries the provenance rule
+ the retention notice, and EVERY item in every results array is labeled 'doc' or 'operational'.
The two-tier labeling is the product's core promise — this eval pins it across the whole surface."""
import pytest
from fastapi.testclient import TestClient

from help_mcp import PROVENANCE_RULE, RETENTION_NOTE

CALLS = [
    ("search_vexa_docs", "POST", "/search-docs", {"json": {"query": "docker compose quick start"}}),
    ("get_deploy_guide", "GET", "/deploy-guide", {"params": {"target": "overview"}}),
    ("check_known_issues", "GET", "/known-issues", {}),
    ("escalate", "POST", "/escalate", {"json": {"question": "how do I publish the gateway?"}}),
    ("review_question_log", "GET", "/question-log", {}),
]


@pytest.mark.parametrize("name,method,path,kwargs", CALLS, ids=[c[0] for c in CALLS])
def test_envelope_and_provenance_on_every_tool(client: TestClient, name, method, path, kwargs):
    body = client.request(method, path, **kwargs).json()
    assert body["provenance_rule"] == PROVENANCE_RULE
    assert body["retention"] == RETENTION_NOTE
    assert isinstance(body["results"], list) and isinstance(body["notes"], list)
    for item in body["results"]:
        assert item["provenance"] in {"doc", "operational"}, f"{name}: unlabeled result {item}"


def test_search_results_are_doc_tier_with_citations(client: TestClient):
    results = client.post("/search-docs", json={"query": "docker compose quick start"}).json()["results"]
    assert results
    for r in results:
        assert r["provenance"] == "doc"
        assert r["path"] and r["heading"] and r["snippet"]
        assert isinstance(r["score"], float)
