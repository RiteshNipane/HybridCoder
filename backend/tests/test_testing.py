from pathlib import Path

import pytest

from backend.tools.testing import (
    _captured_test_output,
    _runner_failed_to_report_results,
    extract_test_paths,
    parse_test_results,
)
from backend.agents.self_correction import _check_snapshot


def test_extract_test_paths_keeps_paths_relative_to_workspace(tmp_path: Path):
    assert extract_test_paths(
        "Execute pytest on tests/test_ship.py",
        tmp_path,
    ) == ["tests/test_ship.py"]


def test_extract_test_paths_rejects_paths_outside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "outside_test.py"
    prompt = f"Run pytest on {outside}"
    with pytest.raises(ValueError, match="outside the selected workspace"):
        extract_test_paths(prompt, tmp_path)


def test_pytest_parser_marks_missing_counts_as_unknown():
    result = parse_test_results(
        "ERROR: file or directory not found: tests/test_ship.py\nno tests ran in 0.01s",
        "pytest",
    )
    assert result["passed"] == 0
    assert result["failed"] == 0
    assert result["counts_known"] is True


def test_pytest_parser_marks_command_errors_without_summary_as_unknown():
    result = parse_test_results("ModuleNotFoundError: No module named 'pytest'", "pytest")
    assert result["counts_known"] is False


def test_silent_runner_start_failure_gets_a_diagnostic():
    message = _captured_test_output(
        {"stdout": "", "stderr": "", "returncode": -1, "launch_error": True},
        "python -m pytest tests/test_ship.py",
    )
    assert "Could not start test command" in message
    assert "pytest" in message


def test_silent_nonzero_runner_exit_keeps_the_real_exit_code():
    raw = {"stdout": "", "stderr": "", "returncode": 4}
    message = _captured_test_output(raw, "python -m pytest tests/test_ship.py")
    assert "exited with code 4" in message
    assert _runner_failed_to_report_results(raw)


@pytest.mark.parametrize("runner", ["implementation", "patch-validation"])
def test_non_test_failures_are_not_counted_as_test_results(runner):
    assert _check_snapshot({
        "runner_used": runner,
        "check_passed": False,
        "passed": 0,
        "failed": 0,
    }) is None
