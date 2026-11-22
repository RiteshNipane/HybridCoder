"""Pre-write syntax validation and scoped post-change checks."""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Optional

from backend.tools.terminal import run_command

logger = logging.getLogger(__name__)

_PYTHON_SUFFIXES = {".py"}
_TS_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
_CHECK_OUTPUT_LIMIT = 24_000


class SyntaxValidationError(ValueError):
    """Raised when proposed source content cannot be parsed before it is written."""


def _typescript_module(root: Path) -> Optional[Path]:
    for candidate in (
        root / "node_modules" / "typescript",
        root / "frontend" / "node_modules" / "typescript",
    ):
        if (candidate / "lib" / "typescript.js").is_file():
            return candidate
    return None


def _validate_typescript(path: Path, content: str, workspace_root: Path) -> Optional[str]:
    node = shutil.which("node")
    module = _typescript_module(workspace_root)
    if module and node:
        kind = {
            ".ts": "TS",
            ".tsx": "TSX",
            ".js": "JS",
            ".jsx": "JSX",
            ".mjs": "JS",
            ".cjs": "JS",
        }[path.suffix.lower()]
        script = (
            "const fs=require('fs');"
            f"const ts=require({json.dumps(str(module / 'lib' / 'typescript.js'))});"
            f"const file={json.dumps(path.as_posix())};"
            f"const kind=ts.ScriptKind.{kind};"
            "const source=ts.createSourceFile(file,fs.readFileSync(0,'utf8'),"
            "ts.ScriptTarget.Latest,true,kind);"
            "if(source.parseDiagnostics.length){"
            "for(const d of source.parseDiagnostics){"
            "const p=d.file&&d.start!==undefined?d.file.getLineAndCharacterOfPosition(d.start):null;"
            "console.error((p?'line '+(p.line+1)+': ':'')+"
            "ts.flattenDiagnosticMessageText(d.messageText,'\\n'));}process.exit(1);}"
        )
        try:
            result = subprocess.run(
                [node, "-e", script],
                input=content,
                text=True,
                capture_output=True,
                cwd=str(workspace_root),
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"TypeScript parser could not run: {exc}"
        if result.returncode:
            return result.stderr.strip() or result.stdout.strip() or "TypeScript syntax check failed"
        return None

    if path.suffix.lower() in {".js", ".mjs", ".cjs"} and node:
        try:
            with tempfile.TemporaryDirectory(prefix="hybridcoder-syntax-") as temp_dir:
                temp_path = Path(temp_dir) / f"precheck{path.suffix.lower()}"
                temp_path.write_text(content, encoding="utf-8")
                result = subprocess.run(
                    [node, "--check", str(temp_path)], text=True,
                    capture_output=True, cwd=str(workspace_root), timeout=15, check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"JavaScript parser could not run: {exc}"
        if result.returncode:
            return result.stderr.strip() or result.stdout.strip() or "JavaScript syntax check failed"
    return None


def validate_source_text(path: str | Path, content: str, workspace_root: str | Path) -> dict[str, Any]:
    """Parse supported source/config formats without writing proposed content to disk."""
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    try:
        if suffix in _PYTHON_SUFFIXES:
            ast.parse(content, filename=str(source_path))
            return {"checked": True, "parser": "python-ast"}
        if suffix == ".json":
            json.loads(content)
            return {"checked": True, "parser": "json"}
        if suffix == ".toml":
            tomllib.loads(content)
            return {"checked": True, "parser": "tomllib"}
        if suffix in {".xml", ".svg"}:
            ET.fromstring(content)
            return {"checked": True, "parser": "xml"}
        if suffix in _TS_SUFFIXES:
            error = _validate_typescript(source_path, content, Path(workspace_root).resolve())
            if error:
                raise SyntaxValidationError(error)
            parser_available = bool(
                shutil.which("node")
                and (
                    _typescript_module(Path(workspace_root).resolve())
                    or suffix in {".js", ".mjs", ".cjs"}
                )
            )
            return {"checked": parser_available, "parser": "typescript" if parser_available else "unavailable"}
    except SyntaxValidationError:
        raise
    except (SyntaxError, ValueError, ET.ParseError, tomllib.TOMLDecodeError) as exc:
        line = getattr(exc, "lineno", None)
        where = f" at line {line}" if line else ""
        raise SyntaxValidationError(f"Syntax pre-check failed for {source_path}{where}: {exc}") from exc
    return {"checked": False, "parser": "unsupported"}


async def validate_source_text_async(
    path: str | Path, content: str, workspace_root: str | Path
) -> dict[str, Any]:
    return await asyncio.to_thread(validate_source_text, path, content, workspace_root)


def _test_dirs(root: Path, changed: Iterable[Path]) -> list[Path]:
    dirs: list[Path] = []
    for changed_path in changed:
        parts = changed_path.parts
        candidates = [
            changed_path.parent / "tests",
            changed_path.parent / "test",
            changed_path.parent / "__tests__",
            root / "tests",
            root / "test",
        ]
        if parts and parts[0] in {"backend", "frontend", "src", "app"}:
            candidates.extend((root / parts[0] / "tests", root / parts[0] / "test", root / parts[0] / "__tests__"))
        for candidate in candidates:
            if candidate.is_dir() and candidate not in dirs:
                dirs.append(candidate)
    return dirs


def _find_scoped_tests(root: Path, changed: list[Path]) -> list[str]:
    tests: set[str] = set()
    test_suffixes = (".test.ts", ".test.tsx", ".test.js", ".test.jsx", ".spec.ts", ".spec.tsx", ".spec.js", ".spec.jsx")
    for changed_path in changed:
        name = changed_path.name.lower()
        if name.startswith("test_") or name.endswith("_test.py") or name.endswith(test_suffixes):
            if (root / changed_path).is_file():
                tests.add(changed_path.as_posix())
            continue
        stem = changed_path.stem.lower()
        candidates = {f"test_{stem}.py", f"{stem}_test.py"}
        candidates.update(f"{stem}{suffix}" for suffix in test_suffixes)
        for test_dir in _test_dirs(root, [changed_path]):
            for candidate in candidates:
                for match in test_dir.rglob(candidate):
                    if not any(part in {"node_modules", ".venv", "venv", ".git", "dist"} for part in match.parts):
                        tests.add(match.relative_to(root).as_posix())

    if tests:
        return sorted(tests)

    # If a source file has no matching test, use the nearest test directory as
    # the regression scope rather than running unrelated suites in the repo.
    for test_dir in _test_dirs(root, changed):
        matches = [
            path for path in test_dir.rglob("*")
            if path.is_file()
            and (path.name.startswith("test_") or path.name.endswith("_test.py") or path.name.endswith(test_suffixes))
            and not any(part in {"node_modules", ".venv", "venv", ".git", "dist"} for part in path.parts)
        ]
        if matches:
            return sorted(path.relative_to(root).as_posix() for path in matches)
    return []


async def run_scoped_checks(workspace_root: str | Path, changed_paths: Iterable[str]) -> dict[str, Any]:
    """Run the narrowest available tests or compiler check for changed files."""
    root = Path(workspace_root).resolve()
    normalized: list[Path] = []
    for raw in changed_paths:
        try:
            candidate = Path(raw)
            full = (candidate if candidate.is_absolute() else root / candidate).resolve()
            rel = full.relative_to(root)
        except (OSError, ValueError):
            continue
        if rel not in normalized:
            normalized.append(rel)

    if not normalized:
        return {
            "passed": 0, "failed": 0, "check_passed": True,
            "skipped": True, "runner_used": "none",
            "command": "", "output": "No changed source files were available to check.",
            "passed_count": 0, "failed_count": 0, "returncode": 0,
            "counts_known": False,
            "workspace": str(root),
        }

    python_files = [path for path in normalized if path.suffix.lower() == ".py"]
    js_files = [path for path in normalized if path.suffix.lower() in _TS_SUFFIXES]
    scoped_tests = _find_scoped_tests(root, normalized)
    checks: list[dict[str, Any]] = []
    python_tests = [path for path in scoped_tests if path.lower().endswith(".py")]

    if python_files and python_tests:
        from backend.tools.testing import run_project_tests

        result = await run_project_tests(root, test_path=python_tests)
        unavailable = (
            result.get("runner_used") == "unknown"
            or "no module named pytest" in str(result.get("output", "")).lower()
        )
        result["skipped"] = unavailable or bool(result.get("runner_error"))
        result["check_passed"] = unavailable or (
            result.get("returncode", -1) == 0 and not result.get("timed_out", False)
        )
        result["command"] = "python -m pytest -q --tb=long " + " ".join(python_tests)
        result["output"] = str(result.get("output", ""))[-_CHECK_OUTPUT_LIMIT:]
        checks.append(result)
    elif python_files and shutil.which("ruff"):
        ruff = shutil.which("ruff")
        args = [str(root / path) for path in python_files if (root / path).is_file()]
        if args:
            argv = [ruff, "check", "--output-format", "concise", *args]
            command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
            raw = await run_command(command, cwd=root, timeout=120)
            returncode = raw["returncode"]
            output = (raw["stdout"] or "") + "\n" + (raw["stderr"] or "")
            timed_out = raw["timed_out"]
            checks.append({
                "passed": 1 if returncode == 0 else 0,
                "failed": 0 if returncode == 0 else 1,
                "check_passed": returncode == 0,
                "skipped": False,
                "runner_used": "ruff",
                "command": "ruff check --output-format concise <changed Python files>",
                "output": output[-_CHECK_OUTPUT_LIMIT:],
                "returncode": returncode,
                "timed_out": timed_out,
                "counts_known": True,
            })

    if js_files:
        package_root = root / "frontend" if any(path.parts and path.parts[0] == "frontend" for path in js_files) and (root / "frontend" / "package.json").is_file() else root
        package_path = package_root / "package.json"
        if package_path.is_file():
            try:
                package_data = json.loads(package_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                package_data = {}
            build_script = package_data.get("scripts", {}).get("build")
            if build_script and shutil.which("npm"):
                prefix = package_root.relative_to(root).as_posix() or "."
                command = f"npm run build --prefix {prefix}"
                raw = await run_command(command, cwd=root, timeout=240)
                checks.append({
                    "passed": 1 if raw["returncode"] == 0 and not raw["timed_out"] else 0,
                    "failed": 0 if raw["returncode"] == 0 and not raw["timed_out"] else 1,
                    "check_passed": raw["returncode"] == 0 and not raw["timed_out"],
                    "skipped": False,
                    "runner_used": "frontend-build",
                    "command": command,
                    "output": (raw["stdout"] + "\n" + raw["stderr"])[-_CHECK_OUTPUT_LIMIT:],
                    "passed_count": 0,
                    "failed_count": 0 if raw["returncode"] == 0 else 1,
                    "returncode": raw["returncode"],
                    "timed_out": raw["timed_out"],
                    "counts_known": True,
                })

    if not checks and scoped_tests:
        from backend.tools.testing import run_project_tests

        result = await run_project_tests(root, test_path=scoped_tests)
        unavailable = (
            result.get("runner_used") == "unknown"
            or "no module named pytest" in str(result.get("output", "")).lower()
        )
        result["check_passed"] = unavailable or (
            result.get("returncode", -1) == 0 and not result.get("timed_out", False)
        )
        result["skipped"] = unavailable or bool(result.get("runner_error"))
        result["command"] = f"{result.get('runner_used', 'test')} " + " ".join(scoped_tests)
        result["output"] = str(result.get("output", ""))[-_CHECK_OUTPUT_LIMIT:]
        checks.append(result)

    if checks:
        failed_codes = [
            check.get("returncode") for check in checks
            if not check.get("check_passed") and isinstance(check.get("returncode"), int)
            and check.get("returncode") != 0
        ]
        check_passed = all(bool(check.get("check_passed")) for check in checks)
        return {
            "passed": sum(int(check.get("passed", 0) or 0) for check in checks),
            "failed": sum(int(check.get("failed", 0) or 0) for check in checks),
            "check_passed": check_passed,
            "skipped": all(bool(check.get("skipped")) for check in checks),
            "runner_used": " + ".join(str(check.get("runner_used", "unknown")) for check in checks),
            "command": " && ".join(str(check.get("command", "")) for check in checks),
            "output": "\n\n".join(
                f"[{check.get('runner_used', 'check')}]\n{check.get('output', '')}" for check in checks
            )[-_CHECK_OUTPUT_LIMIT:],
            "passed_count": sum(int(check.get("passed", 0) or 0) for check in checks),
            "failed_count": sum(int(check.get("failed", 0) or 0) for check in checks),
            "returncode": 0 if check_passed else (failed_codes[0] if failed_codes else 1),
            "timed_out": any(bool(check.get("timed_out")) for check in checks),
            "counts_known": all(bool(check.get("counts_known", True)) for check in checks),
            "runner_error": any(bool(check.get("runner_error")) for check in checks),
            "workspace": str(root),
            "checks": [
                {
                    "runner_used": check.get("runner_used"),
                    "check_passed": check.get("check_passed"),
                    "skipped": check.get("skipped", False),
                    "returncode": check.get("returncode"),
                    "runner_error": check.get("runner_error", False),
                }
                for check in checks
            ],
        }

    return {
        "passed": 0, "failed": 0, "check_passed": True,
        "skipped": True, "runner_used": "syntax-only",
        "command": "pre-write parser checks",
        "output": "No matching scoped tests or linter are configured; pre-write syntax checks were used where supported.",
        "passed_count": 0, "failed_count": 0, "returncode": 0, "timed_out": False,
        "counts_known": False,
        "workspace": str(root),
    }
