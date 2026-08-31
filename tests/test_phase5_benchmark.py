import hashlib
import json
from pathlib import Path

import duckdb
import pytest

import src.benchmark.evaluate_phase5 as phase5
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
SUMMARY = PROJECT_ROOT / "benchmarks" / "dirty_hospital" / "phase5_held_out_summary.json"


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
        "provenance": {
            "database": "private-path", "git_commit": "abc",
            "onnxruntime": "1.28.0", "onnx_providers": ["CUDAExecutionProvider"],
        },
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
    assert summary["provenance"]["onnx_providers"] == ["CUDAExecutionProvider"]
    assert "cases" not in summary["arms"]["arm"]
    assert summary["clinical_validation_required"] is True


def test_protocol_rejects_a_different_fixture(tmp_path):
    changed = tmp_path / "cases.jsonl"
    changed.write_bytes(FIXTURE.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Fixture hash"):
        load_protocol(PROTOCOL, changed)


def test_versioned_phase5_result_is_case_free_and_matches_frozen_run():
    text = SUMMARY.read_text(encoding="utf-8")
    result = json.loads(text)
    assert '"cases"' not in text
    assert '"expected"' not in text
    assert '"database"' not in text
    assert result["selection"] == {"case_count": 40, "split": "held_out"}
    assert result["provenance"]["git_commit"].startswith("b59e7c7")
    assert result["arms"]["deterministic-code-only"]["metrics"]["overall_accuracy"] == 0.5
    assert result["arms"]["fuzzy-lexical"]["metrics"]["wrong_map"] == 1
    for name in ("retrieval-llm-qwen", "retrieval-llm-llama"):
        assert result["arms"][name]["performance"]["contract_failures"] == 0


def test_phase5_evaluator_orchestrates_all_arms_without_external_services(
    tmp_path, monkeypatch
):
    fixture = tmp_path / "cases.jsonl"
    protocol_path = tmp_path / "phase5_protocol.json"
    database = tmp_path / "omop.duckdb"
    fixture.write_text("smoke fixture\n", encoding="utf-8")
    protocol_path.write_text("{}", encoding="utf-8")

    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE cdm_source (vocabulary_version VARCHAR)")
        connection.execute("INSERT INTO cdm_source VALUES ('test-vocabulary')")
        connection.execute(
            "CREATE TABLE etl_run "
            "(run_id VARCHAR, status VARCHAR, completed_at TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO etl_run VALUES "
            "('RUN-test', 'SUCCESS', CURRENT_TIMESTAMP)"
        )

    case = {
        "case_id": "phase5-smoke-1",
        "split": "held_out",
        "domain": "Condition",
        "source": {"text": "unknown condition"},
        "context": {},
        "expected": {"decision": "ABSTAIN", "concept_id": None},
    }
    protocol = {
        "protocol_version": "phase5-v1",
        "fixture_sha256": "test-hash",
        "evaluation_split": "held_out",
        "retrieval": {"top_k": 5},
        "threshold_curve": [0.0, 0.9],
        "generation": {"timeout_seconds": 1},
        "policy": {"held_out_adjustment_forbidden": True},
        "arms": [
            {"name": "deterministic-code-only"},
            {"name": "fuzzy-lexical", "selection_threshold": 0.9},
            {"name": "embedding-retrieval", "selection_threshold": 0.9},
            {
                "name": "retrieval-llm-qwen",
                "selection_threshold": 0.9,
                "model": "qwen-test",
            },
            {
                "name": "retrieval-llm-llama",
                "selection_threshold": 0.9,
                "model": "llama-test",
            },
        ],
    }

    class FakeCollection:
        metadata = {"index_signature": "test-index"}

        @staticmethod
        def count():
            return 1

    def abstain_prediction(*args, **kwargs):
        return {
            "decision": "ABSTAIN",
            "concept_id": None,
            "score": 0.0,
            "raw_decision": "ABSTAIN",
            "raw_concept_id": None,
            "retrieval_candidate_ids": [],
        }

    monkeypatch.setattr(phase5, "load_protocol", lambda *args: protocol)
    monkeypatch.setattr(phase5, "load_cases", lambda *args: [case])
    monkeypatch.setattr(phase5, "validate_cases", lambda *args: None)
    monkeypatch.setattr(phase5, "validate_reference_concepts", lambda *args: None)
    monkeypatch.setattr(
        phase5, "get_versioned_collection", lambda *args: FakeCollection()
    )
    monkeypatch.setattr(phase5, "deterministic_prediction", abstain_prediction)
    monkeypatch.setattr(phase5, "fuzzy_prediction", abstain_prediction)
    monkeypatch.setattr(phase5, "embedding_prediction", abstain_prediction)
    monkeypatch.setattr(
        phase5,
        "llm_prediction",
        lambda *args, **kwargs: (abstain_prediction(), {"llm_called": False}),
    )
    monkeypatch.setattr(phase5, "_model_digest", lambda *args: "sha256:test")
    monkeypatch.setattr(phase5, "_git_commit", lambda: "test-commit")

    report = phase5.evaluate_phase5(
        fixture,
        database,
        protocol_path,
        tmp_path / "chroma",
        client=object(),
    )

    assert list(report["arms"]) == [
        "deterministic-code-only",
        "fuzzy-lexical",
        "embedding-retrieval",
        "retrieval-llm-qwen",
        "retrieval-llm-llama",
    ]
    assert report["selection"] == {"split": "held_out", "case_count": 1}
    assert report["provenance"]["vocabulary_version"] == "test-vocabulary"
    assert report["provenance"]["etl_run_id"] == "RUN-test"
    assert all(
        arm["metrics"]["overall_accuracy"] == 1.0
        for arm in report["arms"].values()
    )
    for name in ("retrieval-llm-qwen", "retrieval-llm-llama"):
        assert report["arms"][name]["performance"]["llm_calls"] == 0
        assert report["arms"][name]["performance"]["contract_failures"] == 0
