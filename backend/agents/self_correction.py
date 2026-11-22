"""Bounded local repair loop for structured worker edits."""

from __future__ import annotations

import difflib
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional

from backend.agents.expert_agent import ExpertAgent
from backend.agents.local_coder import LocalCoderAgent
from backend.config import Config
from backend.schemas.task import FileEdit, ImplementationTask, ModelUsage, ProviderName, TaskSpec
from backend.storage.run_telemetry import append_task_run
from backend.tools.filesystem import read_file
from backend.tools.verification import run_scoped_checks


_CLOUD_REPAIR_PROMPT = """You are repairing a code change after an automated scoped check failed.
Use the exact failure output, diff, and file snippets supplied below. Make the smallest
change that fixes the failure. Return only structured patches for existing files using
<hybridcoder_patch>{\"path\":\"relative/path\",\"search\":\"exact unique text\",\"replace\":\"replacement\"}</hybridcoder_patch>
For a new file use <hybridcoder_new_file>{\"path\":\"relative/path\",\"content\":\"complete content\"}</hybridcoder_new_file>.
Do not rewrite existing files or claim a check passed unless its output says so."""


def _usage_totals(usages: list[ModelUsage]) -> dict[str, int]:
    return {
        "prompt_tokens": sum(int(item.prompt_tokens or 0) for item in usages),
        "completion_tokens": sum(int(item.completion_tokens or 0) for item in usages),
        "total_tokens": sum(int(item.total_tokens or 0) for item in usages),
    }


