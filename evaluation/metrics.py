from __future__ import annotations

from collections import Counter
from typing import Any


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None or precision + recall == 0:
        return None
    return 2 * precision * recall / (precision + recall)


def _match_findings(
    expected: list[dict[str, Any]], predicted: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    candidates = []
    for expected_index, expected_issue in enumerate(expected):
        for predicted_index, predicted_issue in enumerate(predicted):
            if expected_issue["category"] != predicted_issue.get("category"):
                continue
            expected_line = expected_issue.get("line")
            predicted_line = predicted_issue.get("line")
            line_distance = (
                abs(expected_line - predicted_line)
                if expected_line is not None and predicted_line is not None
                else 1_000_000
            )
            candidates.append(
                (
                    line_distance,
                    expected_index,
                    predicted_index,
                )
            )

    matches = []
    used_expected: set[int] = set()
    used_predicted: set[int] = set()
    for _, expected_index, predicted_index in sorted(candidates):
        if expected_index in used_expected or predicted_index in used_predicted:
            continue
        used_expected.add(expected_index)
        used_predicted.add(predicted_index)
        matches.append((expected[expected_index], predicted[predicted_index]))
    return matches


def calculate_metrics(
    cases: list[dict[str, Any]], predictions: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    true_positives = false_positives = false_negatives = 0
    severity_correct = severity_total = 0
    line_correct = line_total = 0
    category_counts: dict[str, Counter[str]] = {}

    for case in cases:
        expected = case["expected"]
        predicted = predictions.get(case["id"], [])
        matches = _match_findings(expected, predicted)
        true_positives += len(matches)
        false_positives += len(predicted) - len(matches)
        false_negatives += len(expected) - len(matches)

        for expected_issue, predicted_issue in matches:
            severity_total += 1
            severity_correct += (
                expected_issue["severity"] == predicted_issue.get("severity")
            )
            if expected_issue.get("line") is not None:
                line_total += 1
                line_correct += expected_issue["line"] == predicted_issue.get("line")

        for category in {
            issue["category"] for issue in expected + predicted
        }:
            counts = category_counts.setdefault(
                category, Counter({"tp": 0, "fp": 0, "fn": 0})
            )
            expected_category = [
                issue for issue in expected if issue["category"] == category
            ]
            predicted_category = [
                issue for issue in predicted if issue.get("category") == category
            ]
            category_matches = _match_findings(expected_category, predicted_category)
            counts["tp"] += len(category_matches)
            counts["fp"] += len(predicted_category) - len(category_matches)
            counts["fn"] += len(expected_category) - len(category_matches)

    precision = _ratio(true_positives, true_positives + false_positives)
    recall = _ratio(true_positives, true_positives + false_negatives)
    per_category = {}
    for category, counts in sorted(category_counts.items()):
        category_precision = _ratio(counts["tp"], counts["tp"] + counts["fp"])
        category_recall = _ratio(counts["tp"], counts["tp"] + counts["fn"])
        per_category[category] = {
            **counts,
            "precision": category_precision,
            "recall": category_recall,
            "f1": _f1(category_precision, category_recall),
        }

    return {
        "cases": len(cases),
        "expected_findings": sum(len(case["expected"]) for case in cases),
        "predicted_findings": sum(len(predictions.get(case["id"], [])) for case in cases),
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
        "f1": _f1(precision, recall),
        "severity_accuracy": _ratio(severity_correct, severity_total),
        "severity_compared": severity_total,
        "line_accuracy": _ratio(line_correct, line_total),
        "lines_compared": line_total,
        "per_category": per_category,
    }


def findings_from_response(response: Any) -> list[dict[str, Any]]:
    if hasattr(response, "model_dump"):
        data = response.model_dump()
    else:
        data = response
    findings = data.get("issues", [])
    return [
        {
            "category": finding["category"],
            "severity": finding["severity"],
            "line": finding.get("line"),
            "title": finding["title"],
        }
        for finding in findings
    ]
