"""
backend/routing/escalation.py
Escalation logic: local → gemini → grok chain with configurable retries.
Tracks which providers were tried and surfaces detailed error context.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator, Optional

from backend.config import Config, get_config
from backend.agents.team_context import add_team_context
from backend.providers.gemini_provider import BaseProvider
from backend.providers.grok_provider import GrokProvider
from backend.providers.ollama_provider import OllamaProvider
from backend.routing.model_router import ModelRouter
from backend.schemas.task import ModelUsage, ProviderName, TaskSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EscalationAttempt:
    provider: ProviderName
    success: bool
    error: Optional[str] = None
    latency_ms: float = 0.0
    usage: Optional[ModelUsage] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class EscalationResult:
    text: str
    usage: ModelUsage
    provider: ProviderName
    attempts: list[EscalationAttempt] = field(default_factory=list)
    escalated: bool = False  # True if we had to go beyond the first provider


# ---------------------------------------------------------------------------
# Escalation engine
# ---------------------------------------------------------------------------

class EscalationEngine:
    """
    Runs providers in a configurable chain.
    Default escalation order: LOCAL → GEMINI → NVIDIA NIM.

    If a provider fails (exception, timeout, empty response), the engine
    moves to the next one after the configured retry attempts.
    """

    DEFAULT_CHAIN: list[ProviderName] = [
        ProviderName.OLLAMA,
        ProviderName.GEMINI,
        ProviderName.NVIDIA,
    ]

    def __init__(
        self,
        config: Optional[Config] = None,
        router: Optional[ModelRouter] = None,
    ) -> None:
        self._cfg = config or get_config()
        self._router = router or ModelRouter(self._cfg)
        self._max_retries = self._cfg.escalation_max_retries

    # ------------------------------------------------------------------
    # Build chain based on availability
    # ------------------------------------------------------------------

    def _build_chain(self, hint_chain: Optional[list[ProviderName]] = None) -> list[ProviderName]:
        chain = hint_chain or self.DEFAULT_CHAIN
        available: list[ProviderName] = []
        for p in chain:
            if p == ProviderName.GEMINI and not self._cfg.gemini_available:
                logger.debug("Skipping GEMINI in escalation chain (key not set)")
                continue
            if p in (ProviderName.NVIDIA, ProviderName.GROK) and not self._cfg.nvidia_available:
                logger.debug("Skipping NVIDIA in escalation chain (key not set)")
                continue
            available.append(p)
        return available

    # ------------------------------------------------------------------
    # Core escalation logic
    # ------------------------------------------------------------------

    async def run(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        task: Optional[TaskSpec] = None,
        chain: Optional[list[ProviderName]] = None,
        hint_chain: Optional[list[ProviderName]] = None,
        **kwargs: Any,
    ) -> EscalationResult:
        """
        Try providers in chain order. Retry each up to max_retries times before escalating.
        Returns the first successful result.
        """
        effective_chain = self._build_chain(chain or hint_chain)
        if not effective_chain:
            raise RuntimeError("No providers available in escalation chain")

        all_attempts: list[EscalationAttempt] = []
        first_provider = effective_chain[0]

        for idx, provider_name in enumerate(effective_chain):
            provider = self._router.get_provider(provider_name)
            attempt_errors: list[str] = []

            for retry in range(self._max_retries):
                import time as _time
                t0 = _time.monotonic()
                try:
                    provider_system_prompt = add_team_context(system_prompt, self._cfg, provider_name)
                    text, usage = await provider.complete(prompt, provider_system_prompt, **kwargs)
                    latency_ms = (_time.monotonic() - t0) * 1000

                    if not text.strip():
                        raise ValueError("Empty response from provider")

                    attempt = EscalationAttempt(
                        provider=provider_name,
                        success=True,
                        latency_ms=latency_ms,
                        usage=usage,
                    )
                    all_attempts.append(attempt)

                    escalated = provider_name != first_provider
                    if escalated:
                        logger.info(
                            "Escalated to %s after failures: %s",
                            provider_name.value,
                            attempt_errors,
                        )

                    return EscalationResult(
                        text=text,
                        usage=usage,
                        provider=provider_name,
                        attempts=all_attempts,
                        escalated=escalated,
                    )

                except Exception as exc:
                    latency_ms = (_time.monotonic() - t0) * 1000
                    err_msg = f"{type(exc).__name__}: {exc}"
                    attempt_errors.append(err_msg)
                    attempt = EscalationAttempt(
                        provider=provider_name,
                        success=False,
                        error=err_msg,
                        latency_ms=latency_ms,
                    )
                    all_attempts.append(attempt)
                    logger.warning(
                        "Escalation: %s attempt %d/%d failed: %s",
                        provider_name.value,
                        retry + 1,
                        self._max_retries,
                        err_msg,
                    )

                    if retry < self._max_retries - 1:
                        await asyncio.sleep(1.5 ** retry)

            logger.warning("All retries exhausted for %s — escalating", provider_name.value)

        # All providers exhausted
        errors_summary = "; ".join(
            f"{a.provider.value}: {a.error}" for a in all_attempts if not a.success
        )
        raise RuntimeError(
            f"All escalation providers failed. Errors: {errors_summary}"
        )

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        task: Optional[TaskSpec] = None,
        chain: Optional[list[ProviderName]] = None,
        hint_chain: Optional[list[ProviderName]] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[tuple[str, ProviderName, bool], None]:
        """
        Stream with escalation. Yields (chunk, provider_name, is_final).
        If a provider fails mid-stream, retries or escalates from the beginning.
        """
        effective_chain = self._build_chain(chain or hint_chain)
        if not effective_chain:
            raise RuntimeError("No providers available in escalation chain")

        for provider_name in effective_chain:
            provider = self._router.get_provider(provider_name)
            try:
                provider_system_prompt = add_team_context(system_prompt, self._cfg, provider_name)
                async for chunk in provider.stream(prompt, provider_system_prompt, **kwargs):
                    yield chunk, provider_name, False
                yield "", provider_name, True  # signal completion
                return
            except Exception as exc:
                logger.warning(
                    "Stream escalation: %s failed (%s) — trying next provider",
                    provider_name.value,
                    exc,
                )
                continue

        raise RuntimeError("All stream escalation providers failed")

    # ------------------------------------------------------------------
    # Health-aware routing
    # ------------------------------------------------------------------

    async def healthy_chain(self) -> list[ProviderName]:
        """Return only the healthy providers in chain order."""
        statuses = await self._router.health_check_all()
        chain = self._build_chain()
        return [p for p in chain if statuses.get(p.value, False)]
