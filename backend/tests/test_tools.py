"""
Tests for filesystem tools and workspace guard security.
"""
import pytest
from pathlib import Path
from backend.security.workspace_guard import validate_path, is_dangerous_command, sanitize_output
from backend.tools.filesystem import write_file, read_file, edit_file, list_files


@pytest.mark.asyncio
async def test_workspace_boundary_security(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")

    # Path traversal attempt
    with pytest.raises(ValueError):
        validate_path("../outside.txt", workspace)

    import os
    if os.name == "nt":  # backslash is only a path separator on Windows
        with pytest.raises(ValueError):
            validate_path("..\\outside.txt", workspace)


def test_dangerous_command_detection():
    assert is_dangerous_command("format C:")
    assert is_dangerous_command("rm -rf /")
    assert is_dangerous_command("del /f /s /q C:\\Windows")
    assert not is_dangerous_command("git status")
    assert not is_dangerous_command("pytest")
    assert not is_dangerous_command("python test.py")


def test_sanitize_output():
    raw = "GEMINI_API_KEY=AIzaSy1234567890abcdef and token 123"
    sanitized = sanitize_output(raw)
    assert "AIzaSy" not in sanitized or "[REDACTED" in sanitized


@pytest.mark.asyncio
async def test_file_crud_operations(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Create / Write
    file_path = workspace / "src" / "test.py"
    await write_file(file_path, "def hello():\n    return 'world'\n", workspace)

    # Read
    content = await read_file(file_path, workspace)
    assert "def hello():" in content

    # Edit
    await edit_file(file_path, "return 'world'", "return 'hybrid'", workspace)
    updated = await read_file(file_path, workspace)
    assert "return 'hybrid'" in updated

    # List
    files = await list_files(workspace)
    assert any("test.py" in f["path"] for f in files)
