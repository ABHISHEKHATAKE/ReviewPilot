from __future__ import annotations

import hashlib
import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.config import get_settings
from backend.database import (
    ReviewJob,
    ReviewRecord,
    GitHubWebhookDelivery,
    get_session,
    initialize_database,
)
from backend.models import (
    GitHubPullRequestRequest,
    RepositoryIndexRequest,
    ReviewJobResponse,
    ReviewRequest,
    ReviewResponse,
)
from backend.services.github import GitHubServiceError
from backend.services.groq import GroqServiceError
from backend.services.review_engine import ReviewEngine
from backend.services.retriever import index_repository, retrieve_context
from backend.worker import review_code_task, review_github_task

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Groq-powered AI code review with repository context and GitHub PR support.",
    lifespan=lifespan,
)
dashboard_assets = Path(__file__).resolve().parent.parent / "frontend" / "dist" / "assets"
if dashboard_assets.is_dir():
    app.mount("/assets", StaticFiles(directory=dashboard_assets), name="dashboard-assets")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-GitHub-Event", "X-Hub-Signature-256"],
)
engine = ReviewEngine()


def get_engine() -> ReviewEngine:
    return engine


def _persist_review(
    session: Session, request: ReviewRequest, response: ReviewResponse
) -> ReviewRecord:
    record = ReviewRecord(
        repository=request.repository,
        file_path=request.file_path,
        language=request.language,
        summary=response.summary,
        overall_risk=response.overall_risk,
        issue_count=len(response.issues),
        issues_json=json.dumps([issue.model_dump() for issue in response.issues]),
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _validate_code_size(code: str) -> None:
    if len(code.encode("utf-8")) > settings.max_code_bytes:
        raise HTTPException(status_code=413, detail="Code exceeds the configured size limit.")


def _serialize_review(record: ReviewRecord) -> dict:
    return {
        "id": record.id,
        "repository": record.repository,
        "file_path": record.file_path,
        "language": record.language,
        "status": record.status,
        "summary": record.summary,
        "overall_risk": record.overall_risk,
        "issue_count": record.issue_count,
        "issues": json.loads(record.issues_json),
        "created_at": record.created_at.isoformat(),
    }


@app.exception_handler(GroqServiceError)
async def groq_error_handler(_: Request, exc: GroqServiceError):
    status_code = 503 if "GROQ_API_KEY is not configured" in str(exc) else 502
    logger.warning("Groq review failed: %s", exc)
    return JSONResponse(
        status_code=status_code, content={"detail": str(exc)}
    )


@app.exception_handler(GitHubServiceError)
async def github_error_handler(_: Request, exc: GitHubServiceError):
    status_code = 503 if "GITHUB_TOKEN is not configured" in str(exc) else 502
    logger.warning("GitHub operation failed: %s", exc)
    return JSONResponse(
        status_code=status_code, content={"detail": str(exc)}
    )


@app.get("/")
def root():
    dashboard = Path(__file__).resolve().parent.parent / "frontend" / "dist" / "index.html"
    if dashboard.is_file():
        return FileResponse(dashboard)
    return {"message": "AI Code Review Agent is running.", "docs": "/docs"}


@app.get("/health")
def healthcheck() -> dict[str, str]:
    return {"status": "ok", "service": "ai-code-reviewer"}


@app.post("/review", response_model=ReviewResponse)
async def review_code(
    request: ReviewRequest,
    session: Session = Depends(get_session),
    review_engine: ReviewEngine = Depends(get_engine),
) -> ReviewResponse:
    _validate_code_size(request.code)
    context = request.context
    if request.repository and not context:
        context = retrieve_context(session, request.repository, request.code)
    response = await review_engine.review(request, context)
    _persist_review(session, request, response)
    return response


@app.get("/reviews")
def list_reviews(
    limit: int = 50,
    session: Session = Depends(get_session),
) -> list[dict]:
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200.")
    records = session.scalars(
        select(ReviewRecord).order_by(desc(ReviewRecord.created_at)).limit(limit)
    ).all()
    return [_serialize_review(record) for record in records]


@app.get("/reviews/{review_id}")
def get_review(review_id: int, session: Session = Depends(get_session)) -> dict:
    record = session.get(ReviewRecord, review_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Review not found.")
    return _serialize_review(record)


@app.get("/dashboard/stats")
def dashboard_stats(session: Session = Depends(get_session)) -> dict:
    total_reviews = session.scalar(select(func.count()).select_from(ReviewRecord)) or 0
    risk_rows = session.execute(
        select(ReviewRecord.overall_risk, func.count())
        .group_by(ReviewRecord.overall_risk)
    ).all()
    category_counts: dict[str, int] = {}
    for (issues_json,) in session.execute(select(ReviewRecord.issues_json)).all():
        for issue in json.loads(issues_json):
            category = issue.get("category", "OTHER")
            category_counts[category] = category_counts.get(category, 0) + 1
    return {
        "total_reviews": total_reviews,
        "risk_counts": {risk: count for risk, count in risk_rows},
        "issue_counts": category_counts,
    }


@app.post("/repositories/index")
def index_repository_endpoint(
    request: RepositoryIndexRequest,
    session: Session = Depends(get_session),
) -> dict[str, int | str]:
    total_bytes = sum(len(file.content.encode("utf-8")) for file in request.files)
    if total_bytes > 5_000_000:
        raise HTTPException(status_code=413, detail="Indexed source exceeds the 5 MB request limit.")
    chunk_count = index_repository(session, request.repository, request.files)
    return {
        "repository": request.repository,
        "files_received": len(request.files),
        "chunks_indexed": chunk_count,
    }


@app.post("/review/jobs", response_model=ReviewJobResponse, status_code=202)
def enqueue_code_review(
    request: ReviewRequest, session: Session = Depends(get_session)
) -> ReviewJobResponse:
    _validate_code_size(request.code)
    job = ReviewJob(repository=request.repository)
    session.add(job)
    session.commit()
    session.refresh(job)
    try:
        task = review_code_task.delay(job.id, request.model_dump())
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:2000]
        session.commit()
        logger.exception("Could not enqueue review job %s", job.id)
        raise HTTPException(status_code=503, detail="Review queue is unavailable.") from exc
    job.task_id = task.id
    session.commit()
    return ReviewJobResponse(id=job.id, status=job.status, message="Review queued.")


@app.get("/review/jobs/{job_id}")
def get_review_job(job_id: int, session: Session = Depends(get_session)) -> dict:
    job = session.get(ReviewJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Review job not found.")
    return {
        "id": job.id,
        "status": job.status,
        "repository": job.repository,
        "review_id": job.review_id,
        "error": job.error,
        "created_at": job.created_at.isoformat(),
    }


@app.post("/github/pull-requests/review", response_model=ReviewJobResponse, status_code=202)
def enqueue_github_review(
    request: GitHubPullRequestRequest, session: Session = Depends(get_session)
) -> ReviewJobResponse:
    job = ReviewJob(repository=f"{request.owner}/{request.repository}")
    session.add(job)
    session.commit()
    session.refresh(job)
    try:
        task = review_github_task.delay(job.id, request.model_dump())
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:2000]
        session.commit()
        logger.exception("Could not enqueue GitHub review job %s", job.id)
        raise HTTPException(status_code=503, detail="Review queue is unavailable.") from exc
    job.task_id = task.id
    session.commit()
    return ReviewJobResponse(id=job.id, status=job.status, message="GitHub review queued.")


@app.post("/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    signature: str | None = Header(default=None, alias="X-Hub-Signature-256"),
    event: str | None = Header(default=None, alias="X-GitHub-Event"),
    delivery_id: str | None = Header(default=None, alias="X-GitHub-Delivery"),
    session: Session = Depends(get_session),
) -> dict[str, str | int | bool]:
    secret = settings.github_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="GITHUB_WEBHOOK_SECRET is not configured.")
    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(status_code=413, detail="Webhook payload is too large.")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if signature is None or not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature.")
    if not delivery_id or len(delivery_id) > 100:
        raise HTTPException(status_code=400, detail="Missing or invalid X-GitHub-Delivery header.")

    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Webhook payload must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object.")

    delivery = GitHubWebhookDelivery(delivery_id=delivery_id)
    session.add(delivery)
    retry_failed_delivery = False
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        delivery = session.scalar(
            select(GitHubWebhookDelivery).where(
                GitHubWebhookDelivery.delivery_id == delivery_id
            )
        )
        if delivery is None:
            raise HTTPException(status_code=409, detail="Webhook delivery is being processed.")
        if delivery.status != "failed":
            return {
                "status": "duplicate",
                "duplicate": True,
                **({"job_id": delivery.job_id} if delivery.job_id is not None else {}),
            }
        retry_failed_delivery = True

    if event != "pull_request" or payload.get("action") not in {
        "opened",
        "synchronize",
        "reopened",
    }:
        delivery.status = "ignored"
        session.commit()
        return {"status": "ignored"}

    repository = payload.get("repository", {})
    if not isinstance(repository, dict):
        raise HTTPException(status_code=400, detail="Webhook repository data is invalid.")
    owner_data = repository.get("owner", {})
    if not isinstance(owner_data, dict):
        raise HTTPException(status_code=400, detail="Webhook repository owner data is invalid.")
    owner = owner_data.get("login")
    name = repository.get("name")
    pull_number = payload.get("number")
    if (
        not isinstance(owner, str)
        or not isinstance(name, str)
        or type(pull_number) is not int
        or not owner
        or not name
        or pull_number < 1
    ):
        raise HTTPException(status_code=400, detail="Webhook is missing repository or pull request data.")

    job = (
        session.get(ReviewJob, delivery.job_id)
        if retry_failed_delivery and delivery.job_id is not None
        else None
    )
    if job is None:
        job = ReviewJob(repository=f"{owner}/{name}")
        session.add(job)
        session.flush()
    else:
        job.status = "queued"
        job.error = None
        job.task_id = None
    delivery.job_id = job.id
    delivery.status = "queued"
    session.commit()
    request_data = {
        "owner": owner,
        "repository": name,
        "pull_number": pull_number,
    }
    try:
        task = review_github_task.delay(job.id, request_data)
    except Exception as exc:
        job.status = "failed"
        job.error = str(exc)[:2000]
        delivery.status = "failed"
        session.commit()
        logger.exception("Could not enqueue webhook review job %s", job.id)
        raise HTTPException(status_code=503, detail="Review queue is unavailable.") from exc
    job.task_id = task.id
    delivery.status = "queued"
    session.commit()
    return {"status": "queued", "job_id": job.id}
