from __future__ import annotations

import asyncio
import json
import logging

from celery import Celery

from backend.config import get_settings
from backend.database import ReviewJob, ReviewRecord, create_session, initialize_database
from backend.models import GitHubPullRequestRequest, ReviewRequest
from backend.services.github import GitHubClient
from backend.services.review_engine import ReviewEngine
from backend.services.retriever import retrieve_context

settings = get_settings()
celery_app = Celery("ai_code_reviewer", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)
logger = logging.getLogger(__name__)


def _save_review(session, request: ReviewRequest, response) -> int:
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
    session.flush()
    return record.id


@celery_app.task(name="backend.worker.review_code_task")
def review_code_task(job_id: int, request_data: dict) -> int:
    return _execute_review_job(job_id, request_data)


@celery_app.task(name="backend.worker.review_github_task")
def review_github_task(job_id: int, request_data: dict) -> int:
    initialize_database()
    session = create_session()
    try:
        job = session.get(ReviewJob, job_id)
        if job is None:
            raise ValueError(f"Review job {job_id} does not exist.")
        job.status = "running"
        session.commit()
        request = GitHubPullRequestRequest.model_validate(request_data)
        response, repository = asyncio.run(
            GitHubClient().review_pull_request(
                request.owner,
                request.repository,
                request.pull_number,
                ReviewEngine(),
                session,
            )
        )
        review_id = _save_review(
            session,
            ReviewRequest(
                language="mixed",
                code="GitHub pull request review",
                repository=repository,
            ),
            response,
        )
        job.status = "completed"
        job.review_id = review_id
        job.repository = repository
        session.commit()
        return review_id
    except Exception as exc:
        session.rollback()
        job = session.get(ReviewJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = str(exc)[:2000]
            session.commit()
        logger.exception("GitHub review job %s failed", job_id)
        raise
    finally:
        session.close()


def _execute_review_job(job_id: int, request_data: dict) -> int:
    initialize_database()
    session = create_session()
    try:
        job = session.get(ReviewJob, job_id)
        if job is None:
            raise ValueError(f"Review job {job_id} does not exist.")
        job.status = "running"
        session.commit()
        request = ReviewRequest.model_validate(request_data)
        context = (
            retrieve_context(session, request.repository, request.code)
            if request.repository
            else None
        )
        response = asyncio.run(ReviewEngine().review(request, context))
        review_id = _save_review(session, request, response)
        job.status = "completed"
        job.review_id = review_id
        job.repository = request.repository
        session.commit()
        return review_id
    except Exception as exc:
        session.rollback()
        job = session.get(ReviewJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error = str(exc)[:2000]
            session.commit()
        logger.exception("Review job %s failed", job_id)
        raise
    finally:
        session.close()
