"""
backend/agents/reviewer.py
Independent code reviewer agent using expert cloud models (Gemini or Grok).
Provides architectural critique, security audits, and second-opinion reviews.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend.config import Config, get_config
from backend.routing.escalation import EscalationEngine
from backend.routing.model_router import ModelRouter
from backend.schemas.task import ModelUsage, ProviderName, TaskResult, TaskStatus
from backend.storage.usage_tracker import UsageTracker

logger = logging.getLogger(__name__)

_SYSTEM_REVIEWER = """\
You are a senior principal engineer and code reviewer.
Review the following code diff or implementation thoroughly.

Analyze for:
1. Correctness: Does it solve the specified objective without subtle bugs?
2. Security: Check for injection, path traversal, unsafe input handling, credential leaks.
3. Performance: Big-O complexity, unnecessary allocations, unbounded resource usage.
4. Edge cases: Null/undefined handling, empty collections, off-by-one errors.
5. Code style: Adherence to language idioms and clean architecture.

Provide your review in this structure:
### Summary
[Verdict: APPROVED / CHANGES REQUESTED]
Brief overview of changes.

### Key Strengths
- ...

### Issues Found (if any)
- [Critical/Major/Minor] Description and suggested fix

### Recommendations
- ...
"""


class CodeReviewerAgent:
    """
    Independent reviewer using cloud expert models (Gemini / Grok).
    Verifies changes before committing or finalizing implementations.
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        usage_tracker: Optional[UsageTracker] = None,
        router: Optional[ModelRouter] = None,
    ) -> None:
        self._cfg = config or get_config()
        self._tracker = usage_tracker or UsageTracker(self._cfg)
        self._router = router or ModelRouter(self._cfg)
        self._escalation = EscalationEngine(self._cfg, self._router)

    async def review_diff(
        self,
        diff: str,
        task_description: str,
        preferred_provider: Optional[ProviderName] = None,
    ) -> tuple[str, bool, ModelUsage]:
        """
        Review a git diff or code edit.
        Returns (review_text, is_approved, usage).
        """
        prompt = (
            f"Task Objective:\n{task_description}\n\n"
            f"Code Changes (Diff):\n```\n{diff}\n```\n\n"
            "Please provide your critical review."
        )

        chain = [preferred_provider] if preferred_provider else [ProviderName.GEMINI, ProviderName.NVIDIA]
        # Filter available
        chain = [p for p in chain if (p == ProviderName.GEMINI and self._cfg.gemini_available) or (p in (ProviderName.NVIDIA, ProviderName.GROK) and self._cfg.nvidia_available)]

        if not chain:
            # Fallback to local model if cloud models not configured
            chain = [ProviderName.OLLAMA]

        result = await self._escalation.run(
            prompt=prompt,
            system_prompt=_SYSTEM_REVIEWER,
            hint_chain=chain,
            temperature=0.1,
        )

        await self._tracker.record(result.usage)

        is_approved = "CHANGES REQUESTED" not in result.text.upper()
        return result.text, is_approved, result.usage

