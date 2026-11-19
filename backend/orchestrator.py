"""
backend/orchestrator.py
Main orchestrator coordinating models, tools, escalation, and the autonomous coding workflow.
Implements the 3-model hybrid architecture (Gemini + Grok + Local Qwen).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from backend.agents.expert_agent import ExpertAgent
from backend.agents.local_coder import LocalCoderAgent
from backend.agents.reviewer import CodeReviewerAgent
from backend.config import Config, RoutingMode, get_config
from backend.providers.gemini_provider import GeminiProvider
from backend.routing.escalation import EscalationEngine
from backend.routing.intent import (
    Intent,
    IntentResult,
    classify_with_fallback,
    project_terms,
    strip_override,
)
from backend.routing.model_router import ModelRouter
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
from backend.task_manager import TaskManager
from backend.tools.filesystem import create_directory, create_file, delete_file, edit_file, list_files, read_file, write_file
from backend.tools.git_manager import create_checkpoint, git_diff, git_status
from backend.tools.testing import run_project_tests
from backend.tools.tool_executor import (
    ToolExecutor,
    has_tool_calls,
    parse_tool_calls,
    run_agentic_loop,
    strip_tool_calls,
)

logger = logging.getLogger(__name__)


class CodingOrchestrator:
    """
    Central orchestrator for the autonomous coding agent.
    Coordinates planning (Gemini), review (NVIDIA/Gemini), and local implementation (Qwen).
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
        """Apply a list of file edits to the workspace.

        Edits whose path points outside the workspace and its parent project
        are SKIPPED and recorded in ``self.skipped_edits``.
        """
        root = (self._workspace_root or self._cfg.workspace_root or Path.cwd()).resolve()
        self.skipped_edits = []
        applied_paths: List[str] = []
        for edit in edits:
            raw = (edit.path or "").strip().strip("\"'`")
            try:
                candidate = Path(raw)
                full_path = (candidate if candidate.is_absolute() else root / candidate).resolve()
                rel = full_path.relative_to(root)
                target_root = root
            except Exception:
                logger.warning("Skipping edit outside workspace: %s", raw)
                self.skipped_edits.append(raw)
                continue

            rel_path = str(rel).replace("\\", "/")
            try:
                if edit.original_content:
                    await edit_file(
                        path=full_path,
                        old_content=edit.original_content,
                        new_content=edit.new_content,
                        workspace_root=target_root,
                    )
                else:
                    await write_file(
                        path=full_path,
                        content=edit.new_content,
                        workspace_root=target_root,
                    )
                applied_paths.append(rel_path)
                logger.info("Applied edit to: %s", rel_path)
            except Exception as e:
                logger.error("Failed to apply edit to %s: %s", rel_path, e)
                raise
        return applied_paths

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
        prior_context = self._history_text(history)
        contextual_request = (
            f"Conversation so far:\n{prior_context}\n\nCurrent request: {prompt}"
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
                history=history,
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
                history=history,
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
                    ProviderName.GEMINI if self._cfg.gemini_available
                    else ProviderName.NVIDIA if self._cfg.nvidia_available
                    else ProviderName.OLLAMA
                )
                async for chunk in self._run_claude_code_agent(
                    prompt=prompt,
                    workspace_context=workspace_context_str,
                    history=history,
                    provider=provider,
                ):
                    yield chunk
                return

            async for chunk in self._run_all_models_workflow(
                prompt=prompt,
                workspace_context=workspace_context_str,
                history=history,
            ):
                yield chunk

    # ------------------------------------------------------------------
    # Autonomous Workflow (AUTO Mode)
    # ------------------------------------------------------------------

    async def _classify_with_llm(self, question: str) -> str:
        """Use the low-demand Gemini model for a short tie-break; rules remain fallback."""
        fast_config = self._cfg.model_copy(update={
            "gemini_model": self._cfg.gemini_model_fast,
            "gemini_timeout": 5,
            "gemini_max_retries": 1,
        })
        provider = GeminiProvider(fast_config)
        text, _usage = await provider.complete(
            prompt=question,
            max_tokens=32,
            temperature=0.0,
        )
        return text

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
                chain=[ProviderName.GEMINI, ProviderName.NVIDIA, ProviderName.OLLAMA],
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
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Run an autonomous, Claude Code-style agentic workflow with real filesystem execution.
        The agent directly inspects, creates, edits, and searches files, and runs terminal commands.
        """
        history_list = history or []
        provider_name = provider or (
            ProviderName.GEMINI if self._cfg.gemini_available
            else (ProviderName.NVIDIA if self._cfg.nvidia_available else ProviderName.OLLAMA)
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

        # Define LLM completion callable for the tool loop
        async def _llm_complete(messages: List[Dict[str, str]], system_prompt: str) -> str:
            # If Ollama local
            if provider_name == ProviderName.OLLAMA:
                text, usage = await self._local_coder.provider.complete_chat(
                    messages=messages,
                    system_prompt=system_prompt,
                    temperature=0.1,
                )
                await self._tracker.record(usage)
                return text

            # Cloud provider via escalation engine
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
            return res.text

        all_modified_files: List[str] = []
        full_assistant_output: List[str] = []

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
                full_assistant_output.append(content)
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
                yield ev

                # If this tool created or wrote a file, record it and notify the UI
                if t_name in ("write_file", "create_file", "create_directory", "edit_file") and not is_err:
                    target_path = None
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
                            pass
                        if t_name != "create_directory":
                            all_modified_files.append(rel)
                        yield {
                            "type": "edit",
                            "files": [rel],
                            "action": "create" if t_name in ("create_file", "create_directory") else "edit",
                        }

            elif ev_type == "error":
                yield ev

        # Fallback: scan any fenced file edits that the LLM wrote in prose without a tool call
        joined_output = "".join(full_assistant_output)
        additional_edits = self._local_coder.extract_file_edits(joined_output)
        if additional_edits and self._workspace_root:
            try:
                applied = await self.apply_file_edits(additional_edits)
                if applied:
                    all_modified_files.extend(applied)
                    yield {"type": "edit", "files": applied}
            except Exception as e:
                logger.warning("Failed applying fallback file edits: %s", e)

        # Run tests if files were modified
        if all_modified_files and self._workspace_root:
            try:
                test_results = await run_project_tests(self._workspace_root)
                if test_results and (test_results.get("passed", 0) > 0 or test_results.get("failed", 0) > 0):
                    yield {"type": "test", "results": test_results}
            except Exception:
                pass

        yield {"type": "done", "provider": provider_name.value, "model": "mini-claude-code"}

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

        # ── Small standalone code request: local Qwen writes it in chat, apply edits if any
        if intent_kind == Intent.SNIPPET:
            yield {"type": "status", "message": "Writing code with local Qwen..."}
            try:
                accumulated = []
                convo = self._history_text(history)
                contextual_prompt = f"Conversation so far:\n{convo}\n\nCurrent request: {prompt}" if convo else prompt
                async for text_chunk in self._local_coder.stream_code(prompt=contextual_prompt, context=None):
                    accumulated.append(text_chunk)
                    yield {"type": "text", "content": text_chunk, "model": self._cfg.ollama_model}
                edits = self._local_coder.extract_file_edits("".join(accumulated))
                if edits and self._workspace_root:
                    applied = await self.apply_file_edits(edits)
                    if applied:
                        yield {"type": "edit", "files": applied}
                yield {"type": "done", "provider": "ollama", "model": self._cfg.ollama_model}
                return
            except Exception as e:
                logger.warning("Local snippet generation failed (%s); falling back to cloud", e)
                async for chunk in self._answer_directly(prompt, "", history, "Local model unavailable; asking cloud model..."):
                    yield chunk
                return

        # ── TASK: Claude Code autonomous tool loop!
        # Runs tools directly (create_directory, create_file, write_file, edit_file, read_file, run_command)
        async for chunk in self._run_claude_code_agent(
            prompt=prompt,
            workspace_context=workspace_context,
            history=history,
            provider=None,  # auto-selects best available
        ):
            yield chunk

        yield {"type": "done", "provider": "auto", "model": "hybrid"}

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
        convo = self._history_text(history)
        contextual_prompt = f"Conversation so far:\n{convo}\n\nCurrent request: {prompt}" if convo else prompt
        parent_task = TaskSpec(description=contextual_prompt, context=workspace_context)
        try:
            subtasks, _ = await self._expert.plan_task(parent_task)
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
        for st in subtasks or [ImplementationTask(parent_task_id=parent_task.id, title="Implement", description=prompt)]:
            res = await self._local_coder.implement_task(st, context="\n\n".join(x for x in (convo, workspace_context) if x))
            if res.content:
                yield {"type": "text", "content": f"\n#### {st.title}\n{res.content}\n", "model": self._cfg.ollama_model}
            if res.file_edits:
                all_edits.extend(res.file_edits)

        if all_edits and self._workspace_root:
            applied = await self.apply_file_edits(all_edits)
            yield {"type": "edit", "files": applied}
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
        """Triple model workflow: Gemini plans, Grok reviews plan, Local implements, both review."""
        yield {"type": "status", "message": "Model 1/3 (Gemini): Designing architecture..."}
        convo = self._history_text(history)
        contextual_prompt = f"Conversation so far:\n{convo}\n\nCurrent request: {prompt}" if convo else prompt
        parent_task = TaskSpec(description=contextual_prompt, context=workspace_context)
        try:
            subtasks, _ = await self._expert.plan_task(parent_task)
        except Exception as e:
            logger.warning("Cloud planning failed (%s); continuing with a single local task", e)
            yield {"type": "status", "message": f"Cloud planning unavailable ({str(e)[:120]}); continuing locally..."}
            subtasks = []

        yield {"type": "status", "message": "Model 2/3 (NVIDIA NIM): Reviewing architecture & edge cases..."}
        nvidia_critique = ""
        if self._cfg.nvidia_available:
            try:
                nvidia_res = await self._escalation.run(
                    prompt=f"Task: {contextual_prompt}\nProposed Plan:\n" + "\n".join(f"- {st.title}" for st in subtasks) + "\nPlease provide architectural critique and potential risks.",
                    hint_chain=[ProviderName.NVIDIA],
                )
                nvidia_critique = nvidia_res.text
                yield {"type": "text", "content": f"### NVIDIA NIM Critique\n{nvidia_critique}\n\n---\n", "model": "nvidia"}
            except Exception as e:
                logger.debug("NVIDIA critique skipped: %s", e)

        yield {"type": "status", "message": "Model 3/3 (Local Qwen): Implementing code..."}
        for st in subtasks or [ImplementationTask(parent_task_id=parent_task.id, title="Implement", description=prompt)]:
            res = await self._local_coder.implement_task(st, context="\n\n".join(x for x in (convo, workspace_context) if x))
            if res.content:
                yield {"type": "text", "content": f"\n#### {st.title}\n{res.content}\n", "model": self._cfg.ollama_model}
            if res.file_edits and self._workspace_root:
                await self.apply_file_edits(res.file_edits)

        yield {"type": "done", "provider": "all", "model": "tri-hybrid"}
