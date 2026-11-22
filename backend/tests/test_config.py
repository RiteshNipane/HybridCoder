"""
Tests for backend/config.py
"""
import os
import pytest
from backend.config import Config, RoutingMode, LogLevel, get_config


def test_config_defaults():
    cfg = Config()
    assert cfg.gemini_model == "gemini-3.5-flash-lite"
    assert cfg.nvidia_model  # configurable via NVIDIA_MODEL; default may change when NVIDIA retires models
    assert cfg.ollama_model == "qwen2.5-coder:7b-instruct-q4_K_M"
    assert cfg.routing_mode == RoutingMode.AUTO
    assert cfg.escalation_enabled is True
    assert cfg.port == 8000


def test_routing_mode_normalization():
    cfg = Config(routing_mode="gemini-local")
    assert cfg.routing_mode == RoutingMode.GEMINI_LOCAL


def test_cors_origins_parsing():
    cfg = Config(cors_origins="http://localhost:3000, http://localhost:8000")
    assert "http://localhost:3000" in cfg.cors_origins
    assert "http://localhost:8000" in cfg.cors_origins


def test_availability_flags():
    cfg_no_keys = Config(gemini_api_key=None, xai_api_key=None)
    assert not cfg_no_keys.gemini_available
    assert not cfg_no_keys.grok_available

    cfg_with_keys = Config(gemini_api_key="test_key", xai_api_key="test_xai")
    assert cfg_with_keys.gemini_available
    assert cfg_with_keys.grok_available

