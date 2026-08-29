from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
import tracemalloc
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb
import ollama
import onnxruntime

from src.benchmark.evaluate_dirty_hospital import load_cases, validate_reference_concepts, deterministic_prediction, score_predictions, validate_cases
from src.mapping.mapping_service import get_versioned_collection
from src.utils.config import SETTINGS
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

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = PROJECT_ROOT / "benchmarks" / "dirty_hospital" / "cases.jsonl"
DEFAULT_PROTOCOL = PROJECT_ROOT / "benchmarks" / "dirty_hospital" / "phase5_protocol.json"
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

            def run_llm_arm(model=model, config=config):
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
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
