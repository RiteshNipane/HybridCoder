"""Explicit, conservative complexity routing for engineering requests."""

from __future__ import annotations

import re
from dataclasses import dataclass


_CLOUD_PATTERNS = (
    re.compile(r"\b(?:architecture|architectural|redesign|migration|migrate|system-wide|cross[- ]module)\b", re.I),
    re.compile(r"\b(?:across|between)\s+(?:the\s+)?(?:modules?|services?|packages?|layers?|codebase|project)\b", re.I),
    re.compile(r"\b(?:across|between)\s+(?:the\s+)?(?:frontend\s+and\s+backend|backend\s+and\s+frontend)\b", re.I),
    re.compile(r"\b(?:multi[- ]file|multiple files|several files|whole codebase|entire project|end[- ]to[- ]end)\b", re.I),
    re.compile(r"\b(?:new project|from scratch|full[- ]stack|complete application|entire app)\b", re.I),
    re.compile(r"\b(?:integrate|integration|refactor)\b.{0,60}\b(?:multiple|several|across|whole|entire)\b", re.I),
)
_FILE_REFERENCE = re.compile(
    r"(?<![\w])(?:[\w.-]+[\\/])*[\w.-]+\.(?:py|ts|tsx|js|jsx|go|rs|java|cs|cpp|h|json|ya?ml|toml|sql)(?![\w])",
    re.I,
)


@dataclass(frozen=True)
class TaskRouteDecision:
    route: str
    complexity: str
    reason: str


def classify_task_complexity(prompt: str) -> TaskRouteDecision:
    """Route broad, cross-component work to a cloud planner; default targeted work to local Qwen."""
    text = prompt.strip()
    for pattern in _CLOUD_PATTERNS:
        match = pattern.search(text)
        if match:
            return TaskRouteDecision(
                route="cloud",
                complexity="high",
                reason=f"request signals multi-component work ({match.group(0)})",
            )

    paths = {match.group(0).lower().replace("\\", "/") for match in _FILE_REFERENCE.finditer(text)}
    if len(paths) >= 2:
        return TaskRouteDecision(
            route="cloud",
            complexity="high",
            reason=f"request names {len(paths)} files",
        )

    return TaskRouteDecision(
        route="local",
        complexity="targeted",
        reason="localized request; use local Qwen first",
    )
