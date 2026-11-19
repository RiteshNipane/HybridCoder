"""
git_manager.py - Async Git operations for the coding agent.

Design decisions:
- Never automatically reset/discard pre-existing uncommitted changes.
- Uses checkpoints (git stash) for safe rollback.
- All ops run via `git` subprocess – no libgit2 / pygit2 dependency.
"""
from __future__ import annotations

import logging
from pathlib import Path

from backend.tools.terminal import run_command

logger = logging.getLogger(__name__)


async def _git(args: str, workspace_root: Path, timeout: int = 30, approved: bool = False) -> dict:
    """Helper: run a git command inside *workspace_root*."""
    return await run_command(f"git {args}", cwd=workspace_root, timeout=timeout, approved=approved)


# ---------------------------------------------------------------------------
# git_status
# ---------------------------------------------------------------------------
async def git_status(workspace_root: str | Path) -> dict:
    """
    Return the working-tree status.

    Returns:
        {returncode, stdout (porcelain v2 output), stderr, clean (bool), branch}
    """
    root = Path(workspace_root).resolve()
    result = await _git("status --porcelain=v2 --branch", root)
    lines = result["stdout"].splitlines()
    branch = "unknown"
    for line in lines:
        if line.startswith("# branch.head "):
            branch = line.split(" ", 2)[-1].strip()
            break
    changed = [l for l in lines if not l.startswith("#")]
    result["clean"] = len(changed) == 0
    result["branch"] = branch
    result["changed_files"] = changed
    return result


# ---------------------------------------------------------------------------
# git_diff
# ---------------------------------------------------------------------------
async def git_diff(workspace_root: str | Path, staged: bool = False) -> dict:
    """
    Return the diff of the working tree (or staged changes if *staged* is True).

    Returns:
        {returncode, diff (str), stderr}
    """
    root = Path(workspace_root).resolve()
    flag = "--cached" if staged else ""
    result = await _git(f"diff {flag} --stat -p", root, timeout=30)
    result["diff"] = result.pop("stdout", "")
    return result


# ---------------------------------------------------------------------------
# create_checkpoint  (git stash push with a descriptive message)
# ---------------------------------------------------------------------------
async def create_checkpoint(workspace_root: str | Path, message: str) -> dict:
    """
    Stash current changes with *message* as the stash description.

    Only stashes if there are uncommitted changes; noop otherwise.
    Returns:
        {stash_ref (str | None), message, clean_before, returncode}
    """
    root = Path(workspace_root).resolve()

    # Check if there is anything to stash
    status = await git_status(root)
    if status["clean"]:
        return {
            "stash_ref": None,
            "message": message,
            "clean_before": True,
            "returncode": 0,
            "stdout": "Nothing to stash.",
            "stderr": "",
        }

    result = await _git(f'stash push -u -m "{message}"', root, approved=True)

    # Retrieve the stash ref (stash@{0} after a successful push)
    stash_ref: str | None = None
    if result["returncode"] == 0:
        list_result = await _git("stash list --format=%gd", root)
        refs = list_result["stdout"].splitlines()
        stash_ref = refs[0].strip() if refs else "stash@{0}"

    result["stash_ref"] = stash_ref
    result["message"] = message
    result["clean_before"] = False
    logger.info("Checkpoint created: %s → %s", message, stash_ref)
    return result


# ---------------------------------------------------------------------------
# rollback_checkpoint  (git stash pop / apply)
# ---------------------------------------------------------------------------
async def rollback_checkpoint(workspace_root: str | Path, stash_ref: str) -> dict:
    """
    Restore from a previously created stash checkpoint.

    Uses `git stash apply` (keeps the stash) rather than `git stash pop` (drops it)
    so the user can re-apply it again if needed.

    Returns:
        {returncode, stdout, stderr, stash_ref}
    """
    root = Path(workspace_root).resolve()
    result = await _git(f"stash apply {stash_ref}", root, approved=True)
    result["stash_ref"] = stash_ref
    logger.info("Checkpoint rolled back: %s (rc=%s)", stash_ref, result["returncode"])
    return result


# ---------------------------------------------------------------------------
# git_log
# ---------------------------------------------------------------------------
async def git_log(workspace_root: str | Path, n: int = 10) -> dict:
    """
    Return recent git commits.

    Returns:
        {returncode, commits (list[dict]), stderr}
    """
    root = Path(workspace_root).resolve()
    fmt = "--format=%H|%h|%an|%ae|%ai|%s"
    result = await _git(f"log {fmt} -n {n}", root)
    commits: list[dict] = []
    for line in result["stdout"].splitlines():
        parts = line.split("|", 5)
        if len(parts) == 6:
            commits.append({
                "hash": parts[0],
                "short_hash": parts[1],
                "author_name": parts[2],
                "author_email": parts[3],
                "date": parts[4],
                "message": parts[5],
            })
    result["commits"] = commits
    result.pop("stdout", None)
    return result
