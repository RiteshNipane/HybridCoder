"""
Live "which model is working on the answer right now?" tracking.

Every provider call (Gemini / NVIDIA / Ollama, ``complete`` and ``stream``) is wrapped
automatically (see ``BaseProvider.__init_subclass__``) so it reports:

    thinking    request sent, waiting for the model
    generating  tokens are arriving
    done        finished OK
    failed      errored (the engine may then fall back to the next provider)

Use :func:`get_active_model` anywhere in the backend to ask what is working *now*.
The WebSocket endpoint also forwards each change to the UI as a ``model_activity`` event.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import time
from typing import Any, Callable, Optional

Listener = Callable[[dict], None]

_listener: contextvars.ContextVar[Optional[Listener]] = contextvars.ContextVar(
    "activity_listener", default=None
)
_active: Optional[dict[str, Any]] = None  # most recent activity event (process-wide)

_RUNNING = ("thinking", "generating")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_active_model() -> Optional[dict[str, Any]]:
    """
    Return the model that is working on an answer right now, or ``None`` if idle.

    Example result:
        {"provider": "nvidia", "model": "meta/llama-3.3-70b-instruct",
         "state": "generating", "timestamp": 1731950000.1}
    """
    if _active and _active.get("state") in _RUNNING:
        return dict(_active)
    return None


def set_listener(callback: Optional[Listener]) -> contextvars.Token:
    """Receive every activity event produced by tasks created from this context."""
    return _listener.set(callback)


def reset_listener(token: contextvars.Token) -> None:
    _listener.reset(token)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def emit(provider: str, model: str, state: str, **extra: Any) -> None:
    global _active
    event = {"provider": provider, "model": model, "state": state, "timestamp": time.time(), **extra}
    _active = event
    cb = _listener.get()
    if cb is not None:
        try:
            cb(event)
        except Exception:  # a broken listener must never break a model call
            pass


def _identity(provider_obj: Any, kwargs: dict) -> tuple[str, str]:
    name = getattr(provider_obj, "provider_name", None)
    provider = getattr(name, "value", None) or str(name or "unknown")
    model = kwargs.get("model") or getattr(provider_obj, "_model", None) or "unknown"
    return provider, str(model)


def track_provider_call(fn: Callable) -> Callable:
    """Wrap a provider's ``complete`` (coroutine) or ``stream`` (async generator) to report activity."""

    if inspect.isasyncgenfunction(fn):

        @functools.wraps(fn)
        async def stream_wrapper(self, *args, **kwargs):
            provider, model = _identity(self, kwargs)
            emit(provider, model, "thinking")
            settled = False
            first = True
            try:
                async for chunk in fn(self, *args, **kwargs):
                    if first:
                        emit(provider, model, "generating")
                        first = False
                    yield chunk
                settled = True
                emit(provider, model, "done")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                settled = True
                emit(provider, model, "failed", error=str(exc)[:200])
                raise
            finally:
                if not settled:  # consumer stopped early / cancelled
                    emit(provider, model, "done")

        return stream_wrapper

    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def call_wrapper(self, *args, **kwargs):
            provider, model = _identity(self, kwargs)
            emit(provider, model, "thinking")
            try:
                result = await fn(self, *args, **kwargs)
            except asyncio.CancelledError:
                emit(provider, model, "done")
                raise
            except Exception as exc:
                emit(provider, model, "failed", error=str(exc)[:200])
                raise
            emit(provider, model, "done")
            return result

        return call_wrapper

    return fn
