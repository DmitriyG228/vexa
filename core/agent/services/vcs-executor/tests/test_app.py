"""L3 seam — app assembly: the lifespan-managed consumer and the fail-closed configuration gate.

Proves: a fully-configured app (App id + key file + report secret in env) builds a real consumer
and the lifespan starts/stops its thread; an UNCONFIGURED app comes up (health serves, the gap is
recorded on ``app.state``) but consumes NOTHING — approved entries stay on the stream, no
approval is burned by a misconfiguration.
"""
from __future__ import annotations

import threading

import httpx
from fastapi.testclient import TestClient

from conftest import REPORT_TOKEN, APP_ID
from vcs_executor import create_app


class StubConsumer:
    """Records the lifespan contract: run_forever(stop) runs until stop is set."""

    def __init__(self):
        self.started = threading.Event()
        self.stopped = threading.Event()

    def run_forever(self, stop: threading.Event, **_kwargs) -> None:
        self.started.set()
        stop.wait(timeout=10)
        self.stopped.set()


def test_lifespan_starts_and_stops_the_consumer():
    stub = StubConsumer()
    app = create_app("http://agent-api.test", consumer=stub, consume=True)
    with TestClient(app):
        assert stub.started.wait(timeout=5)
        assert not stub.stopped.is_set()
    assert stub.stopped.wait(timeout=5)          # shutdown set the stop event and joined


def test_unconfigured_app_serves_health_but_consumes_nothing(
    monkeypatch, redis_client, agent_api, github
):
    for var in ("VEXA_GITHUB_APP_ID", "VEXA_GITHUB_APP_PRIVATE_KEY_PATH", "VEXA_EXECUTOR_RESULT_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    app = create_app(
        "http://agent-api.test",
        redis_client=redis_client,
        agent_api_transport=httpx.MockTransport(agent_api.handler),
        github_transport=httpx.MockTransport(github.handler),
        consume=True,                              # asked to consume — but the config gate says no
    )
    assert app.state.consumer is None
    assert app.state.consumer_missing               # the gap is a visible state (P18)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert agent_api.requests == [] and github.requests == []


def test_configured_app_builds_a_real_consumer(monkeypatch, tmp_path, rsa_keypair,
                                               redis_client, agent_api, github):
    key_file = tmp_path / "app-key.pem"
    key_file.write_text(rsa_keypair[0])
    monkeypatch.setenv("VEXA_GITHUB_APP_ID", APP_ID)
    monkeypatch.setenv("VEXA_GITHUB_APP_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("VEXA_EXECUTOR_RESULT_TOKEN", REPORT_TOKEN)
    monkeypatch.setenv("VEXA_PROTECTED_BRANCHES", "main,release/2026")
    app = create_app(
        "http://agent-api.test",
        redis_client=redis_client,
        agent_api_transport=httpx.MockTransport(agent_api.handler),
        github_transport=httpx.MockTransport(github.handler),
        consume=False,                             # wiring proof only — no thread in this test
    )
    assert app.state.consumer is not None
    assert app.state.consumer_missing == []
