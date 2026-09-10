"""Probe the operational mapping prompt on a small, labelled synthetic sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import chromadb
import duckdb
import ollama

from src.clinical_mapping_core import DECISION_SCHEMA, parse_mapping_decision
from src.mapping.mapping_service import (
    TARGETS,
    get_few_shot_prompt,
    vocabulary_signature,
)
from src.mapping.retrieval_normalization import normalize_retrieval_text
from src.mapping.semantic_mapper import (
    GENERATION_PARAMETERS,
    OLLAMA_TIMEOUT,
    PROMPT_VERSION,
    _model_digest,
    _response_content,
    build_prompt,
)
from src.utils.config import SETTINGS

TARGET_TABLE = "measurement"
POSITIVE_TERMS = (
    "CREA",
    "Cholesterol total",
    "Creatinine serum",
    "Glu (Blood)",
    "HGB",
    "Hb blood test",
    "RBC count",
    "WBC count",
)
NEGATIVE_TERMS = (
    "Priority Level",
    "A great deal of time is spent in activities necessary to obtain the opioid, "
    "use the opioid, or recover from its effects",
    "Recurrent opioid use in situations in which it is physically hazardous",
    "Operative Status Value",
)
CALIBRATION_THRESHOLDS = (0.70, 0.75, 0.80, 0.85, 0.90)


def load_probe_cases(con) -> list[dict]:
    """Load fixed positives from LIS ground truth and fixed abstention controls."""
    placeholders = ", ".join("?" for _ in POSITIVE_TERMS)
    rows = con.execute(f"""
        SELECT corrupted_source_value, true_concept_id, true_source_value
        FROM lis_noise_ground_truth
        WHERE corrupted_source_value IN ({placeholders})
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY corrupted_source_value ORDER BY measurement_id
        ) = 1
        ORDER BY corrupted_source_value
    """, list(POSITIVE_TERMS)).fetchall()
    found = {row[0] for row in rows}
    missing = sorted(set(POSITIVE_TERMS) - found)
    if missing:
        raise ValueError(
            "The published LIS ground truth does not contain probe terms: "
            f"{missing}. Run a synthetic noise-enabled pipeline first."
        )
    positives = [
        {
            "case_id": f"POS-{index:02d}",
            "source_value": source,
            "expected_decision": "SELECT",
            "expected_concept_id": int(concept_id),
            "expected_concept_name": concept_name,
        }
        for index, (source, concept_id, concept_name) in enumerate(rows, start=1)
    ]
    negatives = [
        {
            "case_id": f"NEG-{index:02d}",
            "source_value": source,
            "expected_decision": "ABSTAIN",
            "expected_concept_id": None,
            "expected_concept_name": None,
        }
        for index, source in enumerate(NEGATIVE_TERMS, start=1)
    ]
    return positives + negatives


def load_expanded_cases(
    con, *, positive_limit: int = 40, split: str = "development"
) -> list[dict]:
    """Load a deterministic, unambiguous synthetic LIS calibration sample."""
    if positive_limit <= 0:
        raise ValueError("positive_limit must be positive")
    if split not in {"development", "holdout", "all"}:
        raise ValueError("split must be development, holdout or all")
    rows = con.execute("""
        SELECT corrupted_source_value,
               MIN(true_concept_id) AS true_concept_id,
               MIN(true_source_value) AS true_source_value
        FROM lis_noise_ground_truth
        WHERE corrupted_source_value IS NOT NULL
          AND TRIM(corrupted_source_value) <> ''
          AND true_concept_id IS NOT NULL
        GROUP BY corrupted_source_value
        HAVING COUNT(DISTINCT true_concept_id) = 1
    """).fetchall()

    selected = []
    for source, concept_id, concept_name in rows:
        digest = hashlib.sha256(source.encode("utf-8")).digest()
        case_split = "development" if digest[0] < 204 else "holdout"
        if split == "all" or split == case_split:
            selected.append((digest.hex(), source, concept_id, concept_name))
    selected.sort(key=lambda row: (row[0], row[1].casefold()))
    selected = selected[:positive_limit]
    if not selected:
        raise ValueError(f"No unambiguous LIS cases are available for split {split!r}.")

    positives = [
        {
            "case_id": f"POS-{index:03d}",
            "source_value": source,
            "expected_decision": "SELECT",
            "expected_concept_id": int(concept_id),
            "expected_concept_name": concept_name,
        }
        for index, (_digest, source, concept_id, concept_name) in enumerate(
            selected, start=1
        )
    ]
    negatives = [
        {
            "case_id": f"NEG-{index:03d}",
            "source_value": source,
            "expected_decision": "ABSTAIN",
            "expected_concept_id": None,
            "expected_concept_name": None,
        }
        for index, source in enumerate(NEGATIVE_TERMS, start=1)
    ]
    return positives + negatives


def query_candidates(collection, source_value: str, top_k: int) -> list[dict]:
    result = collection.query(query_texts=[source_value], n_results=top_k)
    ids = result.get("ids", [[]])[0]
    names = result.get("documents", [[]])[0]
    distances = result.get("distances", [[]])[0]
    return [
        {
            "rank": index + 1,
            "concept_id": int(concept_id),
            "concept_name": names[index],
            "distance": float(distances[index]) if index < len(distances) else None,
        }
        for index, concept_id in enumerate(ids)
    ]


def summarize(cases: list[dict]) -> dict:
    positives = [case for case in cases if case["expected_decision"] == "SELECT"]
    negatives = [case for case in cases if case["expected_decision"] == "ABSTAIN"]
    return {
        "positive_cases": len(positives),
        "retrieval_hits": sum(case["retrieval_hit"] for case in positives),
        "correct_positive_selections": sum(
            case["selected_correctly"] for case in positives
        ),
        "positive_abstentions": sum(
            case["decision"] == "ABSTAIN" for case in positives
        ),
        "negative_cases": len(negatives),
        "safe_negative_abstentions": sum(
            case["decision"] == "ABSTAIN" for case in negatives
        ),
        "contract_failures": sum(case["contract_error"] is not None for case in cases),
    }


def threshold_analysis(cases: list[dict]) -> list[dict]:
    """Report review-queue precision and coverage without authorizing publication."""
    positives = [case for case in cases if case["expected_decision"] == "SELECT"]
    rows = []
    for threshold in CALIBRATION_THRESHOLDS:
        admitted = [
            case for case in cases
            if case["decision"] == "SELECT"
            and case.get("governed_score", 0.0) >= threshold
        ]
        correct = sum(case.get("selected_correctly") is True for case in admitted)
        rows.append({
            "threshold": threshold,
            "admitted_proposals": len(admitted),
            "correct_proposals": correct,
            "incorrect_proposals": len(admitted) - correct,
            "review_queue_precision": correct / len(admitted) if admitted else None,
            "positive_case_coverage": correct / len(positives) if positives else None,
        })
    return rows


def calibrate_prompt(
    database_path: Path,
    chroma_path: Path,
    *,
    top_k: int = 5,
    model: str | None = None,
    sample_mode: str = "expanded",
    positive_limit: int = 40,
    split: str = "development",
    client=None,
    collection=None,
) -> dict:
    """Run a non-publishing prompt probe against synthetic labelled cases."""
    if SETTINGS.data_classification != "SYNTHETIC":
        raise ValueError("Prompt calibration is restricted to SYNTHETIC data.")
    model = model or SETTINGS.model_name
    client = client or ollama.Client(timeout=OLLAMA_TIMEOUT)
    with duckdb.connect(str(database_path), read_only=True) as con:
        cases = (
            load_probe_cases(con)
            if sample_mode == "fixed"
            else load_expanded_cases(
                con, positive_limit=positive_limit, split=split
            )
        )
        few_shot = get_few_shot_prompt(
            con, TARGET_TABLE, "LOINC Measurement", 3
        )
        expected_signature = vocabulary_signature(con, TARGET_TABLE)
        if collection is None:
            chroma_client = chromadb.PersistentClient(path=str(chroma_path))
            collection = chroma_client.get_collection(
                TARGETS[TARGET_TABLE]["collection"]
            )
        metadata = collection.metadata or {}
        if metadata.get("index_signature") != expected_signature:
            raise ValueError("Measurement Chroma index is stale or unversioned.")
        if not metadata.get("build_complete"):
            raise ValueError("Measurement Chroma index build is incomplete.")

        results = []
        for case in cases:
            normalization = normalize_retrieval_text(
                case["source_value"],
                TARGET_TABLE,
                data_classification=SETTINGS.data_classification,
            )
            candidates = query_candidates(
                collection, normalization.retrieval_text, top_k
            )
            candidate_ids = [candidate["concept_id"] for candidate in candidates]
            prompt = build_prompt(
                TARGET_TABLE, case["source_value"], candidates, few_shot
            )
            started = time.perf_counter()
            response = client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                format=DECISION_SCHEMA,
                options=GENERATION_PARAMETERS,
            )
            elapsed = time.perf_counter() - started
            contract_error = None
            try:
                decision = parse_mapping_decision(
                    _response_content(response), candidate_ids
                ).to_dict()
            except ValueError as exc:
                contract_error = str(exc)
                decision = {
                    "decision": "ABSTAIN",
                    "selected_concept_id": None,
                    "confidence": 0.0,
                    "reason": "INVALID_LLM_RESPONSE",
                    "clinical_signals": [],
                }
            expected_id = case["expected_concept_id"]
            result = {
                **case,
                "retrieval_text": normalization.retrieval_text,
                "retrieval_alias_id": normalization.alias_id,
                "retrieval_lexicon_version": normalization.lexicon_version,
                "retrieval_lexicon_sha256": normalization.lexicon_sha256,
                "candidates": candidates,
                "retrieval_hit": expected_id in candidate_ids if expected_id else None,
                **decision,
                "selected_correctly": (
                    decision["decision"] == "SELECT"
                    and decision["selected_concept_id"] == expected_id
                ) if expected_id else None,
                "contract_error": contract_error,
                "wall_seconds": elapsed,
            }
            selected = next(
                (
                    candidate for candidate in candidates
                    if candidate["concept_id"] == decision["selected_concept_id"]
                ),
                None,
            )
            distance = selected["distance"] if selected else None
            metric = metadata.get("distance_metric", "cosine")
            retrieval_score = None
            if distance is not None:
                retrieval_score = (
                    max(0.0, min(1.0, 1.0 - distance))
                    if metric == "cosine"
                    else 1.0 / (1.0 + max(0.0, distance))
                )
            result["selected_retrieval_score"] = retrieval_score
            result["governed_score"] = (
                min(retrieval_score, decision["confidence"])
                if retrieval_score is not None else 0.0
            )
            results.append(result)
            print(
                f"{case['case_id']} {case['source_value']!r}: "
                f"expected={case['expected_decision']} "
                f"retrieval_hit={result['retrieval_hit']} "
                f"decision={result['decision']} "
                f"selected={result['selected_concept_id']}"
            )

    return {
        "calibration": "operational-prompt-probe",
        "status": "DEVELOPMENT_ONLY",
        "deployment_authorized": False,
        "generated_at": datetime.now(UTC).isoformat(),
        "model": model,
        "model_digest": _model_digest(client, model),
        "prompt_version": PROMPT_VERSION,
        "generation_parameters": GENERATION_PARAMETERS,
        "top_k": top_k,
        "sample_mode": sample_mode,
        "positive_limit": positive_limit,
        "split": split,
        "index_signature": metadata.get("index_signature"),
        "summary": summarize(results),
        "threshold_analysis": threshold_analysis(results),
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=SETTINGS.db_path)
    parser.add_argument("--chroma", type=Path, default=SETTINGS.chroma_path)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--model", default=SETTINGS.model_name)
    parser.add_argument(
        "--sample-mode", choices=("fixed", "expanded"), default="expanded"
    )
    parser.add_argument("--positive-limit", type=int, default=40)
    parser.add_argument(
        "--split", choices=("development", "holdout", "all"),
        default="development",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark_results") / "prompt_calibration.json",
    )
    args = parser.parse_args()
    if args.top_k <= 0:
        parser.error("--top-k must be positive")
    report = calibrate_prompt(
        args.database, args.chroma, top_k=args.top_k, model=args.model,
        sample_mode=args.sample_mode, positive_limit=args.positive_limit,
        split=args.split,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {args.output}")
    print(json.dumps(report["summary"], indent=2, sort_keys=True))
    print(json.dumps(report["threshold_analysis"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
