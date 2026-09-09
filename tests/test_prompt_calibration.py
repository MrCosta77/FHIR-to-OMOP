import duckdb
import pytest

from src.benchmark.calibrate_mapping_prompt import (
    NEGATIVE_TERMS,
    POSITIVE_TERMS,
    load_probe_cases,
    summarize,
)


def test_probe_cases_are_fixed_balanced_and_grounded():
    with duckdb.connect(":memory:") as con:
        con.execute("""
            CREATE TABLE lis_noise_ground_truth (
                measurement_id BIGINT,
                true_concept_id INTEGER,
                true_source_value VARCHAR,
                corrupted_source_value VARCHAR
            )
        """)
        con.executemany(
            "INSERT INTO lis_noise_ground_truth VALUES (?, ?, ?, ?)",
            [
                (index, 1000 + index, f"Truth {index}", source)
                for index, source in enumerate(POSITIVE_TERMS, start=1)
            ],
        )
        cases = load_probe_cases(con)

    assert len(cases) == len(POSITIVE_TERMS) + len(NEGATIVE_TERMS)
    assert sum(case["expected_decision"] == "SELECT" for case in cases) == 8
    assert sum(case["expected_decision"] == "ABSTAIN" for case in cases) == 4


def test_probe_refuses_missing_ground_truth():
    with duckdb.connect(":memory:") as con:
        con.execute("""
            CREATE TABLE lis_noise_ground_truth (
                measurement_id BIGINT,
                true_concept_id INTEGER,
                true_source_value VARCHAR,
                corrupted_source_value VARCHAR
            )
        """)
        with pytest.raises(ValueError, match="does not contain probe terms"):
            load_probe_cases(con)


def test_probe_summary_separates_retrieval_llm_and_negative_safety():
    cases = [
        {
            "expected_decision": "SELECT", "retrieval_hit": True,
            "selected_correctly": True, "decision": "SELECT",
            "contract_error": None,
        },
        {
            "expected_decision": "SELECT", "retrieval_hit": False,
            "selected_correctly": False, "decision": "ABSTAIN",
            "contract_error": None,
        },
        {
            "expected_decision": "ABSTAIN", "retrieval_hit": None,
            "selected_correctly": None, "decision": "ABSTAIN",
            "contract_error": None,
        },
    ]

    assert summarize(cases) == {
        "positive_cases": 2,
        "retrieval_hits": 1,
        "correct_positive_selections": 1,
        "positive_abstentions": 1,
        "negative_cases": 1,
        "safe_negative_abstentions": 1,
        "contract_failures": 0,
    }
