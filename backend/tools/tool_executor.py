"""
backend/tools/tool_executor.py

Claude Code-style agentic tool execution loop.

The agent (Qwen / Gemini) produces structured tool calls in its output.
This module parses those calls, executes the matching tool, feeds results
back into the conversation, and loops until the model stops calling tools
or the step limit is reached.

Tool call format (XML-like, works with every model):

    <tool_call>
    {"name": "read_file", "args": {"path": "src/main.py"}}
    </tool_call>

Result injected as:

    <tool_result name="read_file">
    … content …
    </tool_result>
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

from backend.tools.filesystem import (
    create_file,
    create_directory,
    delete_file,
    edit_file,
    list_files,
    read_file,
    search_files,
    write_file,
)
from backend.tools.terminal import CommandBlockedError, CommandRequiresApprovalError, run_command
from backend.tools.verification import validate_source_text_async

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_TOOL_STEPS = 20          # max tool-call iterations per request
MAX_OUTPUT_CHARS = 12_000    # truncate tool output to keep context manageable

# ---------------------------------------------------------------------------
# Tool definitions (shown to the LLM in the system prompt)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS = """
You have access to the following tools. Use them whenever you need to read, create, or modify files, run commands, or search the codebase. Call ONE tool at a time and wait for the result.

<tools>
  <tool name="read_file">
    <description>Read the full content of a file.</description>
    <args>
      <arg name="path" type="string" required="true">Relative or absolute path to the file.</arg>
    </args>
  </tool>

  <tool name="write_file">
    <description>Create a genuinely new file with complete content. For existing files use edit_file with the smallest exact section that should change.</description>
    <args>
      <arg name="path" type="string" required="true">Path to the file to write.</arg>
      <arg name="content" type="string" required="true">Full content to write to the file.</arg>
    </args>
  </tool>

  <tool name="create_file">
    <description>Create a NEW file only. Fails if the file already exists.</description>
    <args>
      <arg name="path" type="string" required="true">Path to the new file.</arg>
      <arg name="content" type="string" required="true">Content for the new file.</arg>
    </args>
  </tool>

  <tool name="create_directory">
    <description>Create a folder (and any missing parent folders) inside the active workspace.</description>
    <args>
      <arg name="path" type="string" required="true">Relative path for the new folder, inside the workspace.</arg>
    </args>
  </tool>

  <tool name="edit_file">
    <description>Make a minimal patch to an existing file by replacing one exact, unique section. Prefer this over rewriting a whole file. old_content must match exactly.</description>
    <args>
      <arg name="path" type="string" required="true">Path to the file.</arg>
      <arg name="old_content" type="string" required="true">Exact substring to find and replace.</arg>
      <arg name="new_content" type="string" required="true">Replacement text.</arg>
    </args>
  </tool>

  <tool name="delete_file">
    <description>Delete a file. Use with caution — this is irreversible.</description>
    <args>
      <arg name="path" type="string" required="true">Path to the file to delete.</arg>
    </args>
  </tool>

  <tool name="list_directory">
    <description>List files and directories at the given path (recursive).</description>
    <args>
      <arg name="path" type="string" required="false">Directory path. Defaults to workspace root.</arg>
    </args>
  </tool>

  <tool name="run_command">
    <description>Execute a shell command in the workspace directory. Output is captured and returned. Avoid interactive commands.</description>
    <args>
      <arg name="command" type="string" required="true">The command to run.</arg>
      <arg name="cwd" type="string" required="false">Working directory (defaults to workspace root).</arg>
      <arg name="timeout" type="integer" required="false">Timeout in seconds (default 30, max 120).</arg>
    </args>
  </tool>

  <tool name="search_files">
    <description>Search for a regex pattern across all source files in the workspace.</description>
    <args>
      <arg name="pattern" type="string" required="true">Python regex pattern to search for.</arg>
      <arg name="extensions" type="string" required="false">Comma-separated list of file extensions to filter, e.g. ".py,.ts"</arg>
    </args>
  </tool>
</tools>

To call a tool, output a JSON block like this (and NOTHING else on those lines):

<tool_call>
{"name": "tool_name", "args": {"arg1": "value1", "arg2": "value2"}}
</tool_call>

After getting the tool result, continue reasoning and optionally call more tools.
When you are done, provide your final answer WITHOUT any tool calls.
"""

# ---------------------------------------------------------------------------
# System prompt for the agentic coding session
# ---------------------------------------------------------------------------

AGENT_SYSTEM_PROMPT = """You are an expert software engineer and autonomous coding assistant, similar to Claude Code.

