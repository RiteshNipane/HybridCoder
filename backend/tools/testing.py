"""
testing.py - Test runner detection, execution, and result parsing.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from backend.tools.terminal import run_command

logger = logging.getLogger(__name__)

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
        "conftest.py", "tox.ini",
    ):
        return "pytest"

    return "unknown"


# ---------------------------------------------------------------------------
# run_project_tests
# ---------------------------------------------------------------------------
async def run_project_tests(
    workspace_root: str | Path,
    test_path: str | None = None,
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
        "pytest": "python -m pytest -v --tb=short --no-header",
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
            "output": "",
            "runner_used": "unknown",
            "returncode": -1,
            "timed_out": False,
        }

    base_cmd = runner_cmds[runner]
    if test_path:
        # Append the test path where appropriate
        if runner in ("pytest",):
            base_cmd += f" {test_path}"
        elif runner in ("jest", "vitest"):
            base_cmd += f" {test_path}"
        elif runner == "go":
            base_cmd = f"go test -v {test_path}"

    raw = await run_command(base_cmd, cwd=root, timeout=180)

    combined = raw["stdout"] + "\n" + raw["stderr"]
    parsed = parse_test_results(combined, runner)
    parsed["runner_used"] = runner
    parsed["returncode"] = raw["returncode"]
    parsed["timed_out"] = raw["timed_out"]
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

    if runner == "pytest":
        # e.g. "5 passed, 2 failed, 1 error in 3.14s"
        summary_re = re.search(
            r"(?:(\d+) passed)?[,\s]*(?:(\d+) failed)?[,\s]*(?:(\d+) error)?",
            output,
            re.IGNORECASE,
        )
        if summary_re:
            passed = int(summary_re.group(1) or 0)
            failed = int(summary_re.group(2) or 0)
            err_count = int(summary_re.group(3) or 0)
            if err_count:
                # Collect ERRORS sections
                for m in re.finditer(r"ERRORS?\s*\n(.+?)(?=\n={3,}|\Z)", output, re.DOTALL):
                    errors.append(m.group(1).strip())
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
        for m in re.finditer(r"FAILED (.+)", output):
            errors.append(m.group(1).strip())

    elif runner == "cargo":
        # "test result: FAILED. 1 passed; 2 failed; 0 ignored"
        m = re.search(r"test result:.*?(\d+) passed.*?(\d+) failed", output, re.IGNORECASE)
        if m:
            passed = int(m.group(1))
            failed = int(m.group(2))
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

    return {
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "output": output,
    }
