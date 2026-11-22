"""
backend/agents/local_coder.py
Local coder agent delegating code generation and modifications to Ollama (Qwen2.5-Coder 7B).
Translates expert implementation tasks into concrete file changes and code edits.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, AsyncGenerator, Optional

from backend.config import Config, get_config
from backend.providers.ollama_provider import OllamaProvider
from backend.schemas.task import (
    FileEdit,
    ImplementationTask,
    ModelUsage,
    ProviderName,
    TaskResult,
    TaskSpec,
    TaskStatus,
)
from backend.storage.usage_tracker import UsageTracker

logger = logging.getLogger(__name__)

_SYSTEM_CODER = """\
You are an expert software engineer and precision code generator.
Your objective is to implement the requested task cleanly, idiomatically, and completely.

CRITICAL INSTRUCTIONS:
1. Keep edits minimal. For an existing file, emit one or more exact search/replace blocks; do not rewrite the whole file.
2. Encode each existing-file patch as JSON inside these tags:
<hybridcoder_patch>
{"path":"relative/file.py","search":"exact existing text","replace":"replacement text"}
</hybridcoder_patch>
The search text must be specific and present exactly once. Escape JSON strings correctly.
3. For a genuinely new file only, emit JSON inside these tags:
<hybridcoder_new_file>
{"path":"relative/new_file.py","content":"complete file content"}
</hybridcoder_new_file>
4. Do NOT use a patch block to create a file. Do NOT use a new-file block to overwrite an existing file.
5. Follow existing conventions and imports. Do not add placeholders such as '# ... rest of code' or TODOs.
6. Provide a concise summary after the patch blocks. Do not put unrelated full-file code fences in the response.
7. File paths MUST be relative to the workspace root. NEVER use absolute paths, drive letters, `/tmp` or `C:\\tmp`.
8. Do NOT create scratch, verification or test-marker files unless requested.
9. Treat the task objective and acceptance criteria as the complete scope. Context is reference material only; do not continue unrelated tasks from conversation history.
"""

_SYSTEM_TASK_IMPLEMENTER = """\
You are an autonomous implementation worker executing a subtask from an expert architectural plan.

Task Details:
- Title: {title}
- Objective: {description}
- Files to modify: {files_to_modify}
- Files to create: {files_to_create}
- Acceptance criteria: {acceptance_criteria}

Context & Relevant Files:
{context}