You have direct access to the user's filesystem and can execute terminal commands.
You MUST use tools to actually read, write, and create files — do NOT just describe what you would do.

ENVIRONMENT & OPERATING SYSTEM:
- OS: Windows 11.
- Shell: Windows PowerShell / cmd.exe.
- Python executable: 'python' (NOT 'python3').
- Package manager: 'pip' (NOT 'pip3').
- File inspection: ALWAYS use the provided 'read_file', 'write_file', 'list_directory', or 'search_files' tools instead of running shell commands like 'cat', 'ls', or 'grep'.
- When running commands via run_command, avoid Unix-specific utilities (no 'tail', no 'head', no '/dev/null', no 'which'). Use Windows equivalents if needed ('where' instead of 'which', 'NUL' instead of '/dev/null').

IMPORTANT RULES:
1. When asked to create a folder, use create_directory immediately. Use create_file or write_file only for genuinely new files.
2. When asked to edit an existing file, first read_file it, then use edit_file with a small, exact, unique search section. Do not overwrite whole files to make a local change.
3. When asked to run something, use run_command.
4. Always confirm what you did by showing the file path and a brief summary.
5. Never produce fake file contents in markdown without actually writing the file.
6. After writing files, check with list_directory or read_file to verify success.
7. Keep responses concise. Show tool calls and their results, then summarize.
8. File operations are restricted to the active workspace. If the requested location is outside it, ask the user to select that location as the workspace; never create a similarly named folder in a different location.
9. The orchestrator runs scoped tests or compiler checks after edits and feeds failures back for a bounded repair pass. Do not run a guessed full test suite yourself unless the user explicitly asks.

