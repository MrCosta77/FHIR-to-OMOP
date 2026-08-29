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


