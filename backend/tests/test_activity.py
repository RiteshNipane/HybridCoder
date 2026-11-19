"""Tests for live active-model tracking."""
import asyncio

from backend import activity


class _Fake:
    provider_name = type("P", (), {"value": "gemini"})()
    _model = "fake-model"

    @activity.track_provider_call
    async def complete(self, prompt, **kw):
        return "ok"

    @activity.track_provider_call
    async def stream(self, prompt, **kw):
        yield "a"
        yield "b"

    @activity.track_provider_call
    async def boom(self, prompt, **kw):
        raise RuntimeError("503")


def _collect(coro_factory):
    events = []

    async def run():
        token = activity.set_listener(events.append)
        try:
            await coro_factory()
        finally:
            activity.reset_listener(token)

    asyncio.run(run())
    return events


def test_complete_reports_thinking_then_done():
    ev = _collect(lambda: _Fake().complete("hi"))
    assert [e["state"] for e in ev] == ["thinking", "done"]
    assert ev[0]["provider"] == "gemini" and ev[0]["model"] == "fake-model"


def test_stream_reports_generating():
    async def go():
        async for _ in _Fake().stream("hi"):
            pass

    ev = _collect(go)
    assert [e["state"] for e in ev] == ["thinking", "generating", "done"]


def test_failure_is_reported_and_reraised():
    async def go():
        try:
            await _Fake().boom("hi")
        except RuntimeError:
            pass

    ev = _collect(go)
    assert ev[-1]["state"] == "failed" and "503" in ev[-1]["error"]


def test_get_active_model_is_none_when_idle():
    _collect(lambda: _Fake().complete("hi"))
    assert activity.get_active_model() is None
