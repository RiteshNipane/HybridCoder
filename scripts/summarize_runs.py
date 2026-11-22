"""Print a Markdown summary of recent local task telemetry."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG = ROOT / "logs" / "task_runs.jsonl"


def load_records(path: Path, limit: int) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
    return records[-limit:] if limit > 0 else records


def check_counts(records: list[dict], field: str, benchmark_field: str | None = None) -> tuple[int, int, int]:
    passed = failed = skipped = 0
    for record in records:
        result = record.get(benchmark_field) if benchmark_field and record.get(benchmark_field) else record.get(field)
        if not isinstance(result, dict):
            continue
        if result.get("skipped"):
            skipped += 1
        elif result.get("check_passed"):
            passed += 1
        else:
            failed += 1
    return passed, failed, skipped


def summarize(records: list[dict]) -> str:
    if not records:
        return "No task runs recorded yet. Run a coding task, then rerun this command."

    # Benchmark harnesses can measure the full cloud-plan + local-worker
    # request. Prefer that end-to-end duration; otherwise use backend timing.
    durations = [
        float(r.get("end_to_end_duration_ms", r.get("duration_ms", 0)) or 0)
        for r in records
    ]
    prompt_tokens = sum(int(r.get("prompt_tokens", 0) or 0) for r in records)
    completion_tokens = sum(int(r.get("completion_tokens", 0) or 0) for r in records)
    escalations = sum(int(r.get("escalation_count", 0) or 0) for r in records)
    before = check_counts(records, "test_before", "benchmark_test_before")
    after = check_counts(records, "test_after", "benchmark_test_after")
    routes: dict[str, int] = {}
    for record in records:
        route = str(record.get("route", "unknown"))
        routes[route] = routes.get(route, 0) + 1
    route_text = ", ".join(f"{name}: {count}" for name, count in sorted(routes.items()))

    lines = [
        "| Measure | Recorded result |",
        "|---|---:|",
        f"| Tasks in sample | {len(records)} |",
        f"| Routes | {route_text} |",
        f"| Escalations | {escalations} |",
        f"| Average duration | {mean(durations):.0f} ms |",
        f"| Prompt tokens reported | {prompt_tokens} |",
        f"| Completion tokens reported | {completion_tokens} |",
        f"| Baseline checks (pass / fail / skipped) | {before[0]} / {before[1]} / {before[2]} |",
        f"| Final checks (pass / fail / skipped) | {after[0]} / {after[1]} / {after[2]} |",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--last", type=int, default=20, help="summarize the most recent N runs (default: 20)")
    parser.add_argument("--file", type=Path, default=DEFAULT_LOG, help="JSONL telemetry path")
    args = parser.parse_args()
    print(summarize(load_records(args.file, args.last)))


if __name__ == "__main__":
    main()
