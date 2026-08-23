"""Cross-table OMOP acceptance checks for a locally published database."""

from pathlib import Path

import duckdb
import pytest

from src.utils.config import DB_PATH


pytestmark = pytest.mark.integration

TABLE_KEYS = {
    "person": "person_id",
    "observation_period": "observation_period_id",
    "visit_occurrence": "visit_occurrence_id",
    "condition_occurrence": "condition_occurrence_id",
    "drug_exposure": "drug_exposure_id",
    "measurement": "measurement_id",
    "observation": "observation_id",
    "procedure_occurrence": "procedure_occurrence_id",
    "device_exposure": "device_exposure_id",
}

EVENTS = {
    "visit_occurrence": ("visit_start_date", "visit_end_date"),
    "condition_occurrence": ("condition_start_date", "condition_end_date"),
    "drug_exposure": ("drug_exposure_start_date", "drug_exposure_end_date"),
    "measurement": ("measurement_date", "measurement_date"),
    "observation": ("observation_date", "observation_date"),
    "procedure_occurrence": ("procedure_date", "procedure_date"),
    "device_exposure": ("device_exposure_start_date", "device_exposure_end_date"),
}

CONCEPT_FIELDS = {
    ("person", "gender_concept_id"): "Gender",
    ("person", "race_concept_id"): "Race",
    ("person", "ethnicity_concept_id"): "Ethnicity",
    ("visit_occurrence", "visit_concept_id"): "Visit",
    ("condition_occurrence", "condition_concept_id"): "Condition",
    ("drug_exposure", "drug_concept_id"): "Drug",
    ("measurement", "measurement_concept_id"): "Measurement",
    ("measurement", "unit_concept_id"): "Unit",
    ("observation", "observation_concept_id"): "Observation",
    ("procedure_occurrence", "procedure_concept_id"): "Procedure",
    ("device_exposure", "device_concept_id"): "Device",
}


@pytest.fixture(scope="module")
def con():
    if not Path(DB_PATH).is_file():
        pytest.skip("Integration database is not present; run the pipeline first.")
    connection = duckdb.connect(DB_PATH, read_only=True)
    yield connection
    connection.close()


def test_all_published_primary_keys_are_complete_and_unique(con):
    for table, key in TABLE_KEYS.items():
        nulls, duplicates = con.execute(f"""
            SELECT
                COUNT(*) FILTER (WHERE {key} IS NULL),
                COUNT(*) - COUNT(DISTINCT {key})
            FROM {table}
        """).fetchone()
        assert nulls == 0, f"{table}.{key}: {nulls} NULL values"
        assert duplicates == 0, f"{table}.{key}: {duplicates} duplicate values"


def test_observation_periods_and_event_ranges_are_ordered(con):
    invalid_periods = con.execute("""
        SELECT COUNT(*) FROM observation_period
        WHERE observation_period_start_date IS NULL
           OR observation_period_end_date IS NULL
           OR observation_period_end_date < observation_period_start_date
    """).fetchone()[0]
    assert invalid_periods == 0

    for table, (start, end) in EVENTS.items():
        invalid = con.execute(f"""
            SELECT COUNT(*) FROM {table}
            WHERE {start} IS NULL OR {end} IS NULL OR {end} < {start}
        """).fetchone()[0]
        assert invalid == 0, f"{table}: {invalid} invalid date ranges"


def test_all_events_are_covered_by_an_observation_period(con):
    for table, (start, end) in EVENTS.items():
        outside = con.execute(f"""
            SELECT COUNT(*)
            FROM {table} event
            WHERE NOT EXISTS (
                SELECT 1 FROM observation_period period
                WHERE period.person_id = event.person_id
                  AND event.{start} >= period.observation_period_start_date
                  AND event.{end} <= period.observation_period_end_date
            )
        """).fetchone()[0]
        assert outside == 0, f"{table}: {outside} events outside OBSERVATION_PERIOD"


def test_nonzero_target_concepts_are_current_standard_and_in_domain(con):
    for (table, field), expected_domain in CONCEPT_FIELDS.items():
        invalid = con.execute(f"""
            SELECT COUNT(*)
            FROM {table} event
            LEFT JOIN concept c ON event.{field} = c.concept_id
            WHERE event.{field} <> 0
              AND (
                  c.concept_id IS NULL
                  OR c.domain_id <> ?
                  OR c.standard_concept <> 'S'
                  OR c.invalid_reason IS NOT NULL
                  OR COALESCE(
                      TRY_CAST(c.valid_end_date AS DATE),
                      TRY_STRPTIME(c.valid_end_date, '%Y%m%d')::DATE
                  ) IS NULL
                  OR COALESCE(
                      TRY_CAST(c.valid_end_date AS DATE),
                      TRY_STRPTIME(c.valid_end_date, '%Y%m%d')::DATE
                  ) < CURRENT_DATE
              )
        """, [expected_domain]).fetchone()[0]
        assert invalid == 0, f"{table}.{field}: {invalid} invalid target concepts"


def test_visit_links_reference_the_same_person_and_cover_event_date(con):
    linked_events = {
        "condition_occurrence": "condition_start_date",
        "drug_exposure": "drug_exposure_start_date",
        "measurement": "measurement_date",
        "observation": "observation_date",
        "procedure_occurrence": "procedure_date",
        "device_exposure": "device_exposure_start_date",
    }
    for table, event_date in linked_events.items():
        invalid = con.execute(f"""
            SELECT COUNT(*)
            FROM {table} event
            LEFT JOIN visit_occurrence visit
              ON event.visit_occurrence_id = visit.visit_occurrence_id
            WHERE event.visit_occurrence_id IS NOT NULL
              AND (
                  visit.visit_occurrence_id IS NULL
                  OR visit.person_id <> event.person_id
                  OR event.{event_date} < visit.visit_start_date
                  OR event.{event_date} > visit.visit_end_date
              )
        """).fetchone()[0]
        assert invalid == 0, f"{table}: {invalid} inconsistent visit links"


def test_cross_domain_targets_are_not_left_as_concept_zero(con):
    misplaced_measurements = con.execute("""
        SELECT COUNT(*)
        FROM measurement m
        JOIN concept_relationship cr
          ON cr.concept_id_1 = m.measurement_source_concept_id
         AND cr.relationship_id = 'Maps to'
         AND cr.invalid_reason IS NULL
        JOIN concept target
          ON target.concept_id = cr.concept_id_2
         AND target.standard_concept = 'S'
         AND target.invalid_reason IS NULL
        WHERE m.measurement_concept_id = 0
          AND target.domain_id <> 'Measurement'
    """).fetchone()[0]
    assert misplaced_measurements == 0

    misplaced_procedures = con.execute("""
        SELECT COUNT(*)
        FROM procedure_occurrence p
        JOIN concept_relationship cr
          ON cr.concept_id_1 = p.procedure_source_concept_id
         AND cr.relationship_id = 'Maps to'
         AND cr.invalid_reason IS NULL
        JOIN concept target
          ON target.concept_id = cr.concept_id_2
         AND target.standard_concept = 'S'
         AND target.invalid_reason IS NULL
        WHERE p.procedure_concept_id = 0
    """).fetchone()[0]
    assert misplaced_procedures == 0
