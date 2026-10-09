from __future__ import annotations

import asyncio
from dataclasses import replace

from backend.agents.reviewer import ReviewerWorkflow
from backend.config import get_settings
from backend.models import ReviewResponse
from backend.services.github import GitHubClient
from backend.services.static_analysis import StaticAnalyzer


class FakeGroqClient:
    async def complete_json(self, system_prompt: str, user_prompt: str):
        assert isinstance(user_prompt, str)
        if "You are the critic" in system_prompt:
            return {
                "findings": [
                    {
                        "category": "SECURITY",
                        "severity": "HIGH",
                        "title": "Possible SQL injection",
                        "line": 1,
                        "description": "A SQL query includes user-provided input.",
                        "suggestion": "Use parameterized query arguments.",
                        "confidence": 0.91,
                    }
                ],
                "summary": "One high-severity SQL injection risk was confirmed.",
            }
        if "code review planner" in system_prompt:
            return {"categories": ["SECURITY"]}
        return {"findings": []}


def test_langgraph_workflow_returns_critic_result():
    workflow = ReviewerWorkflow(client=FakeGroqClient())
    issues, summary = asyncio.run(
        workflow.review(
            code='query = f"SELECT * FROM users WHERE id = {user_id}"',
            language="python",
            file_path="users.py",
            context=None,
            static_findings=[],
        )
    )
    assert len(issues) == 1
    assert issues[0].category == "SECURITY"
    assert summary.startswith("One high-severity")


def test_static_analyzer_detects_eval():
    findings = StaticAnalyzer().analyze("result = eval(user_input)", "python")
    assert any(finding.title == "Dynamic code execution" for finding in findings)


def test_static_analyzer_detects_sql_fstring_and_constant_zero():
    source = 'query = f"SELECT * FROM users WHERE id = {user_id}"\nresult = total / 0'
    findings = StaticAnalyzer().analyze(source, "python")
    assert {finding.title for finding in findings} == {
        "Possible SQL injection",
        "Division by zero",
    }


def test_static_analyzer_ignores_comments_and_strings():
    source = '# eval(user_input)\nmessage = "f\\"SELECT * FROM users\\""\n'
    findings = StaticAnalyzer().analyze(source, "python")
    assert findings == []


def test_github_added_line_parser_tracks_hunks():
    patch = "@@ -3,2 +3,3 @@\n old\n+added one\n context\n+added two"
    assert GitHubClient._added_line_numbers(patch) == {4, 6}


def test_github_pull_request_review_sets_combined_response_model(monkeypatch):
    client = GitHubClient()
    monkeypatch.setattr(client, "_get_json", _async_result({"head": {"sha": "head"}}))
    monkeypatch.setattr(client, "_get_paginated_list", _async_result([]))
    monkeypatch.setattr(client, "_retrieve_context", lambda *_: None)
    monkeypatch.setattr(client, "_post_review", _async_result(None))

    class FakeReviewEngine:
        @staticmethod
        def _risk_from_issues(_):
            return "LOW"

    combined, repository = asyncio.run(
        client.review_pull_request("acme", "service", 1, FakeReviewEngine(), None)
    )

    assert repository == "acme/service"
    assert combined.issues == []
    assert combined.model == get_settings().groq_model


def test_github_file_list_follows_next_page_link(monkeypatch):
    class FakeResponse:
        def __init__(self, page):
            self.page = page
            self.links = (
                {"next": {"url": "https://api.github.com/repos/acme/service/pulls/1/files?page=2"}}
                if page == 1
                else {}
            )
            self.text = ""

        def json(self):
            return [{"filename": f"file-{self.page}.py"}]

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        requested_urls = []

        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, **_):
            self.requested_urls.append(url)
            page = 1 if "page=2" not in url else 2
            return FakeResponse(page)

    monkeypatch.setattr("backend.services.github.httpx.AsyncClient", FakeAsyncClient)
    client = GitHubClient()
    client.settings = replace(client.settings, github_token="test-token")

    files = asyncio.run(
        client._get_paginated_list(
            "https://api.github.com/repos/acme/service/pulls/1/files?per_page=100"
        )
    )

    assert [file["filename"] for file in files] == ["file-1.py", "file-2.py"]
    assert len(FakeAsyncClient.requested_urls) == 2


def _async_result(result):
    async def resolve(*_):
        return result

    return resolve
