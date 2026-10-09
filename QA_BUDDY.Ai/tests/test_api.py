import base64
import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from qabuddy import api
from test_answer import FakeLLM, FakeRetriever, _hit


class FakeStore:
    def health(self):
        return {"ok": True, "collection": "qabuddy", "exists": True}

    def counts_by(self, field):
        return {"selenium": 12, "test_cases": 600}

    def get(self, point_id):
        return None


class FakeEmbedder:
    def health(self):
        return {"ok": True, "model": "qwen3-embedding:0.6b"}


@pytest.fixture
def client(monkeypatch):
    retriever = FakeRetriever([_hit("p1", "Selenium framework · RetryAnalyzer.java\nmaxRetryCount = 3")])
    monkeypatch.setattr(api, "services", lambda: (FakeStore(), FakeEmbedder(), retriever, FakeLLM()))
    return TestClient(api.app)


def test_health_and_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    config = client.get("/api/config").json()
    counts = {s["key"]: s["chunks"] for s in config["sources"]}
    assert counts["selenium"] == 12 and counts["jira"] == 0
    assert len(config["sources"]) == 10
    assert {m["key"] for m in config["modes"]} >= {"ask", "test_design", "rca", "framework", "flaky", "rtm"}


def test_chat_streams_server_sent_events(client):
    response = client.post("/api/chat", json={"question": "How many retries?", "sources": ["selenium"]})
    assert response.status_code == 200
    events = [block.split("\n") for block in response.text.strip().split("\n\n")]
    names = [lines[0].removeprefix("event: ") for lines in events]
    assert names[0] == "sources" and names[-1] == "done"
    done = json.loads(events[-1][1].removeprefix("data: "))
    assert done["cited"] == [1]


def test_chat_validates_input(client):
    assert client.post("/api/chat", json={"question": ""}).status_code == 422


def test_web_app_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "QABuddy" in page.text


def test_basic_auth_when_configured(client, monkeypatch):
    monkeypatch.setattr(api, "settings", dataclasses.replace(api.settings, auth_user="qa", auth_password="s3cret"))
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/health").status_code == 200  # health stays open for uptime checks
    token = base64.b64encode(b"qa:s3cret").decode()
    assert client.get("/api/config", headers={"Authorization": f"Basic {token}"}).status_code == 200