{workspace_info}
{tool_definitions}
"""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(.*?)\s*</tool_call>",
    re.DOTALL,
)

_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\{\s*\"(?:name|tool)\"\s*:\s*\"[^\"]+\".*?\})\s*```",
    re.DOTALL,
)


def parse_tool_calls(text: str) -> List[Dict[str, Any]]:
    """Extract all tool_call JSON blocks from model output."""
    calls = []
    # 1. <tool_call> tags
    for match in _TOOL_CALL_RE.finditer(text):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and ("name" in obj or "tool" in obj):
                name = obj.get("name") or obj.get("tool")
                args = obj.get("args") or obj.get("parameters") or obj.get("arguments") or {}
                calls.append({"name": name, "args": args})
        except json.JSONDecodeError:
            pass

    # 2. ```json blocks containing tool call object
    for match in _JSON_FENCE_RE.finditer(text):
        raw = match.group(1).strip()
        try:
            obj = json.loads(raw)
            if isinstance(obj, dict) and ("name" in obj or "tool" in obj):
                name = obj.get("name") or obj.get("tool")
                args = obj.get("args") or obj.get("parameters") or obj.get("arguments") or {}
                if not any(c["name"] == name and c["args"] == args for c in calls):
                    calls.append({"name": name, "args": args})
        except json.JSONDecodeError:
            pass

    return calls


def has_tool_calls(text: str) -> bool:
    return bool(_TOOL_CALL_RE.search(text) or _JSON_FENCE_RE.search(text))


def strip_tool_calls(text: str) -> str:
    """Remove tool call blocks from text (they're handled separately)."""
    s = _TOOL_CALL_RE.sub("", text)
    s = _JSON_FENCE_RE.sub("", s)
    return s.strip()


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class ToolExecutor:
    """
    Execute tool calls produced by an LLM.

    Usage:
        executor = ToolExecutor(workspace_root="/path/to/workspace")
        result_text = await executor.execute(tool_name, args)
    """

    def __init__(
        self,
        workspace_root: Optional[Path] = None,
        require_approval_callback: Optional[Callable[[str, str], asyncio.Future]] = None,
    ) -> None:
        self.workspace_root = workspace_root
        self._approval_cb = require_approval_callback
        self.last_change: Optional[Dict[str, Optional[str]]] = None

    def _root(self, path_arg: Optional[str] = None) -> Path:
        """Resolve a path relative to workspace_root."""
        if path_arg:
            p = Path(path_arg)
            if p.is_absolute():
                return p
            if self.workspace_root:
                return self.workspace_root / p
            return p
        return self.workspace_root or Path.cwd()

    async def execute(self, name: str, args: Dict[str, Any]) -> Tuple[str, bool]:
        """
        Execute *name* with *args*.

        Returns:
            (result_text, is_error)
        """
        self.last_change = None
        try:
            if name == "read_file":
                path = self._root(args.get("path"))
                ws = self.workspace_root or path.parent
                content = await read_file(path, ws)
                return _truncate(content), False

            elif name == "write_file":
                path = self._root(args.get("path"))
                content = args.get("content", "")
                ws = self.workspace_root or path.parent
                before = await read_file(path, ws) if path.exists() else None
                if before is not None:
                    raise ValueError("write_file will not replace an existing file; use edit_file with a minimal exact search block")
                await validate_source_text_async(path, content, ws)
                result = await write_file(path, content, ws)
                self.last_change = {"path": str(Path(result["path"]).relative_to(ws.resolve())).replace("\\", "/"), "before": before, "after": content}
                return f"✅ Written: {result['path']} ({result['bytes_written']} bytes)", False

            elif name == "create_file":
                path = self._root(args.get("path"))
                content = args.get("content", "")
                ws = self.workspace_root or path.parent
                await validate_source_text_async(path, content, ws)
                result = await create_file(path, content, ws)
                self.last_change = {"path": str(Path(result["path"]).relative_to(ws.resolve())).replace("\\", "/"), "before": None, "after": content}
                return f"✅ Created: {result['path']} ({result['bytes_written']} bytes)", False

            elif name == "create_directory":
                path = self._root(args.get("path"))
                ws = self.workspace_root or path.parent
                result = await create_directory(path, ws)
                return f"✅ Created directory: {result['path']}", False

            elif name == "edit_file":
                path = self._root(args.get("path"))
                old = args.get("old_content", "")
                new = args.get("new_content", "")
                ws = self.workspace_root or path.parent
                before = await read_file(path, ws)
                matches = before.count(old) if old else 0
                if matches != 1:
                    raise ValueError(f"edit_file requires an exact search block that occurs once; found {matches} matches in {path}")
                after = before.replace(old, new, 1)
                await validate_source_text_async(path, after, ws)
                result = await edit_file(path, old, new, ws)
                self.last_change = {"path": str(Path(result["path"]).relative_to(ws.resolve())).replace("\\", "/"), "before": before, "after": after}
                return f"✅ Edited: {result['path']} ({result['original_length']} → {result['new_length']} chars)", False

            elif name == "delete_file":
                path = self._root(args.get("path"))
                ws = self.workspace_root or path.parent
                before = await read_file(path, ws)
                result = await delete_file(path, ws, require_approval=False)
                self.last_change = {"path": str(Path(result["deleted"]).relative_to(ws.resolve())).replace("\\", "/"), "before": before, "after": None}
                return f"✅ Deleted: {result['deleted']}", False

            elif name == "list_directory":
                path_arg = args.get("path")
                root = self._root(path_arg) if path_arg else (self.workspace_root or Path.cwd())
                entries = await list_files(root)
                if not entries:
                    return "(empty directory)", False
                lines = []
                for e in entries[:200]:
                    icon = "📁" if e["is_dir"] else "📄"
                    size = f" ({e['size']:,} bytes)" if not e["is_dir"] else ""
                    lines.append(f"{icon} {e['path']}{size}")
                truncated = "\n".join(lines)
                if len(entries) > 200:
                    truncated += f"\n… and {len(entries) - 200} more"
                return truncated, False

            elif name == "run_command":
                cmd = args.get("command", "")
                cwd_arg = args.get("cwd")
                timeout = min(int(args.get("timeout", 30)), 120)
                cwd = self._root(cwd_arg) if cwd_arg else (self.workspace_root or Path.cwd())
                result = await run_command(cmd, cwd, timeout=timeout, approved=True)
                out = result.get("stdout", "")
                err = result.get("stderr", "")
                rc = result.get("returncode", 0)
                timed = result.get("timed_out", False)
                parts = []
                if out:
                    parts.append(f"STDOUT:\n{_truncate(out)}")
                if err:
                    parts.append(f"STDERR:\n{_truncate(err)}")
                if timed:
                    parts.append("⚠️ Command timed out")
                parts.append(f"Exit code: {rc}")
                return "\n".join(parts) if parts else "(no output)", rc != 0

            elif name == "search_files":
                pattern = args.get("pattern", "")
                exts_raw = args.get("extensions", "")
                exts = [e.strip() for e in exts_raw.split(",") if e.strip()] if exts_raw else None
                ws = self.workspace_root or Path.cwd()
                results = await search_files(ws, pattern, exts)
                if not results:
                    return f"No matches for pattern: {pattern!r}", False
                lines = [
                    f"{r['file']}:{r['line_number']}: {r['line'].strip()}"
                    for r in results[:100]
                ]
                out = "\n".join(lines)
                if len(results) > 100:
                    out += f"\n… and {len(results) - 100} more matches"
                return out, False

            else:
                return f"❌ Unknown tool: {name!r}", True

        except CommandBlockedError as e:
            return f"🚫 Command blocked (dangerous): {e}", True
        except CommandRequiresApprovalError as e:
            return f"🔐 Command requires approval: {e}", True
        except FileNotFoundError as e:
            return f"❌ File not found: {e}", True
        except FileExistsError as e:
            return f"❌ File already exists: {e}", True
        except ValueError as e:
            return f"❌ Invalid argument: {e}", True
        except PermissionError as e:
            return f"❌ Permission denied: {e}", True
        except Exception as e:
            logger.exception("Tool %s failed: %s", name, e)
            return f"❌ Tool error ({name}): {type(e).__name__}: {e}", True


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n\n… [truncated {len(text) - limit:,} chars] …\n\n" + text[-half:]


# ---------------------------------------------------------------------------
# Agentic Loop
# ---------------------------------------------------------------------------


async def run_agentic_loop(
    *,
    prompt: str,
    conversation_history: List[Dict[str, str]],
    workspace_root: Optional[Path],
    llm_complete: Callable[[List[Dict[str, str]], str], asyncio.coroutines],
    on_tool_call: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    on_tool_result: Optional[Callable[[str, str, bool], None]] = None,
    max_steps: int = MAX_TOOL_STEPS,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    Run the full agentic tool-use loop.

    Args:
        prompt:               The user's request.
        conversation_history: Prior messages (role/content dicts).
        workspace_root:       Root of the workspace (tool sandbox).
        llm_complete:         Async callable(messages, system) → str.
        on_tool_call:         Optional callback before each tool call.
        on_tool_result:       Optional callback after each tool result.
        max_steps:            Maximum number of tool-use iterations.

    Yields dicts with:
        {"type": "text",        "content": str}         — model text
        {"type": "tool_call",   "name": str, "args": dict}
        {"type": "tool_result", "name": str, "output": str, "error": bool}
        {"type": "done"}
    """
    executor = ToolExecutor(workspace_root=workspace_root)

    ws_info = ""
    if workspace_root:
        ws_info = f"Current workspace: {workspace_root}"
        try:
            entries = await list_files(workspace_root)
            top = [e["path"] for e in entries if "/" not in e["path"]][:20]
            ws_info += f"\nTop-level entries: {', '.join(top)}"
        except Exception:
            pass

    system_prompt = AGENT_SYSTEM_PROMPT.format(
        workspace_info=ws_info,
        tool_definitions=TOOL_DEFINITIONS,
    )

    # Build initial messages
    messages: List[Dict[str, str]] = list(conversation_history)
    messages.append({"role": "user", "content": prompt})

    for step in range(max_steps):
        # Call the LLM
        try:
            response_text: str = await llm_complete(messages, system_prompt)
        except Exception as e:
            yield {"type": "error", "error": f"LLM call failed at step {step}: {e}"}
            return

        # Yield any visible text (outside tool_call blocks)
        visible_text = strip_tool_calls(response_text)
        if visible_text:
            yield {"type": "text", "content": visible_text}

        # Parse tool calls
        tool_calls = parse_tool_calls(response_text)

        if not tool_calls:
            # No more tool calls — we're done
            break

        # Add the assistant message to history
        messages.append({"role": "assistant", "content": response_text})

        # Execute each tool call
        tool_results_text = ""
        for call in tool_calls:
            tool_name = call.get("name", "unknown")
            tool_args = call.get("args", {})

            yield {"type": "tool_call", "name": tool_name, "args": tool_args}
            if on_tool_call:
                on_tool_call(tool_name, tool_args)

            output, is_error = await executor.execute(tool_name, tool_args)

            result_event = {"type": "tool_result", "name": tool_name, "output": output, "error": is_error}
            if not is_error and executor.last_change:
                # Kept internal by the orchestrator; it must not be sent to the browser
                # because the snapshot can contain complete source files.
                result_event["change"] = executor.last_change
            yield result_event
            if on_tool_result:
                on_tool_result(tool_name, output, is_error)

            tool_results_text += (
                f"<tool_result name=\"{tool_name}\">\n{output}\n</tool_result>\n\n"
            )

        # Inject tool results as a user turn so the model can continue
        messages.append({"role": "user", "content": tool_results_text.strip()})

    else:
        yield {
            "type": "text",
            "content": f"\n\n⚠️ Reached maximum tool-use steps ({max_steps}). Stopping.",
        }

    yield {"type": "done"}