def _check_snapshot(result: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
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


class SelfCorrectionLoop:
    """Applies validated Qwen patches, checks them, repairs twice, then escalates once."""

    def __init__(
        self,
        *,
        config: Config,
        local_coder: LocalCoderAgent,
        expert: ExpertAgent,
        workspace_root: Path,
        apply_edits: Callable[[list[FileEdit]], Awaitable[list[str]]],
        preferred_provider: Optional[ProviderName] = None,
        max_local_retries: int = 2,
    ) -> None:
        self.config = config
        self.local_coder = local_coder
        self.expert = expert
        self.workspace_root = workspace_root.resolve()
        self.apply_edits = apply_edits
        self.preferred_provider = preferred_provider
        self.max_local_retries = max(0, min(max_local_retries, 3))

    async def run(
        self,
        *,
        task_id: Optional[str],
        task_description: str,
        context: str,
        edits: list[FileEdit],
        initial_usages: Optional[list[ModelUsage]] = None,
        initial_failure: Optional[str] = None,
        route: str = "local-first",
        route_reason: str = "",
        conversation_id: Optional[str] = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        run_id = task_id or str(uuid.uuid4())
        started = time.perf_counter()
        usages = list(initial_usages or [])
        all_paths: list[str] = []
        originals: dict[str, Optional[str]] = {}
        retry_count = 0
        escalation_count = 0
        cloud_attempted = False

        def add_paths(new_edits: list[FileEdit]) -> None:
            for edit in new_edits:
                path = edit.path.replace("\\", "/")
                if path not in all_paths:
                    all_paths.append(path)

        async def capture_originals(new_edits: list[FileEdit]) -> None:
            for edit in new_edits:
                path = edit.path.replace("\\", "/")
                if path in originals:
                    continue
                candidate = (self.workspace_root / path).resolve()
                try:
                    candidate.relative_to(self.workspace_root)
                except ValueError:
                    continue
                originals[path] = await read_file(candidate, self.workspace_root) if candidate.is_file() else None

        add_paths(edits)
        await capture_originals(edits)
        test_before = await run_scoped_checks(self.workspace_root, all_paths)
        if not test_before.get("skipped"):
            yield {"type": "status", "message": f"Baseline check: {test_before.get('runner_used')}"}

        pending_edits = edits
        last_result: dict[str, Any] = {
            "passed": 0, "failed": 0, "check_passed": False, "skipped": True,
            "counts_known": False,
            "runner_used": "implementation", "command": "extract structured file patches",
            "output": initial_failure or "The implementation worker returned no structured file patches.",
        }
        applied_paths: list[str] = []

        while True:
            if pending_edits:
                add_paths(pending_edits)
                await capture_originals(pending_edits)
                try:
                    just_applied = await self.apply_edits(pending_edits)
                    applied_paths = list(dict.fromkeys([*applied_paths, *just_applied]))
                    if applied_paths:
                        last_result = await run_scoped_checks(self.workspace_root, applied_paths)
                    else:
                        last_result = {
                            "passed": 0, "failed": 0, "check_passed": False, "skipped": False,
                            "counts_known": False,
                            "runner_used": "patch-validation", "command": "apply structured patches",
                            "output": "All proposed edits were rejected as outside the selected workspace.",
                        }
                except Exception as exc:
                    last_result = {
                        "passed": 0, "failed": 0, "check_passed": False, "skipped": False,
                        "counts_known": False,
                        "runner_used": "patch-validation", "command": "syntax pre-check / transactional patch apply",
                        "output": f"{type(exc).__name__}: {exc}",
                    }
                event_type = "validation" if last_result.get("runner_used") == "patch-validation" else "test"
                yield {"type": event_type, "results": last_result}
                pending_edits = []

            if last_result.get("check_passed") and initial_failure:
                last_result = {
                    **last_result,
                    "check_passed": False,
                    "runner_used": "implementation-completeness",
                    "command": "finish all planned implementation subtasks",
                    "output": (
                        f"Scoped checks passed, but one or more local worker calls failed:\n{initial_failure}"
                    ),
                }

            if last_result.get("check_passed"):
                break

            if last_result.get("runner_error"):
                yield {
                    "type": "status",
                    "message": "The verification command could not start; skipping code repair retries.",
                }
                break

            feedback = await self._build_feedback(
                task_description=task_description,
                paths=all_paths,
                originals=originals,
                result=last_result,
            )
            if initial_failure is None and retry_count < self.max_local_retries:
                retry_count += 1
                yield {"type": "status", "message": f"Scoped check failed; asking local Qwen to repair (attempt {retry_count}/{self.max_local_retries})..."}
                repair_task = ImplementationTask(
                    parent_task_id=run_id,
                    title="Repair scoped verification failure",
                    description="Repair the scoped verification failure with the smallest exact patch.",
                    files_to_modify=all_paths,
                    acceptance_criteria=["The reported scoped verification failure is fixed."],
                )
                repair = await self.local_coder.implement_task(
                    repair_task,
                    context=(
                        f"Original task: {task_description}\n"
                        f"Relevant project context:\n{context[-2500:]}\n\n"
                        f"Exact verification feedback:\n{feedback}"
                    ),
                )
                if repair.usage:
                    usages.append(repair.usage)
                pending_edits = repair.file_edits
                if repair.status.value != "completed":
                    last_result["output"] = f"Local repair call failed: {repair.error or 'no details'}\n\n{last_result.get('output', '')}"
                continue

            cloud_available = self.config.gemini_available or self.config.nvidia_available
            if cloud_available and not cloud_attempted:
                cloud_attempted = True
                escalation_count += 1
                yield {"type": "status", "message": "Local repair budget exhausted; escalating the diff and exact check output to a cloud expert..."}
                cloud_prompt = (
                    f"Original task: {task_description}\n\n"
                    f"Routing decision: {route} ({route_reason})\n\n"
                    f"Repair request and evidence:\n{feedback}"
                )
                try:
                    text, usage = await self.expert.chat(
                        prompt=cloud_prompt,
                        system_prompt=_CLOUD_REPAIR_PROMPT,
                        task=TaskSpec(description=task_description, context=context, id=run_id),
                        task_id=run_id,
                        conversation_id=conversation_id,
                        preferred_provider=self.preferred_provider,
                    )
                    usages.append(usage)
                    pending_edits = self.local_coder.extract_file_edits(text)
                    initial_failure = None
                    if not pending_edits:
                        last_result["output"] = (
                            "Cloud repair returned no structured patch.\n\n" + feedback
                        )
                except Exception as exc:
                    last_result["output"] = f"Cloud repair failed: {type(exc).__name__}: {exc}\n\n{feedback}"
                    pending_edits = []
                if pending_edits:
                    continue
            break

        yield {"type": "edit", "files": applied_paths} if applied_paths else {"type": "status", "message": "No file changes were applied."}
        if not applied_paths and last_result.get("runner_used") == "implementation":
            yield {
                "type": "text",
                "content": (
                    "\n\n**Implementation incomplete:** no structured file patch was produced, "
                    "so no files changed and no tests were run.\n\n"
                    + str(last_result.get("output", ""))[-1200:]
                ),
            }
        elif not applied_paths and last_result.get("runner_used") == "patch-validation":
            yield {
                "type": "text",
                "content": (
                    "\n\n**Patch rejected:** no file changes were applied. See the validation "
                    "details above for the exact reason.\n"
                ),
            }
        await append_task_run({
            "task_id": run_id,
            "route": route,
            "complexity": "high" if route.startswith("cloud-plan") else "targeted",
            "route_reason": route_reason,
            "escalation_count": escalation_count,
            "local_retry_count": retry_count,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            **_usage_totals(usages),
            "test_before": _check_snapshot(test_before),
            "test_after": _check_snapshot(last_result),
            "implementation_outcome": {
                "status": (
                    "patch_validation_failed" if last_result.get("runner_used") == "patch-validation"
                    else "no_patch" if last_result.get("runner_used") == "implementation"
                    else "verification_unavailable" if last_result.get("runner_error")
                    else "verification_skipped" if last_result.get("skipped")
                    else "verified" if last_result.get("check_passed")
                    else "check_failed"
                ),
                "detail": str(last_result.get("output", ""))[-1000:],
            },
            "changed_files": applied_paths,
            "success": bool(last_result.get("check_passed")),
        })

    async def _build_feedback(
        self,
        *,
        task_description: str,
        paths: list[str],
        originals: dict[str, Optional[str]],
        result: dict[str, Any],
    ) -> str:
        diff_blocks: list[str] = []
        snippets: list[str] = []
        for rel in paths:
            path = (self.workspace_root / rel).resolve()
            try:
                path.relative_to(self.workspace_root)
            except ValueError:
                continue
            before = originals.get(rel) or ""
            after = await read_file(path, self.workspace_root) if path.is_file() else ""
            diff = difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
            )
            block = "".join(diff)
            if block:
                diff_blocks.append(block)
            if after:
                snippets.append(f"--- Current file: {rel} ---\n{after[:2400]}")

        return (
            f"Task: {task_description}\n\n"
            f"Check command: {result.get('command', 'unknown')}\n"
            f"Runner: {result.get('runner_used', 'unknown')}\n"
            f"Exact failing output:\n{str(result.get('output', ''))[-5000:]}\n\n"
            f"Unified diff of the current change:\n{''.join(diff_blocks)[-3500:] or '(No file diff; patch was rejected before writing.)'}\n\n"
            f"Relevant current file snippets:\n{''.join(snippets)[-3000:] or '(No changed file content is available.)'}"
        )
