from copy import deepcopy

import pytest

from src.benchmark.compare_prompt_calibrations import compare_reports, render_markdown


def _report():
    cases = [{
        "case_id": "POS-001", "source_value": "CREA",
        "expected_decision": "SELECT", "expected_concept_id": 1,
        "retrieval_hit": False, "decision": "ABSTAIN",
        "selected_concept_id": None, "selected_correctly": False,
    }]
    return {
        "status": "DEVELOPMENT_ONLY", "deployment_authorized": False,
        "model": "qwen", "prompt_version": "v1", "top_k": 5,
        "sample_mode": "expanded", "split": "holdout",
        "loinc_reranker_version": "before", "cases": cases,
        "summary": {
            "positive_cases": 1, "retrieval_hits": 0,
            "correct_positive_selections": 0, "positive_abstentions": 1,
            "negative_cases": 0, "safe_negative_abstentions": 0,
            "contract_failures": 0,
        },
        "threshold_analysis": [{
            "threshold": 0.8, "admitted_proposals": 0,
            "correct_proposals": 0, "incorrect_proposals": 0,
            "review_queue_precision": None, "positive_case_coverage": 0.0,
        }],
    }


def test_comparison_reports_improvement_and_transition():
    before = _report()
    after = deepcopy(before)
    after["loinc_reranker_version"] = "after"
    after["cases"][0].update({
        "retrieval_hit": True, "decision": "SELECT",
        "selected_concept_id": 1, "selected_correctly": True,
    })
    after["summary"].update({
        "retrieval_hits": 1, "correct_positive_selections": 1,
        "positive_abstentions": 0,
    })
    after["threshold_analysis"][0].update({
        "admitted_proposals": 1, "correct_proposals": 1,
        "review_queue_precision": 1.0, "positive_case_coverage": 1.0,
    })

    comparison = compare_reports(before, after)

    assert comparison["summary"]["retrieval_hits"]["delta"] == 1
    assert comparison["summary"]["positive_abstentions"]["delta"] == -1
    assert comparison["case_transitions"][0]["case_id"] == "POS-001"
    assert "0 → 1" in render_markdown(comparison)


def test_comparison_rejects_different_case_sets():
    before = _report()
    after = deepcopy(before)
    after["cases"][0]["source_value"] = "Different"

    with pytest.raises(ValueError, match="same labelled cases"):
        compare_reports(before, after)
