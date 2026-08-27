import hashlib
from pathlib import Path

import pytest

from src.benchmark.evaluate_phase5 import (
    _extended_metrics,
    _threshold_prediction,
    blind_inputs,
    llm_source_record,
    load_protocol,
    public_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "benchmarks" / "dirty_hospital" / "cases.jsonl"
PROTOCOL = PROJECT_ROOT / "benchmarks" / "dirty_hospital" / "phase5_protocol.json"


def test_phase5_protocol_is_frozen_to_the_release_fixture():
    protocol = load_protocol(PROTOCOL, FIXTURE)
    assert protocol["evaluation_split"] == "held_out"
    assert protocol["policy"]["held_out_adjustment_forbidden"] is True
    assert protocol["fixture_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert [arm["name"] for arm in protocol["arms"]] == [
        "deterministic-code-only",
        "fuzzy-lexical",
        "embedding-retrieval",
        "retrieval-llm-qwen",
        "retrieval-llm-llama",
    ]


def test_phase5_predictors_receive_no_labels_or_curation():
    case = {
        "case_id": "blind-1",
        "domain": "Condition",
        "source": {"text": "dirty text"},
        "context": {"unit": None},
        "expected": {"decision": "MAP", "concept_id": 123},
        "curation": {"reviewer": "hidden"},
        "family_id": "hidden-family",
    }
    assert blind_inputs([case]) == [{
        "case_id": "blind-1",
        "domain": "Condition",
        "source": {"text": "dirty text"},
        "context": {"unit": None},
    }]
    serialized = llm_source_record(blind_inputs([case])[0])
    assert "dirty text" in serialized
    assert "unit" in serialized
    assert "expected" not in serialized
    assert "hidden" not in serialized


def test_threshold_prediction_keeps_raw_candidate_but_abstains_safely():
    prediction = _threshold_prediction(
        [{"concept_id": 123, "concept_name": "Candidate", "score": 0.89}],
        0.9,
        "embedding-retrieval",
    )
    assert prediction["decision"] == "ABSTAIN"
    assert prediction["concept_id"] is None
    assert prediction["raw_decision"] == "MAP"
    assert prediction["raw_concept_id"] == 123


def test_extended_metrics_separate_top_k_recall_domain_errors_and_curve():
    cases = [
        {
            "case_id": "a", "split": "held_out", "domain": "Condition",
            "expected": {"decision": "MAP", "concept_id": 10},
        },
        {
            "case_id": "b", "split": "held_out", "domain": "Condition",
            "expected": {"decision": "ABSTAIN", "concept_id": None},
        },
    ]
    predictions = [
        {
            "decision": "MAP", "concept_id": 10, "score": 0.95,
            "raw_decision": "MAP", "raw_concept_id": 10,
            "retrieval_candidate_ids": [10, 11],
            "target_domain": "Condition", "target_valid": True,
        },
        {
            "decision": "ABSTAIN", "concept_id": None, "score": 0.2,
            "raw_decision": "ABSTAIN", "raw_concept_id": None,
            "retrieval_candidate_ids": [12, 13],
            "target_domain": None, "target_valid": None,
        },
    ]
    metrics = _extended_metrics(cases, predictions, [0.0, 0.9])
    assert metrics["top_k_recall"] == 1.0
    assert metrics["retrieval_by_domain"]["Condition"]["top_k_recall"] == 1.0
    assert metrics["domain_errors"] == 0
    assert metrics["invalid_target_errors"] == 0
    assert metrics["precision_coverage_curve"][1]["accepted_precision"] == 1.0


def test_public_summary_drops_case_labels_and_local_database_path():
    report = {
        "benchmark": "dirty-hospital-to-omop",
        "evaluator": {"name": "phase5-comparison", "version": "test"},
        "generated_at": "now",
        "protocol": {
            "protocol_version": "phase5-v1", "fixture_sha256": "hash",
            "policy": {"held_out_adjustment_forbidden": True},
        },
        "selection": {"split": "held_out", "case_count": 1},
        "provenance": {"database": "private-path", "git_commit": "abc"},
        "index_preparation": {},
        "arms": {
            "arm": {
                "metrics": {}, "by_domain": {}, "extended_metrics": {},
                "performance": {}, "cases": [{"expected": {"concept_id": 1}}],
            }
        },
    }
    summary = public_summary(report)
    assert "database" not in summary["provenance"]
    assert "cases" not in summary["arms"]["arm"]
    assert summary["clinical_validation_required"] is True


def test_protocol_rejects_a_different_fixture(tmp_path):
    changed = tmp_path / "cases.jsonl"
    changed.write_bytes(FIXTURE.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Fixture hash"):
        load_protocol(PROTOCOL, changed)
