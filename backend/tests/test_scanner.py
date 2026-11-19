"""
Tests for repository scanner and context retriever.
"""
import pytest
from pathlib import Path
from backend.scanner.repository_scanner import scan_repository
from backend.scanner.context_retriever import retrieve_relevant_context, extract_file_summary


@pytest.mark.asyncio
async def test_repository_scanner(tmp_path: Path):
    # Create mock repo
    repo = tmp_path / "mock_project"
    repo.mkdir()

    (repo / "main.py").write_text("def run():\n    print('Hello')\n")
    (repo / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (repo / "package.json").write_text('{"name": "test-app", "dependencies": {"react": "^18.0.0"}}')
    
    # Ignored directory
    git_dir = repo / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]\n")

    ctx = await scan_repository(repo)
    assert "Python" in ctx.languages
    assert "JavaScript/TypeScript" in ctx.languages or "React" in ctx.frameworks
    assert any("main.py" in f.path for f in ctx.files)
    # Ensure .git was ignored
    assert not any(".git" in f.path for f in ctx.files)


def test_file_summary():
    py_code = '"""Calculates Fibonacci numbers."""\ndef fib(n):\n    return n\n'
    summary = extract_file_summary("fib.py", py_code)
    assert "Fibonacci" in summary or "fib" in summary
