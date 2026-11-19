"""Shared description of this application's model team for all model prompts."""

from __future__ import annotations

from typing import Optional

from backend.config import Config
from backend.schemas.task import ProviderName


def model_name(config: Config, provider: ProviderName) -> str:
    if provider == ProviderName.GEMINI:
        return config.gemini_model
    if provider in (ProviderName.NVIDIA, ProviderName.GROK):
        return config.nvidia_model
    if provider == ProviderName.OLLAMA:
        return config.ollama_model
    return provider.value


def team_context(config: Config, current_provider: Optional[ProviderName] = None) -> str:
    """Describe actual runtime model IDs, responsibilities, and handoffs."""
    current = ""
    if current_provider:
        current = (
            f"\nYou are currently answering as {current_provider.value} "
            f"({model_name(config, current_provider)}). If asked your identity, use this exact provider/model.\n"
        )
    return f"""HYBRIDCODER MODEL TEAM
{current}
The orchestrator routes each request and passes explicit context between separate model calls. Models do not share hidden state or private reasoning with each other. Gemini and NVIDIA NIM are peer cloud experts with the same general capabilities. AUTO currently prefers NVIDIA NIM because Gemini is returning temporary high-demand 503 errors; Gemini remains the fallback and is still used when explicitly selected.

- Gemini ({config.gemini_model}): cloud expert for direct answers, ambiguous intent tie-breaks, architecture and task planning, code review, and cloud-led workflows.
- NVIDIA NIM ({config.nvidia_model}): peer cloud expert with the same capabilities: direct answers, ambiguous intent tie-breaks, architecture and task planning, code review, implementation reasoning, and cloud-led workflows. It can lead when selected and can provide an independent second opinion or fallback for Gemini.
- Local Ollama ({config.ollama_model}, Qwen coder): local implementation worker. It writes code and file changes from the task, plan, workspace context, and tool results passed to it; it can also run as the offline/local route.
- Python orchestrator and rule-based intent classifier: select the workflow, retrieve relevant workspace context, and pass conversation history, plans, review notes, or tool outputs to the next model when relevant. The classifier is not a separate LLM.

In NVIDIA+LOCAL mode, NVIDIA leads planning and local Qwen implements the task. In GEMINI+LOCAL mode, Gemini leads planning and Qwen implements it. AUTO and ALL currently prefer NVIDIA for cloud planning and direct answers; Gemini is tried if NVIDIA is unavailable or fails. Explicit Gemini mode still puts Gemini first. In AUTO, the orchestrator selects a lightweight answer or an engineering workflow based on intent. Do not claim you are unaware of these configured teammates. Describe only the work actually handed to you; do not claim another model ran unless the orchestrator's supplied context says it did."""


def add_team_context(system_prompt: Optional[str], config: Config, provider: ProviderName) -> str:
    """Append the team briefing once to a provider's system instructions."""
    prompt = system_prompt or ""
    if "HYBRIDCODER MODEL TEAM" in prompt:
        return prompt
    return f"{prompt}\n\n{team_context(config, provider)}".strip()
