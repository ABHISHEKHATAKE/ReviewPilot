import { useCallback, useEffect, useState } from "react";

const starterCode = `def get_user(user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return db.execute(query)`;

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
  return data;
}

function RiskPill({ risk }) {
  return <span className={`risk risk-${(risk || "low").toLowerCase()}`}>{risk || "UNKNOWN"}</span>;
}

export default function App() {
  const [code, setCode] = useState(starterCode);
  const [language, setLanguage] = useState("python");
  const [filePath, setFilePath] = useState("src/users.py");
  const [repository, setRepository] = useState("");
  const [result, setResult] = useState(null);
  const [stats, setStats] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [apiState, setApiState] = useState("checking");
  const [owner, setOwner] = useState("");
  const [repo, setRepo] = useState("");
  const [pullNumber, setPullNumber] = useState("");
  const [indexPath, setIndexPath] = useState("");
  const [indexCode, setIndexCode] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [nextStats, nextReviews, health] = await Promise.all([
        api("/dashboard/stats"),
        api("/reviews?limit=12"),
        api("/health"),
      ]);
      setStats(nextStats);
      setReviews(nextReviews);
      setApiState(health.status === "ok" ? "online" : "offline");
    } catch {
      setApiState("offline");
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function submitReview(event) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    setResult(null);
    try {
      const data = await api("/review", {
        method: "POST",
        body: JSON.stringify({
          language,
          code,
          file_path: filePath || null,
          repository: repository || null,
        }),
      });
      setResult(data);
      await refresh();
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function indexFile(event) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const data = await api("/repositories/index", {
        method: "POST",
        body: JSON.stringify({
          repository,
          files: [{ path: indexPath, content: indexCode }],
        }),
      });
      setMessage(`Indexed ${data.files_received} file into ${data.chunks_indexed} context chunk(s).`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function reviewPullRequest(event) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      const job = await api("/github/pull-requests/review", {
        method: "POST",
        body: JSON.stringify({
          owner,
          repository: repo,
          pull_number: Number(pullNumber),
        }),
      });
      setMessage(`Review job #${job.id} queued.`);
      let finished = false;
      for (let attempt = 0; attempt < 90; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 2000));
        const status = await api(`/review/jobs/${job.id}`);
        if (status.status === "completed") {
          if (status.review_id) setResult(await api(`/reviews/${status.review_id}`));
          setMessage("GitHub pull request review completed.");
          await refresh();
          finished = true;
          break;
        }
        if (status.status === "failed") throw new Error(status.error || "GitHub review failed.");
      }
      if (!finished) setMessage(`Review job #${job.id} is still processing. Check Recent reviews in a moment.`);
    } catch (error) {
      setMessage(error.message);
    } finally {
      setBusy(false);
    }
  }

  async function showReview(id) {
    try {
      setResult(await api(`/reviews/${id}`));
      setMessage("");
    } catch (error) {
      setMessage(error.message);
    }
  }

  const issueCount = stats ? Object.values(stats.issue_counts || {}).reduce((sum, count) => sum + count, 0) : 0;

  return (
    <main className="shell">
      <header className="topbar">
        <a className="brand" href="/">
          <span className="brand-mark">R</span>
          <span>Review<span className="brand-light">Pilot</span></span>
        </a>
        <div className="topbar-right">
          <span className={`connection connection-${apiState}`}>
            <i /> API {apiState}
          </span>
          <a className="docs-link" href="/docs" target="_blank" rel="noreferrer">API docs ↗</a>
        </div>
      </header>

      <section className="hero">
        <div>
          <p className="eyebrow">GROQ-POWERED CODE INTELLIGENCE</p>
          <h1>Ship safer code,<br /><span>with a second set of eyes.</span></h1>
          <p className="hero-copy">Review a code sample or inspect a GitHub pull request with specialized AI reviewers, static checks, and repository context.</p>
        </div>
        <div className="hero-chip"><span>✦</span> Multi-agent review</div>
      </section>

      <section className="metrics">
        <article className="metric-card"><span>Reviews run</span><strong>{stats?.total_reviews ?? "—"}</strong><small>Saved to review history</small></article>
        <article className="metric-card"><span>Potential findings</span><strong>{issueCount}</strong><small>Across all completed reviews</small></article>
        <article className="metric-card"><span>High risk</span><strong>{stats?.risk_counts?.HIGH ?? 0}</strong><small>Reviews requiring attention</small></article>
        <article className="metric-card metric-provider"><span>AI provider</span><strong>Groq</strong><small>Structured review workflow</small></article>
      </section>

      <div className="workspace">
        <section className="panel review-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">CODE REVIEW</p><h2>Analyze a snippet</h2></div>
            <span className="step-tag">01 / REVIEW</span>
          </div>
          <form onSubmit={submitReview}>
            <div className="form-row">
              <label>Language
                <select value={language} onChange={(event) => setLanguage(event.target.value)}>
                  {["python", "javascript", "typescript", "java", "go", "rust", "text"].map((item) => <option key={item}>{item}</option>)}
                </select>
              </label>
              <label>File path <span className="optional">optional</span>
                <input value={filePath} onChange={(event) => setFilePath(event.target.value)} placeholder="src/module.py" />
              </label>
            </div>
            <label>Repository key <span className="optional">optional · enables indexed context</span>
              <input value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="owner/project" />
            </label>
            <label className="code-label">Code to review
              <textarea className="code-input" value={code} onChange={(event) => setCode(event.target.value)} spellCheck="false" required />
            </label>
            <div className="form-footer">
              <span className="privacy-note">Your code is analyzed, never executed.</span>
              <button className="primary-button" disabled={busy || !code.trim()}>{busy ? "Reviewing…" : "Run code review"} <span>→</span></button>
            </div>
          </form>

          <div className="divider"><span>OR REVIEW A PULL REQUEST</span></div>
          <form className="pr-form" onSubmit={reviewPullRequest}>
            <label>Owner<input value={owner} onChange={(event) => setOwner(event.target.value)} placeholder="acme" required /></label>
            <label>Repository<input value={repo} onChange={(event) => setRepo(event.target.value)} placeholder="service-api" required /></label>
            <label className="pr-number">PR #<input type="number" min="1" value={pullNumber} onChange={(event) => setPullNumber(event.target.value)} placeholder="42" required /></label>
            <button className="secondary-button" disabled={busy}>Review PR</button>
          </form>
        </section>

        <section className="panel result-panel">
          <div className="panel-heading">
            <div><p className="eyebrow">LATEST RESULT</p><h2>Review findings</h2></div>
            {result && <RiskPill risk={result.overall_risk} />}
          </div>
          {!result ? (
            <div className="empty-state">
              <div className="empty-icon">⌁</div>
              <h3>Ready when you are</h3>
              <p>Submit code or a pull request. Confirmed findings and suggested fixes will appear here.</p>
              <span>BUGS · SECURITY · PERFORMANCE · QUALITY</span>
            </div>
          ) : (
            <div className="results">
              <p className="result-summary">{result.summary}</p>
              <div className="result-meta">{result.issues?.length ?? result.issue_count ?? 0} finding(s) <span>·</span> {result.provider || "Groq"} <span>·</span> {result.model || "review history"}</div>
              {result.issues?.length ? result.issues.map((issue, index) => (
                <article className="issue-card" key={`${issue.title}-${index}`}>
                  <div className="issue-top"><span className={`category category-${issue.category?.toLowerCase()}`}>{issue.category}</span><RiskPill risk={issue.severity} /></div>
                  <h3>{issue.title}</h3>
                  <p>{issue.description}</p>
                  <div className="suggestion"><strong>Suggestion</strong><span>{issue.suggestion}</span></div>
                  <div className="issue-foot">{issue.file_path || filePath}{issue.line ? ` · line ${issue.line}` : ""}<span>{Math.round((issue.confidence || 0) * 100)}% confidence</span></div>
                </article>
              )) : <div className="clear-state"><span>✓</span><div><strong>No actionable findings confirmed</strong><p>Always validate critical changes with tests and human review.</p></div></div>}
              <p className="disclaimer">AI findings can be incomplete or incorrect. Review suggestions before applying.</p>
            </div>
          )}
          {message && <div className="notice" role="status">{message}</div>}
        </section>
      </div>

      <div className="lower-grid">
        <section className="panel index-panel">
          <div className="panel-heading"><div><p className="eyebrow">CODE RAG</p><h2>Index repository context</h2></div><span className="step-tag">02 / CONTEXT</span></div>
          <p className="section-copy">Add related source files for retrieval during future reviews. Context helps agents understand callers, models, and project conventions.</p>
          <form className="index-form" onSubmit={indexFile}>
            <label>Repository key<input value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="owner/project" required /></label>
            <label>File path<input value={indexPath} onChange={(event) => setIndexPath(event.target.value)} placeholder="src/models/user.py" required /></label>
            <label>File contents<textarea value={indexCode} onChange={(event) => setIndexCode(event.target.value)} placeholder="Paste repository source to make it searchable…" required /></label>
            <button className="secondary-button" disabled={busy}>Index source file</button>
          </form>
        </section>

        <section className="panel history-panel">
          <div className="panel-heading"><div><p className="eyebrow">ACTIVITY</p><h2>Recent reviews</h2></div><button className="text-button" onClick={refresh}>Refresh ↻</button></div>
          {!reviews.length ? <div className="history-empty">No saved reviews yet.</div> : <div className="history-list">{reviews.map((review) => (
            <button className="history-item" key={review.id} onClick={() => showReview(review.id)}>
              <span className="history-icon">{review.language === "mixed" ? "⌘" : "</>"}</span>
              <span className="history-main"><strong>{review.file_path || review.repository || "Code snippet"}</strong><small>{new Date(review.created_at).toLocaleString()} · {review.issue_count} findings</small></span>
              <RiskPill risk={review.overall_risk} />
            </button>
          ))}</div>}
        </section>
      </div>

      <footer><span>ReviewPilot</span><span>Groq · FastAPI · LangGraph</span><a href="/docs">Explore the API →</a></footer>
    </main>
  );
}
