"""
backend/providers/nvidia_provider.py
Full NVIDIA NIM API provider using the OpenAI-compatible client.
Supports streaming, reasoning models, and usage tracking via integrate.api.nvidia.com.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Optional

from openai import APIError, APIStatusError, AsyncOpenAI, RateLimitError

from backend.config import Config, get_config
from backend.providers.gemini_provider import BaseProvider
from backend.schemas.task import ModelUsage, ProviderName

logger = logging.getLogger(__name__)


class NvidiaProvider(BaseProvider):
    provider_name = ProviderName.NVIDIA

    def __init__(self, config: Optional[Config] = None) -> None:
        self._cfg = config or get_config()
        api_key = self._cfg.nvidia_api_key or self._cfg.xai_api_key
        if not api_key:
            raise RuntimeError("NVIDIA_API_KEY is not set")
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=self._cfg.nvidia_base_url,
            timeout=float(self._cfg.nvidia_timeout),
            max_retries=0,  # We handle retries ourselves
        )
        self._model = self._cfg.nvidia_model
        logger.info("NvidiaProvider initialised (model=%s, base_url=%s)", self._model, self._cfg.nvidia_base_url)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self._cfg.nvidia_input_cost_per_m / 1_000_000
            + completion_tokens * self._cfg.nvidia_output_cost_per_m / 1_000_000
        )

    def _make_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
    ) -> ModelUsage:
        return ModelUsage(
            provider=ProviderName.NVIDIA,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=self._compute_cost(prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
        )

    def _build_messages(
        self, prompt: str, system_prompt: Optional[str]
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return messages

    async def _retry(self, coro_fn, *args, **kwargs):
        """Execute coro_fn with exponential back-off on rate limit / server errors."""
        attempt = 0
        last_exc: Exception = RuntimeError("No attempts made")
        while attempt < self._cfg.nvidia_max_retries:
            attempt += 1
            try:
                return await coro_fn(*args, **kwargs)
            except RateLimitError as exc:
                logger.warning("NVIDIA NIM rate limit (attempt %d/%d)", attempt, self._cfg.nvidia_max_retries)
                last_exc = exc
                if attempt < self._cfg.nvidia_max_retries:
                    await asyncio.sleep(2 ** attempt)
            except APIStatusError as exc:
                try:
                    body = exc.response.text[:300]
                except Exception:
                    body = exc.message
                logger.warning("NVIDIA API error %d (attempt %d, model=%s): %s", exc.status_code, attempt, self._model, body)
                last_exc = exc
                if exc.status_code >= 500 and attempt < self._cfg.nvidia_max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
            except asyncio.TimeoutError:
                logger.warning("NVIDIA timeout (attempt %d/%d)", attempt, self._cfg.nvidia_max_retries)
                last_exc = TimeoutError(f"NVIDIA API timed out after {self._cfg.nvidia_timeout}s")
        raise last_exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> tuple[str, ModelUsage]:
        """Non-streaming chat completion."""
        target_model = model or self._model
        messages = self._build_messages(prompt, system_prompt)
        t0 = time.monotonic()

        async def _call():
            return await self._client.chat.completions.create(
                model=target_model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )

        resp = await self._retry(_call)
        latency_ms = (time.monotonic() - t0) * 1000

        choice = resp.choices[0]
        text = choice.message.content or ""

        pt = resp.usage.prompt_tokens if resp.usage else 0
        ct = resp.usage.completion_tokens if resp.usage else 0
        usage = self._make_usage(target_model, pt, ct, latency_ms)

        logger.debug("NVIDIA complete: %d tokens in %.0fms", pt + ct, latency_ms)
        return text, usage

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Streaming chat completion."""
        target_model = model or self._model
        messages = self._build_messages(prompt, system_prompt)

        stream = await self._client.chat.completions.create(
            model=target_model,
            messages=messages,  # type: ignore[arg-type]
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
            **kwargs,
        )

        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def health_check(self) -> bool:
        """Check if NVIDIA API key is configured and can connect."""
        if not self._cfg.nvidia_api_key and not self._cfg.xai_api_key:
            return False
        try:
            await self._client.models.list()
            return True
        except Exception as exc:
            logger.warning("NVIDIA health check failed: %s", exc)
            return False


# Backwards compatibility alias
GrokProvider = NvidiaProvider

