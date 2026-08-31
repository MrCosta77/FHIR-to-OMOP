import json
import os
from collections import Counter, defaultdict
from pathlib import Path

import pytest

from benchmarks.dirty_hospital.build_fixture import build_cases, write_fixture
from src.benchmark.evaluate_dirty_hospital import (
    evaluate,
    load_cases,
    score_predictions,
    validate_cases,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "benchmarks" / "dirty_hospital" / "cases.jsonl"
DATABASE = Path(
    os.environ.get("CMF_DB_PATH", PROJECT_ROOT / "data" / "omop_clinical.duckdb")
)


def test_release_fixture_contract_and_no_split_leakage():
    cases = load_cases(FIXTURE)
    validate_cases(cases)
    assert len(cases) == 100
    assert Counter(case["split"] for case in cases) == {"development": 60, "held_out": 40}
    assert Counter(case["domain"] for case in cases) == {
        "Condition": 20, "Measurement": 20, "Procedure": 20, "Observation": 20, "Drug": 20
    }
    assert Counter(case["expected"]["decision"] for case in cases) == {"MAP": 75, "ABSTAIN": 25}

    concepts_by_split = defaultdict(set)
    families_by_split = defaultdict(set)
    for case in cases:
        families_by_split[case["split"]].add(case["family_id"])
        if case["expected"]["concept_id"] is not None:
            concepts_by_split[case["split"]].add(case["expected"]["concept_id"])
    assert concepts_by_split["development"].isdisjoint(concepts_by_split["held_out"])
    assert families_by_split["development"].isdisjoint(families_by_split["held_out"])


def test_fixture_builder_is_byte_deterministic():
    output_dir = PROJECT_ROOT / ".pytest-tmp-benchmark-builder"
    generated, manifest_path = write_fixture(output_dir)
    assert generated.read_bytes() == FIXTURE.read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["case_count"] == 100
    assert manifest["clinical_validation_required"] is True
    assert manifest["curation_status"] == "PROVISIONAL_TECHNICAL"


def test_scorer_counts_safe_abstention_separately():
    cases = build_cases()
    predictions = []
    for case in cases:
        if case["variant"] == "coded_exact":
            predictions.append({"decision": "MAP", "concept_id": case["expected"]["concept_id"]})
        else:
            predictions.append({"decision": "ABSTAIN", "concept_id": None})
    result = score_predictions(cases, predictions)
    metrics = result["metrics"]
    assert metrics["correct_map"] == 25
    assert metrics["correct_abstain"] == 25
    assert metrics["missed_map"] == 50
    assert metrics["false_map"] == 0
    assert metrics["accepted_precision"] == 1.0
    assert metrics["mappable_recall"] == pytest.approx(1 / 3)
    assert metrics["coverage"] == 0.25
    assert metrics["overall_accuracy"] == 0.5
    assert result["by_split"]["development"]["total"] == 60
    assert result["by_split"]["held_out"]["total"] == 40


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE.exists(), reason="Local Athena-backed DuckDB is not available")
def test_deterministic_baseline_against_official_vocabulary():
    report = evaluate(FIXTURE, DATABASE)
    assert report["provenance"]["vocabulary_version"]
    assert report["metrics"]["accepted_precision"] == 1.0
    assert report["metrics"]["mappable_recall"] == pytest.approx(1 / 3)
    assert report["metrics"]["false_map"] == 0
    assert report["metrics"]["overall_accuracy"] == 0.5
