import pytest

from src.quality.merge_dqd_results import merge_dqd_results


def _shard(check_id, category="Plausibility", failed=0, error=0):
    return {
        "startTimestamp": ["2026-01-01 10:00:00"],
        "endTimestamp": ["2026-01-01 10:00:01"],
        "executionTimeSeconds": [1.0],
        "Metadata": [{"cdmVersion": "5.4", "dqdVersion": "2.8.9"}],
        "CheckResults": [{
            "checkId": check_id,
            "category": category,
            "failed": failed,
            "isError": error,
            "passed": int(not failed and not error),
        }],
    }


def test_merge_preserves_disjoint_checks_and_recalculates_overview():
    merged = merge_dqd_results([
        _shard("base-check", category="Conformance"),
        _shard("high-check", failed=1),
    ])
    assert len(merged["CheckResults"]) == 2
    assert merged["Overview"]["countTotal"] == [2]
    assert merged["Overview"]["countPassed"] == [1]
    assert merged["Overview"]["countThresholdFailed"] == [1]
    assert merged["Overview"]["countFailedPlausibility"] == [1]


def test_merge_rejects_overlapping_check_ids():
    with pytest.raises(ValueError, match="Duplicate"):
        merge_dqd_results([_shard("duplicate"), _shard("duplicate")])


def test_merge_rejects_different_metadata():
    second = _shard("second")
    second["Metadata"][0]["cdmVersion"] = "5.3"
    with pytest.raises(ValueError, match="different CDM metadata"):
        merge_dqd_results([_shard("first"), second])
