"""Compare two compatible prompt-calibration reports without rerunning the LLM."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

SUMMARY_METRICS = (
    "positive_cases",
    "retrieval_hits",
    "correct_positive_selections",
    "positive_abstentions",
    "negative_cases",
    "safe_negative_abstentions",
    "contract_failures",
)


def _load(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _case_signature(report: dict) -> list[tuple]:
    return [
        (
            case.get("case_id"), case.get("source_value"),
            case.get("expected_decision"), case.get("expected_concept_id"),
        )
        for case in report.get("cases", [])
    ]


def _validate_compatible(before: dict, after: dict) -> None:
    for field in ("model", "prompt_version", "top_k", "sample_mode", "split"):
        if before.get(field) != after.get(field):
            raise ValueError(f"Incompatible calibration field: {field}")
    if _case_signature(before) != _case_signature(after):
        raise ValueError("Calibration reports do not contain the same labelled cases.")
    for report in (before, after):
        if report.get("status") != "DEVELOPMENT_ONLY":
            raise ValueError("Only DEVELOPMENT_ONLY calibration reports are supported.")
        if report.get("deployment_authorized") is not False:
            raise ValueError("Calibration reports must not authorize deployment.")


def compare_reports(before: dict, after: dict) -> dict:
    """Return metric deltas and case transitions for compatible reports."""
    _validate_compatible(before, after)
    summary = {}
    for metric in SUMMARY_METRICS:
        old = before["summary"][metric]
        new = after["summary"][metric]
        summary[metric] = {"before": old, "after": new, "delta": new - old}

    old_thresholds = {
        float(row["threshold"]): row for row in before["threshold_analysis"]
    }
    new_thresholds = {
        float(row["threshold"]): row for row in after["threshold_analysis"]
    }
    if old_thresholds.keys() != new_thresholds.keys():
        raise ValueError("Calibration reports use different threshold grids.")
    thresholds = []
    threshold_metrics = (
        "admitted_proposals", "correct_proposals", "incorrect_proposals",
        "review_queue_precision", "positive_case_coverage",
    )
    for threshold in sorted(old_thresholds):
        row = {"threshold": threshold}
        for metric in threshold_metrics:
            old = old_thresholds[threshold].get(metric)
            new = new_thresholds[threshold].get(metric)
            row[metric] = {
                "before": old,
                "after": new,
                "delta": None if old is None or new is None else new - old,
            }
        thresholds.append(row)

    transitions = []
    for old, new in zip(before["cases"], after["cases"], strict=True):
        changes = []
        if old.get("retrieval_hit") != new.get("retrieval_hit"):
            changes.append("retrieval_hit")
        if old.get("decision") != new.get("decision"):
            changes.append("decision")
        if old.get("selected_concept_id") != new.get("selected_concept_id"):
            changes.append("selected_concept_id")
        if old.get("selected_correctly") != new.get("selected_correctly"):
            changes.append("selected_correctly")
        if changes:
            transitions.append({
                "case_id": new["case_id"],
                "source_value": new["source_value"],
                "changed_fields": changes,
                "before": {
                    "retrieval_hit": old.get("retrieval_hit"),
                    "decision": old.get("decision"),
                    "selected_concept_id": old.get("selected_concept_id"),
                    "selected_correctly": old.get("selected_correctly"),
                },
                "after": {
                    "retrieval_hit": new.get("retrieval_hit"),
                    "decision": new.get("decision"),
                    "selected_concept_id": new.get("selected_concept_id"),
                    "selected_correctly": new.get("selected_correctly"),
                },
            })
    return {
        "comparison": "prompt-calibration-before-vs-after",
        "status": "DEVELOPMENT_ONLY",
        "deployment_authorized": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "model": after["model"],
        "prompt_version": after["prompt_version"],
        "split": after["split"],
        "before_reranker_version": before.get("loinc_reranker_version"),
        "after_reranker_version": after.get("loinc_reranker_version"),
        "summary": summary,
        "threshold_analysis": thresholds,
        "case_transitions": transitions,
    }


def _format_pair(row: dict, metric: str, *, percent: bool = False) -> str:
    values = row[metric]
    formatter = (
        (lambda value: "—" if value is None else f"{value:.1%}")
        if percent
        else str
    )
    return f"{formatter(values['before'])} → {formatter(values['after'])}"


def render_markdown(report: dict) -> str:
    """Render a compact human-readable comparison."""
    lines = [
        "# Prompt calibration: before vs after",
        "",
        f"- Split: `{report['split']}`",
        f"- Model: `{report['model']}`",
        f"- Prompt: `{report['prompt_version']}`",
        f"- Reranker: `{report['before_reranker_version']}` → "
        f"`{report['after_reranker_version']}`",
        "- Safety status: development-only; no deployment authorization",
        "",
        "## Summary",
        "",
        "| Metric | Before | After | Delta |",
        "|---|---:|---:|---:|",
    ]
    for metric, values in report["summary"].items():
        lines.append(
            f"| {metric} | {values['before']} | {values['after']} | "
            f"{values['delta']:+} |"
        )
    lines.extend([
        "", "## Threshold analysis", "",
        "| Threshold | Proposals | Correct | Incorrect | Precision | Coverage |",
        "|---:|---:|---:|---:|---:|---:|",
    ])
    for row in report["threshold_analysis"]:
        lines.append(
            f"| {row['threshold']:.2f} | "
            f"{_format_pair(row, 'admitted_proposals')} | "
            f"{_format_pair(row, 'correct_proposals')} | "
            f"{_format_pair(row, 'incorrect_proposals')} | "
            f"{_format_pair(row, 'review_queue_precision', percent=True)} | "
            f"{_format_pair(row, 'positive_case_coverage', percent=True)} |"
        )
    lines.extend([
        "", "## Changed cases", "",
        f"{len(report['case_transitions'])} cases changed retrieval or selection outcome.",
    ])
    for case in report["case_transitions"]:
        lines.append(
            f"- `{case['case_id']}` {case['source_value']}: "
            f"{', '.join(case['changed_fields'])}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before, before_sha = _load(args.before)
    after, after_sha = _load(args.after)
    report = compare_reports(before, after)
    report["inputs"] = {
        "before": {"path": str(args.before), "sha256": before_sha},
        "after": {"path": str(args.after), "sha256": after_sha},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_markdown(report), encoding="utf-8")
    json_path = args.output.with_suffix(".json")
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")
    print(f"Wrote {json_path}")


if __name__ == "__main__":
    main()
