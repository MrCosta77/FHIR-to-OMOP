"""Merge disjoint DQD result shards into one complete, auditable JSON report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CATEGORIES = ("Plausibility", "Conformance", "Completeness")


def _scalar(value):
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def _overview(checks: list[dict]) -> dict[str, list[int | float]]:
    errors = [check for check in checks if int(check.get("isError") or 0) == 1]
    failed = [check for check in checks if int(check.get("failed") or 0) == 1]
    passed = [
        check for check in checks
        if int(check.get("isError") or 0) == 0
        and int(check.get("failed") or 0) == 0
    ]
    total = len(checks)
    result = {
        "countTotal": [total],
        "countPassed": [len(passed)],
        "countErrorFailed": [len(errors)],
        "countThresholdFailed": [len(failed)],
        "countOverallFailed": [len(failed)],
        "percentPassed": [round(100 * len(passed) / total, 2)],
        "percentFailed": [round(100 * len(failed) / total, 2)],
    }
    for category in CATEGORIES:
        category_checks = [c for c in checks if c.get("category") == category]
        category_failed = [c for c in category_checks if int(c.get("failed") or 0) == 1]
        category_passed = [
            c for c in category_checks
            if int(c.get("failed") or 0) == 0
            and int(c.get("isError") or 0) == 0
        ]
        result[f"countTotal{category}"] = [len(category_checks)]
        result[f"countFailed{category}"] = [len(category_failed)]
        result[f"countPassed{category}"] = [len(category_passed)]
    return result


def merge_dqd_results(shards: list[dict]) -> dict:
    if len(shards) < 2:
        raise ValueError("At least two DQD shards are required")
    metadata = shards[0].get("Metadata")
    checks = []
    seen = set()
    for shard in shards:
        if shard.get("Metadata") != metadata:
            raise ValueError("DQD shards have different CDM metadata")
        shard_checks = shard.get("CheckResults")
        if not isinstance(shard_checks, list) or not shard_checks:
            raise ValueError("DQD shard has no CheckResults")
        for check in shard_checks:
            check_id = check.get("checkId")
            if not check_id or check_id in seen:
                raise ValueError(f"Duplicate or missing DQD checkId: {check_id}")
            seen.add(check_id)
            checks.append(check)

    checks.sort(key=lambda check: (check.get("_row", 0), check["checkId"]))
    starts = [str(_scalar(shard.get("startTimestamp"))) for shard in shards]
    ends = [str(_scalar(shard.get("endTimestamp"))) for shard in shards]
    seconds = sum(
        float(_scalar(shard.get("executionTimeSeconds")) or 0)
        for shard in shards
    )
    return {
        "startTimestamp": [min(starts)],
        "endTimestamp": [max(ends)],
        "executionTime": [f"{seconds:.2f} secs across {len(shards)} isolated processes"],
        "executionTimeSeconds": [seconds],
        "CheckResults": checks,
        "Metadata": metadata,
        "Overview": _overview(checks),
    }


def merge_dqd_files(paths: list[Path], output: Path) -> Path:
    shards = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    merged = merge_dqd_results(shards)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("shards", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    path = merge_dqd_files(args.shards, args.output)
    print(f"Combined DQD report written to: {path}")


if __name__ == "__main__":
    main()
