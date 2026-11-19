"""
backend/config.py
Loads and validates all environment variables. Provides a typed Config object.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load .env from project root (two levels up from this file)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=False)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RoutingMode(str, Enum):
    AUTO = "AUTO"
    GEMINI = "GEMINI"
    NVIDIA = "NVIDIA"
    GROK = "NVIDIA"  # Alias
    LOCAL = "LOCAL"
    GEMINI_LOCAL = "GEMINI+LOCAL"
    NVIDIA_LOCAL = "NVIDIA+LOCAL"
    GROK_LOCAL = "NVIDIA+LOCAL"  # Alias
    ALL = "ALL"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------

class Config(BaseModel):
    model_config = {"frozen": True}

    # --- Gemini ---
    gemini_api_key: Optional[str] = Field(default=None)
    gemini_model: str = Field(default="gemini-3.5-flash-lite")
    gemini_model_powerful: str = Field(default="gemini-3.8-flash")
    gemini_model_fast: str = Field(default="gemini-3.5-flash-lite")
    gemini_timeout: int = Field(default=180)
    gemini_max_retries: int = Field(default=1)

    # --- NVIDIA NIM ---
    nvidia_api_key: Optional[str] = Field(default=None)
    nvidia_model: str = Field(default="meta/llama-3.3-70b-instruct")
    nvidia_base_url: str = Field(default="https://integrate.api.nvidia.com/v1")
    nvidia_timeout: int = Field(default=120)
    nvidia_max_retries: int = Field(default=3)

    # --- Grok / xAI (backwards compatibility) ---
    xai_api_key: Optional[str] = Field(default=None)
    xai_model: str = Field(default="meta/llama-3.3-70b-instruct")
    xai_base_url: str = Field(default="https://integrate.api.nvidia.com/v1")
    xai_timeout: int = Field(default=120)
    xai_max_retries: int = Field(default=3)

    # --- Ollama ---
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_model: str = Field(default="qwen2.5-coder:7b-instruct-q4_K_M")
    ollama_timeout: int = Field(default=180)

    # --- Routing ---
    routing_mode: RoutingMode = Field(default=RoutingMode.AUTO)
    escalation_enabled: bool = Field(default=True)
    escalation_max_retries: int = Field(default=2)

    # --- Storage ---
    db_path: Path = Field(default=_PROJECT_ROOT / "data" / "usage.db")
    workspace_root: Path = Field(default=_PROJECT_ROOT)

    # --- Server ---
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)
    debug: bool = Field(default=False)
    cors_origins: list[str] = Field(default=["http://localhost:3000", "http://localhost:5173"])

    # --- Logging ---
    log_level: LogLevel = Field(default=LogLevel.INFO)

    # --- Token cost hints (USD per 1M tokens) ---
    gemini_input_cost_per_m: float = Field(default=0.075)
    gemini_output_cost_per_m: float = Field(default=0.30)
    nvidia_input_cost_per_m: float = Field(default=0.50)
    nvidia_output_cost_per_m: float = Field(default=1.50)
    grok_input_cost_per_m: float = Field(default=0.50)
    grok_output_cost_per_m: float = Field(default=1.50)

    # ---------------------------------------------------------------------------
    # Validators
    # ---------------------------------------------------------------------------

    @field_validator("routing_mode", mode="before")
    @classmethod
    def _parse_routing_mode(cls, v: object) -> str:
        if isinstance(v, str):
            return v.upper().replace("-", "+")
        return v

    @field_validator("db_path", "workspace_root", mode="before")
    @classmethod
    def _coerce_path(cls, v: object) -> Path:
        return Path(v) if not isinstance(v, Path) else v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v  # type: ignore[return-value]

    @model_validator(mode="after")
    def _warn_missing_keys(self) -> "Config":
        if not self.gemini_api_key:
            logger.warning("GEMINI_API_KEY not set — Gemini provider disabled")
        if not (self.nvidia_api_key or self.xai_api_key):
            logger.warning("NVIDIA_API_KEY not set — NVIDIA provider disabled")
        # Ensure DB directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        return self

    # ---------------------------------------------------------------------------
    # Convenience helpers
    # ---------------------------------------------------------------------------

    @property
    def gemini_available(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def nvidia_available(self) -> bool:
        return bool(self.nvidia_api_key or self.xai_api_key)

    @property
    def grok_available(self) -> bool:
        return self.nvidia_available

    def configure_logging(self) -> None:
        logging.basicConfig(
            level=self.log_level.value,
            format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )


# ---------------------------------------------------------------------------
# Factory — cached singleton
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_config() -> Config:
    """Return the application-wide Config singleton."""
    cfg = Config(
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"),
        gemini_model_powerful=os.getenv("GEMINI_MODEL_POWERFUL", "gemini-3.8-flash"),
        gemini_model_fast=os.getenv("GEMINI_MODEL_FAST", "gemini-3.5-flash-lite"),
        gemini_timeout=int(os.getenv("GEMINI_TIMEOUT", "180")),
        gemini_max_retries=int(os.getenv("GEMINI_MAX_RETRIES", "1")),

        nvidia_api_key=os.getenv("NVIDIA_API_KEY") or os.getenv("XAI_API_KEY"),
        nvidia_model=os.getenv("NVIDIA_MODEL") or os.getenv("XAI_MODEL", "meta/llama-3.3-70b-instruct"),
        nvidia_base_url=os.getenv("NVIDIA_BASE_URL") or os.getenv("XAI_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        nvidia_timeout=int(os.getenv("NVIDIA_TIMEOUT", os.getenv("XAI_TIMEOUT", "120"))),
        nvidia_max_retries=int(os.getenv("NVIDIA_MAX_RETRIES", os.getenv("XAI_MAX_RETRIES", "3"))),

        xai_api_key=os.getenv("NVIDIA_API_KEY") or os.getenv("XAI_API_KEY"),
        xai_model=os.getenv("NVIDIA_MODEL") or os.getenv("XAI_MODEL", "meta/llama-3.3-70b-instruct"),
        xai_base_url=os.getenv("NVIDIA_BASE_URL") or os.getenv("XAI_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        xai_timeout=int(os.getenv("NVIDIA_TIMEOUT", os.getenv("XAI_TIMEOUT", "120"))),
        xai_max_retries=int(os.getenv("NVIDIA_MAX_RETRIES", os.getenv("XAI_MAX_RETRIES", "3"))),

        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b-instruct-q4_K_M"),
        ollama_timeout=int(os.getenv("OLLAMA_TIMEOUT", "180")),

        routing_mode=os.getenv("ROUTING_MODE", "AUTO"),
        escalation_enabled=os.getenv("ESCALATION_ENABLED", "true").lower() == "true",
        escalation_max_retries=int(os.getenv("ESCALATION_MAX_RETRIES", "2")),

        db_path=os.getenv("DB_PATH", str(_PROJECT_ROOT / "data" / "usage.db")),
        workspace_root=os.getenv("WORKSPACE_ROOT", str(_PROJECT_ROOT)),

        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        debug=os.getenv("DEBUG", "false").lower() == "true",
        cors_origins=os.getenv("CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"),

        log_level=os.getenv("LOG_LEVEL", "INFO"),

        gemini_input_cost_per_m=float(os.getenv("GEMINI_INPUT_COST_PER_M", "0.075")),
        gemini_output_cost_per_m=float(os.getenv("GEMINI_OUTPUT_COST_PER_M", "0.30")),
        nvidia_input_cost_per_m=float(os.getenv("NVIDIA_INPUT_COST_PER_M", "0.50")),
        nvidia_output_cost_per_m=float(os.getenv("NVIDIA_OUTPUT_COST_PER_M", "1.50")),
        grok_input_cost_per_m=float(os.getenv("GROK_INPUT_COST_PER_M", "0.50")),
        grok_output_cost_per_m=float(os.getenv("GROK_OUTPUT_COST_PER_M", "1.50")),
    )
    cfg.configure_logging()
    return cfg
