"""
backend/providers/ollama_provider.py
Full Ollama provider using httpx to call the local Ollama API directly.
Supports streaming via async generators.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Optional

import httpx

from backend.config import Config, get_config
from backend.agents.team_context import add_team_context
from backend.providers.gemini_provider import BaseProvider
from backend.schemas.task import ModelUsage, ProviderName

logger = logging.getLogger(__name__)


class OllamaProvider(BaseProvider):
    provider_name = ProviderName.OLLAMA

    def __init__(self, config: Optional[Config] = None) -> None:
        self._cfg = config or get_config()
        self._base_url = self._cfg.ollama_base_url.rstrip("/")
        self._model = self._cfg.ollama_model
        self._timeout = self._cfg.ollama_timeout
        logger.info("OllamaProvider initialised (model=%s, url=%s)", self._model, self._base_url)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(
        self, prompt: str, system_prompt: Optional[str]
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        messages.append({
            "role": "system",
            "content": add_team_context(system_prompt, self._cfg, ProviderName.OLLAMA),
        })
        messages.append({"role": "user", "content": prompt})
        return messages

    def _make_usage(
        self, model: str, prompt_tokens: int, completion_tokens: int, latency_ms: float
    ) -> ModelUsage:
        return ModelUsage(
            provider=ProviderName.OLLAMA,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            estimated_cost_usd=0.0,  # Local — no cost
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
        """Non-streaming completion via /api/chat."""
        messages = self._build_messages(prompt, system_prompt)
        return await self.complete_chat(
            messages=messages,
            system_prompt=None,
            max_tokens=max_tokens,
            temperature=temperature,
            model=model,
        )

    async def complete_chat(
        self,
        messages: list[dict[str, str]],
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> tuple[str, ModelUsage]:
        """Non-streaming multi-turn chat completion via /api/chat."""
        model = model or self._model
        payload_messages: list[dict[str, str]] = [dict(message) for message in messages]
        system_message = next((m for m in payload_messages if m.get("role") == "system"), None)
        if system_message:
            system_message["content"] = add_team_context(
                system_message["content"], self._cfg, ProviderName.OLLAMA
            )
        else:
            payload_messages.insert(0, {
                "role": "system",
                "content": add_team_context(system_prompt, self._cfg, ProviderName.OLLAMA),
            })
        payload = {
            "model": model,
            "messages": payload_messages,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "num_ctx": 2048,
            },
        }
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()

            latency_ms = (time.monotonic() - t0) * 1000
            text: str = data.get("message", {}).get("content", "")
            pt: int = data.get("prompt_eval_count", 0)
            ct: int = data.get("eval_count", 0)
            usage = self._make_usage(model, pt, ct, latency_ms)
            logger.debug("Ollama complete_chat: %d tokens in %.0fms", pt + ct, latency_ms)
            return text, usage

        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self._base_url}. Is Ollama running?"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama API error {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Ollama timed out after {self._timeout}s") from exc

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        model: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Streaming completion via /api/chat with stream=True."""
        model = model or self._model
        messages = self._build_messages(prompt, system_prompt)
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
                "num_ctx": 2048,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/api/chat",
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        content = data.get("message", {}).get("content", "")
                        if content:
                            yield content
                        if data.get("done"):
                            break
        except httpx.ConnectError as exc:
            raise ConnectionError(
                f"Cannot connect to Ollama at {self._base_url}."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Ollama API error {exc.response.status_code}: {exc.response.text}"
            ) from exc

    async def stream_with_usage(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        model: Optional[str] = None,
    ) -> AsyncGenerator[str | ModelUsage, None]:
        """Yields text chunks then a final ModelUsage."""
        model = model or self._model
        messages = self._build_messages(prompt, system_prompt)
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"num_predict": max_tokens, "temperature": temperature, "num_ctx": 2048},
        }
        t0 = time.monotonic()
        pt, ct = 0, 0

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST", f"{self._base_url}/api/chat", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if data.get("done"):
                        pt = data.get("prompt_eval_count", 0)
                        ct = data.get("eval_count", 0)
                        break

        latency_ms = (time.monotonic() - t0) * 1000
        yield self._make_usage(model, pt, ct, latency_ms)

    async def list_models(self) -> list[str]:
        """Return names of locally available Ollama models."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                resp.raise_for_status()
                data = resp.json()
                return [m["name"] for m in data.get("models", [])]
        except Exception as exc:
            logger.warning("Could not list Ollama models: %s", exc)
            return []

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/api/tags")
                return resp.status_code == 200
        except Exception as exc:
            logger.debug("Ollama health check failed: %s", exc)
            return False
