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


