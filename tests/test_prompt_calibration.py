import duckdb
import pytest

from src.benchmark.calibrate_mapping_prompt import (
    NEGATIVE_TERMS,
    POSITIVE_TERMS,
    _text_sha256,
    calibration_few_shot,
    calibration_reference_exclusions,
    load_expanded_cases,
    load_probe_cases,
    summarize,
    threshold_analysis,
    validate_calibration_references,
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


def _concept_table(con):
    con.execute("""
        CREATE TABLE concept (
            concept_id INTEGER, concept_name VARCHAR, domain_id VARCHAR,
            vocabulary_id VARCHAR, standard_concept VARCHAR,
            invalid_reason VARCHAR
        )
    """)


def test_calibration_references_must_match_searchable_loinc_slice():
    cases = [{
        "case_id": "POS-01",
        "expected_decision": "SELECT",
        "expected_concept_id": 100,
        "expected_concept_name": "Creatinine in serum",
    }]
    with duckdb.connect(":memory:") as con:
        _concept_table(con)
        con.execute(
            "INSERT INTO concept VALUES (100, ?, 'Measurement', 'LOINC', 'S', NULL)",
            ["Creatinine in serum"],
        )

        evidence = validate_calibration_references(con, cases)

    assert evidence["searchable_slice_validated"] is True
    assert evidence["clinical_gold_standard"] is False
    assert evidence["positive_reference_count"] == 1


@pytest.mark.parametrize(
    "row",
    [
        (100, "Creatinine in serum", "Procedure", "LOINC", "S", None),
        (100, "Creatinine in serum", "Measurement", "SNOMED", "S", None),
        (100, "Creatinine in serum", "Measurement", "LOINC", None, None),
        (100, "Creatinine in serum", "Measurement", "LOINC", "S", "D"),
        (100, "Different name", "Measurement", "LOINC", "S", None),
    ],
)
def test_calibration_rejects_reference_outside_searchable_slice(row):
    cases = [{
        "case_id": "POS-01",
        "expected_decision": "SELECT",
        "expected_concept_id": 100,
        "expected_concept_name": "Creatinine in serum",
    }]
    with duckdb.connect(":memory:") as con:
        _concept_table(con)
        con.execute("INSERT INTO concept VALUES (?, ?, ?, ?, ?, ?)", row)

        with pytest.raises(ValueError, match="outside the searchable"):
            validate_calibration_references(con, cases)


def test_expanded_cases_are_deterministic_split_and_exclude_ambiguous_labels():
    with duckdb.connect(":memory:") as con:
        con.execute("""
            CREATE TABLE lis_noise_ground_truth (
                measurement_id BIGINT, true_concept_id INTEGER,
                true_source_value VARCHAR, corrupted_source_value VARCHAR
            )
        """)
        con.executemany(
            "INSERT INTO lis_noise_ground_truth VALUES (?, ?, ?, ?)",
            [
                (1, 100, "Truth A", "LIS-A"),
                (2, 101, "Truth B", "LIS-B"),
                (3, 102, "Truth C", "AMBIG"),
                (4, 103, "Truth D", "AMBIG"),
            ],
        )
        _concept_table(con)
        con.executemany(
            "INSERT INTO concept VALUES (?, ?, 'Measurement', 'LOINC', 'S', NULL)",
            [
                (100, "Truth A"),
                (101, "Truth B"),
                (102, "Truth C"),
                (103, "Truth D"),
            ],
        )
        first = load_expanded_cases(con, positive_limit=10, split="all")
        second = load_expanded_cases(con, positive_limit=10, split="all")

    assert first == second
    positive_sources = {
        case["source_value"] for case in first
        if case["expected_decision"] == "SELECT"
    }
    assert positive_sources == {"LIS-A", "LIS-B"}


def test_synthetic_few_shot_is_holdout_only_and_uses_development_cases():
    with duckdb.connect(":memory:") as con:
        con.execute("""
            CREATE TABLE lis_noise_ground_truth (
                measurement_id BIGINT, true_concept_id INTEGER,
                true_source_value VARCHAR, corrupted_source_value VARCHAR
            )
        """)
        con.executemany(
            "INSERT INTO lis_noise_ground_truth VALUES (?, ?, ?, ?)",
            [
                (index, 1000 + index, f"Truth {index}", f"LIS-{index:03d}")
                for index in range(1, 101)
            ],
        )
        _concept_table(con)
        con.executemany(
            "INSERT INTO concept VALUES (?, ?, 'Measurement', 'LOINC', 'S', NULL)",
            [(1000 + index, f"Truth {index}") for index in range(1, 101)],
        )
        prompt, manifest = calibration_few_shot(
            con,
            mode="synthetic-development",
            sample_mode="expanded",
            evaluation_split="holdout",
            example_limit=3,
            evaluation_cases=[],
        )

        development_ids = {
            case["expected_concept_id"]
            for case in load_expanded_cases(
                con, positive_limit=100, split="development"
            )
            if case["expected_decision"] == "SELECT"
        }
        assert len(manifest) == 3
        assert {row["expected_concept_id"] for row in manifest} <= development_ids
        assert "synthetic_development_examples" in prompt

        with pytest.raises(ValueError, match="prevent calibration leakage"):
            calibration_few_shot(
                con,
                mode="synthetic-development",
                sample_mode="expanded",
                evaluation_split="development",
                evaluation_cases=[],
            )


def test_approved_arm_excludes_evaluation_identity_and_concept_family():
    with duckdb.connect(":memory:") as con:
        con.execute("""
            CREATE TABLE mapping_provenance (
                provenance_id BIGINT, target_table VARCHAR,
                source_value VARCHAR, assigned_concept_id INTEGER,
                normalized_value VARCHAR, reviewed_by VARCHAR,
                created_at TIMESTAMP
            );
            INSERT INTO mapping_provenance VALUES
                (1, 'measurement', 'CREA', 3016723, 'Creatinine',
                 'Approved_by_Human', '2026-01-01'),
                (2, 'measurement', 'Creatinine legacy', 3016723, 'Creatinine',
                 'Approved_by_Human', '2026-01-02'),
                (3, 'measurement', 'WBC', 3000905, 'Leukocytes',
                 'Approved_by_Human', '2026-01-03');
        """)

        prompt, manifest = calibration_few_shot(
            con,
            mode="approved",
            sample_mode="expanded",
            evaluation_split="holdout",
            evaluation_cases=[{
                "source_value": "crea",
                "expected_concept_id": 3016723,
            }],
        )

    assert manifest == [{
        "source_value": "WBC",
        "expected_concept_id": 3000905,
        "expected_concept_name": "Leukocytes",
    }]
    assert "human_approved_examples" in prompt
    assert "CREA" not in prompt
    assert "Creatinine legacy" not in prompt


def test_approved_few_shot_requires_cases_for_leakage_control():
    with duckdb.connect(":memory:") as con:
        with pytest.raises(ValueError, match="requires the labelled evaluation cases"):
            calibration_few_shot(
                con,
                mode="approved",
                sample_mode="expanded",
                evaluation_split="holdout",
            )


def test_few_shot_hash_identifies_exact_rendered_text():
    first = _text_sha256("example A\n")
    second = _text_sha256("example B\n")

    assert first != second
    assert first == _text_sha256("example A\n")


def test_expanded_sample_excludes_and_reports_out_of_scope_truth():
    with duckdb.connect(":memory:") as con:
        con.execute("""
            CREATE TABLE lis_noise_ground_truth (
                measurement_id BIGINT, true_concept_id INTEGER,
                true_source_value VARCHAR, corrupted_source_value VARCHAR
            )
        """)
        con.executemany(
            "INSERT INTO lis_noise_ground_truth VALUES (?, ?, ?, ?)",
            [
                (1, 100, "Canonical LOINC", "LOCAL-A"),
                (2, 200, "Procedure-like SNOMED", "LOCAL-B"),
            ],
        )
        _concept_table(con)
        con.executemany(
            "INSERT INTO concept VALUES (?, ?, 'Measurement', ?, 'S', NULL)",
            [
                (100, "Canonical LOINC", "LOINC"),
                (200, "SNOMED label", "SNOMED"),
            ],
        )

        cases = load_expanded_cases(con, positive_limit=10, split="all")
        exclusions = calibration_reference_exclusions(con)

    positive_ids = {
        case["expected_concept_id"]
        for case in cases
        if case["expected_decision"] == "SELECT"
    }
    assert positive_ids == {100}
    assert exclusions == {"OUTSIDE_LOINC_VOCABULARY": 1}


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


def test_threshold_analysis_never_treats_abstention_as_review_proposal():
    cases = [
        {
            "expected_decision": "SELECT", "decision": "SELECT",
            "selected_correctly": True, "governed_score": 0.82,
        },
        {
            "expected_decision": "SELECT", "decision": "SELECT",
            "selected_correctly": False, "governed_score": 0.78,
        },
        {
            "expected_decision": "ABSTAIN", "decision": "ABSTAIN",
            "selected_correctly": None, "governed_score": 0.99,
        },
    ]

    at_080 = next(
        row for row in threshold_analysis(cases) if row["threshold"] == 0.80
    )
    assert at_080 == {
        "threshold": 0.80,
        "admitted_proposals": 1,
        "correct_proposals": 1,
        "incorrect_proposals": 0,
        "review_queue_precision": 1.0,
        "positive_case_recall": 0.5,
    }
