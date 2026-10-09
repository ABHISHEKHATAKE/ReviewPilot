from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.models import ReviewRequest
from backend.services.review_engine import ReviewEngine
from evaluation.extended_cases import EXTENDED_CASES
from evaluation.metrics import calculate_metrics, findings_from_response

BENCHMARK_PATH = Path(__file__).with_name("benchmark.json")


def load_benchmark(path: Path = BENCHMARK_PATH) -> list[dict[str, Any]]:
    cases = json.loads(path.read_text(encoding="utf-8")) + EXTENDED_CASES
    if not isinstance(cases, list) or not cases:
        raise ValueError("Benchmark must be a non-empty JSON list.")
    ids = [case.get("id") for case in cases]
    if any(not isinstance(case_id, str) or not case_id for case_id in ids):
        raise ValueError("Every benchmark case must have a non-empty id.")
    if len(ids) != len(set(ids)):
        raise ValueError("Benchmark case IDs must be unique.")
    for case in cases:
        if not isinstance(case.get("code"), str) or not case["code"].strip():
            raise ValueError(f"Case {case['id']} has no code.")
        if not isinstance(case.get("expected"), list):
            raise ValueError(f"Case {case['id']} expected value must be a list.")
        for issue in case["expected"]:
            if (
                issue.get("category")
                not in {"BUG", "SECURITY", "PERFORMANCE", "QUALITY"}
                or issue.get("severity") not in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
                or not isinstance(issue.get("line"), int)
                or issue["line"] < 1
                or not isinstance(issue.get("title"), str)
            ):
                raise ValueError(f"Case {case['id']} has an invalid expected finding.")
    return cases


async def run_evaluation(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not get_settings().groq_api_key:
        raise RuntimeError("Set GROQ_API_KEY in .env before running the live evaluation.")

    engine = ReviewEngine()
    predictions: dict[str, list[dict[str, Any]]] = {}
    case_results = []
    for case in cases:
        response = await engine.review(
            ReviewRequest(language=case["language"], code=case["code"])
        )
        findings = findings_from_response(response)
        predictions[case["id"]] = findings
        case_results.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "predicted": findings,
                "summary": response.summary,
            }
        )

    return {
        "model": get_settings().groq_model,
        "metrics": calculate_metrics(cases, predictions),
        "results": case_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the labeled code-review benchmark against the configured Groq model."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON report path. By default, print the report to stdout.",
    )
    args = parser.parse_args()
    report = asyncio.run(run_evaluation(load_benchmark()))
    rendered = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Evaluation report written to {args.output}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
