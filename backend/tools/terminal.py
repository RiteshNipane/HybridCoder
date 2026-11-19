"""
terminal.py - Async terminal execution with safety checks.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from backend.security.workspace_guard import is_dangerous_command, requires_approval, sanitize_output

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_IS_WINDOWS = sys.platform == "win32"


def _which(exe: str) -> bool:
    return shutil.which(exe) is not None


def _normalize_windows_command(cmd: str) -> str:
    """Normalize common Unixisms so Linux-trained LLMs work reliably on Windows."""
    if not _IS_WINDOWS:
        return cmd

    # Replace python3 with python if python3 is not an installed executable
    if not _which("python3") and _which("python"):
        cmd = re.sub(r"\bpython3\b", "python", cmd)
        cmd = re.sub(r"\bpip3\b", "pip", cmd)

    # Replace /dev/null with NUL
    cmd = re.sub(r"/dev/null", "NUL", cmd)

    # Replace which with where
    cmd = re.sub(r"\bwhich\s+", "where ", cmd)

    return cmd


def _sync_subprocess_run(cmd_str: str, cwd: Path, timeout: int, capture: bool) -> dict:
    cmd_to_run = _normalize_windows_command(cmd_str)
    timed_out = False
    try:
        res = subprocess.run(
            cmd_to_run,
            cwd=str(cwd),
            shell=True,
            capture_output=capture,
            text=True,
            timeout=timeout,
            env=os.environ,
        )
        stdout = sanitize_output(res.stdout or "")
        stderr = sanitize_output(res.stderr or "")
        rc = res.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        raw_out = exc.stdout or ""
        raw_err = exc.stderr or ""
        if isinstance(raw_out, bytes):
            raw_out = raw_out.decode("utf-8", errors="replace")
        if isinstance(raw_err, bytes):
            raw_err = raw_err.decode("utf-8", errors="replace")
        stdout = sanitize_output(raw_out)
        stderr = sanitize_output(raw_err)
        rc = -1
    except Exception as e:
        stdout = ""
        stderr = str(e)
        rc = -1

    return {
        "returncode": rc,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
    }


class CommandBlockedError(RuntimeError):
    """Raised when a command matches the dangerous blocklist."""


class CommandRequiresApprovalError(RuntimeError):
    """Raised when a command requires explicit user approval before execution."""
    def __init__(self, cmd: str) -> None:
        super().__init__(f"Command requires user approval before running: {cmd!r}")
        self.cmd = cmd


# ---------------------------------------------------------------------------
# run_command
# ---------------------------------------------------------------------------
async def run_command(
    cmd: str | list[str],
    cwd: str | Path,
    timeout: int = 30,
    capture: bool = True,
    approved: bool = False,
) -> dict:
    """
    Run *cmd* as a subprocess inside *cwd*.

    Args:
        cmd: Shell command string or argv list.
        cwd: Working directory (must exist).
        timeout: Seconds before the process is killed.
        capture: If True, capture stdout/stderr; else let them print live.
        approved: Set to True if the caller has already obtained user approval
                  for commands that `requires_approval()` flags.

    Returns:
        {returncode, stdout, stderr, timed_out}
    """
    cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)

    # Safety checks
    if is_dangerous_command(cmd_str):
        raise CommandBlockedError(f"Dangerous command blocked: {cmd_str!r}")
    if requires_approval(cmd_str) and not approved:
        raise CommandRequiresApprovalError(cmd_str)

    cwd_path = Path(cwd).resolve()
    if not cwd_path.exists():
        raise FileNotFoundError(f"Working directory does not exist: {cwd_path}")

    result = await asyncio.to_thread(_sync_subprocess_run, cmd_str, cwd_path, timeout, capture)

    if result.get("timed_out"):
        logger.warning("Command timed out after %ds: %s", timeout, cmd_str[:200])
    else:
        logger.info("Command finished (rc=%s): %s", result.get("returncode"), cmd_str[:200])

    return result


# ---------------------------------------------------------------------------
# _detect_runner helpers
# ---------------------------------------------------------------------------
def _has_file(cwd: Path, *names: str) -> bool:
    return any((cwd / n).exists() for n in names)


def _which(exe: str) -> bool:
    return shutil.which(exe) is not None


# ---------------------------------------------------------------------------
# run_tests
# ---------------------------------------------------------------------------
async def run_tests(cwd: str | Path, test_runner: str = "auto") -> dict:
    """
    Detect and run tests in *cwd*.

    Supported runners: pytest, jest, vitest, flutter, cargo, go.
    Pass test_runner="auto" to auto-detect.
    """
    cwd_path = Path(cwd).resolve()

    if test_runner == "auto":
        test_runner = _detect_test_runner_simple(cwd_path)

    runner_map: dict[str, str] = {
        "pytest": "python -m pytest -v --tb=short",
        "jest": "npx jest --colors",
        "vitest": "npx vitest run",
        "flutter": "flutter test",
        "cargo": "cargo test",
        "go": "go test ./...",
    }

    cmd = runner_map.get(test_runner)
    if not cmd:
        return {
            "returncode": -1,
            "stdout": "",
            "stderr": f"Unknown test runner: {test_runner}",
            "runner": test_runner,
            "timed_out": False,
        }

    result = await run_command(cmd, cwd_path, timeout=120)
    result["runner"] = test_runner
    return result


def _detect_test_runner_simple(cwd: Path) -> str:
    if _has_file(cwd, "pubspec.yaml"):
        return "flutter"
    if _has_file(cwd, "Cargo.toml"):
        return "cargo"
    if _has_file(cwd, "go.mod"):
        return "go"
    pkg = cwd / "package.json"
    if pkg.exists():
        try:
            import json
            data = json.loads(pkg.read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})
            devdeps = {**data.get("devDependencies", {}), **data.get("dependencies", {})}
            if "vitest" in devdeps or "vitest" in str(scripts):
                return "vitest"
            if "jest" in devdeps or "jest" in str(scripts):
                return "jest"
        except Exception:
            return "jest"
        return "jest"
    if _has_file(cwd, "pytest.ini", "setup.cfg", "pyproject.toml", "conftest.py"):
        return "pytest"
    return "pytest"  # fallback


# ---------------------------------------------------------------------------
# run_linter
# ---------------------------------------------------------------------------
async def run_linter(cwd: str | Path) -> dict:
    """Auto-detect and run linter (ruff > flake8 > eslint)."""
    cwd_path = Path(cwd).resolve()

    # Python linters
    if _has_file(cwd_path, "pyproject.toml", "setup.cfg", "setup.py", ".flake8", "ruff.toml"):
        if _which("ruff"):
            return await run_command("ruff check .", cwd_path, timeout=60)
        if _which("flake8"):
            return await run_command("flake8 .", cwd_path, timeout=60)

    # JS/TS linter
    if _has_file(cwd_path, "package.json"):
        eslint_cfg = any(
            (cwd_path / f).exists()
            for f in [".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yaml", "eslint.config.js"]
        )
        if eslint_cfg:
            return await run_command("npx eslint . --ext .js,.jsx,.ts,.tsx", cwd_path, timeout=60)

    return {"returncode": 0, "stdout": "No linter detected.", "stderr": "", "timed_out": False}


# ---------------------------------------------------------------------------
# run_build
# ---------------------------------------------------------------------------
async def run_build(cwd: str | Path) -> dict:
    """Auto-detect and run the build command."""
    cwd_path = Path(cwd).resolve()

    if _has_file(cwd_path, "pubspec.yaml"):
        return await run_command("flutter build", cwd_path, timeout=300)
    if _has_file(cwd_path, "Cargo.toml"):
        return await run_command("cargo build --release", cwd_path, timeout=300)
    if _has_file(cwd_path, "go.mod"):
        return await run_command("go build ./...", cwd_path, timeout=120)
    if _has_file(cwd_path, "package.json"):
        # Check scripts.build
        try:
            import json
            data = json.loads((cwd_path / "package.json").read_text(encoding="utf-8"))
            scripts = data.get("scripts", {})
            if "build" in scripts:
                return await run_command("npm run build", cwd_path, timeout=300)
        except Exception:
            pass
    if _has_file(cwd_path, "pyproject.toml", "setup.py"):
        return await run_command("python -m build", cwd_path, timeout=120)
    if _has_file(cwd_path, "Makefile"):
        return await run_command("make", cwd_path, timeout=300)

    return {"returncode": 0, "stdout": "No build system detected.", "stderr": "", "timed_out": False}
