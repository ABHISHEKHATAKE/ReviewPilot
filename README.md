# ReviewPilot — Groq AI Code Reviewer

ReviewPilot analyzes code samples and GitHub pull requests with a multi-agent LangGraph workflow powered by Groq. It combines deterministic Python checks, specialized AI reviewers, repository context retrieval, review history, a React dashboard, and asynchronous jobs.

## What it does

- Reviews code for bugs, security, performance, and material code-quality concerns.
- Uses a Groq planner to select relevant review categories, parallel LangGraph agents to inspect them, and a Groq critic to filter unsupported or duplicate findings.
- Adds lightweight retrieval-augmented context from source files indexed for a repository.
- Stores review results, job state, and indexed code chunks in SQLite by default or PostgreSQL.
- Queues longer-running code and GitHub reviews with Celery and Redis.
- Verifies GitHub webhook signatures and can publish pull-request reviews, including inline comments on changed lines where possible.
- Provides a React dashboard for sample reviews, GitHub PR reviews, indexed context, metrics, and recent history.

The reviewed source is never executed. Findings are AI-assisted suggestions and should be validated by a developer.

## Review flow

```text
Code sample or GitHub PR
        |
        v
FastAPI request validation + deterministic Python checks
        |
        v
Retrieve related repository chunks (when indexed)
        |
        v
Groq planner -> LangGraph category agents in parallel
        |
        v
Groq critic -> structured, deduplicated findings
        |
        +----> Persist history and return to dashboard/API
        |
        +----> For PRs, publish a GitHub review
```

The repository retriever currently ranks SQL-stored source chunks by lexical token overlap. It is a lightweight RAG implementation, not an embedding/vector database.

## Requirements

- Python 3.10+
- Node.js 22+
- A Groq API key
- Optional for queued jobs and GitHub integration: Redis, a GitHub token, and a webhook secret
- Optional for deployment: Docker and Docker Compose

## Local setup

From the project root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Set `GROQ_API_KEY` in `.env`. Then start the API:

```powershell
uvicorn backend.main:app --reload
```

Open the API at `http://127.0.0.1:8000`, or interactive API docs at `http://127.0.0.1:8000/docs`.

To run the React dashboard in development, use a second terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`. Vite proxies API calls to port 8000. The production dashboard is served by FastAPI at the root URL after the frontend is built.

### Optional async worker

Start Redis locally, then run:

```powershell
celery -A backend.worker.celery_app worker --loglevel=info
```

Asynchronous endpoints return a job ID. Poll `GET /review/jobs/{job_id}` for the status and completed review ID.

## Docker Compose

Copy `.env.example` to `.env`, set `GROQ_API_KEY` (and GitHub credentials if using PR review), and run:

```powershell
docker compose up --build
```

Compose starts the web API/dashboard, Celery worker, PostgreSQL, and Redis. Visit `http://localhost:8000`. PostgreSQL and Redis data use named volumes.

## Configuration

| Variable | Required | Description |
|---|---:|---|
| `GROQ_API_KEY` | For reviews | API key for Groq's OpenAI-compatible chat completions endpoint |
| `GROQ_MODEL` | No | Groq model; defaults to `qwen/qwen3.8-27b` |
| `GROQ_BASE_URL` | No | Defaults to `https://api.groq.com/openai/v1` |
| `GITHUB_TOKEN` | For PR reviews | GitHub token able to read pull requests and create pull-request reviews |
| `GITHUB_WEBHOOK_SECRET` | For webhooks | Shared secret used to validate `X-Hub-Signature-256` |
| `DATABASE_URL` | No | Defaults to `sqlite:///./ai_code_review.db`; Compose sets PostgreSQL |
| `REDIS_URL` | For queued jobs | Defaults to `redis://localhost:6379/0` |
| `CORS_ORIGINS` | No | Comma-separated browser origins; defaults to Vite on port 5173 |
| `MAX_CODE_BYTES` | No | Maximum submitted source size; defaults to 100,000 bytes |

Never commit `.env` or API credentials.

## API

### Review code directly

`POST /review`

```json
{
  "language": "python",
  "file_path": "src/users.py",
  "repository": "acme/service",
  "code": "def get_user(user_id):\n    query = f\"SELECT * FROM users WHERE id = {user_id}\"\n    return query"
}
```

`repository` is optional; when supplied and previously indexed, relevant chunks are retrieved and provided to the agents.

Response:

```json
{
  "summary": "One high-confidence security issue was confirmed.",
  "issues": [
    {
      "category": "SECURITY",
      "severity": "HIGH",
      "title": "Possible SQL injection",
      "file_path": "src/users.py",
      "line": 2,
      "description": "User-controlled data is interpolated into a SQL query.",
      "suggestion": "Use a parameterized query.",
      "confidence": 0.94
    }
  ],
  "overall_risk": "HIGH",
  "provider": "groq",
  "model": "qwen/qwen3.8-27b"
}
```

### Index repository context

`POST /repositories/index`

```json
{
  "repository": "acme/service",
  "files": [
    {
      "path": "src/models/user.py",
      "content": "class User: ..."
    }
  ]
}
```

