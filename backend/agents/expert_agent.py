"""
backend/agents/expert_agent.py
Expert agent using Gemini and NVIDIA NIM as peer models to plan, decompose, answer, and review tasks.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncGenerator, Optional

from backend.config import Config, get_config
from backend.routing.escalation import EscalationEngine
from backend.routing.model_router import ModelRouter
from backend.schemas.task import (
    ConversationMessage,
    ImplementationTask,
    MessageRole,
    ModelUsage,
    ProviderName,
    TaskResult,
    TaskSpec,
    TaskStatus,
    TaskType,
)
from backend.storage.usage_tracker import UsageTracker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_SYSTEM_PLAN = """\
You are an expert software architect and senior developer. Your job is to:
1. Analyze the user's task carefully.
2. Decompose it into ordered, concrete implementation sub-tasks.
3. For each sub-task, specify: title, description, files_to_modify, files_to_create, acceptance_criteria.
4. Identify dependencies between sub-tasks.
   In `depends_on`, refer to earlier sub-task `order` values as strings (for example `["1"]`); task IDs are generated after the plan is parsed.
5. Output ONLY valid JSON — no markdown fences, no commentary.
6. The workspace may be EMPTY. For a new project, plan the real files to build it and list every one in files_to_create.
7. All paths are RELATIVE to the workspace root. Never use absolute paths, drive letters or /tmp. Never plan scratch or verification files.
8. Treat the current Task description as the complete authoritative request. Context and prior conversation are reference material only; do not repeat old tasks or assistant suggestions unless the current Task explicitly asks for them.
9. Inspect the listed relevant files before planning. If one already implements the requested concept, modify that file instead of creating a duplicate with the same basename in another directory.

JSON format:
{
  "sub_tasks": [
    {
      "order": 1,
      "title": "...",
      "description": "...",
      "files_to_modify": ["path/to/file.py"],
      "files_to_create": [],
      "acceptance_criteria": ["..."],
      "estimated_complexity": "low|medium|high",
      "depends_on": []
    }
  ]
}
"""

_SYSTEM_REVIEW = """\
You are a senior code reviewer. Analyze the provided code for:
- Correctness and logical errors
- Security vulnerabilities (injection, path traversal, secrets in code)
- Performance issues
- Code quality and maintainability
- Test coverage gaps

