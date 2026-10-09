from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: Literal["BUG", "SECURITY", "PERFORMANCE", "QUALITY"]
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]
    title: str = Field(min_length=1, max_length=200)
    file_path: str | None = None
    line: int | None = Field(default=None, ge=1)
    description: str = Field(min_length=1)
    suggestion: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


class ReviewRequest(BaseModel):
    language: str = Field(default="python", min_length=1, max_length=50)
    code: str = Field(min_length=1, max_length=100_000)
    file_path: str | None = Field(default=None, max_length=500)
    repository: str | None = Field(default=None, max_length=300)
    context: str | None = Field(default=None, max_length=50_000)


class ReviewResponse(BaseModel):
    summary: str
    issues: list[ReviewIssue]
    overall_risk: str
    provider: str = "groq"
    model: str


class RepositoryFile(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str = Field(max_length=100_000)


class RepositoryIndexRequest(BaseModel):
    repository: str = Field(min_length=1, max_length=300)
    files: list[RepositoryFile] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def require_unique_paths(self) -> RepositoryIndexRequest:
        paths = [file.path for file in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("Each file path must be unique within an indexing request.")
        return self


class GitHubPullRequestRequest(BaseModel):
    owner: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    repository: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    pull_number: int = Field(ge=1)


class ReviewJobResponse(BaseModel):
    id: int
    status: str
    message: str
