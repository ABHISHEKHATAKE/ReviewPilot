from __future__ import annotations

from backend.agents.reviewer import ReviewerWorkflow
from backend.config import get_settings
from backend.models import ReviewRequest, ReviewResponse
from backend.services.static_analysis import StaticAnalyzer


class ReviewEngine:
    def __init__(
        self,
        workflow: ReviewerWorkflow | None = None,
        static_analyzer: StaticAnalyzer | None = None,
    ) -> None:
        self.workflow = workflow or ReviewerWorkflow()
        self.static_analyzer = static_analyzer or StaticAnalyzer()

    async def review(
        self, request: ReviewRequest, context: str | None = None
    ) -> ReviewResponse:
        static_findings = [
            {
                "category": finding.category,
                "severity": finding.severity,
                "title": finding.title,
                "line": finding.line,
                "description": finding.description,
                "suggestion": finding.suggestion,
                "confidence": 0.85,
                "file_path": request.file_path,
            }
            for finding in self.static_analyzer.analyze(request.code, request.language)
        ]
        issues, summary = await self.workflow.review(
            code=request.code,
            language=request.language,
            file_path=request.file_path,
            context=context or request.context,
            static_findings=static_findings,
        )
        return ReviewResponse(
            summary=summary,
            issues=issues,
            overall_risk=self._risk_from_issues(issues),
            model=get_settings().groq_model,
        )

    @staticmethod
    def _risk_from_issues(issues) -> str:
        if any(issue.severity == "CRITICAL" for issue in issues):
            return "CRITICAL"
        if any(issue.severity == "HIGH" for issue in issues):
            return "HIGH"
        if any(issue.severity == "MEDIUM" for issue in issues):
            return "MEDIUM"
        return "LOW"