Provide a structured review with: overall assessment, issues (critical/major/minor), suggestions.
"""

_SYSTEM_GENERAL = """\
You are an expert AI coding assistant. You have deep knowledge of software engineering,
multiple programming languages, frameworks, and best practices. Be concise but thorough.
When writing code, always write complete, working implementations.
"""


class ExpertAgent:
    """
    Uses Gemini and NVIDIA NIM as peer cloud experts for high-level reasoning:
    - Task planning and decomposition
    - Code review
    - Architecture decisions
    - Explanation and documentation
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        usage_tracker: Optional[UsageTracker] = None,
    ) -> None:
        self._cfg = config or get_config()
        self._tracker = usage_tracker or UsageTracker(self._cfg)
        self._router = ModelRouter(self._cfg)
        self._escalation = EscalationEngine(self._cfg, self._router)

    @staticmethod
    def _expert_chain(preferred_provider: Optional[ProviderName] = None) -> list[ProviderName]:
        """Put the selected expert first; AUTO defaults to NVIDIA during Gemini capacity issues."""
        if preferred_provider in (ProviderName.NVIDIA, ProviderName.GROK):
            return [ProviderName.NVIDIA, ProviderName.GEMINI]
        if preferred_provider == ProviderName.GEMINI:
            return [ProviderName.GEMINI, ProviderName.NVIDIA]
        return [ProviderName.NVIDIA, ProviderName.GEMINI]

    # ------------------------------------------------------------------
    # Task planning
    # ------------------------------------------------------------------

    async def plan_task(
        self,
        task: TaskSpec,
        conversation_id: Optional[str] = None,
        preferred_provider: Optional[ProviderName] = None,
    ) -> tuple[list[ImplementationTask], Any]:
        """Decompose a high-level task into ordered implementation sub-tasks.

        Returns (sub_tasks, usage) where usage is the ModelUsage of the planning call
        (or None if planning failed and a single fallback task was returned).
        """
        context_block = f"\nContext:\n{task.context}\n" if task.context else ""
        context_paths = re.findall(r"--- File: (.*?) ---", task.context or "")
        relevant_paths = list(dict.fromkeys([*task.file_paths, *context_paths]))
        files_block = (
            "\nRelevant files:\n" + "\n".join(f"- {p}" for p in relevant_paths)
            if relevant_paths
            else ""
        )
        prompt = (
            f"Task: {task.description}{context_block}{files_block}\n\n"
            "Decompose this into implementation sub-tasks as described."
        )

        try:
            result = await self._escalation.run(
                prompt=prompt,
                system_prompt=_SYSTEM_PLAN,
                task=task,
                chain=[*self._expert_chain(preferred_provider), ProviderName.OLLAMA],
                temperature=0.1,
            )
            await self._tracker.record(
                result.usage,
                task_id=task.id,
                conversation_id=conversation_id,
            )

            # Parse JSON
            text = result.text
            text = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
            data = json.loads(text)
            sub_task_dicts = data.get("sub_tasks", [])

            planned_tasks: list[tuple[ImplementationTask, Any]] = []
            for d in sub_task_dicts:
                planned_tasks.append((
                    ImplementationTask(
                        parent_task_id=task.id,
                        order=d.get("order", len(planned_tasks) + 1),
                        title=d.get("title", ""),
                        description=d.get("description", ""),
                        files_to_modify=d.get("files_to_modify", []),
                        files_to_create=d.get("files_to_create", []),
                        acceptance_criteria=d.get("acceptance_criteria", []),
                        estimated_complexity=d.get("estimated_complexity", "medium"),
                    ),
                    d.get("depends_on", []),
                ))

            # Models commonly use numeric order values in dependencies despite
            # the JSON schema's string type. Resolve those order references to
            # the generated task IDs and stringify any other returned values.
            order_to_id = {str(subtask.order): subtask.id for subtask, _ in planned_tasks}
            impl_tasks = [subtask for subtask, _ in planned_tasks]
            for subtask, dependencies in planned_tasks:
                if dependencies is None:
                    dependencies = []
                elif not isinstance(dependencies, list):
                    dependencies = [dependencies]
                subtask.depends_on = [
                    order_to_id.get(str(dependency), str(dependency))
                    for dependency in dependencies
                    if dependency is not None
                ]
            return impl_tasks, result.usage

        except Exception as exc:
            logger.error("plan_task failed: %s", exc)
            # Return single fallback task
            return [
                ImplementationTask(
                    parent_task_id=task.id,
                    order=1,
                    title="Implement task",
                    description=task.description,
                    estimated_complexity="high",
                )
            ], None

    # ------------------------------------------------------------------
    # Code review
    # ------------------------------------------------------------------

    async def review_code(
        self,
        code: str,
        language: str = "python",
        task_description: Optional[str] = None,
        task_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        preferred_provider: Optional[ProviderName] = None,
    ) -> dict[str, Any]:
        """Review code and return structured feedback."""
        task_ctx = f"\nTask context: {task_description}" if task_description else ""
        prompt = (
            f"Review the following {language} code:{task_ctx}\n\n"
            f"```{language}\n{code}\n```\n\n"
            "Return JSON with keys: overall (pass|warning|fail), score (0-10), "
            "issues (list of {severity, message, line_hint}), suggestions (list of strings)."
        )

        try:
            result = await self._escalation.run(
                prompt=prompt,
                system_prompt=_SYSTEM_REVIEW,
                chain=self._expert_chain(preferred_provider),
                temperature=0.0,
            )
            await self._tracker.record(
                result.usage, task_id=task_id, conversation_id=conversation_id
            )
            text = re.sub(r"```(?:json)?\s*", "", result.text).strip().rstrip("`")
            return json.loads(text)
        except json.JSONDecodeError:
            return {"overall": "unknown", "score": 0, "issues": [], "suggestions": [], "raw": result.text}
        except Exception as exc:
            logger.error("review_code failed: %s", exc)
            return {"overall": "error", "score": 0, "issues": [], "suggestions": [], "error": str(exc)}

    # ------------------------------------------------------------------
    # General chat / explanation
    # ------------------------------------------------------------------

    async def chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        conversation_history: Optional[list[ConversationMessage]] = None,
        task: Optional[TaskSpec] = None,
        task_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        preferred_provider: Optional[ProviderName] = None,
    ) -> tuple[str, ModelUsage]:
        """Single-shot expert response."""
        full_prompt = self._build_conversation_prompt(prompt, conversation_history)
        result = await self._escalation.run(
            prompt=full_prompt,
            system_prompt=system_prompt or _SYSTEM_GENERAL,
            task=task,
            chain=self._expert_chain(preferred_provider),
        )
        await self._tracker.record(
            result.usage, task_id=task_id, conversation_id=conversation_id
        )
        return result.text, result.usage

    async def stream_chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        conversation_history: Optional[list[ConversationMessage]] = None,
        task_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        preferred_provider: Optional[ProviderName] = None,
    ) -> AsyncGenerator[str, None]:
        """Streaming expert response."""
        full_prompt = self._build_conversation_prompt(prompt, conversation_history)
        total_text = ""
        provider_used: Optional[ProviderName] = None

        async for chunk, prov, is_done in self._escalation.stream(
            prompt=full_prompt,
            system_prompt=system_prompt or _SYSTEM_GENERAL,
            chain=self._expert_chain(preferred_provider),
        ):
            if not is_done and chunk:
                total_text += chunk
                provider_used = prov
                yield chunk

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_conversation_prompt(
        self,
        prompt: str,
        history: Optional[list[ConversationMessage]],
    ) -> str:
        if not history:
            return prompt
        lines: list[str] = []
        for msg in history[-10:]:  # Last 10 messages for context
            role = "Human" if msg.role == MessageRole.USER else "Assistant"
            lines.append(f"{role}: {msg.content}")
        lines.append(f"Human: {prompt}")
        return "\n".join(lines)
