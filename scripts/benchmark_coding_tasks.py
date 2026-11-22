"""Run the HybridCoder cloud-planner + local-worker 20-task benchmark.

Each task gets a fresh temporary Python workspace and a deliberately failing
pytest suite. The normal HybridCoder orchestrator plans with NVIDIA (Gemini
fallback), implements with local Ollama/Qwen, and runs its scoped verifier.
Aggregate request telemetry and per-case outcomes are written under logs/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from textwrap import dedent
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOGS = ROOT / "logs"
TELEMETRY_FILE = LOGS / "benchmark_20_latest.jsonl"
CASES_FILE = LOGS / "benchmark_20_cases_latest.jsonl"


@dataclass(frozen=True)
class BenchmarkCase:
    slug: str
    title: str
    prompt: str
    source: str
    tests: str


def case(slug: str, title: str, function: str, requirements: str, source: str, tests: str) -> BenchmarkCase:
    prompt = (
        f"/task Update feature.py to implement {function} as follows: {requirements} "
        "Preserve the current function signature, change only feature.py, and do not edit the tests."
    )
    return BenchmarkCase(slug, title, prompt, dedent(source).lstrip(), dedent(tests).lstrip())


CASES = [
    case("clamp", "Clamp a value to bounds", "clamp(value, lower, upper)",
         "return the input constrained inclusively between lower and upper.",
         """\
         def clamp(value, lower, upper):
             return value
         """, """\
         from feature import clamp

         def test_clamp_inside_and_at_bounds():
             assert clamp(5, 0, 10) == 5
             assert clamp(-2, 0, 10) == 0
             assert clamp(12, 0, 10) == 10
             assert clamp(0, 0, 10) == 0
         """),
    case("normalize_email", "Normalize an email address", "normalize_email(email)",
         "strip surrounding whitespace and lowercase the address.",
         """\
         def normalize_email(email):
             return email
         """, """\
         from feature import normalize_email

         def test_normalize_email():
             assert normalize_email("  User.Name@Example.COM ") == "user.name@example.com"
             assert normalize_email("already@example.com") == "already@example.com"
         """),
    case("stable_unique", "Remove duplicates while preserving order", "remove_duplicates(items)",
         "return each value once, keeping the order of its first occurrence.",
         """\
         def remove_duplicates(items):
             return list(items)
         """, """\
         from feature import remove_duplicates

         def test_remove_duplicates_keeps_first_occurrence_order():
             assert remove_duplicates([3, 1, 3, 2, 1]) == [3, 1, 2]
             assert remove_duplicates([]) == []
         """),
    case("safe_divide", "Handle division by zero", "safe_divide(numerator, denominator)",
         "return None when denominator is zero; otherwise return the quotient.",
         """\
         def safe_divide(numerator, denominator):
             return numerator / denominator
         """, """\
         from feature import safe_divide

         def test_safe_divide():
             assert safe_divide(9, 3) == 3
             assert safe_divide(1, 0) is None
             assert safe_divide(-5, 2) == -2.5
         """),
    case("palindrome", "Check normalized palindromes", "is_palindrome(text)",
         "ignore case and non-alphanumeric characters before comparing the text in reverse.",
         """\
         def is_palindrome(text):
             return text == text[::-1]
         """, """\
         from feature import is_palindrome

         def test_is_palindrome_ignores_case_and_punctuation():
             assert is_palindrome("A man, a plan, a canal: Panama!") is True
             assert is_palindrome("race a car") is False
             assert is_palindrome("") is True
         """),
    case("discount", "Validate and apply a discount", "calculate_discount(price, percent)",
         "accept percentages from 0 through 100, return the discounted price, and raise ValueError otherwise.",
         """\
         def calculate_discount(price, percent):
             return price
         """, """\
         import pytest
         from feature import calculate_discount

         def test_calculate_discount_and_range_validation():
             assert calculate_discount(100, 20) == 80
             assert calculate_discount(12.5, 0) == 12.5
             assert calculate_discount(80, 100) == 0
             with pytest.raises(ValueError):
                 calculate_discount(10, 101)
             with pytest.raises(ValueError):
                 calculate_discount(10, -1)
         """),
    case("slugify", "Create a readable slug", "slugify(title)",
         "lowercase the text, replace each run of non-alphanumeric characters with one hyphen, and trim edge hyphens.",
         """\
         def slugify(title):
             return title
         """, """\
         from feature import slugify

         def test_slugify_collapses_punctuation_and_spaces():
             assert slugify("  Hello, World!  ") == "hello-world"
             assert slugify("A---B__C") == "a-b-c"
             assert slugify("Already-clean") == "already-clean"
         """),
    case("average", "Average a collection", "average(numbers)",
         "return the arithmetic mean, or None for an empty iterable.",
         """\
         def average(numbers):
             values = list(numbers)
             return sum(values) / len(values) if values else 0
         """, """\
         from feature import average

         def test_average_for_values_and_empty_iterable():
             assert average([2, 4, 6]) == 4
             assert average(iter([1, 2])) == 1.5
             assert average([]) is None
         """),
    case("fibonacci", "Return a Fibonacci number", "fibonacci(n)",
         "use zero-based indexing (F0=0, F1=1) and raise ValueError for negative n.",
         """\
         def fibonacci(n):
             return n
         """, """\
         import pytest
         from feature import fibonacci

         def test_fibonacci_sequence_and_negative_input():
             assert [fibonacci(n) for n in range(8)] == [0, 1, 1, 2, 3, 5, 8, 13]
             with pytest.raises(ValueError):
                 fibonacci(-1)
         """),
    case("word_count", "Count whitespace-separated words", "count_words(text)",
         "count whitespace-separated words and return zero for empty or whitespace-only text.",
         """\
         def count_words(text):
             return len(text)
         """, """\
         from feature import count_words

         def test_count_words_handles_whitespace():
             assert count_words("one two   three\\nfour") == 4
             assert count_words("") == 0
             assert count_words("   ") == 0
         """),
    case("adults", "Filter adults from records", "filter_adults(people, minimum_age=18)",
         "return people whose age is at least minimum_age while preserving input order.",
         """\
         def filter_adults(people, minimum_age=18):
             return list(people)
         """, """\
         from feature import filter_adults

         def test_filter_adults_preserves_order_and_threshold():
             people = [{"name": "A", "age": 17}, {"name": "B", "age": 18}, {"name": "C", "age": 30}]
             assert filter_adults(people) == people[1:]
             assert filter_adults(people, 30) == people[2:]
         """),
    case("currency", "Format currency with two decimals", "format_currency(amount)",
         "format a number with a dollar sign, comma thousands separators, and exactly two decimal places.",
         """\
         def format_currency(amount):
             return str(amount)
         """, """\
         from feature import format_currency

         def test_format_currency():
             assert format_currency(1234.5) == "$1,234.50"
             assert format_currency(-2) == "-$2.00"
             assert format_currency(0) == "$0.00"
         """),
    case("parse_bool", "Parse common boolean values", "parse_bool(value)",
         "accept bools, whitespace-trimmed case-insensitive true/false strings, and integers 1/0; raise ValueError for anything else.",
         """\
         def parse_bool(value):
             return bool(value)
         """, """\
         import pytest
         from feature import parse_bool

         def test_parse_bool_supported_values_and_invalid_input():
             assert parse_bool(True) is True
             assert parse_bool(" TrUe ") is True
             assert parse_bool(" FALSE ") is False
             assert parse_bool(0) is False
             with pytest.raises(ValueError):
                 parse_bool("perhaps")
         """),
    case("unique_sorted", "Sort values and remove duplicates", "unique_sorted(numbers)",
         "return the distinct input numbers in ascending order.",
         """\
         def unique_sorted(numbers):
             return list(numbers)
         """, """\
         from feature import unique_sorted

         def test_unique_sorted():
             assert unique_sorted([3, 1, 3, 2]) == [1, 2, 3]
             assert unique_sorted([]) == []
         """),
    case("nested_get", "Read a dotted dictionary path", "get_nested(data, path, default=None)",
         "return the value found by walking dot-separated dictionary keys, or default if a key is missing.",
         """\
         def get_nested(data, path, default=None):
             return data.get(path, default)
         """, """\
         from feature import get_nested

         def test_get_nested_reads_and_defaults():
             data = {"user": {"profile": {"name": "Riya"}}}
             assert get_nested(data, "user.profile.name") == "Riya"
             assert get_nested(data, "user.profile.age", 18) == 18
             assert get_nested(data, "") == data
         """),
    case("chunk", "Split an iterable into chunks", "chunk(items, size)",
         "return a list of lists no larger than size, keeping order, and raise ValueError if size is not positive.",
         """\
         def chunk(items, size):
             return [list(items)]
         """, """\
         import pytest
         from feature import chunk

         def test_chunk_and_invalid_size():
             assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
             assert chunk([], 3) == []
             with pytest.raises(ValueError):
                 chunk([1], 0)
         """),
    case("second_largest", "Find the second distinct maximum", "second_largest(numbers)",
         "return the second-largest distinct value, or None if fewer than two distinct values exist.",
         """\
         def second_largest(numbers):
             values = list(numbers)
             return sorted(values)[-2] if len(values) > 1 else None
         """, """\
         from feature import second_largest

         def test_second_largest_distinct_value():
             assert second_largest([4, 1, 4, 3]) == 3
             assert second_largest([5, 5]) is None
             assert second_largest([8]) is None
         """),
    case("shipping", "Calculate flat-rate shipping", "calculate_shipping(subtotal)",
         "return 5.99 below a subtotal of 50 and zero at or above 50; raise ValueError for negative subtotals.",
         """\
         def calculate_shipping(subtotal):
             return 5.99
         """, """\
         import pytest
         from feature import calculate_shipping

         def test_calculate_shipping_threshold_and_negative_value():
             assert calculate_shipping(49.99) == 5.99
             assert calculate_shipping(50) == 0
             assert calculate_shipping(100) == 0
             with pytest.raises(ValueError):
                 calculate_shipping(-1)
         """),
    case("hashtags", "Extract hashtags from text", "extract_hashtags(text)",
         "return hashtag names without #, lowercased, in order, matching letters, digits, and underscores.",
         """\
         def extract_hashtags(text):
             return []
         """, """\
         from feature import extract_hashtags

         def test_extract_hashtags():
             assert extract_hashtags("Hi #Python and #AI_2026, #python!") == ["python", "ai_2026", "python"]
             assert extract_hashtags("no tags") == []
         """),
    case("password", "Validate a basic password", "validate_password(password)",
         "return true only when the password has at least 8 characters and at least one digit.",
         """\
         def validate_password(password):
             return len(password) >= 8
         """, """\
         from feature import validate_password

         def test_validate_password_length_and_digit():
             assert validate_password("secret123") is True
             assert validate_password("short1") is False
             assert validate_password("longpassword") is False
         """),
]


def _run_pytest(workspace: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short", "tests/test_feature.py"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=90,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        passed_match = re.search(r"(\d+) passed\b", output)
        failed_match = re.search(r"(\d+) failed\b", output)
        passed = int(passed_match.group(1)) if passed_match else 0
        failed = int(failed_match.group(1)) if failed_match else 0
        if result.returncode != 0 and not (passed_match or failed_match):
            failed = 1
        return {
            "returncode": result.returncode,
            "passed": passed,
            "failed": failed,
            "output": output[-3000:],
        }
    except Exception as exc:
        return {"returncode": -1, "passed": 0, "failed": 0, "output": f"{type(exc).__name__}: {exc}"}


def _write_case(workspace: Path, item: BenchmarkCase) -> None:
    (workspace / "tests").mkdir(parents=True)
    (workspace / "feature.py").write_text(item.source, encoding="utf-8")
    (workspace / "tests" / "test_feature.py").write_text(item.tests, encoding="utf-8")


def _record_count(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.open("r", encoding="utf-8") if line.strip())


def _annotate_benchmark_results(path: Path, previous_count: int, result: dict[str, Any]) -> bool:
    """Attach harness wall time and independent test results to this run's telemetry."""
    if _record_count(path) <= previous_count:
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[-1])
    record["end_to_end_duration_ms"] = result["duration_ms"]
    for source, target in (("baseline", "benchmark_test_before"), ("final_test", "benchmark_test_after")):
        check = result[source]
        record[target] = {
            "runner_used": "pytest",
            "command": "python -m pytest -q --tb=short tests/test_feature.py",
            "check_passed": check["returncode"] == 0,
            "skipped": check["returncode"] < 0,
            "passed": int(check.get("passed", 0)),
            "failed": int(check.get("failed", 0)),
            "counts_known": check["returncode"] >= 0,
            "returncode": check["returncode"],
        }
    lines[-1] = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return True


