"""
Tests for model routing and escalation logic.
"""
import pytest
from backend.config import Config, RoutingMode
from backend.routing.model_router import ModelRouter
from backend.routing.escalation import EscalationEngine
from backend.schemas.task import ProviderName


def test_routing_decisions():
    cfg = Config(gemini_api_key="mock", xai_api_key="mock")
    router = ModelRouter(cfg)

    # AUTO mode
    auto_res = router.decide(override_mode=RoutingMode.AUTO)
    assert auto_res.mode == RoutingMode.AUTO
    assert ProviderName.GEMINI in auto_res.primary or ProviderName.OLLAMA in auto_res.primary

    # LOCAL mode
    local_res = router.decide(override_mode=RoutingMode.LOCAL)
    assert local_res.primary == [ProviderName.OLLAMA]

    # GEMINI mode
    gem_res = router.decide(override_mode=RoutingMode.GEMINI)
    assert gem_res.primary == [ProviderName.GEMINI]

    # GROK mode
    grok_res = router.decide(override_mode=RoutingMode.GROK)
    assert grok_res.primary == [ProviderName.NVIDIA]  # GROK is a legacy alias of NVIDIA


def test_escalation_chain_availability():
    # Only Ollama available (no cloud keys)
    cfg_local_only = Config(gemini_api_key=None, xai_api_key=None)
    router_local = ModelRouter(cfg_local_only)
    escalation = EscalationEngine(cfg_local_only, router_local)

    chain = escalation._build_chain()
    assert chain == [ProviderName.OLLAMA]

    # All available
    cfg_all = Config(gemini_api_key="mock", xai_api_key="mock")
    router_all = ModelRouter(cfg_all)
    escalation_all = EscalationEngine(cfg_all, router_all)

    chain_all = escalation_all._build_chain()
    assert chain_all == [ProviderName.OLLAMA, ProviderName.GEMINI, ProviderName.NVIDIA]

