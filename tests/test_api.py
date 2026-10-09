from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database import Base, get_session
from backend.main import app, get_engine
from backend.config import get_settings
from backend.models import ReviewIssue, ReviewRequest, ReviewResponse


class FakeReviewEngine:
    last_context: str | None = None

    async def review(self, request: ReviewRequest, context: str | None = None):
        FakeReviewEngine.last_context = context
        issues = []
        if "select" in request.code.lower():
            issues = [
                ReviewIssue(
                    category="SECURITY",
                    severity="HIGH",
                    title="Possible SQL injection",
                    file_path=request.file_path,
                    line=2,
                    description="Query text may contain user input.",
                    suggestion="Use a parameterized query.",
                    confidence=0.92,
                )
            ]
        return ReviewResponse(
            summary=f"Found {len(issues)} issue(s).",
            issues=issues,
            overall_risk="HIGH" if issues else "LOW",
            model="test-model",
        )


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def test_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_engine] = FakeReviewEngine
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


def test_healthcheck(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_review_endpoint_detects_issue_and_saves_history(client: TestClient):
    payload = {
        "language": "python",
        "file_path": "src/users.py",
        "code": 'def get_user(user_id):\n    query = f"SELECT * FROM users WHERE id = {user_id}"\n    return query',
    }
    response = client.post("/review", json=payload)
    assert response.status_code == 200
    assert response.json()["overall_risk"] == "HIGH"
    assert response.json()["issues"][0]["category"] == "SECURITY"
    assert response.json()["model"] == "test-model"

    history = client.get("/reviews")
    assert history.status_code == 200
    assert history.json()[0]["file_path"] == "src/users.py"
    assert history.json()[0]["issue_count"] == 1
    assert client.get("/dashboard/stats").json()["total_reviews"] == 1


def test_review_requires_non_empty_code(client: TestClient):
    response = client.post("/review", json={"language": "python", "code": ""})
    assert response.status_code == 422


def test_review_history_returns_not_found_for_missing_review(client: TestClient):
    response = client.get("/reviews/999")
    assert response.status_code == 404


def test_code_review_can_be_queued(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    class FakeTask:
        id = "test-task-id"

    monkeypatch.setattr("backend.main.review_code_task.delay", lambda *_: FakeTask())
    response = client.post(
        "/review/jobs",
        json={"language": "python", "code": "print('hello')"},
    )
    assert response.status_code == 202
    job_id = response.json()["id"]
    status = client.get(f"/review/jobs/{job_id}")
    assert status.json()["status"] == "queued"


def test_github_webhook_verifies_signature_and_queues_review(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class FakeTask:
        id = "github-test-task"

    enqueue_calls = 0

    def enqueue(*_):
        nonlocal enqueue_calls
        enqueue_calls += 1
        return FakeTask()

    monkeypatch.setattr("backend.main.settings", replace(get_settings(), github_webhook_secret="test-secret"))
    monkeypatch.setattr("backend.main.review_github_task.delay", enqueue)
    body = json.dumps(
        {
            "action": "opened",
            "number": 42,
            "repository": {"name": "service", "owner": {"login": "acme"}},
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()

    rejected = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=bad",
            "X-GitHub-Delivery": "delivery-42",
        },
    )
    assert rejected.status_code == 401

    accepted = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Delivery": "delivery-42",
        },
    )
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "queued"
    job_id = accepted.json()["job_id"]
    assert client.get(f"/review/jobs/{job_id}").json()["status"] == "queued"

    duplicate = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Delivery": "delivery-42",
        },
    )
    assert duplicate.status_code == 202
    assert duplicate.json() == {
        "status": "duplicate",
        "duplicate": True,
        "job_id": job_id,
    }
    assert enqueue_calls == 1


def test_github_webhook_can_retry_when_queue_temporarily_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class FakeTask:
        id = "github-retry-task"

    monkeypatch.setattr(
        "backend.main.settings",
        replace(get_settings(), github_webhook_secret="test-secret"),
    )
    enqueue_calls = 0

    def enqueue(*_):
        nonlocal enqueue_calls
        enqueue_calls += 1
        if enqueue_calls == 1:
            raise RuntimeError("temporary queue outage")
        return FakeTask()

    monkeypatch.setattr("backend.main.review_github_task.delay", enqueue)
    body = json.dumps(
        {
            "action": "opened",
            "number": 42,
            "repository": {"name": "service", "owner": {"login": "acme"}},
        }
    ).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-Hub-Signature-256": signature,
        "X-GitHub-Delivery": "retry-delivery",
    }

    first_attempt = client.post("/webhooks/github", content=body, headers=headers)
    assert first_attempt.status_code == 503
    retry = client.post("/webhooks/github", content=body, headers=headers)
    assert retry.status_code == 202
    assert retry.json()["status"] == "queued"
    assert enqueue_calls == 2


def test_github_webhook_ignores_non_pull_request_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "backend.main.settings",
        replace(get_settings(), github_webhook_secret="test-secret"),
    )
    body = json.dumps({"action": "created"}).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "issue_comment",
            "X-Hub-Signature-256": signature,
            "X-GitHub-Delivery": "ignored-delivery",
        },
    )
    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}


def test_github_webhook_requires_delivery_id(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        "backend.main.settings",
        replace(get_settings(), github_webhook_secret="test-secret"),
    )
    body = json.dumps({"action": "opened"}).encode()
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": signature,
        },
    )
    assert response.status_code == 400


def test_repository_context_is_indexed_and_used_for_reviews(client: TestClient):
    indexed = client.post(
        "/repositories/index",
        json={
            "repository": "acme/service",
            "files": [
                {
                    "path": "models/user.py",
                    "content": "def load_user(user_id):\n    return User.query.get(user_id)",
                }
            ],
        },
    )
    assert indexed.status_code == 200
    assert indexed.json()["chunks_indexed"] == 1

    second_index = client.post(
        "/repositories/index",
        json={
            "repository": "acme/service",
            "files": [{"path": "src/helpers.py", "content": "def format_name(name): return name.strip()"}],
        },
    )
    assert second_index.status_code == 200

    reviewed = client.post(
        "/review",
        json={
            "language": "python",
            "repository": "acme/service",
            "code": "def get_user(user_id):\n    return load_user(user_id)",
        },
    )
    assert reviewed.status_code == 200
    assert FakeReviewEngine.last_context
    assert "models/user.py" in FakeReviewEngine.last_context