Respond with a short strategy, then minimal exact patches for existing files using
<hybridcoder_patch>{{"path":"relative/path","search":"exact text","replace":"new text"}}</hybridcoder_patch>
For new files, use
<hybridcoder_new_file>{{"path":"relative/path","content":"complete file content"}}</hybridcoder_new_file>
Do not output complete replacements for existing files. The system runs scoped checks after applying changes.
Implement only the stated subtask and acceptance criteria. Treat context as background, not additional tasks.
"""


class LocalCoderAgent:
    """
    Local implementation worker powered by Ollama (Qwen2.5-Coder 7B).
    Handles routine implementation, bug fixes, refactoring, and file edits.
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        usage_tracker: Optional[UsageTracker] = None,
        provider: Optional[OllamaProvider] = None,
    ) -> None:
        self._cfg = config or get_config()
        self._tracker = usage_tracker or UsageTracker(self._cfg)
        self._provider = provider or OllamaProvider(self._cfg)

    @property
    def provider(self) -> OllamaProvider:
        return self._provider

    # ------------------------------------------------------------------
    # File Edit Extraction Helper
    # ------------------------------------------------------------------

    def extract_file_edits(self, text: str) -> list[FileEdit]:
        """
        Robustly extract file modifications from model output.
        Recognizes:
          - ```filepath:path/to/file.ext
          - ```file:path/to/file.ext
          - ```path/to/file.ext (with recognized extension)
          - ```lang filename="path" or ```lang:path
          - Preceding 'File: path/to/file.ext' / '### path/to/file.ext' before a code block
          - First line comment inside block: '# filepath: ...', '// file: ...'
          - Tool calls: <tool_call> with write_file / create_file / edit_file
        """
        edits: list[FileEdit] = []
        seen_paths: set[str] = set()

        # Preferred format: exact, structured search/replace patches. Multiple
        # patches may target one file and are applied transactionally later.
        structured_paths: set[str] = set()
        for match in re.finditer(r"<hybridcoder_patch>\s*(.*?)\s*</hybridcoder_patch>", text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                path = str(data.get("path", "")).strip().strip("\"'`")
                search = data.get("search")
                replacement = data.get("replace")
                if path and isinstance(search, str) and search and isinstance(replacement, str):
                    structured_paths.add(path)
                    edits.append(FileEdit(
                        path=path,
                        original_content=search,
                        new_content=replacement,
                        description=f"Structured search/replace patch for {path}",
                    ))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        for match in re.finditer(r"<hybridcoder_new_file>\s*(.*?)\s*</hybridcoder_new_file>", text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                path = str(data.get("path", "")).strip().strip("\"'`")
                content = data.get("content")
                if path and isinstance(content, str) and path not in structured_paths:
                    structured_paths.add(path)
                    edits.append(FileEdit(
                        path=path,
                        new_content=content,
                        description=f"Structured new file for {path}",
                    ))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
        seen_paths.update(structured_paths)

        # 1. Tool call extraction (<tool_call> ... </tool_call>)
        tool_call_matches = re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL)
        for tm in tool_call_matches:
            try:
                data = json.loads(tm.group(1))
                name = data.get("name")
                args = data.get("args", {})
                if name in ("write_file", "create_file") and args.get("path") and args.get("content") is not None:
                    p = args["path"].strip().strip("\"'`")
                    if p not in seen_paths:
                        seen_paths.add(p)
                        edits.append(FileEdit(path=p, new_content=args["content"], description=f"Tool call {name} for {p}"))
                elif name == "edit_file" and args.get("path") and args.get("new_content") is not None:
                    p = args["path"].strip().strip("\"'`")
                    if p not in seen_paths:
                        seen_paths.add(p)
                        edits.append(FileEdit(path=p, original_content=args.get("old_content"), new_content=args["new_content"], description=f"Tool call edit_file for {p}"))
            except Exception:
                pass

        # 2. Header immediately followed by code block:
        # e.g.: "### File: src/main.py\n```python\n..." or "**File**: `main.py`\n```\n..."
        header_pattern = re.compile(
            r"(?:###?\s*(?:File:?|Filename:?)\s*[`'\"]?([^\n`'\"]+)[`'\"]?|"
            r"\*\*(?:File:?|Filename:?)\*\*\s*[`'\"]?([^\n`'\"]+)[`'\"]?|"
            r"File:\s*[`'\"]?([^\n`'\"]+)[`'\"]?)\s*\n\s*```[a-zA-Z0-9_-]*\r?\n(.*?)```",
            re.DOTALL | re.IGNORECASE,
        )
        for m in header_pattern.finditer(text):
            p = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            content = m.group(4)
            if p and ("." in p or "/" in p or "\\" in p) and p not in seen_paths:
                seen_paths.add(p)
                edits.append(FileEdit(path=p, new_content=content.rstrip() + "\n", description=f"Header-extracted edit for {p}"))

        # 3. Standard code fence matches: ```filepath:path or ```path
        # Also handles ```lang filename="path" or ```lang:path
        fence_pattern = re.compile(
            r"```(?:filepath:|file:|filename=[\"']?|title=[\"']?|[a-zA-Z0-9_-]+:)?([^\n`\r\"'>]+)[\"']?\r?\n(.*?)```",
            re.DOTALL,
        )
        matches = fence_pattern.findall(text)
        known_langs = {
            "python", "py", "javascript", "js", "typescript", "ts", "tsx", "jsx",
            "json", "bash", "sh", "html", "css", "sql", "yaml", "yml", "markdown",
            "md", "c", "cpp", "csharp", "cs", "go", "rust", "rs", "php", "ruby", "rb",
            "powershell", "ps1", "dockerfile", "text", "txt", "diff",
        }
        for path_str, content in matches:
            clean_path = path_str.strip().strip("\"'`")
            # If the path is just a language name without extension or separator, check inside the block
            if clean_path.lower() in known_langs:
                # Check first line of block for comment like "# filepath: ..." or "// file: ..."
                first_line = content.strip().split("\n", 1)[0].strip() if content.strip() else ""
                first_match = re.match(r"^(?:#|//|<!--|/\*)\s*(?:filepath:|file:|filename:)\s*([^\s*>-]+)", first_line, re.IGNORECASE)
                if first_match:
                    detected_p = first_match.group(1).strip().strip("\"'`")
                    if detected_p and detected_p not in seen_paths:
                        seen_paths.add(detected_p)
                        edits.append(FileEdit(path=detected_p, new_content=content.rstrip() + "\n", description=f"Comment-extracted edit for {detected_p}"))
                continue

            if ("/" in clean_path or "\\" in clean_path or "." in clean_path) and clean_path not in seen_paths:
                # Validate that it looks like a file name (has extension or path separator)
                seen_paths.add(clean_path)
                edits.append(
                    FileEdit(
                        path=clean_path,
                        new_content=content.rstrip() + "\n",
                        description=f"Generated implementation for {clean_path}",
                    )
                )
        return edits

    # ------------------------------------------------------------------
    # Implement Sub-Task
    # ------------------------------------------------------------------

    async def implement_task(
        self,
        task: ImplementationTask,
        context: str = "",
        workspace_root: Optional[str] = None,
    ) -> TaskResult:
        """Execute an implementation sub-task planned by an expert."""
        prompt = _SYSTEM_TASK_IMPLEMENTER.format(
            title=task.title,
            description=task.description,
            files_to_modify=", ".join(task.files_to_modify) or "None",
            files_to_create=", ".join(task.files_to_create) or "None",
            acceptance_criteria="\n".join(f"- {c}" for c in task.acceptance_criteria) or "None",
            context=context or "No additional file context provided.",
        )

        try:
            content, usage = await self._provider.complete(
                prompt=prompt,
                system_prompt=_SYSTEM_CODER,
                max_tokens=4096,
                temperature=0.2,
            )
            await self._tracker.record(usage, task_id=task.id)

            file_edits = self.extract_file_edits(content)

            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                content=content,
                file_edits=file_edits,
                provider=ProviderName.OLLAMA,
                model=self._cfg.ollama_model,
                usage=usage,
            )
        except Exception as exc:
            logger.error("Local coder failed on task %s: %s", task.id, exc)
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(exc),
                provider=ProviderName.OLLAMA,
                model=self._cfg.ollama_model,
            )

    # ------------------------------------------------------------------
    # Streaming Generation
    # ------------------------------------------------------------------

    async def stream_code(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream code generation directly for chat interactions."""
        full_prompt = prompt
        if context:
            full_prompt = f"Workspace Context:\n{context}\n\nTask:\n{prompt}"

        async for chunk in self._provider.stream(
            prompt=full_prompt,
            system_prompt=system_prompt or _SYSTEM_CODER,
            temperature=0.2,
        ):
            yield chunk

    # ------------------------------------------------------------------
    # General Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        task: TaskSpec,
    ) -> TaskResult:
        """Execute a general coding task."""
        context = task.context or ""
        prompt = task.description
        if context:
            prompt = f"Context:\n{context}\n\nTask:\n{prompt}"

        try:
            content, usage = await self._provider.complete(
                prompt=prompt,
                system_prompt=_SYSTEM_CODER,
                max_tokens=task.max_tokens,
                temperature=task.temperature,
            )
            await self._tracker.record(usage, task_id=task.id)
            file_edits = self.extract_file_edits(content)

            return TaskResult(
                task_id=task.id,
                status=TaskStatus.COMPLETED,
                content=content,
                file_edits=file_edits,
                provider=ProviderName.OLLAMA,
                model=self._cfg.ollama_model,
                usage=usage,
            )
        except Exception as exc:
            logger.error("Local coder execution failed: %s", exc)
            return TaskResult(
                task_id=task.id,
                status=TaskStatus.FAILED,
                error=str(exc),
                provider=ProviderName.OLLAMA,
                model=self._cfg.ollama_model,
            )

