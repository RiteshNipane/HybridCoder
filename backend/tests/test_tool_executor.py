import asyncio
from pathlib import Path
import pytest

from backend.tools.tool_executor import ToolExecutor, parse_tool_calls, has_tool_calls, strip_tool_calls, run_agentic_loop


@pytest.mark.asyncio
async def test_tool_executor_file_lifecycle(tmp_path: Path):
    root = tmp_path.resolve()
    executor = ToolExecutor(workspace_root=root)

    # 1. create_file
    out, err = await executor.execute("create_file", {"path": "hello.py", "content": "print('hello world')\n"})
    assert not err
    assert "Created" in out
    assert (root / "hello.py").exists()

    # 2. read_file
    out, err = await executor.execute("read_file", {"path": "hello.py"})
    assert not err
    assert "print('hello world')" in out

    # 3. edit_file
    out, err = await executor.execute(
        "edit_file",
        {"path": "hello.py", "old_content": "hello world", "new_content": "hello mini claude code"},
    )
    assert not err
    assert "Edited" in out
    assert (root / "hello.py").read_text(encoding="utf-8") == "print('hello mini claude code')\n"

    # 4. list_directory
    out, err = await executor.execute("list_directory", {})
    assert not err
    assert "hello.py" in out

    # 5. search_files
    out, err = await executor.execute("search_files", {"pattern": "claude"})
    assert not err
    assert "hello.py" in out

    # 6. run_command
    out, err = await executor.execute("run_command", {"command": "python -c \"print('ran successfully')\""})
    assert not err
    assert "ran successfully" in out

    # 7. delete_file
    out, err = await executor.execute("delete_file", {"path": "hello.py"})
    assert not err
    assert not (root / "hello.py").exists()


def test_tool_call_parsing():
    raw = """
I will now create the file.
<tool_call>
{"name": "write_file", "args": {"path": "app.py", "content": "print(1)"}}
</tool_call>
Please check the result.
"""
    assert has_tool_calls(raw)
    calls = parse_tool_calls(raw)
    assert len(calls) == 1
    assert calls[0]["name"] == "write_file"
    assert calls[0]["args"]["path"] == "app.py"

    stripped = strip_tool_calls(raw)
    assert "<tool_call>" not in stripped
    assert "I will now create the file." in stripped


@pytest.mark.asyncio
async def test_agentic_loop_execution(tmp_path: Path):
    root = tmp_path.resolve()

    # Mock LLM that emits a tool call on step 0, then finishes on step 1
    turns = [
        '<tool_call>{"name": "write_file", "args": {"path": "auto_test.txt", "content": "autonomous"}}</tool_call>',
        'I have successfully created auto_test.txt with the requested content.',
    ]
    turn_idx = 0

    async def mock_llm(messages, system):
        nonlocal turn_idx
        res = turns[turn_idx]
        turn_idx += 1
        return res

    events = []
    async for ev in run_agentic_loop(
        prompt="create auto_test.txt",
        conversation_history=[],
        workspace_root=root,
        llm_complete=mock_llm,
    ):
        events.append(ev)

    assert (root / "auto_test.txt").exists()
    assert (root / "auto_test.txt").read_text() == "autonomous"
    assert any(e.get("type") == "tool_call" and e.get("name") == "write_file" for e in events)
    assert any(e.get("type") == "tool_result" and "Written" in e.get("output", "") for e in events)
    assert any(e.get("type") == "done" for e in events)
