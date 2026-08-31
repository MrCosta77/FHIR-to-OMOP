"""Validate an OHDSI DataQualityDashboard JSON result against a reviewed budget."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = PROJECT_ROOT / "quality" / "dqd_policy.json"


def validate_dqd_result(result: dict, policy: dict) -> dict[str, int | float]:
    checks = result.get("CheckResults")
    if not isinstance(checks, list) or not checks:
        raise ValueError("DQD result has no CheckResults")
    if policy.get("status") != "approved":
        raise ValueError("DQD policy has not been approved by a human reviewer")

    errors = [check for check in checks if int(check.get("isError") or 0) == 1]
    failed = [check for check in checks if int(check.get("failed") or 0) == 1]
    max_errors = int(policy.get("max_errors", 0))
    max_failed = int(policy["max_failed_checks"])
    if len(errors) > max_errors:
        sample = [check.get("checkId") for check in errors[:5]]
        raise ValueError(f"DQD error budget exceeded: {len(errors)} > {max_errors}; examples={sample}")
    if len(failed) > max_failed:
        raise ValueError(f"DQD failed-check budget exceeded: {len(failed)} > {max_failed}")

    allowances = policy.get("allowed_failure_ids", {})
    failed_by_id = {check.get("checkId"): check for check in failed}
    failed_ids = set(failed_by_id)
    allowance_ids = set(allowances)

    unjustified = sorted(failed_ids - allowance_ids)
    if unjustified:
        raise ValueError(f"DQD contains unreviewed failed checks: {unjustified[:10]}")

    stale = sorted(allowance_ids - failed_ids)
    if stale:
        raise ValueError(f"DQD policy contains stale failure allowances: {stale[:10]}")

    for check_id, check in failed_by_id.items():
        allowance = allowances[check_id]
        if not str(allowance.get("reason", "")).strip():
            raise ValueError(f"DQD allowance has no review reason: {check_id}")
        if (
            "max_violated_rows" not in allowance
            or "max_pct_violated_rows" not in allowance
        ):
            raise ValueError(f"DQD allowance has no numeric caps: {check_id}")

        rows = check.get("numViolatedRows")
        pct = check.get("pctViolatedRows")
        if rows is None or pct is None:
            raise ValueError(f"DQD failed check has no violation metrics: {check_id}")
        if int(rows) > int(allowance["max_violated_rows"]):
            raise ValueError(
                f"DQD violated-row cap exceeded for {check_id}: "
                f"{rows} > {allowance['max_violated_rows']}"
            )
        if float(pct) > float(allowance["max_pct_violated_rows"]):
            raise ValueError(
                f"DQD violated-percent cap exceeded for {check_id}: "
                f"{pct} > {allowance['max_pct_violated_rows']}"
            )

    total = len(checks)
    return {
        "total": total,
        "errors": len(errors),
        "failed": len(failed),
        "passed": total - len(failed) - len(errors),
        "percent_without_failure": round(100 * (total - len(failed) - len(errors)) / total, 2),
    }


def validate_dqd_file(result_path: Path, policy_path: Path = DEFAULT_POLICY):
    result = json.loads(Path(result_path).read_text(encoding="utf-8"))
    policy = json.loads(Path(policy_path).read_text(encoding="utf-8"))
    return validate_dqd_result(result, policy)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    args = parser.parse_args()
    summary = validate_dqd_file(args.result, args.policy)
    print(f"DQD acceptance passed: {summary}")


if __name__ == "__main__":
    main()
