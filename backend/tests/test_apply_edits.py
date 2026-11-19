import os
from pathlib import Path

import pytest

os.environ.setdefault("GEMINI_API_KEY", "test")

from backend.orchestrator import CodingOrchestrator
from backend.schemas.task import FileEdit


@pytest.mark.asyncio
async def test_edits_outside_workspace_are_skipped_not_fatal(tmp_path: Path):
    root = tmp_path.resolve()
    o = CodingOrchestrator()
    o._workspace_root = root
    applied = await o.apply_file_edits([
        FileEdit(path="index.html", new_content="<h1>hi</h1>"),
        FileEdit(path="/tmp/test_file_direct_creation.txt", new_content="bad"),
        FileEdit(path="../escape.txt", new_content="bad"),
        FileEdit(path=str(root / "inside_abs.txt"), new_content="ok"),
    ])
    assert applied == ["index.html", "inside_abs.txt"]
    assert len(o.skipped_edits) == 2
    assert not (root.parent / "escape.txt").exists()
    assert "Skipped 2 file(s)" in o._skipped_status()["message"]
