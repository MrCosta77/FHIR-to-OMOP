from pathlib import Path

from src.benchmark.evaluate_dirty_hospital import load_cases
from src.benchmark.calibrate_development import (
    load_development_protocol,
    select_domain_thresholds,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "benchmarks" / "dirty_hospital" / "cases.jsonl"
PROTOCOL = (
    PROJECT_ROOT / "benchmarks" / "dirty_hospital" /
    "phase6_development_protocol.json"
)


def test_calibration_protocol_is_development_only():
    protocol = load_development_protocol(PROTOCOL, FIXTURE)
    assert protocol["split"] == "development"
    assert protocol["policy"]["held_out_access_forbidden"] is True
    cases = [case for case in load_cases(FIXTURE) if case["split"] == protocol["split"]]
    assert len(cases) == 60
    assert all(case["split"] == "development" for case in cases)


def test_domain_threshold_selection_maximizes_recall_under_safety_constraints():
    cases = [
        {"case_id": "1", "split": "development", "domain": "Condition",
         "expected": {"decision": "MAP", "concept_id": 10}},
        {"case_id": "2", "split": "development", "domain": "Condition",
         "expected": {"decision": "MAP", "concept_id": 20}},
        {"case_id": "3", "split": "development", "domain": "Condition",
         "expected": {"decision": "ABSTAIN", "concept_id": None}},
    ]
    predictions = [
        {"raw_decision": "MAP", "raw_concept_id": 10, "score": 0.95},
        {"raw_decision": "MAP", "raw_concept_id": 20, "score": 0.8},
        {"raw_decision": "MAP", "raw_concept_id": 99, "score": 0.7},
    ]
    result = select_domain_thresholds(
        cases, predictions, [0.7, 0.8, 0.9],
        minimum_precision=1.0, maximum_false_maps=0,
    )
    selected = result["Condition"]
    assert selected["selected_threshold"] == 0.8
    assert selected["selected_metrics"]["mappable_recall"] == 1.0
    assert selected["selected_metrics"]["false_map"] == 0
