"""Severity-graded validation issues, modeled on qdmr's RadioLimits.

qdmr grades verification findings (Hint/Warning/Critical) and records the
path to the offending element, so one run reports every problem instead of
stopping at the first. This module provides the same shape for profile and
capabilities validation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Severity(enum.IntEnum):
    """How much a finding threatens the generated codeplug.

    Mirrors qdmr's RadioLimitIssue levels: a HINT changes nothing, a WARNING
    means the codeplug gets adjusted but should still work, and CRITICAL means
    assembly would fail or produce a non-functional codeplug.
    """

    HINT = 0
    WARNING = 1
    CRITICAL = 2


@dataclass(frozen=True)
class ValidationIssue:
    """One finding, with the path to the element that produced it."""

    severity: Severity
    path: tuple[str, ...]
    message: str

    def format(self) -> str:
        location = " > ".join(self.path)
        prefix = f"{location}: " if location else ""
        return f"{prefix}{self.message}"


class ValidationReport:
    """Collects issues across a whole validation run."""

    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []

    def add(self, severity: Severity, path: tuple[str, ...], message: str) -> None:
        self.issues.append(ValidationIssue(severity, path, message))

    def hint(self, path: tuple[str, ...], message: str) -> None:
        self.add(Severity.HINT, path, message)

    def warning(self, path: tuple[str, ...], message: str) -> None:
        self.add(Severity.WARNING, path, message)

    def critical(self, path: tuple[str, ...], message: str) -> None:
        self.add(Severity.CRITICAL, path, message)

    @property
    def has_critical(self) -> bool:
        return any(issue.severity is Severity.CRITICAL for issue in self.issues)

    def critical_message(self) -> str:
        return "; ".join(
            issue.format()
            for issue in self.issues
            if issue.severity is Severity.CRITICAL
        )

    def non_critical(self) -> list[ValidationIssue]:
        return [
            issue for issue in self.issues if issue.severity is not Severity.CRITICAL
        ]
