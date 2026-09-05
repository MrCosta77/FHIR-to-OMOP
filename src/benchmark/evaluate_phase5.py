"""Run the frozen Phase 5 comparison on the dirty-hospital benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
import tracemalloc
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import ollama
import onnxruntime

from src.benchmark.evaluate_dirty_hospital import (
    deterministic_prediction,
    load_cases,
    score_predictions,
    validate_cases,
    validate_reference_concepts,
)
from src.mapping.mapping_service import TARGETS, get_versioned_collection
from src.mapping.semantic_mapper import (
    DECISION_SCHEMA,
    GENERATION_PARAMETERS,
    OLLAMA_TIMEOUT,
    PROMPT_VERSION,
    _model_digest,
    _response_content,
    build_prompt,
    parse_llm_decision,
)
from src.utils.assets import runtime_asset
from src.utils.config import SETTINGS

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FIXTURE = runtime_asset("benchmarks", "dirty_hospital", "cases.jsonl")
DEFAULT_PROTOCOL = runtime_asset(
    "benchmarks", "dirty_hospital", "phase5_protocol.json"
)
DEFAULT_DATABASE = SETTINGS.db_path
DEFAULT_CHROMA = SETTINGS.chroma_path
EVALUATOR_VERSION = "1.0.0"
DOMAIN_TARGETS = {
    "Condition": "condition_occurrence",
    "Drug": "drug_exposure",
    "Measurement": "measurement",
    "Observation": "observation",
    "Procedure": "procedure_occurrence",
}


def load_protocol(path: Path, fixture_path: Path) -> dict:
    protocol = json.loads(path.read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    if protocol.get("protocol_version") != "phase5-v1":
        raise ValueError("Unsupported or missing Phase 5 protocol version.")
    if protocol.get("evaluation_split") != "held_out":
        raise ValueError("The frozen Phase 5 protocol must evaluate held_out.")
    if protocol.get("fixture_sha256") != actual_hash:
        raise ValueError("Fixture hash differs from the frozen Phase 5 protocol.")
    if not protocol.get("policy", {}).get("held_out_adjustment_forbidden"):
        raise ValueError("The protocol must forbid held-out adjustment.")
    return protocol


def blind_inputs(cases: list[dict]) -> list[dict]:
    """Expose only fields available to a real mapper, never benchmark labels."""
    return [
        {key: case[key] for key in ("case_id", "domain", "source", "context")}
        for case in cases
    ]


def llm_source_record(case: dict) -> str:
    """Serialize only observable source/context fields for LLM adjudication."""
    return json.dumps(
        {"source": case["source"], "context": case["context"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _valid_date(field: str) -> str:
    return (
        f"COALESCE(TRY_CAST({field} AS DATE), "
        f"TRY_STRPTIME(CAST({field} AS VARCHAR), '%Y%m%d')::DATE)"
    )


def fuzzy_candidates(
    connection: duckdb.DuckDBPyConnection,
    case: dict,
    *,
    top_k: int,
) -> list[dict]:
    target = TARGETS[DOMAIN_TARGETS[case["domain"]]]
    vocabularies = target.get("vocabularies") or (target["vocabulary"],)
    placeholders = ", ".join("?" for _ in vocabularies)
    source_text = str(case["source"].get("text") or "").strip()
    if not source_text:
        return []
    start = _valid_date("valid_start_date")
    end = _valid_date("valid_end_date")
    rows = connection.execute(f"""
        SELECT concept_id, concept_name,
               jaro_winkler_similarity(LOWER(TRIM(concept_name)), LOWER(?)) AS score
        FROM concept
        WHERE vocabulary_id IN ({placeholders}) AND domain_id = ?
          AND standard_concept = 'S'
          AND (invalid_reason IS NULL OR invalid_reason = '')
          AND CURRENT_DATE BETWEEN {start} AND {end}
        ORDER BY score DESC, CAST(concept_id AS BIGINT)
        LIMIT ?
    """, [source_text, *vocabularies, target["domain"], int(top_k)]).fetchall()
    return [
        {"concept_id": int(row[0]), "concept_name": row[1], "score": float(row[2])}
        for row in rows
    ]


def retrieval_candidates(collection, source_text: str, *, top_k: int) -> list[dict]:
    result = collection.query(query_texts=[source_text], n_results=top_k)
    ids = result.get("ids", [[]])[0]
    names = result.get("documents", [[]])[0]
    distances = result.get("distances", [[]])[0]
    metric = (collection.metadata or {}).get("distance_metric", "cosine")
    candidates = []
    for index, concept_id in enumerate(ids):
        distance = float(distances[index]) if index < len(distances) else 1.0
        score = 1.0 - (distance / 2.0) if metric == "l2" else 1.0 - distance
        candidates.append({
            "concept_id": int(concept_id),
            "concept_name": names[index],
            "distance": distance,
            "score": round(max(0.0, min(1.0, score)), 6),
        })
    return candidates


def _candidate_ids(candidates: list[dict]) -> list[int]:
    return [candidate["concept_id"] for candidate in candidates]


def _exact_or_none(connection, case: dict) -> dict | None:
    prediction = deterministic_prediction(connection, case)
    if prediction["decision"] == "MAP":
        return {
            **prediction,
            "score": 1.0,
            "raw_decision": "MAP",
            "raw_concept_id": prediction["concept_id"],
            "method": "deterministic-code-only",
        }
    return None


def _threshold_prediction(candidates: list[dict], threshold: float, method: str) -> dict:
    if not candidates:
        return {
            "decision": "ABSTAIN", "concept_id": None, "score": 0.0,
            "raw_decision": "ABSTAIN", "raw_concept_id": None,
            "reason": "NO_CANDIDATES", "method": method,
        }
    top = candidates[0]
    accepted = top["score"] >= threshold
    return {
        "decision": "MAP" if accepted else "ABSTAIN",
        "concept_id": top["concept_id"] if accepted else None,
        "concept_name": top["concept_name"] if accepted else None,
        "domain": None,
        "score": top["score"],
        "raw_decision": "MAP",
        "raw_concept_id": top["concept_id"],
        "reason": "ABOVE_THRESHOLD" if accepted else "BELOW_THRESHOLD",
        "method": method,
    }


def fuzzy_prediction(connection, case: dict, *, top_k: int, threshold: float) -> dict:
    candidates = fuzzy_candidates(connection, case, top_k=top_k)
    prediction = _exact_or_none(connection, case)
    if prediction is None:
        prediction = _threshold_prediction(candidates, threshold, "fuzzy-lexical")
    prediction["retrieval_candidate_ids"] = _candidate_ids(candidates)
    return prediction


def embedding_prediction(connection, case: dict, collection, *, top_k: int, threshold: float) -> dict:
    candidates = retrieval_candidates(
        collection, str(case["source"].get("text") or ""), top_k=top_k
    )
    prediction = _exact_or_none(connection, case)
    if prediction is None:
        prediction = _threshold_prediction(candidates, threshold, "embedding-retrieval")
    prediction["retrieval_candidate_ids"] = _candidate_ids(candidates)
    return prediction


def llm_prediction(
    connection,
    case: dict,
    collection,
    *,
    client,
    model: str,
    top_k: int,
    threshold: float,
) -> tuple[dict, dict]:
    candidates = retrieval_candidates(
        collection, str(case["source"].get("text") or ""), top_k=top_k
    )
    candidate_ids = _candidate_ids(candidates)
    exact = _exact_or_none(connection, case)
    if exact is not None:
        exact["retrieval_candidate_ids"] = candidate_ids
        return exact, {"llm_called": False}

    prompt_candidates = [
        {"concept_id": item["concept_id"], "concept_name": item["concept_name"]}
        for item in candidates
    ]
    target_table = DOMAIN_TARGETS[case["domain"]]
    prompt = build_prompt(
        target_table, llm_source_record(case), prompt_candidates, ""
    )
    started = time.perf_counter()
    response = client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        format=DECISION_SCHEMA,
        options=GENERATION_PARAMETERS,
    )
    elapsed = time.perf_counter() - started
    telemetry = {
        "llm_called": True,
        "wall_seconds": elapsed,
        "total_duration_ns": getattr(response, "total_duration", None),
        "eval_count": getattr(response, "eval_count", None),
    }
    try:
        decision = parse_llm_decision(_response_content(response), candidate_ids)
    except ValueError as exc:
        return {
            "decision": "ABSTAIN", "concept_id": None, "score": 0.0,
            "raw_decision": "ABSTAIN", "raw_concept_id": None,
            "reason": "INVALID_LLM_RESPONSE", "contract_error": str(exc),
            "method": f"retrieval-llm:{model}",
            "retrieval_candidate_ids": candidate_ids,
        }, {**telemetry, "contract_valid": False}

    if decision["decision"] == "ABSTAIN":
        prediction = {
            "decision": "ABSTAIN", "concept_id": None,
            "score": decision["confidence"], "raw_decision": "ABSTAIN",
            "raw_concept_id": None, "reason": decision["reason"],
            "method": f"retrieval-llm:{model}",
        }
    else:
        selected = next(
            candidate for candidate in candidates
            if candidate["concept_id"] == decision["selected_concept_id"]
        )
        governed_score = min(selected["score"], decision["confidence"])
        accepted = governed_score >= threshold
        prediction = {
            "decision": "MAP" if accepted else "ABSTAIN",
            "concept_id": selected["concept_id"] if accepted else None,
            "concept_name": selected["concept_name"] if accepted else None,
            "score": governed_score,
            "raw_decision": "MAP", "raw_concept_id": selected["concept_id"],
            "reason": decision["reason"], "llm_confidence": decision["confidence"],
            "method": f"retrieval-llm:{model}",
        }
    prediction["retrieval_candidate_ids"] = candidate_ids
    return prediction, {**telemetry, "contract_valid": True}


def _target_quality(connection, predictions: list[dict]) -> None:
    for prediction in predictions:
        concept_id = prediction.get("concept_id")
        if prediction["decision"] != "MAP" or concept_id is None:
            prediction["target_valid"] = None
            prediction["target_domain"] = None
            continue
        row = connection.execute("""
            SELECT domain_id,
                   standard_concept = 'S'
                   AND (invalid_reason IS NULL OR invalid_reason = '')
                   AND CURRENT_DATE BETWEEN
                       COALESCE(TRY_CAST(valid_start_date AS DATE),
                                TRY_STRPTIME(CAST(valid_start_date AS VARCHAR), '%Y%m%d')::DATE)
                       AND COALESCE(TRY_CAST(valid_end_date AS DATE),
                                    TRY_STRPTIME(CAST(valid_end_date AS VARCHAR), '%Y%m%d')::DATE)
            FROM concept WHERE concept_id = ?
        """, [int(concept_id)]).fetchone()
        prediction["target_domain"] = row[0] if row else None
        prediction["target_valid"] = bool(row and row[1])


def _extended_metrics(cases: list[dict], predictions: list[dict], thresholds: list[float]) -> dict:
    has_retrieval = any("retrieval_candidate_ids" in prediction for prediction in predictions)
    expected_maps = [
        (case, prediction) for case, prediction in zip(cases, predictions, strict=True)
        if case["expected"]["decision"] == "MAP"
    ]
    top_k_hits = sum(
        case["expected"]["concept_id"] in prediction.get("retrieval_candidate_ids", [])
        for case, prediction in expected_maps
    )
    accepted = [prediction for prediction in predictions if prediction["decision"] == "MAP"]
    domain_errors = sum(
        prediction.get("target_domain") != case["domain"]
        for case, prediction in zip(cases, predictions, strict=True)
        if prediction["decision"] == "MAP"
    )
    invalid_targets = sum(prediction.get("target_valid") is False for prediction in accepted)
    retrieval_by_domain = {}
    for domain in sorted(DOMAIN_TARGETS):
        domain_rows = [
            (case, prediction)
            for case, prediction in zip(cases, predictions, strict=True)
            if case["domain"] == domain
        ]
        domain_maps = [
            (case, prediction) for case, prediction in domain_rows
            if case["expected"]["decision"] == "MAP"
        ]
        domain_hits = sum(
            case["expected"]["concept_id"] in prediction.get("retrieval_candidate_ids", [])
            for case, prediction in domain_maps
        )
        retrieval_by_domain[domain] = {
            "top_k_recall": (
                domain_hits / len(domain_maps)
                if has_retrieval and domain_maps else None
            ),
            "top_k_hits": domain_hits if has_retrieval else None,
            "expected_maps": len(domain_maps),
            "domain_errors": sum(
                prediction["decision"] == "MAP"
                and prediction.get("target_domain") != domain
                for _, prediction in domain_rows
            ),
            "invalid_target_errors": sum(
                prediction["decision"] == "MAP"
                and prediction.get("target_valid") is False
                for _, prediction in domain_rows
            ),
        }

    bins = []
    for lower, upper in zip((0.0, 0.6, 0.7, 0.8, 0.9), (0.6, 0.7, 0.8, 0.9, 1.000001), strict=True):
        bucket = [
            (case, prediction)
            for case, prediction in zip(cases, predictions, strict=True)
            if prediction["decision"] == "MAP" and lower <= prediction.get("score", 0.0) < upper
        ]
        correct = sum(
            prediction.get("concept_id") == case["expected"].get("concept_id")
            for case, prediction in bucket
        )
        bins.append({
            "lower": lower, "upper": min(upper, 1.0), "count": len(bucket),
            "mean_score": (
                sum(item[1].get("score", 0.0) for item in bucket) / len(bucket)
                if bucket else None
            ),
            "accuracy": correct / len(bucket) if bucket else None,
        })

    curve = []
    for threshold in thresholds:
        threshold_predictions = []
        for prediction in predictions:
            raw_map = prediction.get("raw_decision") == "MAP"
            accepted_at_threshold = raw_map and prediction.get("score", 0.0) >= threshold
            threshold_predictions.append({
                "decision": "MAP" if accepted_at_threshold else "ABSTAIN",
                "concept_id": prediction.get("raw_concept_id") if accepted_at_threshold else None,
            })
        metrics = score_predictions(cases, threshold_predictions)["metrics"]
        curve.append({
            "threshold": threshold,
            "coverage": metrics["coverage"],
            "accepted_precision": metrics["accepted_precision"],
            "mappable_recall": metrics["mappable_recall"],
            "false_maps": metrics["false_map"],
        })
    return {
        "top_k_recall": (
            top_k_hits / len(expected_maps)
            if has_retrieval and expected_maps else None
        ),
        "top_k_hits": top_k_hits if has_retrieval else None,
        "expected_maps": len(expected_maps),
        "domain_errors": domain_errors,
        "invalid_target_errors": invalid_targets,
        "retrieval_by_domain": retrieval_by_domain,
        "score_calibration_bins": bins,
        "precision_coverage_curve": curve,
    }


def _measure(name: str, predictor: Callable[[], tuple[list[dict], dict]]) -> tuple[list[dict], dict]:
    tracemalloc.start()
    started = time.perf_counter()
    predictions, telemetry = predictor()
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return predictions, {
        "arm": name,
        "elapsed_seconds": elapsed,
        "peak_python_memory_mb": peak / (1024 * 1024),
        **telemetry,
    }


def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def evaluate_phase5(
    fixture_path: Path,
    database_path: Path,
    protocol_path: Path,
    chroma_path: Path,
    *,
    client=None,
) -> dict:
    protocol = load_protocol(protocol_path, fixture_path)
    all_cases = load_cases(fixture_path)
    validate_cases(all_cases)
    cases = [case for case in all_cases if case["split"] == protocol["evaluation_split"]]
    inputs = blind_inputs(cases)
    top_k = int(protocol["retrieval"]["top_k"])
    thresholds = protocol["threshold_curve"]
    arm_config = {arm["name"]: arm for arm in protocol["arms"]}
    client = client or ollama.Client(timeout=protocol["generation"]["timeout_seconds"])

    with duckdb.connect(str(database_path), read_only=True) as connection:
        validate_reference_concepts(connection, cases)
        vocabulary_version = connection.execute(
            "SELECT vocabulary_version FROM cdm_source LIMIT 1"
        ).fetchone()
        etl_run = connection.execute(
            "SELECT run_id FROM etl_run WHERE status = 'SUCCESS' ORDER BY completed_at DESC LIMIT 1"
        ).fetchone()

        build_started = time.perf_counter()
        collections = {
            domain: get_versioned_collection(
                connection, str(chroma_path), target_table
            )
            for domain, target_table in DOMAIN_TARGETS.items()
        }
        index_preparation = {
            "elapsed_seconds": time.perf_counter() - build_started,
            "collections": {
                domain: {
                    "count": collection.count(),
                    "signature": (collection.metadata or {}).get("index_signature"),
                }
                for domain, collection in collections.items()
            },
        }

        results = {}

        predictions, performance = _measure(
            "deterministic-code-only",
            lambda: ([deterministic_prediction(connection, case) for case in inputs], {}),
        )
        for prediction in predictions:
            prediction.setdefault("score", 1.0 if prediction["decision"] == "MAP" else 0.0)
            prediction.setdefault("raw_decision", prediction["decision"])
            prediction.setdefault("raw_concept_id", prediction.get("concept_id"))
        _target_quality(connection, predictions)
        results["deterministic-code-only"] = _arm_report(
            cases, predictions, performance, thresholds
        )

        fuzzy_threshold = arm_config["fuzzy-lexical"]["selection_threshold"]
        predictions, performance = _measure(
            "fuzzy-lexical",
            lambda: ([
                fuzzy_prediction(connection, case, top_k=top_k, threshold=fuzzy_threshold)
                for case in inputs
            ], {}),
        )
        _target_quality(connection, predictions)
        results["fuzzy-lexical"] = _arm_report(cases, predictions, performance, thresholds)

        retrieval_threshold = arm_config["embedding-retrieval"]["selection_threshold"]
        predictions, performance = _measure(
            "embedding-retrieval",
            lambda: ([
                embedding_prediction(
                    connection, case, collections[case["domain"]],
                    top_k=top_k, threshold=retrieval_threshold,
                ) for case in inputs
            ], {}),
        )
        _target_quality(connection, predictions)
        results["embedding-retrieval"] = _arm_report(
            cases, predictions, performance, thresholds
        )

        for arm_name in ("retrieval-llm-qwen", "retrieval-llm-llama"):
            config = arm_config[arm_name]
            model = config["model"]

            def run_llm_arm(model=model, config=config, arm_name=arm_name):
                predictions = []
                telemetry_rows = []
                for position, case in enumerate(inputs, start=1):
                    prediction, telemetry = llm_prediction(
                        connection, case, collections[case["domain"]], client=client,
                        model=model, top_k=top_k,
                        threshold=config["selection_threshold"],
                    )
                    predictions.append(prediction)
                    telemetry_rows.append(telemetry)
                    print(f"{arm_name} [{position}/{len(inputs)}] {case['case_id']}: {prediction['decision']}")
                calls = [row for row in telemetry_rows if row.get("llm_called")]
                return predictions, {
                    "model": model,
                    "model_digest": _model_digest(client, model),
                    "llm_calls": len(calls),
                    "contract_failures": sum(row.get("contract_valid") is False for row in calls),
                    "llm_wall_seconds": sum(row.get("wall_seconds", 0.0) for row in calls),
                    "eval_tokens": sum(row.get("eval_count") or 0 for row in calls),
                    "ollama_total_duration_seconds": sum(
                        (row.get("total_duration_ns") or 0) / 1_000_000_000 for row in calls
                    ),
                }

            predictions, performance = _measure(arm_name, run_llm_arm)
            _target_quality(connection, predictions)
            results[arm_name] = _arm_report(cases, predictions, performance, thresholds)

    return {
        "benchmark": "dirty-hospital-to-omop",
        "evaluator": {"name": "phase5-comparison", "version": EVALUATOR_VERSION},
        "generated_at": datetime.now(UTC).isoformat(),
        "protocol": protocol,
        "selection": {"split": "held_out", "case_count": len(cases)},
        "provenance": {
            "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
            "database": str(database_path.resolve()),
            "vocabulary_version": vocabulary_version[0] if vocabulary_version else None,
            "etl_run_id": etl_run[0] if etl_run else None,
            "git_commit": _git_commit(),
            "python": platform.python_version(),
            "duckdb": duckdb.__version__,
            "onnxruntime": onnxruntime.__version__,
            "onnx_providers": onnxruntime.get_available_providers(),
            "prompt_version": PROMPT_VERSION,
            "generation_parameters": GENERATION_PARAMETERS,
            "ollama_timeout_seconds": OLLAMA_TIMEOUT,
        },
        "index_preparation": index_preparation,
        "arms": results,
    }


def _arm_report(cases, predictions, performance, thresholds) -> dict:
    scored = score_predictions(cases, predictions)
    return {
        "performance": performance,
        "extended_metrics": _extended_metrics(cases, predictions, thresholds),
        **scored,
    }


def public_summary(report: dict) -> dict:
    return {
        "benchmark": report["benchmark"],
        "evaluator": report["evaluator"],
        "generated_at": report["generated_at"],
        "protocol": {
            "protocol_version": report["protocol"]["protocol_version"],
            "fixture_sha256": report["protocol"]["fixture_sha256"],
            "policy": report["protocol"]["policy"],
        },
        "selection": report["selection"],
        "provenance": {
            key: value for key, value in report["provenance"].items()
            if key != "database"
        },
        "index_preparation": report["index_preparation"],
        "arms": {
            name: {
                "metrics": arm["metrics"],
                "by_domain": arm["by_domain"],
                "extended_metrics": arm["extended_metrics"],
                "performance": arm["performance"],
            }
            for name, arm in report["arms"].items()
        },
        "clinical_validation_required": True,
        "curation_status": "PROVISIONAL_TECHNICAL",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--chroma", type=Path, default=DEFAULT_CHROMA)
    parser.add_argument(
        "--output", type=Path,
        default=PROJECT_ROOT / "benchmark_results" / "phase5_held_out.json",
    )
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()
    report = evaluate_phase5(
        args.fixture, args.database, args.protocol, args.chroma
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(
            json.dumps(public_summary(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(f"Wrote {args.output}")
    for name, arm in report["arms"].items():
        metrics = arm["metrics"]
        print(
            f"{name}: accuracy={metrics['overall_accuracy']:.3f}, "
            f"coverage={metrics['coverage']:.3f}, "
            f"precision={metrics['accepted_precision']}"
        )


if __name__ == "__main__":
    main()
