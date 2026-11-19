"""
backend/providers/grok_provider.py
Full Grok provider using the OpenAI-compatible client pointing at xAI.
Supports streaming and usage tracking via the Responses API.
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


class GrokProvider(BaseProvider):
    provider_name = ProviderName.GROK

    def __init__(self, config: Optional[Config] = None) -> None:
        self._cfg = config or get_config()
        if not self._cfg.xai_api_key:
            raise RuntimeError("XAI_API_KEY is not set")
        self._client = AsyncOpenAI(
            api_key=self._cfg.xai_api_key,
            base_url=self._cfg.xai_base_url,
            timeout=float(self._cfg.xai_timeout),
            max_retries=0,  # We handle retries ourselves
        )
        self._model = self._cfg.xai_model
        logger.info("GrokProvider initialised (model=%s)", self._model)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self._cfg.grok_input_cost_per_m / 1_000_000
            + completion_tokens * self._cfg.grok_output_cost_per_m / 1_000_000
        )

    def _make_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
    ) -> ModelUsage:
        return ModelUsage(
            provider=ProviderName.GROK,
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
        while attempt < self._cfg.xai_max_retries:
            attempt += 1
            try:
                return await coro_fn(*args, **kwargs)
            except RateLimitError as exc:
                logger.warning("Grok rate limit (attempt %d/%d)", attempt, self._cfg.xai_max_retries)
                last_exc = exc
                if attempt < self._cfg.xai_max_retries:
                    await asyncio.sleep(2 ** attempt)
            except APIStatusError as exc:
                logger.warning("Grok API error %d (attempt %d): %s", exc.status_code, attempt, exc.message)
                last_exc = exc
                if exc.status_code >= 500 and attempt < self._cfg.xai_max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
            except asyncio.TimeoutError:
                logger.warning("Grok timeout (attempt %d/%d)", attempt, self._cfg.xai_max_retries)
                last_exc = TimeoutError(f"Grok timed out after {self._cfg.xai_timeout}s")
        raise last_exc

    # ------------------------------------------------------------------
    # Public API — Responses API (preferred)
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
        """Non-streaming completion using the Responses API."""
        model = model or self._model
        messages = self._build_messages(prompt, system_prompt)
        t0 = time.monotonic()

        async def _do_complete():
            # xAI Responses API — mirroring OpenAI's responses.create
            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=temperature,
                stream=False,
            )
            return response

        response = await self._retry(_do_complete)
        latency_ms = (time.monotonic() - t0) * 1000
        text = response.choices[0].message.content or ""
        pt = response.usage.prompt_tokens if response.usage else 0
        ct = response.usage.completion_tokens if response.usage else 0
        usage = self._make_usage(model, pt, ct, latency_ms)
        logger.debug("Grok complete: %d tokens in %.0fms", pt + ct, latency_ms)
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
        """Streaming completion — yields text chunks."""
        model = model or self._model
        messages = self._build_messages(prompt, system_prompt)
        attempt = 0
        last_exc: Exception = RuntimeError("No attempts made")

        while attempt < self._cfg.xai_max_retries:
            attempt += 1
            try:
                async with await self._client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                ) as stream_response:
                    async for chunk in stream_response:
                        delta = chunk.choices[0].delta.content if chunk.choices else None
                        if delta:
                            yield delta
                return
            except RateLimitError as exc:
                logger.warning("Grok stream rate limit (attempt %d)", attempt)
                last_exc = exc
                if attempt < self._cfg.xai_max_retries:
                    await asyncio.sleep(2 ** attempt)
            except APIStatusError as exc:
                logger.warning("Grok stream API error %d: %s", exc.status_code, exc.message)
                last_exc = exc
                if exc.status_code >= 500 and attempt < self._cfg.xai_max_retries:
                    await asyncio.sleep(2 ** attempt)
                else:
                    raise
            except asyncio.TimeoutError:
                logger.warning("Grok stream timeout (attempt %d)", attempt)
                last_exc = TimeoutError("Grok stream timed out")

        raise last_exc

    async def stream_with_usage(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> AsyncGenerator[str | ModelUsage, None]:
        """Yields text chunks then a final ModelUsage object."""
        model = model or self._model
        t0 = time.monotonic()
        total_text = ""
        pt, ct = 0, 0

        messages = self._build_messages(prompt, system_prompt)
        try:
            async with await self._client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                stream_options={"include_usage": True},
            ) as stream_response:
                async for chunk in stream_response:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        total_text += delta
                        yield delta
                    if chunk.usage:
                        pt = chunk.usage.prompt_tokens
                        ct = chunk.usage.completion_tokens
        except APIError as exc:
            logger.error("Grok stream_with_usage error: %s", exc)
            raise

        latency_ms = (time.monotonic() - t0) * 1000
        if not pt:
            pt = len(prompt.split()) * 4 // 3
            ct = len(total_text.split()) * 4 // 3
        yield self._make_usage(model, pt, ct, latency_ms)

    async def health_check(self) -> bool:
        try:
            text, _ = await self.complete("Reply with OK", max_tokens=10)
            return bool(text)
        except Exception as exc:
            logger.debug("Grok health check failed: %s", exc)
            return False
