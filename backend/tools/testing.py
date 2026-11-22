"""
testing.py - Test runner detection, execution, and result parsing.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
from pathlib import Path

from backend.tools.terminal import run_command

logger = logging.getLogger(__name__)

_TEST_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:[A-Za-z]:)?[\\/]?(?:[A-Za-z0-9_.-]+[\\/])*"
    r"[A-Za-z0-9_.-]+\.(?:py|tsx?|jsx?|mjs|cjs)",
    re.IGNORECASE,
)


def extract_test_paths(prompt: str, workspace_root: str | Path) -> list[str]:
    """Extract explicit test paths and keep them inside the selected workspace."""
    root = Path(workspace_root).resolve()
    paths: list[str] = []
    for raw in _TEST_PATH_RE.findall(prompt):
        candidate = Path(raw)
        full = (candidate if candidate.is_absolute() else root / candidate).resolve()
        try:
            relative = full.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"Test path is outside the selected workspace: {raw}") from exc
        if relative not in paths:
            paths.append(relative)
    return paths


def _workspace_relative_test_paths(root: Path, paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in paths:
        candidate = Path(raw)
        full = (candidate if candidate.is_absolute() else root / candidate).resolve()
        try:
            relative = full.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"Test path is outside the selected workspace: {raw}") from exc
        if relative.startswith("-"):
            raise ValueError(f"Invalid test path: {raw}")
        normalized.append(relative)
    return normalized


def _captured_test_output(raw: dict, command: str) -> str:
    """Keep runner startup and silent-exit failures visible in the chat result."""
    output = "\n".join(
        part for part in (str(raw.get("stdout", "")), str(raw.get("stderr", ""))) if part
    ).strip()
    if output:
        return output
    if raw.get("launch_error"):
        detail = str(raw.get("stderr", "")).strip() or "no additional error detail was provided"
        return f"Could not start test command `{command}`: {detail}"
    if raw.get("timed_out"):
        return f"Test command `{command}` timed out without producing output."
    returncode = raw.get("returncode", -1)
    if returncode != 0:
        return f"Test command `{command}` exited with code {returncode} without stdout or stderr."
    return f"Test command `{command}` exited successfully without producing output."


def _runner_failed_to_report_results(raw: dict) -> bool:
    output = str(raw.get("stdout", "")) + str(raw.get("stderr", ""))
    return bool(
        raw.get("launch_error")
        or (
            raw.get("returncode", 0) != 0
            and not output.strip()
            and not raw.get("timed_out")
        )
    )

# ---------------------------------------------------------------------------
# detect_test_runner
# ---------------------------------------------------------------------------
async def detect_test_runner(workspace_root: str | Path) -> str:
    """
    Detect the primary test runner used by the project.

    Returns one of: pytest | jest | vitest | flutter | cargo | go | unknown
    """
    root = Path(workspace_root).resolve()

    def has(*names: str) -> bool:
        return any((root / n).exists() for n in names)

    # Flutter
    if has("pubspec.yaml"):
        return "flutter"

    # Rust
    if has("Cargo.toml"):
        return "cargo"

    # Go
    if has("go.mod"):
        return "go"

    # Node / JS / TS
    pkg_path = root / "package.json"
    if pkg_path.exists():
        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        dev_deps = {**data.get("devDependencies", {}), **data.get("dependencies", {})}
        scripts = data.get("scripts", {})
        test_script = scripts.get("test", "")
        if "vitest" in dev_deps or "vitest" in test_script:
            return "vitest"
        if "jest" in dev_deps or "jest" in test_script or "@jest" in str(dev_deps):
            return "jest"
        # Try to infer from config files
        if has("jest.config.js", "jest.config.ts", "jest.config.json"):
            return "jest"
        if has("vitest.config.ts", "vitest.config.js"):
            return "vitest"
        return "jest"  # default for JS projects

    # Python
    if has(
        "pytest.ini", "pyproject.toml", "setup.cfg", "setup.py",
        "conftest.py", "tox.ini", "tests", "test", "backend/tests",
    ):
        return "pytest"

    return "unknown"


# ---------------------------------------------------------------------------
# run_project_tests
# ---------------------------------------------------------------------------
async def run_project_tests(
    workspace_root: str | Path,
    test_path: str | list[str] | None = None,
) -> dict:
    """
    Run tests for the project, returning structured results.

    Args:
        workspace_root: Project root.
        test_path: Optional specific test file / directory to run.

    Returns:
        {passed, failed, errors, output, runner_used, returncode, timed_out}
    """
    root = Path(workspace_root).resolve()
    runner = await detect_test_runner(root)

    runner_cmds: dict[str, str] = {
        "pytest": "python -m pytest -q --tb=long --no-header",
        "jest": "npx jest --colors --verbose",
        "vitest": "npx vitest run --reporter verbose",
        "flutter": "flutter test --reporter expanded",
        "cargo": "cargo test -- --test-output immediate",
        "go": "go test ./... -v",
    }

    if runner == "unknown":
        return {
            "passed": 0,
            "failed": 0,
            "errors": ["No test runner detected."],
            "output": "No test runner detected in the selected workspace.",
            "runner_used": "unknown",
            "returncode": -1,
            "timed_out": False,
            "check_passed": False,
            "skipped": True,
            "counts_known": False,
            "command": "",
            "workspace": str(root),
        }

    base_cmd = runner_cmds[runner]
    if test_path:
        raw_paths = [test_path] if isinstance(test_path, str) else test_path
        try:
            test_paths = _workspace_relative_test_paths(root, raw_paths)
        except ValueError as exc:
            return {
                "passed": 0, "failed": 0, "errors": [str(exc)], "output": str(exc),
                "runner_used": runner, "returncode": -1, "timed_out": False,
                "check_passed": False, "skipped": False, "counts_known": False,
                "command": "",
                "workspace": str(root),
            }
        # The paths are selected by the scoped verifier. Quote them as command
        # arguments because run_command executes through the user's shell.
        quoted_paths = (
            subprocess.list2cmdline(test_paths)
            if os.name == "nt"
            else shlex.join(test_paths)
        )
        # Append the test path where appropriate
        if runner in ("pytest",):
            base_cmd += f" {quoted_paths}"
        elif runner in ("jest", "vitest"):
            base_cmd += f" {quoted_paths}"
        elif runner == "go":
            base_cmd = f"go test -v {quoted_paths}"

    raw = await run_command(base_cmd, cwd=root, timeout=180)

    combined = _captured_test_output(raw, base_cmd)
    parsed = parse_test_results(combined, runner)
    parsed["runner_used"] = runner
    parsed["returncode"] = raw["returncode"]
    parsed["timed_out"] = raw["timed_out"]
    parsed["check_passed"] = raw["returncode"] == 0 and not raw["timed_out"]
    parsed["skipped"] = False
    parsed["command"] = base_cmd
    parsed["workspace"] = str(root)
    parsed["runner_error"] = _runner_failed_to_report_results(raw)
    return parsed


# ---------------------------------------------------------------------------
# parse_test_results
# ---------------------------------------------------------------------------
def parse_test_results(output: str, runner: str) -> dict:
    """
    Parse raw test output into a structured result.

    Returns:
        {passed (int), failed (int), errors (list[str]), output (str)}
    """
    passed = 0
    failed = 0
    errors: list[str] = []
    counts_known = False

    if runner == "pytest":
        # pytest can print either "5 passed, 2 failed" or "2 failed, 5 passed".
        passed_matches = list(re.finditer(r"(\d+)\s+passed\b", output, re.IGNORECASE))
        failed_matches = list(re.finditer(r"(\d+)\s+failed\b", output, re.IGNORECASE))
        error_matches = list(re.finditer(r"(\d+)\s+errors?\b", output, re.IGNORECASE))
        if passed_matches:
            passed = int(passed_matches[-1].group(1))
            counts_known = True
        if failed_matches:
            failed = int(failed_matches[-1].group(1))
            counts_known = True
        if error_matches and int(error_matches[-1].group(1)):
            counts_known = True
            for m in re.finditer(r"ERRORS?\s*\n(.+?)(?=\n={3,}|\Z)", output, re.DOTALL):
                errors.append(m.group(1).strip())
        if re.search(r"\bno tests ran\b", output, re.IGNORECASE):
            counts_known = True
        # Collect FAILED lines
        for m in re.finditer(r"^FAILED (.+)$", output, re.MULTILINE):
            errors.append(m.group(1).strip())

    elif runner in ("jest", "vitest"):
        # Jest: "Tests: 2 failed, 10 passed, 12 total"
        m = re.search(r"Tests?:\s*(.*)", output)
        if m:
            counts = m.group(1)
            pm = re.search(r"(\d+) passed", counts)
            fm = re.search(r"(\d+) failed", counts)
            passed = int(pm.group(1)) if pm else 0
            failed = int(fm.group(1)) if fm else 0
            counts_known = bool(pm or fm or re.search(r"\d+ total", counts))
        # Collect FAIL lines
        for m2 in re.finditer(r"^\s*✕\s+(.+)$", output, re.MULTILINE):
            errors.append(m2.group(1).strip())
        for m2 in re.finditer(r"^\s*●\s+(.+)$", output, re.MULTILINE):
            errors.append(m2.group(1).strip())

    elif runner == "flutter":
        # Flutter: "Some tests failed."  or  "All tests passed!"
        pm = re.search(r"(\d+) tests? passed", output, re.IGNORECASE)
        fm = re.search(r"(\d+) tests? failed", output, re.IGNORECASE)
        passed = int(pm.group(1)) if pm else (1 if "All tests passed" in output else 0)
        failed = int(fm.group(1)) if fm else 0
        counts_known = bool(pm or fm or "All tests passed" in output)
        for m in re.finditer(r"FAILED (.+)", output):
            errors.append(m.group(1).strip())

    elif runner == "cargo":
        # "test result: FAILED. 1 passed; 2 failed; 0 ignored"
        m = re.search(r"test result:.*?(\d+) passed.*?(\d+) failed", output, re.IGNORECASE)
        if m:
            passed = int(m.group(1))
            failed = int(m.group(2))
            counts_known = True
        for m2 in re.finditer(r"^FAILED (.+)$", output, re.MULTILINE):
            errors.append(m2.group(1).strip())

    elif runner == "go":
        # "--- FAIL: TestFoo (0.00s)"
        for m in re.finditer(r"--- FAIL: (\S+)", output):
            errors.append(m.group(1))
            failed += 1
        # "--- PASS: TestBar (0.00s)"
        passed = len(re.findall(r"--- PASS:", output))
        # "ok  \tpackage"
        ok_pkgs = len(re.findall(r"^ok\s", output, re.MULTILINE))
        if ok_pkgs and passed == 0:
            passed = ok_pkgs  # rough count
        counts_known = bool(passed or failed or "[no test files]" in output)

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "output": output,
        "counts_known": counts_known,
    }
