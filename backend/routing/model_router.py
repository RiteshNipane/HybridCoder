"""
backend/routing/model_router.py
Routing logic for all supported routing modes:
  AUTO, GEMINI, NVIDIA/GROK, LOCAL, GEMINI+LOCAL, NVIDIA+LOCAL, ALL
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncGenerator, Optional

from backend.config import Config, RoutingMode, get_config
from backend.providers.gemini_provider import BaseProvider, GeminiProvider
from backend.providers.nvidia_provider import NvidiaProvider
from backend.providers.ollama_provider import OllamaProvider
from backend.schemas.task import ModelUsage, ProviderName, TaskSpec

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

@dataclass
class ProviderRegistry:
    """Lazily instantiated provider pool."""

    _cfg: Config = field(default_factory=get_config)
    _gemini: Optional[GeminiProvider] = field(default=None, init=False, repr=False)
    _nvidia: Optional[NvidiaProvider] = field(default=None, init=False, repr=False)
    _ollama: Optional[OllamaProvider] = field(default=None, init=False, repr=False)

    def gemini(self) -> GeminiProvider:
        if self._gemini is None:
            self._gemini = GeminiProvider(self._cfg)
        return self._gemini

    def nvidia(self) -> NvidiaProvider:
        if self._nvidia is None:
            self._nvidia = NvidiaProvider(self._cfg)
        return self._nvidia

    def grok(self) -> NvidiaProvider:
        return self.nvidia()

    def ollama(self) -> OllamaProvider:
        if self._ollama is None:
            self._ollama = OllamaProvider(self._cfg)
        return self._ollama

    @property
    def gemini_available(self) -> bool:
        return self._cfg.gemini_available

    @property
    def nvidia_available(self) -> bool:
        return self._cfg.nvidia_available

    @property
    def grok_available(self) -> bool:
        return self.nvidia_available


# ---------------------------------------------------------------------------
# Route result
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    """Describes which providers to use and in what order."""

    primary: list[ProviderName]      # Providers to try in order
    fallback: list[ProviderName]     # Fallbacks if primary fails
    mode: RoutingMode
    reason: str = ""


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class ModelRouter:
    """Decides which provider(s) to use based on the routing mode and task."""

    def __init__(self, config: Optional[Config] = None) -> None:
        self._cfg = config or get_config()
        self._registry = ProviderRegistry(_cfg=self._cfg)

    # ------------------------------------------------------------------
    # Routing decision
    # ------------------------------------------------------------------

    def decide(
        self,
        task: Optional[TaskSpec] = None,
        override_mode: Optional[RoutingMode] = None,
    ) -> RouteResult:
        mode = override_mode or self._cfg.routing_mode

        if mode == RoutingMode.GEMINI:
            return RouteResult(
                primary=[ProviderName.GEMINI],
                fallback=(
                    ([ProviderName.NVIDIA] if self._cfg.nvidia_available else [])
                    + [ProviderName.OLLAMA]
                ) if self._cfg.escalation_enabled else [],
                mode=mode,
                reason="Explicit GEMINI mode",
            )

        if mode in (RoutingMode.NVIDIA, RoutingMode.GROK):
            return RouteResult(
                primary=[ProviderName.NVIDIA],
                fallback=(
                    ([ProviderName.GEMINI] if self._cfg.gemini_available else [])
                    + [ProviderName.OLLAMA]
                ) if self._cfg.escalation_enabled else [],
                mode=mode,
                reason="Explicit NVIDIA mode (Gemini backup)",
            )

        if mode == RoutingMode.LOCAL:
            return RouteResult(
                primary=[ProviderName.OLLAMA],
                fallback=[],
                mode=mode,
                reason="Explicit LOCAL mode",
            )

        if mode == RoutingMode.GEMINI_LOCAL:
            return RouteResult(
                primary=[ProviderName.OLLAMA],
                fallback=[ProviderName.GEMINI],
                mode=mode,
                reason="Local first, escalate to Gemini",
            )

        if mode in (RoutingMode.NVIDIA_LOCAL, RoutingMode.GROK_LOCAL):
            return RouteResult(
                primary=[ProviderName.OLLAMA],
                fallback=[ProviderName.NVIDIA],
                mode=mode,
                reason="Local first, escalate to NVIDIA",
            )

        if mode == RoutingMode.ALL:
            primary = []
            if self._cfg.nvidia_available:
                primary.append(ProviderName.NVIDIA)
            if self._cfg.gemini_available:
                primary.append(ProviderName.GEMINI)
            primary.append(ProviderName.OLLAMA)
            return RouteResult(
                primary=primary[:1],
                fallback=primary[1:],
                mode=mode,
                reason="ALL mode — try all in preference order",
            )

        # AUTO
        return self._auto_route(task)

    def _auto_route(self, task: Optional[TaskSpec]) -> RouteResult:
        """Smart auto-routing based on availability and task type."""
        from backend.schemas.task import TaskType

        # Classify task
        is_complex = (
            task is not None
            and task.type in {TaskType.PLANNING, TaskType.CODE_REVIEW, TaskType.DEBUGGING}
        )

        if is_complex:
            # Complex AUTO tasks prefer NVIDIA, then Gemini, then local.
            primary = []
            if self._cfg.nvidia_available:
                primary.append(ProviderName.NVIDIA)
            if self._cfg.gemini_available:
                primary.append(ProviderName.GEMINI)
            primary.append(ProviderName.OLLAMA)
            return RouteResult(
                primary=primary[:1],
                fallback=primary[1:] if self._cfg.escalation_enabled else [],
                mode=RoutingMode.AUTO,
                reason="Complex task -> NVIDIA first, Gemini peer fallback",
            )

        # Standard / routine task: Local first, fallback to cloud
        fallback = []
        if self._cfg.escalation_enabled:
            if self._cfg.nvidia_available:
                fallback.append(ProviderName.NVIDIA)
            if self._cfg.gemini_available:
                fallback.append(ProviderName.GEMINI)

        return RouteResult(
            primary=[ProviderName.OLLAMA],
            fallback=fallback,
            mode=RoutingMode.AUTO,
            reason="Routine task -> Local Qwen first",
        )

    # ------------------------------------------------------------------
    # Provider resolution
    # ------------------------------------------------------------------

    def get_provider(self, name: ProviderName) -> BaseProvider:
        if name == ProviderName.GEMINI:
            return self._registry.gemini()
        if name in (ProviderName.NVIDIA, ProviderName.GROK):
            return self._registry.nvidia()
        if name == ProviderName.OLLAMA:
            return self._registry.ollama()
        raise ValueError(f"Unknown provider: {name}")

    # ------------------------------------------------------------------
    # Execute with routing
    # ------------------------------------------------------------------

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        task: Optional[TaskSpec] = None,
        override_mode: Optional[RoutingMode] = None,
        **kwargs: Any,
    ) -> tuple[str, ModelUsage, ProviderName]:
        """Route and complete — returns (text, usage, provider_used)."""
        route = self.decide(task, override_mode)
        providers = route.primary + route.fallback

        last_error: Exception = RuntimeError("No providers configured")
        for name in providers:
            try:
                provider = self.get_provider(name)
                text, usage = await provider.complete(prompt, system_prompt, **kwargs)
                logger.info("Routed to %s (%s)", name.value, route.reason)
                return text, usage, name
            except Exception as exc:
                logger.warning("Provider %s failed: %s — trying next", name.value, exc)
                last_error = exc

        raise last_error

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        task: Optional[TaskSpec] = None,
        override_mode: Optional[RoutingMode] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[tuple[str, ProviderName], None]:
        """Route and stream — yields (chunk, provider_name)."""
        route = self.decide(task, override_mode)
        providers = route.primary + route.fallback

        last_error: Exception = RuntimeError("No providers configured")
        for name in providers:
            try:
                provider = self.get_provider(name)
                logger.info("Streaming via %s (%s)", name.value, route.reason)
                async for chunk in provider.stream(prompt, system_prompt, **kwargs):
                    yield chunk, name
                return
            except Exception as exc:
                logger.warning("Stream provider %s failed: %s — trying next", name.value, exc)
                last_error = exc

        raise last_error

    async def health_check_all(self) -> dict[str, bool]:
        """Check which providers are healthy."""
        results: dict[str, bool] = {}
        # Gemini
        if self._cfg.gemini_available:
            try:
                results["gemini"] = await self._registry.gemini().health_check()
            except Exception:
                results["gemini"] = False
        else:
            results["gemini"] = False

        # NVIDIA
        if self._cfg.nvidia_available:
            try:
                results["nvidia"] = await self._registry.nvidia().health_check()
                results["grok"] = results["nvidia"]
            except Exception:
                results["nvidia"] = False
                results["grok"] = False
        else:
            results["nvidia"] = False
            results["grok"] = False

        # Ollama
        try:
            results["ollama"] = await self._registry.ollama().health_check()
        except Exception:
            results["ollama"] = False

        return results
