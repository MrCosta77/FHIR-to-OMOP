"""Calibrate provisional per-domain thresholds using development data only."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import ollama

from src.benchmark.evaluate_dirty_hospital import (
    load_cases,
    score_predictions,
    validate_cases,
    validate_reference_concepts,
)
from src.benchmark.evaluate_phase5 import (
    DEFAULT_CHROMA,
    DEFAULT_DATABASE,
    DEFAULT_FIXTURE,
    DOMAIN_TARGETS,
    _git_commit,
    _model_digest,
    _target_quality,
    blind_inputs,
    llm_prediction,
)
from src.mapping.mapping_service import get_versioned_collection
from src.mapping.semantic_mapper import (
    GENERATION_PARAMETERS,
    OLLAMA_TIMEOUT_SECONDS,
    PROMPT_VERSION,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOL = (
    PROJECT_ROOT / "benchmarks" / "dirty_hospital" /
    "phase6_development_protocol.json"
)


def load_development_protocol(path: Path, fixture_path: Path) -> dict:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    if protocol.get("protocol_version") != "phase6-development-v1":
        raise ValueError("Unsupported development calibration protocol.")
    if protocol.get("split") != "development":
        raise ValueError("Calibration is restricted to development data.")
    if not protocol.get("policy", {}).get("held_out_access_forbidden"):
        raise ValueError("Development calibration must forbid held-out access.")
    actual = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    if protocol.get("fixture_sha256") != actual:
        raise ValueError("Fixture hash differs from the development protocol.")
    return protocol


def _at_threshold(prediction: dict, threshold: float) -> dict:
    accepted = (
        prediction.get("raw_decision") == "MAP"
        and prediction.get("score", 0.0) >= threshold
    )
    return {
        "decision": "MAP" if accepted else "ABSTAIN",
        "concept_id": prediction.get("raw_concept_id") if accepted else None,
    }


def select_domain_thresholds(
    cases: list[dict],
    predictions: list[dict],
    thresholds: list[float],
    *,
    minimum_precision: float,
    maximum_false_maps: int,
) -> dict:
    """Maximize recall under prespecified safety constraints, breaking ties safely."""
    selected = {}
    for domain in sorted(DOMAIN_TARGETS):
        rows = [
            (case, prediction)
            for case, prediction in zip(cases, predictions, strict=True)
            if case["domain"] == domain
        ]
        domain_cases = [case for case, _ in rows]
        curve = []
        eligible = []
        for threshold in thresholds:
            metrics = score_predictions(
                domain_cases,
                [_at_threshold(prediction, threshold) for _, prediction in rows],
            )["metrics"]
            point = {"threshold": threshold, **metrics}
            curve.append(point)
            precision = metrics["accepted_precision"]
            if (
                precision is not None
                and precision >= minimum_precision
                and metrics["false_map"] <= maximum_false_maps
            ):
                eligible.append(point)
        if not eligible:
            selected[domain] = {
                "selected_threshold": None,
                "reason": "NO_THRESHOLD_SATISFIES_SAFETY_CONSTRAINTS",
                "curve": curve,
            }
            continue
        best = max(
            eligible,
            key=lambda point: (
                point["mappable_recall"] or 0.0,
                point["accepted_precision"] or 0.0,
                -point["coverage"],
                point["threshold"],
            ),
        )
        selected[domain] = {
            "selected_threshold": best["threshold"],
            "reason": "MAX_RECALL_WITHIN_PRESPECIFIED_SAFETY_CONSTRAINTS",
            "selected_metrics": {
                key: best[key] for key in (
                    "coverage", "accepted_precision", "mappable_recall",
                    "abstain_accuracy", "overall_accuracy", "wrong_map",
                    "false_map",
                )
            },
            "curve": curve,
        }
    return selected


def calibrate(
    fixture_path: Path,
    database_path: Path,
    protocol_path: Path,
    chroma_path: Path,
    *,
    client=None,
) -> dict:
    protocol = load_development_protocol(protocol_path, fixture_path)
    all_cases = load_cases(fixture_path)
    validate_cases(all_cases)
    cases = [case for case in all_cases if case["split"] == "development"]
    inputs = blind_inputs(cases)
    if any(case["split"] != "development" for case in cases):
        raise ValueError("Held-out case reached development calibration.")
    client = client or ollama.Client(timeout=OLLAMA_TIMEOUT_SECONDS)
    model = protocol["model"]
    top_k = int(protocol["top_k"])

    with duckdb.connect(str(database_path), read_only=True) as connection:
        validate_reference_concepts(connection, cases)
        collections = {
            domain: get_versioned_collection(
                connection, str(chroma_path), target_table
            )
            for domain, target_table in DOMAIN_TARGETS.items()
        }
        predictions = []
        telemetry = []
        started = time.perf_counter()
        for position, case in enumerate(inputs, start=1):
            prediction, event = llm_prediction(
                connection, case, collections[case["domain"]], client=client,
                model=model, top_k=top_k, threshold=0.0,
            )
            predictions.append(prediction)
            telemetry.append(event)
            print(
                f"development [{position}/{len(inputs)}] {case['case_id']}: "
                f"{prediction.get('raw_decision', prediction['decision'])}"
            )
        elapsed = time.perf_counter() - started
        _target_quality(connection, predictions)
        vocabulary_version = connection.execute(
            "SELECT vocabulary_version FROM cdm_source LIMIT 1"
        ).fetchone()
        etl_run = connection.execute(
            "SELECT run_id FROM etl_run WHERE status = 'SUCCESS' "
            "ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()

    thresholds = select_domain_thresholds(
        cases, predictions, protocol["threshold_grid"],
        minimum_precision=protocol["minimum_accepted_precision"],
        maximum_false_maps=protocol["maximum_false_maps"],
    )
    fallback_maps = [
        (case, prediction)
        for case, prediction in zip(cases, predictions, strict=True)
        if case["expected"]["decision"] == "MAP"
        and prediction.get("method") != "deterministic-code-only"
    ]
    top_k_hits = sum(
        case["expected"]["concept_id"]
        in prediction.get("retrieval_candidate_ids", [])
        for case, prediction in fallback_maps
    )
    calls = [row for row in telemetry if row.get("llm_called")]
    return {
        "calibration": "phase6-development-thresholds",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "PROVISIONAL_TECHNICAL",
        "deployment_authorized": False,
        "selection": {"split": "development", "case_count": len(cases)},
        "protocol": protocol,
        "provenance": {
            "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
            "vocabulary_version": vocabulary_version[0] if vocabulary_version else None,
            "model": model,
            "model_digest": _model_digest(client, model),
            "git_commit": _git_commit(),
            "etl_run_id": etl_run[0] if etl_run else None,
            "prompt_version": PROMPT_VERSION,
            "generation_parameters": GENERATION_PARAMETERS,
        },
        "retrieval": {
            "fallback_mappable_cases": len(fallback_maps),
            "top_k_hits": top_k_hits,
            "top_k_recall": top_k_hits / len(fallback_maps) if fallback_maps else None,
        },
        "performance": {
            "elapsed_seconds": elapsed,
            "llm_calls": len(calls),
            "contract_failures": sum(
                row.get("contract_valid") is False for row in calls
            ),
            "eval_tokens": sum(row.get("eval_count") or 0 for row in calls),
        },
        "domains": thresholds,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--chroma", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "benchmark_results" / "phase6_development.json",
    )
    args = parser.parse_args()
    report = calibrate(
        args.fixture, args.database, args.protocol, args.chroma
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")
    for domain, result in report["domains"].items():
        print(f"{domain}: provisional threshold={result['selected_threshold']}")


if __name__ == "__main__":
    main()
