"""
backend/orchestrator.py
Main orchestrator coordinating models, tools, escalation, and the autonomous coding workflow.
Implements the 3-model hybrid architecture (Gemini + Grok + Local Qwen).
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from backend.agents.expert_agent import ExpertAgent
from backend.agents.local_coder import LocalCoderAgent
from backend.agents.reviewer import CodeReviewerAgent
from backend.agents.self_correction import SelfCorrectionLoop
from backend.config import Config, RoutingMode, get_config
from backend.providers.gemini_provider import GeminiProvider
from backend.routing.escalation import EscalationEngine
from backend.routing.intent import (
    Intent,
    IntentResult,
    classify_with_fallback,
    is_verification_only_request,
    project_terms,
    strip_override,
)
from backend.routing.model_router import ModelRouter
from backend.routing.task_complexity import classify_task_complexity
from backend.scanner.context_retriever import retrieve_relevant_context
from backend.scanner.repository_scanner import RepositoryContext, scan_repository
from backend.schemas.task import (
    ConversationMessage,
    FileEdit,
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
from backend.storage.run_telemetry import append_task_run
from backend.task_manager import TaskManager
from backend.tools.filesystem import create_directory, create_file, delete_file, edit_file, list_files, read_file, write_file
from backend.tools.git_manager import create_checkpoint, git_diff, git_status
from backend.tools.verification import run_scoped_checks
from backend.tools.testing import extract_test_paths, run_project_tests
from backend.tools.tool_executor import (
    ToolExecutor,
    has_tool_calls,
    parse_tool_calls,
    run_agentic_loop,
    strip_tool_calls,
)

logger = logging.getLogger(__name__)

_CONTEXT_REFERENCES = re.compile(
    r"^(?:also|and then|then|do that|continue|go ahead|apply that|what about|same for)\b"
    r"|^(?:please\s+|can you\s+|could you\s+|now\s+)?"
    r"(?:fix|change|update|modify|implement|add|remove|delete|make|explain|describe)\s+"
    r"(?:it|that|those|them|the same)\b"
    r"|\b(?:the previous one|the earlier suggestion|as above|your suggestion)\b",
    re.I,
)


class CodingOrchestrator:
    """
    Central orchestrator for the autonomous coding agent.
    Coordinates reasoning and planning with peer Gemini/NVIDIA experts and local Qwen implementation.
    Implements a full Claude Code-style autonomous agent with direct tool execution.
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        task_manager: Optional[TaskManager] = None,
        usage_tracker: Optional[UsageTracker] = None,
    ) -> None:
        self._cfg = config or get_config()
        self._task_manager = task_manager or TaskManager()
        self._tracker = usage_tracker or UsageTracker(self._cfg)
        self._router = ModelRouter(self._cfg)
        self._escalation = EscalationEngine(self._cfg, self._router)

        # Agents
        self._expert = ExpertAgent(self._cfg, self._tracker)
        self._local_coder = LocalCoderAgent(self._cfg, self._tracker)
        self._reviewer = CodeReviewerAgent(self._cfg, self._tracker, self._router)

        # Workspace state: default to config workspace_root or cwd
        default_ws = (
            self._cfg.workspace_root
            if self._cfg.workspace_root and self._cfg.workspace_root.exists()
            else Path.cwd()
        ).resolve()
        self._workspace_root: Path = default_ws
        self._repo_context: Optional[RepositoryContext] = None
        self.skipped_edits: List[str] = []

    @property
    def workspace_root(self) -> Path:
        return self._workspace_root

    @property
    def repo_context(self) -> Optional[RepositoryContext]:
        return self._repo_context

    # ------------------------------------------------------------------
    # Workspace management
    # ------------------------------------------------------------------

    async def set_workspace(self, path: str | Path) -> RepositoryContext:
        """Set active workspace directory and scan it."""
        p = Path(path).resolve()
        if not p.exists() or not p.is_dir():
            raise ValueError(f"Workspace path does not exist or is not a directory: {p}")
        self._workspace_root = p
        self._repo_context = await scan_repository(p)
        logger.info("Workspace set to %s (%d files)", p, len(self._repo_context.files))
        return self._repo_context

    async def scan_workspace(self) -> Optional[RepositoryContext]:
        """Rescan active workspace."""
        if self._workspace_root:
            self._repo_context = await scan_repository(self._workspace_root)
        return self._repo_context

    # ------------------------------------------------------------------
    # Applying File Changes
    # ------------------------------------------------------------------

    async def apply_file_edits(self, edits: List[FileEdit]) -> List[str]:
        """Validate all proposed edits, then apply them as one rollback-safe transaction."""
        root = (self._workspace_root or self._cfg.workspace_root or Path.cwd()).resolve()
        self.skipped_edits = []
        projected: Dict[Path, Optional[str]] = {}
        originals: Dict[Path, Optional[str]] = {}
        relative_paths: Dict[Path, str] = {}
        for edit in edits:
            raw = (edit.path or "").strip().strip("\"'`")
            try:
                candidate = Path(raw)
                full_path = (candidate if candidate.is_absolute() else root / candidate).resolve()
                rel = full_path.relative_to(root)
            except Exception:
                logger.warning("Skipping edit outside workspace: %s", raw)
                self.skipped_edits.append(raw)
                continue
            rel_path = str(rel).replace("\\", "/")
            if full_path.exists() and not full_path.is_file():
                raise ValueError(f"Patch target is not a regular file: {rel_path}")
            if full_path not in projected:
                existing = await read_file(full_path, root) if full_path.is_file() else None
                originals[full_path] = existing
                projected[full_path] = existing
                relative_paths[full_path] = rel_path

            current = projected[full_path]
            if edit.original_content is not None:
                if current is None:
                    raise ValueError(f"Patch target does not exist: {rel_path}")
                search = edit.original_content
                matches = current.count(search) if search else 0
                if matches != 1:
                    raise ValueError(
                        f"Patch for {rel_path} must match one exact section; found {matches} matches. "
                        "Read the current file and provide a smaller, unique search block."
                    )
                projected[full_path] = current.replace(search, edit.new_content, 1)
            else:
                if current is not None:
                    raise ValueError(
                        f"Refusing to replace existing file {rel_path} with a full-file response; "
                        "use a structured search/replace patch."
                    )
                projected[full_path] = edit.new_content

        # Validate every final file before any write takes place.
        from backend.tools.verification import validate_source_text_async
        for full_path, new_content in projected.items():
            if new_content is not None:
                await validate_source_text_async(full_path, new_content, root)

        written: List[Path] = []
        try:
            for full_path, new_content in projected.items():
                if new_content is None:
                    continue
                written.append(full_path)
                await write_file(full_path, new_content, root)
                logger.info("Applied validated patch to: %s", relative_paths[full_path])
        except Exception:
            for full_path in reversed(written):
                original = originals[full_path]
                if original is None:
                    full_path.unlink(missing_ok=True)
                    parent = full_path.parent
                    while parent != root and parent.is_dir():
                        try:
                            parent.rmdir()
                        except OSError:
                            break
                        parent = parent.parent
                else:
                    await write_file(full_path, original, root)
            raise

        return [relative_paths[path] for path in written]

    def _skipped_status(self) -> Optional[Dict[str, Any]]:
        """Status event listing edits that were skipped because they pointed outside the workspace."""
        skipped = getattr(self, "skipped_edits", [])
        if not skipped:
            return None
        shown = ", ".join(skipped[:3]) + (" ..." if len(skipped) > 3 else "")
        return {"type": "status", "message": f"Skipped {len(skipped)} file(s) outside the workspace: {shown}"}

    # ------------------------------------------------------------------
    # Main Streaming Execution Entrypoint
    # ------------------------------------------------------------------

    async def handle_user_message_stream(
        self,
        prompt: str,
        conversation_history: Optional[List[ConversationMessage]] = None,
        mode_override: Optional[RoutingMode] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Process a user message and yield streaming status updates and content chunks.
        Yields dicts with: {"type": "status"|"text"|"edit"|"test"|"done"|"error", ...}
        """
        mode = mode_override or self._cfg.routing_mode
        history = conversation_history or []

        # 0. Classify AUTO and ALL requests before selecting a workflow. ALL means
        # use the multi-model workflow for engineering tasks, not for ordinary Q&A.
        #    Optional prefixes:  /ask  /explain  /code  /plan  force a path.
        prompt, forced_intent = strip_override(prompt)
        if not forced_intent and is_verification_only_request(prompt):
            async for event in self._run_verification_request(prompt):
                yield event
            return

        task_history = self._task_history(prompt, history)
        prior_context = self._history_text(task_history, limit=8)
        contextual_request = (
            "Prior conversation (reference only; use it only to resolve the current request):\n"
            f"{prior_context}\n\nCurrent request: {prompt}"
            if prior_context else prompt
        )
        intent_result: Optional[IntentResult] = None
        if mode in (RoutingMode.AUTO, RoutingMode.ALL):
            if forced_intent:
                intent_result = IntentResult(forced_intent, True, "your command")
            else:
                terms = project_terms(self._repo_context.source_files) if self._repo_context else set()
                intent_result = await classify_with_fallback(
                    contextual_request,
                    has_workspace=bool(self._workspace_root),
                    known_terms=terms,
                    llm_call=self._classify_with_llm,
                )
            logger.info("Intent: %s (%s, confident=%s)", intent_result.intent.value,
                        intent_result.reason, intent_result.confident)

        needs_repo_context = (
            mode not in (RoutingMode.AUTO, RoutingMode.ALL)
            or (intent_result is not None and intent_result.intent in (Intent.CODE_QUESTION, Intent.TASK))
        )

        # 1. Workspace context retrieval
        workspace_context_str = ""
        if self._workspace_root and self._repo_context and needs_repo_context:
            yield {"type": "status", "message": "Analyzing repository context..."}
            retrieval = await retrieve_relevant_context(
                repo_ctx=self._repo_context,
                task_description=contextual_request,
                max_tokens=3000,
            )
            if isinstance(retrieval, dict):
                files = retrieval.get("files", [])
                workspace_context_str = "\n\n".join(
                    f"--- File: {f['path']} ---\n{f.get('content', '')}" for f in files
                )
            else:
                workspace_context_str = str(retrieval)

        # 2. Execution path based on routing mode
        if mode == RoutingMode.AUTO:
            async for chunk in self._run_auto_workflow(
                prompt, workspace_context_str, history, intent_result  # type: ignore[arg-type]
            ):
                yield chunk

        elif mode == RoutingMode.LOCAL:
            async for chunk in self._run_claude_code_agent(
                prompt=prompt,
                workspace_context=workspace_context_str,
                history=task_history,
                provider=ProviderName.OLLAMA,
            ):
                yield chunk

        elif mode in (RoutingMode.GEMINI, RoutingMode.NVIDIA, RoutingMode.GROK):
            provider_name = ProviderName.GEMINI if mode == RoutingMode.GEMINI else ProviderName.NVIDIA
            yield {"type": "status", "message": f"Calling expert model ({provider_name.value.title()})..."}

            # Build full prompt
            convo = self._history_text(history, limit=20)
            full_prompt = f"Conversation so far:\n{convo}\n\nUser: {prompt}" if convo else prompt
            if workspace_context_str:
                full_prompt = f"Workspace Context:\n{workspace_context_str}\n\n{full_prompt}"

            try:
                res = await self._escalation.run(
                    prompt=full_prompt,
                    chain=([ProviderName.GEMINI, ProviderName.NVIDIA] if provider_name == ProviderName.GEMINI else [provider_name, ProviderName.GEMINI]),
                )
                yield {"type": "text", "content": res.text, "model": res.usage.model}
                yield {"type": "done", "provider": res.provider.value, "model": res.usage.model, "usage": res.usage.model_dump(mode="json")}
            except Exception as e:
                yield {"type": "error", "error": str(e)}

        elif mode in (RoutingMode.GEMINI_LOCAL, RoutingMode.NVIDIA_LOCAL, RoutingMode.GROK_LOCAL):
            expert_name = ProviderName.GEMINI if mode == RoutingMode.GEMINI_LOCAL else ProviderName.NVIDIA
            async for chunk in self._run_expert_local_workflow(
                expert_provider=expert_name,
                prompt=prompt,
                workspace_context=workspace_context_str,
                history=task_history,
            ):
                yield chunk

        elif mode == RoutingMode.ALL:
            if intent_result and intent_result.intent in (Intent.CHAT, Intent.CODE_QUESTION):
                async for chunk in self._answer_directly(
                    prompt,
                    workspace_context_str if intent_result.intent == Intent.CODE_QUESTION else "",
                    history,
                    "Answering your question...",
                ):
                    yield chunk
                return

            if intent_result and intent_result.intent == Intent.TASK and re.search(
                r"\b(create|make|add)\b.*\b(folder|directory)\b", prompt, re.IGNORECASE
            ):
                # Folder creation is a direct filesystem operation, not a task
                # that benefits from a plan/critique/implementation round trip.
                provider = (
                    ProviderName.NVIDIA if self._cfg.nvidia_available
                    else ProviderName.GEMINI if self._cfg.gemini_available
                    else ProviderName.OLLAMA
                )
                async for chunk in self._run_claude_code_agent(
                    prompt=prompt,
                    workspace_context=workspace_context_str,
                    history=task_history,
                    provider=provider,
                ):
                    yield chunk
                return

            async for chunk in self._run_all_models_workflow(
                prompt=prompt,
                workspace_context=workspace_context_str,
                history=task_history,
            ):
                yield chunk

    async def _run_verification_request(self, prompt: str) -> AsyncGenerator[Dict[str, Any], None]:
        """Run a read-only test request directly, without planning or patch workflow."""
        root = self._workspace_root
        try:
            test_paths = extract_test_paths(prompt, root)
        except ValueError as exc:
            yield {"type": "error", "error": str(exc)}
            return

        target = ", ".join(test_paths) if test_paths else "the project test suite"
        yield {"type": "status", "message": f"Running tests in {root}..."}
        yield {
            "type": "text",
            "content": f"Running read-only verification in `{root}` for `{target}`.\n",
        }
        result = await run_project_tests(root, test_paths or None)
        result.setdefault("check_passed", result.get("returncode", -1) == 0 and not result.get("timed_out", False))
        result["skipped"] = bool(result.get("skipped") or result.get("runner_used") == "unknown" or result.get("runner_error"))
        result.setdefault("counts_known", False)
        result["workspace"] = str(root)
        yield {"type": "test", "results": result}
        yield {"type": "done", "provider": "system", "model": "verification"}

    # ------------------------------------------------------------------
    # Autonomous Workflow (AUTO Mode)
    # ------------------------------------------------------------------

    async def _classify_with_llm(self, question: str) -> str:
        """Prefer NVIDIA for a short intent tie-break; use Gemini if NIM is unavailable."""
        if self._cfg.nvidia_available:
            try:
                result = await self._escalation.run(
                    prompt=question,
                    system_prompt="Classify this message using exactly one requested label. Return only the label.",
                    hint_chain=[ProviderName.NVIDIA],
                    max_tokens=32,
                    temperature=0.0,
                )
                return result.text
            except Exception as exc:
                if not self._cfg.gemini_available:
                    raise
                logger.info("NVIDIA intent tie-break failed; trying Gemini: %s", exc)

        if self._cfg.gemini_available:
            fast_config = self._cfg.model_copy(update={
                "gemini_model": self._cfg.gemini_model_fast,
                "gemini_timeout": 5,
                "gemini_max_retries": 1,
            })
            provider = GeminiProvider(fast_config)
            try:
                text, _usage = await provider.complete(
                    prompt=question,
                    max_tokens=32,
                    temperature=0.0,
                )
                return text
            except Exception as exc:
                raise RuntimeError(f"Gemini intent tie-break failed: {exc}") from exc
            finally:
                await provider.aclose()

        raise RuntimeError("No cloud model is available for an intent tie-break")

    @staticmethod
    def _history_text(history: List[ConversationMessage], limit: int = 20) -> str:
        """Last few turns as plain text so direct answers keep conversational context."""
        lines = []
        for m in history[-limit:]:
            role = getattr(m.role, "value", str(m.role))
            if role in ("user", "assistant") and m.content:
                lines.append(f"{role.title()}: {m.content[:4000]}")
        return "\n".join(lines)

    @staticmethod
    def _task_history(prompt: str, history: List[ConversationMessage]) -> List[ConversationMessage]:
        """Use prior turns for code tasks only when the current request refers to them."""
        if not history or not _CONTEXT_REFERENCES.search(prompt):
            return []
        return history[-6:]

    @staticmethod
    def _fallback_note(res: Any) -> Optional[str]:
        """If cloud providers failed before this answer, say so (shows why APIs 'fail' in the UI)."""
        failed = [a for a in getattr(res, "attempts", []) if not a.success]
        if not failed:
            return None
        seen: Dict[str, str] = {}
        for a in failed:
            seen.setdefault(a.provider.value, (a.error or "error")[:140])
        details = "; ".join(f"{k}: {v}" for k, v in seen.items())
        return f"Answered by {res.provider.value} after failures -> {details}"

    async def _answer_directly(
        self,
        prompt: str,
        workspace_context: str,
        history: List[ConversationMessage],
        label: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Plain Q&A: no plan, no subtasks, no file edits."""
        yield {"type": "status", "message": label}
        convo = self._history_text(history)
        parts = []
        if convo:
            parts.append(f"Conversation so far:\n{convo}")
        if workspace_context:
            parts.append(f"Project context (for reference):\n{workspace_context}")
        parts.append(f"User: {prompt}")
        try:
            res = await self._escalation.run(
                prompt="\n\n".join(parts),
                system_prompt=(
                    "You are a knowledgeable, concise assistant and senior software engineer. "
                    "Answer the user's message directly. Do NOT create implementation plans, "
                    "subtasks or file changes unless the user explicitly asks for them. "
                    "Use markdown, and code blocks only when code is actually useful."
                ),
                chain=[ProviderName.NVIDIA, ProviderName.GEMINI, ProviderName.OLLAMA],
            )
            note = self._fallback_note(res)
            if note:
                logger.warning(note)
                yield {"type": "status", "message": note}
            yield {"type": "text", "content": res.text, "model": res.usage.model}
            yield {"type": "done", "provider": res.provider.value, "model": res.usage.model,
                   "usage": res.usage.model_dump(mode="json")}
        except Exception as e:
            yield {"type": "error", "error": f"Failed to get an answer: {e}"}

    async def _run_claude_code_agent(
        self,
        prompt: str,
        workspace_context: str = "",
        history: Optional[List[ConversationMessage]] = None,
        provider: Optional[ProviderName] = None,
        max_steps: int = 15,
        route_decision: Optional[Dict[str, str]] = None,
        _repair_depth: int = 0,
        _run_state: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Run a tool-using agent, verify its edits, and repair failed checks within a fixed budget.
        """
        root_call = _run_state is None
        state = _run_state or {
            "task_id": str(uuid.uuid4()),
            "started": time.perf_counter(),
            "route": (route_decision or {}).get("route", "local" if provider == ProviderName.OLLAMA else "cloud"),
            "complexity": (route_decision or {}).get("complexity", "unspecified"),
            "route_reason": (route_decision or {}).get("reason", "explicit provider selection"),
            "retry_count": 0,
            "escalation_count": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "changed_files": [],
            "snapshots": {},
            "test_before": None,
            "test_after": None,
            "cloud_repair_attempted": False,
            "agent_errors": [],
            "success": False,
        }
        history_list = history or []
        provider_name = provider or (
            ProviderName.OLLAMA
        )
        yield {
            "type": "status",
            "message": f"Mini Claude Code: Executing autonomously with {provider_name.value.title()}...",
        }

        # Format conversation history
        formatted_history: List[Dict[str, str]] = []
        for m in history_list[-20:]:
            role_str = getattr(m.role, "value", str(m.role))
            if role_str in ("user", "assistant") and m.content:
                formatted_history.append({"role": role_str, "content": m.content[:4000]})

        # A local-first pass stays local. Cloud passes still have a local fallback
        # in the escalation chain, so provider outages do not stall the task.
        async def _llm_complete(messages: List[Dict[str, str]], system_prompt: str) -> str:
            if provider_name == ProviderName.OLLAMA:
                text, usage = await self._local_coder.provider.complete_chat(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=0.1,
                )
                await self._tracker.record(usage)
                state["prompt_tokens"] += int(usage.prompt_tokens or 0)
                state["completion_tokens"] += int(usage.completion_tokens or 0)
                state["total_tokens"] += int(usage.total_tokens or 0)
                return text

            lines = []
            for msg in messages:
                role = msg.get("role", "user").upper()
                content = msg.get("content", "")
                lines.append(f"[{role}]\n{content}")
            full_prompt = "\n\n".join(lines)

            chain = (
                [ProviderName.GEMINI, ProviderName.NVIDIA, ProviderName.OLLAMA]
                if provider_name == ProviderName.GEMINI
                else [ProviderName.NVIDIA, ProviderName.GEMINI, ProviderName.OLLAMA]
            )
            res = await self._escalation.run(
                prompt=full_prompt,
                system_prompt=system_prompt,
                chain=chain,
                temperature=0.1,
            )
            state["escalation_count"] += sum(1 for attempt in res.attempts if not attempt.success)
            state["prompt_tokens"] += int(res.usage.prompt_tokens or 0)
            state["completion_tokens"] += int(res.usage.completion_tokens or 0)
            state["total_tokens"] += int(res.usage.total_tokens or 0)
            state["last_provider"] = res.provider.value
            return res.text

        if root_call:
            baseline_paths = re.findall(r"--- File: (.*?) ---", workspace_context)
            if baseline_paths:
                yield {"type": "status", "message": "Recording scoped test baseline..."}
                state["test_before"] = await run_scoped_checks(self._workspace_root, baseline_paths)

        # Run the agentic tool-use loop
        async for ev in run_agentic_loop(
            prompt=prompt,
            conversation_history=formatted_history,
            workspace_root=self._workspace_root,
            llm_complete=_llm_complete,
            max_steps=max_steps,
        ):
            ev_type = ev.get("type")
            if ev_type == "text":
                content = ev.get("content", "")
                state.setdefault("assistant_output", []).append(content)
                yield {"type": "text", "content": content, "model": provider_name.value}

            elif ev_type == "tool_call":
                t_name = ev.get("name", "")
                t_args = ev.get("args", {})
                arg_summary = t_args.get("path") or t_args.get("command") or ""
                yield {
                    "type": "status",
                    "message": f"Tool: {t_name}({arg_summary})",
                }
                yield ev

            elif ev_type == "tool_result":
                t_name = ev.get("name", "")
                out = ev.get("output", "")
                is_err = ev.get("error", False)
                change = ev.pop("change", None)
                yield ev
                if is_err:
                    state["agent_errors"].append(out[-2000:])

                if t_name in ("write_file", "create_file", "create_directory", "edit_file", "delete_file") and not is_err:
                    target_path = None
                    if change:
                        target_path = change.get("path")
                        rel = str(target_path).replace("\\", "/")
                        snapshot = state["snapshots"].setdefault(rel, {"before": change.get("before"), "after": None})
                        snapshot["after"] = change.get("after")
                        if rel not in state["changed_files"]:
                            state["changed_files"].append(rel)
                    else:
                        for line in out.splitlines():
                            if "Written:" in line or "Created:" in line or "Edited:" in line:
                                parts = line.split(":", 1)
                                if len(parts) > 1:
                                    target_path = parts[1].split("(")[0].strip()
                    if target_path:
                        rel = target_path
                        try:
                            rel = str(Path(target_path).relative_to(self._workspace_root)).replace("\\", "/")
                        except Exception:
                            rel = str(target_path).replace("\\", "/")
                        if t_name not in ("create_directory",) and rel not in state["changed_files"]:
                            state["changed_files"].append(rel)
                        yield {
                            "type": "edit",
                            "files": [rel],
                            "action": "delete" if t_name == "delete_file" else "create" if t_name in ("create_file", "create_directory") else "edit",
                        }

            elif ev_type == "error":
                state["agent_errors"].append(str(ev.get("error", ""))[-2000:])
                yield ev

        # Compatibility fallback for models that returned structured patch blocks
        # in prose instead of using the edit_file tool.
        joined_output = "".join(state.get("assistant_output", []))
        additional_edits = self._local_coder.extract_file_edits(joined_output)
        if additional_edits and self._workspace_root:
            try:
                applied = await self.apply_file_edits(additional_edits)
                if applied:
                    for rel in applied:
                        if rel not in state["changed_files"]:
                            state["changed_files"].append(rel)
                        if rel not in state["snapshots"]:
                            state["snapshots"][rel] = {"before": None, "after": None}
                        path = (self._workspace_root / rel).resolve()
                        state["snapshots"][rel]["after"] = await read_file(path, self._workspace_root)
                    yield {"type": "edit", "files": applied}
            except Exception as e:
                logger.warning("Failed applying fallback file edits: %s", e)
                state["agent_errors"].append(str(e)[-2000:])

        changed_files = state["changed_files"]
        test_results: Optional[dict[str, Any]] = None
        if changed_files:
            yield {"type": "status", "message": "Running scoped tests or compiler checks..."}
            test_results = await run_scoped_checks(self._workspace_root, changed_files)
            state["test_after"] = test_results
            state["success"] = bool(test_results.get("check_passed"))
            yield {"type": "test", "results": test_results}
        elif state["agent_errors"]:
            state["success"] = False
        else:
            state["success"] = True

        async def persist_telemetry() -> None:
            if not root_call or state.get("telemetry_written"):
                return
            state["telemetry_written"] = True
            await append_task_run({
                "task_id": state["task_id"],
                "route": state["route"],
                "complexity": state["complexity"],
                "route_reason": state["route_reason"],
                "provider": state.get("last_provider", provider_name.value),
                "escalation_count": state["escalation_count"],
                "local_retry_count": state["retry_count"],
                "duration_ms": round((time.perf_counter() - state["started"]) * 1000),
                "prompt_tokens": state["prompt_tokens"],
                "completion_tokens": state["completion_tokens"],
                "total_tokens": state["total_tokens"],
                "test_before": self._telemetry_check(state.get("test_before")),
                "test_after": self._telemetry_check(state.get("test_after")),
                "changed_files": changed_files,
                "success": state["success"],
            })

        def failure_prompt(result: Optional[dict[str, Any]], compact: bool = False) -> str:
            diff_parts: list[str] = []
            snippets: list[str] = []
            for rel, snapshot in state["snapshots"].items():
                before = (snapshot.get("before") or "").splitlines(keepends=True)
                after = (snapshot.get("after") or "").splitlines(keepends=True)
                diff_parts.extend(difflib.unified_diff(before, after, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
                path = (self._workspace_root / rel).resolve()
                try:
                    path.relative_to(self._workspace_root.resolve())
                    if path.is_file():
                        snippets.append(f"--- Current file {rel} ---\n{path.read_text(encoding='utf-8', errors='replace')[:1800 if compact else 5000]}")
                except (ValueError, OSError):
                    continue
            output_limit = 4000 if compact else 12000
            diff_limit = 2500 if compact else 10000
            snippet_limit = 2500 if compact else 8000
            exact_output = str((result or {}).get("output", ""))[-output_limit:]
            recent_context = self._history_text(history_list, limit=6)[-2500:]
            return (
                f"Original task: {prompt}\n\n"
                f"Recent conversation context:\n{recent_context or '(none)'}\n\n"
                f"Check command: {(result or {}).get('command', 'local model call failed')}\n"
                f"Exact failure output:\n{exact_output or '; '.join(state['agent_errors'][-3:])}\n\n"
                f"Unified diff:\n{''.join(diff_parts)[-diff_limit:] or '(no file diff captured)'}\n\n"
                f"Relevant current snippets:\n{''.join(snippets)[-snippet_limit:]}\n\n"
                "Fix the failure with a minimal edit_file patch. Do not rewrite entire files."
            )

        cloud_available = self._cfg.gemini_available or self._cfg.nvidia_available
        local_call_failed = provider_name == ProviderName.OLLAMA and bool(state["agent_errors"]) and not changed_files
        test_failed = bool(test_results and not test_results.get("check_passed"))
        if local_call_failed and cloud_available and not state["cloud_repair_attempted"]:
            state["cloud_repair_attempted"] = True
            state["escalation_count"] += 1
            state["route"] = "local->cloud-fallback"
            cloud_provider = ProviderName.NVIDIA if self._cfg.nvidia_available else ProviderName.GEMINI
            yield {"type": "status", "message": "Local model call failed; continuing with a cloud provider..."}
            async for event in self._run_claude_code_agent(
                prompt=f"{prompt}\n\nLocal provider failure:\n{failure_prompt(None)}",
                workspace_context="",
                history=[],
                provider=cloud_provider,
                max_steps=max_steps,
                _repair_depth=_repair_depth + 1,
                _run_state=state,
            ):
                yield event
            await persist_telemetry()
            return

        if test_failed and provider_name == ProviderName.OLLAMA and state["retry_count"] < 2:
            state["retry_count"] += 1
            yield {"type": "status", "message": f"Scoped check failed; local repair {state['retry_count']}/2..."}
            async for event in self._run_claude_code_agent(
                prompt=f"{prompt}\n\nVerification failure to repair:\n{failure_prompt(test_results, compact=True)}",
                workspace_context="",
                history=[],
                provider=ProviderName.OLLAMA,
                max_steps=8,
                route_decision=route_decision,
                _repair_depth=_repair_depth + 1,
                _run_state=state,
            ):
                yield event
            await persist_telemetry()
            return

        if test_failed and provider_name == ProviderName.OLLAMA and cloud_available and not state["cloud_repair_attempted"]:
            state["cloud_repair_attempted"] = True
            state["escalation_count"] += 1
            state["route"] = "local->cloud-repair"
            cloud_provider = ProviderName.NVIDIA if self._cfg.nvidia_available else ProviderName.GEMINI
            yield {"type": "status", "message": "Local repair budget exhausted; escalating diff and failing check to a cloud expert..."}
            async for event in self._run_claude_code_agent(
                prompt=f"{prompt}\n\nCloud repair evidence:\n{failure_prompt(test_results)}",
                workspace_context="",
                history=[],
                provider=cloud_provider,
                max_steps=8,
                _repair_depth=_repair_depth + 1,
                _run_state=state,
            ):
                yield event
            await persist_telemetry()
            return

        if test_failed:
            yield {"type": "status", "message": "Scoped verification still fails after the bounded repair and escalation attempts."}

        await persist_telemetry()
        yield {"type": "done", "provider": provider_name.value, "model": "mini-claude-code"}

    @staticmethod
    def _telemetry_check(result: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if not result:
            return None
        if result.get("runner_used") in {"implementation", "patch-validation"}:
            return None
        return {
            "runner_used": result.get("runner_used", "unknown"),
            "command": result.get("command", ""),
            "check_passed": bool(result.get("check_passed", False)),
            "skipped": bool(result.get("skipped", False) or result.get("runner_error", False)),
            "runner_error": bool(result.get("runner_error", False)),
            "passed": int(result.get("passed", 0) or 0),
            "failed": int(result.get("failed", 0) or 0),
            "counts_known": bool(result.get("counts_known", False)),
            "returncode": result.get("returncode"),
        }

    async def _run_auto_workflow(
        self,
        prompt: str,
        workspace_context: str,
        history: List[ConversationMessage],
        intent: Optional[IntentResult] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Orchestrate the full cycle:
        - Plain Q&A: answer directly
        - Code snippet: generate in chat, apply any file edits
        - Task: run mini Claude Code autonomous tool execution loop
        """
        intent_kind = intent.intent if intent else Intent.CHAT
        task_history = self._task_history(prompt, history)

        # ── Normal questions: answer directly, never plan
        if intent_kind == Intent.CHAT:
            async for chunk in self._answer_directly(prompt, "", history, "Answering your question..."):
                yield chunk
            return

        if intent_kind == Intent.CODE_QUESTION:
            async for chunk in self._answer_directly(
                prompt, workspace_context, history, "Reading the project to answer..."
            ):
                yield chunk
            return

        if intent_kind == Intent.VERIFY:
            async for chunk in self._run_verification_request(prompt):
                yield chunk
            return

        # ── Small standalone code request: local Qwen writes it in chat, apply edits if any
        if intent_kind == Intent.SNIPPET:
            yield {"type": "status", "message": "Writing code with local Qwen..."}
            try:
                accumulated = []
                convo = self._history_text(task_history, limit=6)
                contextual_prompt = f"Conversation so far:\n{convo}\n\nCurrent request: {prompt}" if convo else prompt
                async for text_chunk in self._local_coder.stream_code(prompt=contextual_prompt, context=None):
                    accumulated.append(text_chunk)
                    yield {"type": "text", "content": text_chunk, "model": self._cfg.ollama_model}
                edits = self._local_coder.extract_file_edits("".join(accumulated))
                if edits and self._workspace_root:
                    loop = SelfCorrectionLoop(
                        config=self._cfg,
                        local_coder=self._local_coder,
                        expert=self._expert,
                        workspace_root=self._workspace_root,
                        apply_edits=self.apply_file_edits,
                    )
                    async for event in loop.run(
                        task_id=str(uuid.uuid4()),
                        task_description=prompt,
                        context=workspace_context,
                        edits=edits,
                        route="local-snippet",
                        route_reason="standalone code request routed to local Qwen",
                    ):
                        yield event
                yield {"type": "done", "provider": "ollama", "model": self._cfg.ollama_model}
                return
            except Exception as e:
                logger.warning("Local snippet generation failed (%s); falling back to cloud", e)
                async for chunk in self._answer_directly(prompt, "", history, "Local model unavailable; asking cloud model..."):
                    yield chunk
                return

        # ── TASK: explicit scope routing. Keep targeted work local; send
        # cross-module and architectural work through cloud planning first.
        decision = classify_task_complexity(prompt)
        cloud_available = self._cfg.gemini_available or self._cfg.nvidia_available
        if decision.route == "cloud" and cloud_available:
            yield {"type": "status", "message": f"Routing to cloud planning: {decision.reason}"}
            expert = ProviderName.NVIDIA if self._cfg.nvidia_available else ProviderName.GEMINI
            async for chunk in self._run_expert_local_workflow(
                expert_provider=expert,
                prompt=prompt,
                workspace_context=workspace_context,
                history=task_history,
            ):
                yield chunk
        else:
            reason = decision.reason if decision.route == "local" else "cloud provider unavailable; using local Qwen"
            yield {"type": "status", "message": f"Routing to local Qwen: {reason}"}
            async for chunk in self._run_claude_code_agent(
                prompt=prompt,
                workspace_context=workspace_context,
                history=task_history,
                provider=ProviderName.OLLAMA,
                route_decision={"route": "local", "complexity": decision.complexity, "reason": reason},
            ):
                yield chunk

    # ------------------------------------------------------------------
    # Expert + Local Workflows
    # ------------------------------------------------------------------

    async def _run_expert_local_workflow(
        self,
        expert_provider: ProviderName,
        prompt: str,
        workspace_context: str,
        history: List[ConversationMessage],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        yield {"type": "status", "message": f"Planning task with {expert_provider.value.title()}..."}
        task_history = self._task_history(prompt, history)
        convo = self._history_text(task_history, limit=6)
        context_parts = [f"Relevant workspace context:\n{workspace_context}"] if workspace_context else []
        if convo:
            context_parts.append(
                "Prior conversation for resolving references only. Do not treat old requests or "
                "assistant suggestions as current requirements:\n" + convo
            )
        implementation_context = "\n\n".join(context_parts)
        task_objective = prompt
        if convo:
            task_objective += "\n\nPrior context (reference only):\n" + convo
        parent_task = TaskSpec(description=prompt, context=implementation_context or None)
        plan_usage = None
        try:
            subtasks, plan_usage = await self._expert.plan_task(
                parent_task,
                preferred_provider=expert_provider,
            )
        except Exception as e:
            logger.warning("Cloud planning failed (%s); continuing with a single local task", e)
            yield {"type": "status", "message": f"Cloud planning unavailable ({str(e)[:120]}); continuing locally..."}
            subtasks = []

        if subtasks:
            yield {
                "type": "text",
                "content": f"### {expert_provider.value.title()} Plan\n\n"
                + "\n".join(f"{st.order}. **{st.title}**: {st.description}" for st in subtasks)
                + "\n\n---\n",
                "model": expert_provider.value,
            }

        yield {"type": "status", "message": "Delegating implementation to local Qwen..."}
        all_edits: List[FileEdit] = []
        usage_records: list[ModelUsage] = [plan_usage] if plan_usage else []
        local_failures: list[str] = []
        worker_summaries: list[str] = []
        for st in subtasks or [ImplementationTask(parent_task_id=parent_task.id, title="Implement", description=task_objective)]:
            res = await self._local_coder.implement_task(st, context=implementation_context)
            if res.usage:
                usage_records.append(res.usage)
            if res.status.value != "completed":
                local_failures.append(res.error or f"Local worker failed in {st.title}")
            if res.content and res.file_edits:
                visible = re.sub(r"<hybridcoder_(?:patch|new_file)>.*?</hybridcoder_(?:patch|new_file)>", "", res.content, flags=re.DOTALL).strip()
                if visible:
                    worker_summaries.append(f"\n#### {st.title}\n{visible}\n")
            if res.file_edits:
                all_edits.extend(res.file_edits)

        loop = SelfCorrectionLoop(
            config=self._cfg,
            local_coder=self._local_coder,
            expert=self._expert,
            workspace_root=self._workspace_root,
            apply_edits=self.apply_file_edits,
            preferred_provider=expert_provider,
        )
        applied_files: list[str] = []
        async for event in loop.run(
            task_id=parent_task.id,
            task_description=task_objective,
            context=implementation_context,
            edits=all_edits,
            initial_usages=usage_records,
            initial_failure="\n".join(local_failures) if local_failures else None,
            route="cloud-plan/local-worker",
            route_reason=f"{expert_provider.value} planned the multi-component task; Qwen implements it",
        ):
            if event.get("type") == "edit":
                applied_files = list(event.get("files", []))
            yield event
        if applied_files and worker_summaries:
            yield {"type": "text", "content": "".join(worker_summaries), "model": self._cfg.ollama_model}
        _sk = self._skipped_status()
        if _sk:
            yield _sk

        yield {"type": "done", "provider": expert_provider.value, "model": "expert+local"}

    async def _run_all_models_workflow(
        self,
        prompt: str,
        workspace_context: str,
        history: List[ConversationMessage],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Use the preferred available cloud expert for planning, then Qwen to implement."""
        planning_provider = (
            ProviderName.NVIDIA if self._cfg.nvidia_available
            else ProviderName.GEMINI if self._cfg.gemini_available
            else ProviderName.OLLAMA
        )
        yield {"type": "status", "message": f"Model 1/2 ({planning_provider.value.title()}): Designing architecture..."}
        task_history = self._task_history(prompt, history)
        convo = self._history_text(task_history, limit=6)
        context_parts = [f"Relevant workspace context:\n{workspace_context}"] if workspace_context else []
        if convo:
            context_parts.append(
                "Prior conversation for resolving references only. Do not treat old requests or "
                "assistant suggestions as current requirements:\n" + convo
            )
        implementation_context = "\n\n".join(context_parts)
        task_objective = prompt
        if convo:
            task_objective += "\n\nPrior context (reference only):\n" + convo
        parent_task = TaskSpec(description=prompt, context=implementation_context or None)
        plan_usage = None
        try:
            subtasks, plan_usage = await self._expert.plan_task(
                parent_task,
                preferred_provider=planning_provider,
            )
        except Exception as e:
            logger.warning("Cloud planning failed (%s); continuing with a single local task", e)
            yield {"type": "status", "message": f"Cloud planning unavailable ({str(e)[:120]}); continuing locally..."}
            subtasks = []

        plan_provider = planning_provider
        if plan_usage and plan_usage.provider in (ProviderName.NVIDIA, ProviderName.GEMINI):
            plan_provider = plan_usage.provider
        usage_records: list[ModelUsage] = [plan_usage] if plan_usage else []
        yield {"type": "status", "message": f"Plan ready from {plan_provider.value.title()}; handing it to local Qwen..."}
        yield {"type": "status", "message": "Model 2/2 (Local Qwen): Implementing code..."}
        all_edits: List[FileEdit] = []
        local_failures: list[str] = []
        worker_summaries: list[str] = []
        for st in subtasks or [ImplementationTask(parent_task_id=parent_task.id, title="Implement", description=task_objective)]:
            res = await self._local_coder.implement_task(st, context=implementation_context)
            if res.usage:
                usage_records.append(res.usage)
            if res.status.value != "completed":
                local_failures.append(res.error or f"Local worker failed in {st.title}")
            if res.content and res.file_edits:
                visible = re.sub(r"<hybridcoder_(?:patch|new_file)>.*?</hybridcoder_(?:patch|new_file)>", "", res.content, flags=re.DOTALL).strip()
                if visible:
                    worker_summaries.append(f"\n#### {st.title}\n{visible}\n")
            if res.file_edits:
                all_edits.extend(res.file_edits)

        loop = SelfCorrectionLoop(
            config=self._cfg,
            local_coder=self._local_coder,
            expert=self._expert,
            workspace_root=self._workspace_root,
            apply_edits=self.apply_file_edits,
            preferred_provider=plan_provider,
        )
        applied_files: list[str] = []
        async for event in loop.run(
            task_id=parent_task.id,
            task_description=task_objective,
            context=implementation_context,
            edits=all_edits,
            initial_usages=usage_records,
            initial_failure="\n".join(local_failures) if local_failures else None,
            route="all-models/local-worker",
            route_reason=f"{plan_provider.value} led planning; Qwen implements and the other cloud expert is fallback",
        ):
            if event.get("type") == "edit":
                applied_files = list(event.get("files", []))
            yield event
        if applied_files and worker_summaries:
            yield {"type": "text", "content": "".join(worker_summaries), "model": self._cfg.ollama_model}

        yield {"type": "done", "provider": plan_provider.value, "model": f"{plan_provider.value}+local"}
