"""
backend/providers/gemini_provider.py
Full Gemini provider using the google-genai generate_content API.
Supports streaming, structured output, and usage tracking.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator, Optional, Type

from google import genai
from google.genai import errors as genai_errors
from pydantic import BaseModel

from backend.config import Config, get_config
from backend.schemas.task import ModelUsage, ProviderName

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base interface (all providers implement this)
# ---------------------------------------------------------------------------

class BaseProvider:
    """Abstract base class for LLM providers."""

    provider_name: ProviderName = ProviderName.UNKNOWN

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Auto-report live activity (thinking / generating / done / failed) for every provider."""
        super().__init_subclass__(**kwargs)
        from backend.activity import track_provider_call

        for _name in ("complete", "stream"):
            _fn = cls.__dict__.get(_name)
            if _fn is not None:
                setattr(cls, _name, track_provider_call(_fn))

    async def complete(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> tuple[str, ModelUsage]:
        raise NotImplementedError

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        raise NotImplementedError

    async def health_check(self) -> bool:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Gemini Provider
# ---------------------------------------------------------------------------

class GeminiProvider(BaseProvider):
    provider_name = ProviderName.GEMINI

    def __init__(self, config: Optional[Config] = None) -> None:
        self._cfg = config or get_config()
        if not self._cfg.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self._client = genai.Client(
            api_key=self._cfg.gemini_api_key,
            http_options=genai.types.HttpOptions(timeout=self._cfg.gemini_timeout * 1000),
        )
        # The SDK call runs in a worker thread, which asyncio cannot stop when
        # wait_for expires. Its HTTP client owns the real request timeout.
        self._wait_timeout = self._cfg.gemini_timeout + 10
        self._model = self._cfg.gemini_model
        logger.info("GeminiProvider initialised (model=%s)", self._model)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_input(self, prompt: str, system_prompt: Optional[str]) -> str:
        if system_prompt:
            return f"{system_prompt}\n\n{prompt}"
        return prompt

    def _compute_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (
            prompt_tokens * self._cfg.gemini_input_cost_per_m / 1_000_000
            + completion_tokens * self._cfg.gemini_output_cost_per_m / 1_000_000
        )

    def _make_usage(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
    ) -> ModelUsage:
        return ModelUsage(
            provider=ProviderName.GEMINI,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=self._compute_cost(prompt_tokens, completion_tokens),
            latency_ms=latency_ms,
        )

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
        """Non-streaming completion. Returns (text, usage)."""
        model = model or self._model
        full_input = self._build_input(prompt, system_prompt)
        t0 = time.monotonic()
        attempt = 0
        last_error: Exception = RuntimeError("No attempts made")

        while attempt < self._cfg.gemini_max_retries:
            attempt += 1
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._client.models.generate_content,
                        model=model,
                        contents=full_input,
                        config=genai.types.GenerateContentConfig(
                            max_output_tokens=max_tokens,
                            temperature=temperature,
                        ),
                    ),
                    timeout=self._wait_timeout,
                )
                latency_ms = (time.monotonic() - t0) * 1000
                text = response.text or ""
                # Extract token counts if available
                pt = getattr(getattr(response, "usage_metadata", None), "prompt_token_count", 0) or 0
                ct = getattr(getattr(response, "usage_metadata", None), "candidates_token_count", 0) or 0
                usage = self._make_usage(model, pt, ct, latency_ms)
                logger.debug("Gemini complete: %d tokens in %.0fms", pt + ct, latency_ms)
                return text, usage

            except genai_errors.APIError as exc:
                status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                logger.warning("Gemini API error (attempt %d/%d): %s", attempt, self._cfg.gemini_max_retries, exc)
                last_error = exc
                if status in (429, 503) and attempt < self._cfg.gemini_max_retries:
                    await asyncio.sleep(2 ** attempt)
                elif status and 400 <= status < 500 and status != 429:
                    raise  # Non-retryable client error
            except asyncio.TimeoutError:
                logger.warning("Gemini timeout (attempt %d/%d)", attempt, self._cfg.gemini_max_retries)
                last_error = TimeoutError(f"Gemini timed out after {self._cfg.gemini_timeout}s")

        raise last_error

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
        full_input = self._build_input(prompt, system_prompt)

        def _run_stream() -> list[str]:
            chunks: list[str] = []
            response = self._client.models.generate_content_stream(
                model=model,
                contents=full_input,
                config=genai.types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            )
            for chunk in response:
                if chunk.text:
                    chunks.append(chunk.text)
            return chunks

        attempt = 0
        last_error: Exception = RuntimeError("No attempts made")
        while attempt < self._cfg.gemini_max_retries:
            attempt += 1
            try:
                chunks = await asyncio.wait_for(
                    asyncio.to_thread(_run_stream),
                    timeout=self._wait_timeout,
                )
                for chunk in chunks:
                    yield chunk
                return
            except genai_errors.APIError as exc:
                status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
                logger.warning("Gemini stream error (attempt %d): %s", attempt, exc)
                last_error = exc
                if status in (429, 503) and attempt < self._cfg.gemini_max_retries:
                    await asyncio.sleep(2 ** attempt)
                elif status and 400 <= status < 500 and status != 429:
                    raise
            except asyncio.TimeoutError:
                last_error = TimeoutError("Gemini stream timed out")
                logger.warning("Gemini stream timeout (attempt %d)", attempt)

        raise last_error

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
        full_input = self._build_input(prompt, system_prompt)
        t0 = time.monotonic()
        total_text = ""

        async for chunk in self.stream(prompt, system_prompt, max_tokens, temperature, model=model):
            total_text += chunk
            yield chunk

        latency_ms = (time.monotonic() - t0) * 1000
        # Approximate token count since streaming doesn't always give metadata
        approx_prompt = len(full_input.split()) * 4 // 3
        approx_completion = len(total_text.split()) * 4 // 3
        yield self._make_usage(model, approx_prompt, approx_completion, latency_ms)

    async def structured_output(
        self,
        prompt: str,
        output_schema: Type[BaseModel],
        system_prompt: Optional[str] = None,
        model: Optional[str] = None,
    ) -> BaseModel:
        """Return structured output matching output_schema."""
        schema_desc = output_schema.model_json_schema()
        augmented = (
            f"{prompt}\n\n"
            f"Respond ONLY with valid JSON matching this schema:\n{schema_desc}\n"
            "Do not include any explanation or markdown code fences."
        )
        text, _ = await self.complete(augmented, system_prompt=system_prompt, model=model, temperature=0.0)
        import json, re
        # Strip markdown fences if present
        text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
        return output_schema.model_validate(json.loads(text))

    async def health_check(self) -> bool:
        try:
            text, _ = await self.complete("Reply with OK", max_tokens=10, model=self._cfg.gemini_model_fast)
            return bool(text)
        except Exception as exc:
            logger.debug("Gemini health check failed: %s", exc)
            return False
