"""Build observation periods with an explicit evidence hierarchy."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.mapping.governance import current_run_id
from src.omop.cdm54 import create_table_sql
from src.utils.config import DB_PATH


def ensure_observation_period_provenance(con) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS observation_period_provenance (
            observation_period_id BIGINT NOT NULL,
            person_id BIGINT NOT NULL,
            run_id VARCHAR NOT NULL,
            derivation_method VARCHAR NOT NULL,
            encounter_start_date DATE,
            encounter_end_date DATE,
            event_start_date DATE,
            event_end_date DATE,
            derived_start_date DATE NOT NULL,
            derived_end_date DATE NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (observation_period_id, run_id)
        )
    """)


def build_observation_periods(con, run_id: str | None = None) -> int:
    """Derive one auditable period per person from encounters and event evidence."""
    effective_run_id = run_id or current_run_id() or "UNTRACKED"
    ensure_observation_period_provenance(con)
    con.execute("DROP TABLE IF EXISTS observation_period")
    con.execute(create_table_sql("observation_period"))
    con.execute("DROP TABLE IF EXISTS temp_observation_evidence")
    con.execute("""
        CREATE TEMPORARY TABLE temp_observation_evidence AS
        WITH event_rows AS (
            SELECT person_id, condition_start_date AS start_date,
                   COALESCE(condition_end_date, condition_start_date) AS end_date
            FROM condition_occurrence
            UNION ALL
            SELECT person_id, drug_exposure_start_date,
                   COALESCE(drug_exposure_end_date, drug_exposure_start_date)
            FROM drug_exposure
            UNION ALL
            SELECT person_id, measurement_date, measurement_date FROM measurement
            UNION ALL
            SELECT person_id, observation_date, observation_date FROM observation
            UNION ALL
            SELECT person_id, procedure_date, procedure_date FROM procedure_occurrence
            UNION ALL
            SELECT person_id, device_exposure_start_date,
                   COALESCE(device_exposure_end_date, device_exposure_start_date)
            FROM device_exposure
        ), event_bounds AS (
            SELECT person_id, MIN(start_date) AS event_start_date,
                   MAX(end_date) AS event_end_date
            FROM event_rows WHERE start_date IS NOT NULL GROUP BY person_id
        ), encounter_bounds AS (
            SELECT person_id, MIN(visit_start_date) AS encounter_start_date,
                   MAX(visit_end_date) AS encounter_end_date
            FROM visit_occurrence WHERE visit_start_date IS NOT NULL GROUP BY person_id
        )
        SELECT COALESCE(encounter.person_id, event.person_id) AS person_id,
               encounter.encounter_start_date,
               encounter.encounter_end_date,
               event.event_start_date,
               event.event_end_date,
               CASE
                 WHEN encounter.person_id IS NULL THEN event.event_start_date
                 WHEN event.person_id IS NULL THEN encounter.encounter_start_date
                 ELSE LEAST(encounter.encounter_start_date, event.event_start_date)
               END AS derived_start_date,
               CASE
                 WHEN encounter.person_id IS NULL THEN event.event_end_date
                 WHEN event.person_id IS NULL THEN encounter.encounter_end_date
                 ELSE GREATEST(encounter.encounter_end_date, event.event_end_date)
               END AS derived_end_date,
               CASE
                 WHEN encounter.person_id IS NULL THEN 'CLINICAL_EVENT_ENVELOPE'
                 WHEN event.person_id IS NULL
                   OR (event.event_start_date >= encounter.encounter_start_date
                       AND event.event_end_date <= encounter.encounter_end_date)
                   THEN 'FHIR_ENCOUNTER_COVERAGE'
                 ELSE 'ENCOUNTER_PLUS_EVENT_ENVELOPE'
               END AS derivation_method
        FROM encounter_bounds encounter
        FULL OUTER JOIN event_bounds event USING (person_id)
    """)
    con.execute("""
        INSERT INTO observation_period (
            observation_period_id, person_id,
            observation_period_start_date, observation_period_end_date,
            period_type_concept_id
        )
        SELECT person_id, person_id, derived_start_date, derived_end_date, 32817
        FROM temp_observation_evidence
        WHERE derived_start_date IS NOT NULL AND derived_end_date IS NOT NULL
    """)
    con.execute(
        "DELETE FROM observation_period_provenance WHERE run_id = ?", [effective_run_id]
    )
    con.execute("""
        INSERT INTO observation_period_provenance (
            observation_period_id, person_id, run_id, derivation_method,
            encounter_start_date, encounter_end_date, event_start_date,
            event_end_date, derived_start_date, derived_end_date
        )
        SELECT person_id, person_id, ?, derivation_method,
               encounter_start_date, encounter_end_date, event_start_date,
               event_end_date, derived_start_date, derived_end_date
        FROM temp_observation_evidence
        WHERE derived_start_date IS NOT NULL AND derived_end_date IS NOT NULL
    """, [effective_run_id])
    return con.execute("SELECT COUNT(*) FROM observation_period").fetchone()[0]


def run_observation_period_etl():
    print("⚙️ STARTING GOVERNED OBSERVATION_PERIOD DERIVATION")
    print("-" * 50)
    with duckdb.connect(DB_PATH) as con:
        con.execute("BEGIN TRANSACTION")
        mapped_count = build_observation_periods(con)
        methods = con.execute("""
            SELECT derivation_method, COUNT(*)
            FROM observation_period_provenance
            WHERE run_id = COALESCE(?, 'UNTRACKED')
            GROUP BY derivation_method ORDER BY derivation_method
        """, [current_run_id()]).fetchall()
        con.execute("COMMIT")
    print(f"\n✅ Derived {mapped_count} observation periods")
    for method, count in methods:
        print(f" - {method}: {count}")


if __name__ == "__main__":
    run_observation_period_etl()
