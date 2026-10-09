from __future__ import annotations

import base64
import re
from urllib.parse import quote

import httpx

from backend.config import get_settings
from backend.models import ReviewIssue, ReviewRequest, ReviewResponse

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


class GitHubServiceError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def _headers(self) -> dict[str, str]:
        if not self.settings.github_token:
            raise GitHubServiceError("GITHUB_TOKEN is not configured.")
        return {
            "Authorization": f"Bearer {self.settings.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _get_json(self, url: str):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, headers=self._headers())
                response.raise_for_status()
                return response.json()
        except httpx.HTTPStatusError as exc:
            raise GitHubServiceError(
                f"GitHub API returned HTTP {exc.response.status_code}: {exc.response.text[:500]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GitHubServiceError(f"Could not connect to GitHub: {exc}") from exc

    async def _get_paginated_list(self, url: str) -> list[dict]:
        results: list[dict] = []
        next_url: str | None = url
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                for _ in range(30):
                    if next_url is None:
                        break
                    response = await client.get(next_url, headers=self._headers())
                    response.raise_for_status()
                    page = response.json()
                    if not isinstance(page, list):
                        raise GitHubServiceError(
                            "GitHub returned an invalid pull request files response."
                        )
                    results.extend(item for item in page if isinstance(item, dict))
                    next_url = response.links.get("next", {}).get("url")
                else:
                    raise GitHubServiceError(
                        "Pull request file list exceeded the 30-page safety limit."
                    )
        except httpx.HTTPStatusError as exc:
            raise GitHubServiceError(
                f"GitHub API returned HTTP {exc.response.status_code}: {exc.response.text[:500]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GitHubServiceError(f"Could not connect to GitHub: {exc}") from exc
        return results

    async def review_pull_request(
        self,
        owner: str,
        repository: str,
        pull_number: int,
        engine,
        session,
    ) -> tuple[ReviewResponse, str]:
        base = f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}"
        pull = await self._get_json(f"{base}/pulls/{pull_number}")
        head_sha = pull["head"]["sha"]
        repo_name = f"{owner}/{repository}"
        files = await self._get_paginated_list(
            f"{base}/pulls/{pull_number}/files?per_page=100"
        )

        all_issues: list[ReviewIssue] = []
        file_reviews: list[tuple[str, ReviewResponse, set[int]]] = []
        for changed_file in files:
            path = changed_file.get("filename")
            if not isinstance(path, str) or changed_file.get("status") == "removed":
                continue
            source = await self._get_source(owner, repository, head_sha, path)
            if not source:
                continue
            source = source.encode("utf-8")[: self.settings.max_code_bytes].decode(
                "utf-8", errors="ignore"
            )
            indexed_context = self._retrieve_context(session, repo_name, source)
            response = await engine.review(
                ReviewRequest(
                    language=self._language_for(path),
                    file_path=path,
                    repository=repo_name,
                    code=source,
                    context=indexed_context or None,
                ),
                context=indexed_context or None,
            )
            all_issues.extend(response.issues)
            file_reviews.append(
                (path, response, self._added_line_numbers(changed_file.get("patch", "")))
            )

        combined = ReviewResponse(
            summary=f"Reviewed {len(file_reviews)} changed file(s); found {len(all_issues)} potential issue(s).",
            issues=all_issues,
            overall_risk=engine._risk_from_issues(all_issues),
            model=get_settings().groq_model,
        )
        await self._post_review(base, pull_number, head_sha, file_reviews, combined)
        return combined, repo_name

    async def _get_source(
        self, owner: str, repository: str, ref: str, path: str
    ) -> str | None:
        url = (
            f"https://api.github.com/repos/{quote(owner)}/{quote(repository)}"
            f"/contents/{quote(path, safe='/')}?ref={quote(ref)}"
        )
        payload = await self._get_json(url)
        if payload.get("type") != "file" or not isinstance(payload.get("content"), str):
            return None
        try:
            return base64.b64decode(payload["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GitHubServiceError(f"Could not decode GitHub file {path}.") from exc

    async def _post_review(
        self,
        base: str,
        pull_number: int,
        commit_id: str,
        file_reviews: list[tuple[str, ReviewResponse, set[int]]],
        combined: ReviewResponse,
    ) -> None:
        body_parts = [f"## AI Code Review\n\n{combined.summary}"]
        if combined.issues:
            for issue in combined.issues:
                location = f" (line {issue.line})" if issue.line else ""
                body_parts.append(
                    f"- **{issue.severity} · {issue.category}: {issue.title}**"
                    f"{location} — {issue.description}\n  Suggestion: {issue.suggestion}"
                )
        else:
            body_parts.append("No actionable issues were confirmed.")

        comments = []
        for path, response, added_lines in file_reviews:
            for issue in response.issues:
                if issue.line in added_lines:
                    comments.append(
                        {
                            "path": path,
                            "line": issue.line,
                            "side": "RIGHT",
                            "body": (
                                f"**{issue.severity} · {issue.category}: {issue.title}**\n\n"
                                f"{issue.description}\n\nSuggestion: {issue.suggestion}"
                            ),
                        }
                    )
        payload = {
            "commit_id": commit_id,
            "body": "\n\n".join(body_parts)[:65_000],
            "event": "COMMENT",
            "comments": comments[:50],
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{base}/pulls/{pull_number}/reviews",
                    headers=self._headers(),
                    json=payload,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GitHubServiceError(
                f"Could not post GitHub review (HTTP {exc.response.status_code}): {exc.response.text[:500]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise GitHubServiceError(f"Could not post GitHub review: {exc}") from exc

    @staticmethod
    def _added_line_numbers(patch: str) -> set[int]:
        result: set[int] = set()
        current_line: int | None = None
        for line in patch.splitlines():
            match = HUNK_RE.match(line)
            if match:
                current_line = int(match.group(1))
            elif line.startswith("+") and not line.startswith("+++"):
                if current_line is not None:
                    result.add(current_line)
                    current_line += 1
            elif line.startswith("-") and not line.startswith("---"):
                continue
            elif not line.startswith("\\") and current_line is not None:
                current_line += 1
        return result

    @staticmethod
    def _language_for(path: str) -> str:
        extensions = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".java": "java",
            ".go": "go",
            ".rs": "rust",
            ".c": "c",
            ".cpp": "cpp",
        }
        from pathlib import PurePosixPath

        return extensions.get(PurePosixPath(path).suffix.lower(), "text")

    @staticmethod
    def _retrieve_context(session, repository: str, code: str) -> str:
        from backend.services.retriever import retrieve_context

        return retrieve_context(session, repository, code)