async def run_case(orchestrator: Any, item: BenchmarkCase, workspace: Path) -> dict[str, Any]:
    from backend import activity
    from backend.config import RoutingMode

    await orchestrator.set_workspace(workspace)
    baseline = await asyncio.to_thread(_run_pytest, workspace)
    events: list[dict[str, Any]] = []
    provider_events: list[dict[str, Any]] = []
    token = activity.set_listener(provider_events.append)
    started = time.perf_counter()
    try:
        async for event in orchestrator.handle_user_message_stream(
            prompt=item.prompt,
            conversation_history=[],
            mode_override=RoutingMode.NVIDIA_LOCAL,
        ):
            events.append(event)
    except Exception as exc:
        events.append({"type": "harness_error", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        activity.reset_listener(token)
    duration_ms = round((time.perf_counter() - started) * 1000)
    final_test = await asyncio.to_thread(_run_pytest, workspace)
    check_events = [event for event in events if event.get("type") in ("test", "validation")]
    checked = check_events[-1].get("results", {}) if check_events else {}
    changed_paths = sorted({
        path
        for event in events if event.get("type") == "edit"
        for path in event.get("files", [])
    })
    cloud_models = sorted({
        f"{event.get('provider')}:{event.get('model')}"
        for event in provider_events
        if event.get("provider") in {"nvidia", "gemini"} and event.get("state") == "done"
    })
    local_models = sorted({
        f"{event.get('provider')}:{event.get('model')}"
        for event in provider_events
        if event.get("provider") == "ollama" and event.get("state") == "done"
    })
    hybrid_used = bool(cloud_models and local_models)
    success = bool(
        baseline["returncode"] != 0
        and final_test["returncode"] == 0
        and checked.get("check_passed")
        and "feature.py" in changed_paths
        and hybrid_used
    )
    result = {
        "slug": item.slug,
        "title": item.title,
        "mode": "NVIDIA+LOCAL",
        "success": success,
        "hybrid_used": hybrid_used,
        "cloud_models": cloud_models,
        "local_models": local_models,
        "changed_files": changed_paths,
        "baseline": baseline,
        "scoped_check": {
            "passed": bool(checked.get("check_passed")),
            "runner": checked.get("runner_used"),
            "command": checked.get("command"),
            "passed_count": checked.get("passed"),
            "failed_count": checked.get("failed"),
            "output": checked.get("output", "")[-2000:] if isinstance(checked.get("output"), str) else "",
        },
        "final_test": final_test,
        "duration_ms": duration_ms,
        "errors": [event.get("error") for event in events if event.get("type") in {"error", "harness_error"}],
        "status_messages": [event.get("message", "") for event in events if event.get("type") == "status"],
    }
    return result


async def run(limit: int, selected_cases: list[BenchmarkCase] | None = None) -> int:
    # Let shell-based verification find this project's pytest and dependencies.
    venv_scripts = str(Path(sys.executable).parent)
    os.environ["PATH"] = venv_scripts + os.pathsep + os.environ.get("PATH", "")
    os.environ["HYBRIDCODER_TELEMETRY_PATH"] = str(TELEMETRY_FILE)

    from backend.config import get_config
    from backend.orchestrator import CodingOrchestrator
    from backend.storage.usage_tracker import UsageTracker

    LOGS.mkdir(exist_ok=True)
    TELEMETRY_FILE.unlink(missing_ok=True)
    CASES_FILE.unlink(missing_ok=True)
    config = get_config()
    tracker = UsageTracker(config)
    await tracker.initialise()
    orchestrator = CodingOrchestrator(config=config, usage_tracker=tracker)
    selected = selected_cases if selected_cases is not None else CASES[:limit]
    passed = 0
    run_id = datetime.now().astimezone().isoformat(timespec="seconds")
    with tempfile.TemporaryDirectory(prefix="hybridcoder-benchmark-20-") as temp_root:
        root = Path(temp_root)
        for index, item in enumerate(selected, start=1):
            workspace = root / f"{index:02d}-{item.slug}"
            workspace.mkdir()
            _write_case(workspace, item)
            print(f"[{index:02d}/{len(selected):02d}] {item.title}: running NVIDIA+LOCAL", flush=True)
            telemetry_before = _record_count(TELEMETRY_FILE)
            result = await run_case(orchestrator, item, workspace)
            result["telemetry_end_to_end_duration_recorded"] = _annotate_benchmark_results(
                TELEMETRY_FILE, telemetry_before, result
            )
            result["run_id"] = run_id
            with CASES_FILE.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + "\n")
            passed += int(result["success"])
            cloud = ", ".join(result["cloud_models"]) or "none"
            local = ", ".join(result["local_models"]) or "none"
            print(
                f"  {'PASS' if result['success'] else 'FAIL'}; hybrid={result['hybrid_used']}; "
                f"cloud={cloud}; local={local}; pytest={result['final_test']['passed']} passed/"
                f"{result['final_test']['failed']} failed; {result['duration_ms']} ms",
                flush=True,
            )
    print(f"Completed {len(selected)} tasks: {passed} passed, {len(selected) - passed} failed.")
    print(f"Telemetry: {TELEMETRY_FILE.relative_to(ROOT)}")
    print(f"Case results: {CASES_FILE.relative_to(ROOT)}")
    return 0 if passed == len(selected) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=20, help="run the first N tasks, from 1 to 20")
    parser.add_argument("--case", choices=[item.slug for item in CASES], help="run one named case")
    args = parser.parse_args()
    if not 1 <= args.limit <= len(CASES):
        parser.error(f"--limit must be between 1 and {len(CASES)}")
    selected = None
    if args.case:
        global TELEMETRY_FILE, CASES_FILE
        TELEMETRY_FILE = LOGS / f"benchmark_case_{args.case}_telemetry.jsonl"
        CASES_FILE = LOGS / f"benchmark_case_{args.case}_results.jsonl"
        selected = [next(item for item in CASES if item.slug == args.case)]
    raise SystemExit(asyncio.run(run(args.limit, selected)))


if __name__ == "__main__":
    main()
