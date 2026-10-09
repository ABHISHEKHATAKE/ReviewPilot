from __future__ import annotations

import ast
from dataclasses import dataclass
import re


@dataclass
class StaticFinding:
    category: str
    severity: str
    title: str
    description: str
    line: int | None = None
    suggestion: str = "Review this code path and validate the input assumptions."


class StaticAnalyzer:
    """Deterministic AST checks for a small set of high-signal Python patterns."""

    SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE)\b", re.IGNORECASE)

    def analyze(self, code: str, language: str) -> list[StaticFinding]:
        if language.lower() != "python":
            return []

        try:
            tree = ast.parse(code)
        except SyntaxError:
            return []

        findings: list[StaticFinding] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"eval", "exec"}:
                    findings.append(
                        StaticFinding(
                            category="SECURITY",
                            severity="HIGH",
                            title="Dynamic code execution",
                            description="The code executes dynamic Python statements, which can allow code injection.",
                            line=node.lineno,
                            suggestion="Avoid eval/exec on untrusted input. Prefer explicit parsing or safe APIs.",
                        )
                    )
            elif isinstance(node, ast.JoinedStr) and self._starts_with_sql(node):
                findings.append(
                    StaticFinding(
                        category="SECURITY",
                        severity="HIGH",
                        title="Possible SQL injection",
                        description="A SQL query is built with a Python f-string and may interpolate untrusted values.",
                        line=node.lineno,
                        suggestion="Use parameterized queries and do not interpolate values directly into SQL strings.",
                    )
                )
            elif (
                isinstance(node, ast.BinOp)
                and isinstance(node.op, (ast.Div, ast.FloorDiv))
                and isinstance(node.right, ast.Constant)
                and node.right.value == 0
                and not isinstance(node.right.value, bool)
            ):
                findings.append(
                    StaticFinding(
                        category="BUG",
                        severity="MEDIUM",
                        title="Division by zero",
                        description="The divisor is the constant zero, so evaluating this expression raises an error.",
                        line=node.lineno,
                        suggestion="Use a non-zero divisor or remove the invalid division.",
                    )
                )
        return findings

    def _starts_with_sql(self, node: ast.JoinedStr) -> bool:
        first_text = next(
            (
                value.value
                for value in node.values
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            ),
            "",
        )
        return bool(self.SQL_START.match(first_text))
