"""L3 — check_known_issues: the triage-label fetch (spaces and all), the 15-minute TTL cache
(proven with the injected clock), the anonymous/token header split, client-side query/area
filters, and the rate-limit + network degrade paths (empty + a note, never a crash)."""
from fastapi.testclient import TestClient

from help_mcp.gh import CACHE_TTL_SEC, TRIAGE_LABELS


def test_fetch_uses_the_triage_labels_with_spaces(client: TestClient, gh):
    r = client.get("/known-issues")
    assert r.status_code == 200
    req = gh.requests[0]
    assert req.url.params["labels"] == TRIAGE_LABELS == "status: accepted,type: bug"
    assert req.url.params["state"] == "open"
    assert req.url.path == "/repos/Vexa-ai/vexa/issues"


def test_results_are_operational_and_shaped(client: TestClient):
    results = client.get("/known-issues").json()["results"]
    assert results, "canned issues expected"
    for r in results:
        assert r["provenance"] == "operational"
        assert set(r) == {"provenance", "title", "url", "labels", "updated_at"}
    # the PR riding the issues endpoint is filtered out
    assert not any("/pull/" in r["url"] for r in results)


def test_anonymous_sends_no_auth_header(client: TestClient, gh):
    client.get("/known-issues")
    assert "authorization" not in {k.lower() for k in gh.requests[0].headers}


def test_token_sends_bearer(app_factory, gh):
    TestClient(app_factory(github_token="tok-123")).get("/known-issues")
    assert gh.requests[0].headers["Authorization"] == "Bearer tok-123"


def test_ttl_cache_with_injected_clock(client: TestClient, gh, clock):
    client.get("/known-issues")
    client.get("/known-issues")
    assert len(gh.requests) == 1, "second call within the TTL must be served from cache"
    clock.advance(CACHE_TTL_SEC + 1)
    client.get("/known-issues")
    assert len(gh.requests) == 2, "an expired cache refetches"


def test_query_filter_is_lexical(client: TestClient):
    results = client.get("/known-issues", params={"query": "recording upload"}).json()["results"]
    assert [r["url"] for r in results] == ["https://github.com/Vexa-ai/vexa/issues/102"]


def test_area_filter_matches_label_substring(client: TestClient):
    results = client.get("/known-issues", params={"area": "bot"}).json()["results"]
    assert [r["url"] for r in results] == ["https://github.com/Vexa-ai/vexa/issues/101"]


def test_rate_limit_degrades_with_a_note(client: TestClient, gh):
    gh.status = 403
    body = client.get("/known-issues").json()
    assert body["results"] == []
    assert any("rate limit" in n for n in body["notes"])
    assert any("HELP_GITHUB_TOKEN" in n for n in body["notes"]), "anonymous degrade names the fix"


def test_rate_limit_failure_is_not_cached(client: TestClient, gh):
    gh.status = 429
    assert client.get("/known-issues").json()["results"] == []
    gh.status = 200
    assert client.get("/known-issues").json()["results"], "recovery is immediate — failures never cached"


def test_network_error_degrades_with_a_note(app_factory):
    import httpx

    def boom(request):
        raise httpx.ConnectError("no route to github")

    c = TestClient(app_factory(github_transport=httpx.MockTransport(boom)))
    body = c.get("/known-issues").json()
    assert body["results"] == []
    assert any("unreachable" in n for n in body["notes"])
