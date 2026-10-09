from evaluation.metrics import calculate_metrics, findings_from_response
from evaluation.run import load_benchmark


def test_metrics_calculate_detection_severity_and_line_accuracy():
    cases = [
        {
            "id": "case-one",
            "expected": [
                {"category": "SECURITY", "severity": "HIGH", "line": 3},
                {"category": "BUG", "severity": "MEDIUM", "line": 8},
            ],
        }
    ]
    predictions = {
        "case-one": [
            {"category": "SECURITY", "severity": "MEDIUM", "line": 2},
            {"category": "BUG", "severity": "MEDIUM", "line": 8},
            {"category": "QUALITY", "severity": "LOW", "line": 1},
        ]
    }

    metrics = calculate_metrics(cases, predictions)

    assert metrics["true_positives"] == 2
    assert metrics["false_positives"] == 1
    assert metrics["false_negatives"] == 0
    assert metrics["precision"] == 2 / 3
    assert metrics["recall"] == 1
    assert metrics["f1"] == 0.8
    assert metrics["severity_accuracy"] == 0.5
    assert metrics["line_accuracy"] == 0.5


def test_clean_case_false_positive_is_counted():
    cases = [{"id": "clean", "expected": []}]
    predictions = {
        "clean": [{"category": "BUG", "severity": "LOW", "line": 1}]
    }

    metrics = calculate_metrics(cases, predictions)

    assert metrics["false_positives"] == 1
    assert metrics["precision"] == 0
    assert metrics["recall"] is None


def test_benchmark_contains_positive_and_clean_cases():
    cases = load_benchmark()
    assert len(cases) == 83
    assert any(case["expected"] for case in cases)
    assert any(not case["expected"] for case in cases)
    assert sum(bool(case["expected"]) for case in cases) == 68
    assert sum(not case["expected"] for case in cases) == 15
    assert {
        finding["category"]
        for case in cases
        for finding in case["expected"]
    } == {"BUG", "SECURITY", "PERFORMANCE", "QUALITY"}


def test_findings_from_response_keeps_evaluation_fields():
    response = {
        "issues": [
            {
                "category": "BUG",
                "severity": "MEDIUM",
                "line": 4,
                "title": "Division by zero",
                "description": "desc",
                "suggestion": "fix",
                "confidence": 0.9,
            }
        ]
    }
    assert findings_from_response(response) == [
        {
            "category": "BUG",
            "severity": "MEDIUM",
            "line": 4,
            "title": "Division by zero",
        }
    ]
