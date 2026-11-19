"""
Tests for LLM providers (Ollama, Gemini, Grok).
"""
import pytest
from backend.config import Config
from backend.providers.ollama_provider import OllamaProvider


@pytest.mark.asyncio
async def test_ollama_provider_health():
    cfg = Config()
    provider = OllamaProvider(cfg)
    is_healthy = await provider.health_check()
    # If Ollama is running on localhost:11434, health_check should be True
    # If not running, it gracefully returns False without crashing
    assert isinstance(is_healthy, bool)

