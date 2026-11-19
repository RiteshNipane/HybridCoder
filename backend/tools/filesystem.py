"""
filesystem.py - Async file-system tools with workspace-boundary enforcement.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import List, Optional

import aiofiles

from backend.security.workspace_guard import validate_path, sanitize_output

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_IGNORE_DIRS: set[str] = {
    "node_modules", ".git", "dist", "build", ".venv", "venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".tox",
    ".idea", ".vscode", ".DS_Store", "coverage", ".next",
    ".nuxt", ".output", "target", "out",
}

BINARY_EXTENSIONS: set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".wav",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".pyc",
    ".wasm", ".class", ".jar", ".war",
    ".ttf", ".woff", ".woff2", ".eot", ".otf",
    ".db", ".sqlite", ".sqlite3",
}


def _should_ignore(path: Path, ignore_patterns: list[str]) -> bool:
    """Return True if *path* matches any ignore rule."""
    # Always ignore known binary extensions at any depth
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    # Always ignore default directories
    for part in path.parts:
        if part in DEFAULT_IGNORE_DIRS:
            return True
    # User-supplied patterns (glob-style)
    for pat in ignore_patterns:
        if path.match(pat):
            return True
    return False


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------
async def list_files(path: str | Path, ignore_patterns: list[str] | None = None) -> list[dict]:
    """
    Recursively list files under *path*.

    Returns a list of dicts with keys: path (str), size (int), is_dir (bool).
    Skips ignored dirs/files silently.
    """
    ignore_patterns = ignore_patterns or []
    root = Path(path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path does not exist: {root}")

    results: list[dict] = []

    def _walk(p: Path) -> None:
        try:
            entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name))
        except PermissionError:
            return
        for entry in entries:
            rel = entry.relative_to(root)
            if _should_ignore(rel, ignore_patterns):
                continue
            stat = entry.stat()
            results.append({
                "path": str(rel).replace("\\", "/"),
                "size": stat.st_size if entry.is_file() else 0,
                "is_dir": entry.is_dir(),
            })
            if entry.is_dir():
                _walk(entry)

    await asyncio.to_thread(_walk, root)
    return results


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------
async def read_file(path: str | Path, workspace_root: str | Path) -> str:
    """Read and return file content after workspace-boundary check."""
    target = validate_path(path, workspace_root)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {target}")
    if target.is_dir():
        raise IsADirectoryError(f"Path is a directory: {target}")
    if target.suffix.lower() in BINARY_EXTENSIONS:
        raise ValueError(f"Refusing to read binary file: {target}")

    async with aiofiles.open(target, "r", encoding="utf-8", errors="replace") as fh:
        content = await fh.read()
    return content


# ---------------------------------------------------------------------------
# write_file
# ---------------------------------------------------------------------------
async def write_file(path: str | Path, content: str, workspace_root: str | Path) -> dict:
    """Write *content* to *path* (creates parent dirs as needed). Returns metadata."""
    target = validate_path(path, workspace_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(target, "w", encoding="utf-8") as fh:
        await fh.write(content)
    logger.info("write_file: wrote %d bytes to %s", len(content), target)
    return {"path": str(target), "bytes_written": len(content.encode("utf-8"))}


# ---------------------------------------------------------------------------
# edit_file (targeted replacement)
# ---------------------------------------------------------------------------
async def edit_file(
    path: str | Path,
    old_content: str,
    new_content: str,
    workspace_root: str | Path,
) -> dict:
    """
    Replace the first occurrence of *old_content* with *new_content* in the file.
    Raises ValueError if *old_content* is not found.
    """
    target = validate_path(path, workspace_root)
    current = await read_file(target, workspace_root)
    if old_content not in current:
        raise ValueError(
            f"edit_file: old_content not found verbatim in {target}. "
            "Ensure exact whitespace and line endings match."
        )
    updated = current.replace(old_content, new_content, 1)
    await write_file(target, updated, workspace_root)
    return {
        "path": str(target),
        "original_length": len(current),
        "new_length": len(updated),
    }


# ---------------------------------------------------------------------------
# create_file (fails if file already exists)
# ---------------------------------------------------------------------------
async def create_file(path: str | Path, content: str, workspace_root: str | Path) -> dict:
    """Create a new file; raises FileExistsError if it already exists."""
    target = validate_path(path, workspace_root)
    if target.exists():
        raise FileExistsError(f"create_file: file already exists: {target}")
    return await write_file(target, content, workspace_root)


# ---------------------------------------------------------------------------
# create_directory
# ---------------------------------------------------------------------------
async def create_directory(path: str | Path, workspace_root: str | Path) -> dict:
    """Create a directory inside the active workspace, including missing parents."""
    target = validate_path(path, workspace_root)
    target.mkdir(parents=True, exist_ok=True)
    logger.info("create_directory: ensured %s", target)
    return {"path": str(target)}


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------
async def delete_file(
    path: str | Path,
    workspace_root: str | Path,
    require_approval: bool = True,
) -> dict:
    """
    Delete a file inside the workspace.
    If *require_approval* is True (default), the caller must have confirmed with the user
    before invoking this function. The flag is recorded in the return value.
    """
    target = validate_path(path, workspace_root)
    if not target.exists():
        raise FileNotFoundError(f"delete_file: not found: {target}")
    if target.is_dir():
        raise IsADirectoryError(f"delete_file: target is a directory (use rmdir): {target}")

    await asyncio.to_thread(target.unlink)
    logger.info("delete_file: deleted %s (approval_required=%s)", target, require_approval)
    return {"deleted": str(target), "approval_required": require_approval}


# ---------------------------------------------------------------------------
# search_files (grep-like)
# ---------------------------------------------------------------------------
async def search_files(
    workspace_root: str | Path,
    pattern: str,
    file_extensions: list[str] | None = None,
) -> list[dict]:
    """
    Search for regex *pattern* across all text files under *workspace_root*.

    Args:
        workspace_root: Root to search from.
        pattern: Python regex pattern.
        file_extensions: Optional list like ['.py', '.ts'] to filter file types.

    Returns:
        List of dicts: {file, line_number, line, match}.
    """
    root = Path(workspace_root).resolve()
    compiled = re.compile(pattern)
    exts: set[str] | None = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in file_extensions} if file_extensions else None
    results: list[dict] = []

    def _search() -> None:
        for fpath in root.rglob("*"):
            if not fpath.is_file():
                continue
            rel = fpath.relative_to(root)
            if _should_ignore(rel, []):
                continue
            if exts and fpath.suffix.lower() not in exts:
                continue
            if fpath.suffix.lower() in BINARY_EXTENSIONS:
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
                for lineno, line in enumerate(text.splitlines(), 1):
                    m = compiled.search(line)
                    if m:
                        results.append({
                            "file": str(rel).replace("\\", "/"),
                            "line_number": lineno,
                            "line": line.rstrip(),
                            "match": m.group(0),
                        })
            except (PermissionError, OSError):
                continue

    await asyncio.to_thread(_search)
    return results