Files are split into bounded line chunks and stored in the database. Re-indexing a path replaces that path's older chunks while preserving other indexed files for the same repository.

### Other endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/docs` | Interactive OpenAPI documentation |
| `GET` | `/reviews` | Recent review history |
| `GET` | `/reviews/{review_id}` | One saved review |
| `GET` | `/dashboard/stats` | Review and issue metrics |
| `POST` | `/review/jobs` | Queue a code review |
| `GET` | `/review/jobs/{job_id}` | Read job status |
| `POST` | `/github/pull-requests/review` | Queue a GitHub PR review |
| `POST` | `/webhooks/github` | Verify and queue supported PR webhook events |

## GitHub setup

Automatic webhook reviews require an app endpoint that GitHub can reach over **public HTTPS**. `localhost` by itself is not reachable from GitHub. For local testing, expose port 8000 through a trusted HTTPS tunnel; for ongoing use, deploy the app and worker at a stable public host.

**Before exposing the host publicly:** the dashboard and non-webhook API routes currently have no user authentication. Put them behind an authenticated access proxy or add application authentication before public deployment. Allow GitHub to reach `/webhooks/github` while restricting the other routes; the webhook endpoint independently verifies GitHub's HMAC signature.

1. Create a GitHub fine-grained personal access token scoped only to the target repository. Grant **Contents: Read-only** and **Pull requests: Read and write**. Store it as `GITHUB_TOKEN` in `.env`.
2. Generate a long random value to use as `GITHUB_WEBHOOK_SECRET`. Put the same value in `.env` and in the GitHub webhook configuration. This is separate from the GitHub access token.
3. Start the full stack with `docker compose up --build`. The API, Redis broker, and Celery worker must all stay running. Ensure `GROQ_API_KEY` is set too.
4. In the repository on GitHub, open **Settings → Webhooks → Add webhook**:
   - **Payload URL:** `https://<public-host>/webhooks/github`
   - **Content type:** `application/json`
   - **Secret:** the exact `GITHUB_WEBHOOK_SECRET` value
   - Choose **Let me select individual events** and select **Pull requests**.
   - Save the webhook. GitHub's initial `ping` delivery is acknowledged and ignored by the reviewer.
5. Open or update a pull request. `opened`, `reopened`, and `synchronize` events are queued; the worker reviews files and publishes one pull-request review with a summary and inline comments on changed lines where GitHub accepts the line location.
6. Check **Settings → Webhooks → Recent Deliveries** if GitHub reports a delivery error. Repeated deliveries with the same GitHub delivery ID are deduplicated; if queueing fails, a retry can enqueue the failed delivery again.

The webhook validates GitHub's HMAC signature and delivery ID. GitHub still needs a publicly reachable HTTPS URL: starting the app locally does not by itself enable automatic webhook delivery. The dashboard's manual PR review route remains available as a separate option.

## Project layout

```text
backend/
  agents/reviewer.py       LangGraph planner, specialist reviewers, and critic
  services/groq.py         Groq API client and structured JSON parsing
  services/github.py       GitHub PR fetch and review publication
  services/retriever.py    SQL-backed source indexing and context retrieval
  services/static_analysis.py
  database.py              SQLAlchemy models and database sessions
  worker.py                Celery queue and worker tasks
  main.py                  FastAPI routes and webhook verification
frontend/
  src/                     React dashboard
tests/                     API, persistence, workflow, and helper tests
Dockerfile
docker-compose.yml
requirements.txt
```

## Tests

```powershell
python -m pytest -q
```

The tests use a fake review engine/client; they do not require a Groq key, GitHub token, or external services.

## Evaluate review quality

Run the labeled benchmark against the configured Groq model:

```powershell
python -m evaluation.run
```

Or save the per-case predictions and aggregate metrics as JSON:

```powershell
python -m evaluation.run --output evaluation/report.json
```

The benchmark combines `evaluation/benchmark.json` and `evaluation/extended_cases.py` for **83 labeled cases**: 68 with expected findings and 15 clean examples. Findings span BUG, SECURITY, PERFORMANCE, and QUALITY. The report calculates micro precision, recall, F1, per-category precision/recall/F1, severity accuracy among category-matched findings, and exact line accuracy among matched findings with a labeled line. Findings are matched one-to-one by category, preferring the closest line; line correctness is measured separately. Clean examples add false positives when findings are reported.

This is a small, hand-authored smoke benchmark, not a statistically representative dataset. Treat its scores as a regression signal, not a general accuracy guarantee. Meaningful comparisons should use a larger independently labeled set, freeze the benchmark/model version, and rerun the same cases when prompts or model settings change.

## Known limitations

- The Python deterministic checks are intentionally small; AI review is not a replacement for dedicated language-specific linters or human review.
- Repository context matching uses lexical similarity rather than vector embeddings.
- GitHub webhooks require a public HTTPS URL and the manual/automatic PR review paths require a running worker, Redis, and GitHub permissions.
- The API and dashboard do not authenticate callers and do not provide role-based access control. Keep deployments on a trusted network until authentication, authorization, and rate limiting are added.
- Pull requests with many files may be limited by GitHub API pagination and service rate limits.
