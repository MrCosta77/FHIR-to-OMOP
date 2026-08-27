import duckdb

from src.etl.observation_period import build_observation_periods


def _connection():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE visit_occurrence (
            person_id BIGINT, visit_start_date DATE, visit_end_date DATE
        );
        CREATE TABLE condition_occurrence (
            person_id BIGINT, condition_start_date DATE, condition_end_date DATE
        );
        CREATE TABLE drug_exposure (
            person_id BIGINT, drug_exposure_start_date DATE, drug_exposure_end_date DATE
        );
        CREATE TABLE measurement (person_id BIGINT, measurement_date DATE);
        CREATE TABLE observation (person_id BIGINT, observation_date DATE);
        CREATE TABLE procedure_occurrence (person_id BIGINT, procedure_date DATE);
        CREATE TABLE device_exposure (
            person_id BIGINT, device_exposure_start_date DATE, device_exposure_end_date DATE
        );
    """)
    return con


def test_observation_period_records_evidence_and_fallback_method():
    con = _connection()
    con.executemany(
        "INSERT INTO visit_occurrence VALUES (?, ?, ?)",
        [
            (1, "2025-01-10", "2025-01-12"),
            (3, "2025-03-10", "2025-03-12"),
            (4, "2025-04-10", "2025-04-10"),
        ],
    )
    con.executemany(
        "INSERT INTO condition_occurrence VALUES (?, ?, ?)",
        [
            (1, "2025-01-11", "2025-01-11"),
            (2, "2025-02-03", "2025-02-05"),
            (3, "2025-03-09", "2025-03-13"),
        ],
    )

    assert build_observation_periods(con, "TEST-RUN") == 4

    periods = {
        row[0]: (str(row[1]), str(row[2]))
        for row in con.execute("""
            SELECT person_id, observation_period_start_date, observation_period_end_date
            FROM observation_period ORDER BY person_id
        """).fetchall()
    }
    assert periods == {
        1: ("2025-01-10", "2025-01-12"),
        2: ("2025-02-03", "2025-02-05"),
        3: ("2025-03-09", "2025-03-13"),
        4: ("2025-04-10", "2025-04-10"),
    }
    methods = dict(con.execute("""
        SELECT person_id, derivation_method
        FROM observation_period_provenance WHERE run_id = 'TEST-RUN'
    """).fetchall())
    assert methods == {
        1: "FHIR_ENCOUNTER_COVERAGE",
        2: "CLINICAL_EVENT_ENVELOPE",
        3: "ENCOUNTER_PLUS_EVENT_ENVELOPE",
        4: "FHIR_ENCOUNTER_COVERAGE",
    }


def test_observation_period_derivation_is_idempotent_per_run():
    con = _connection()
    con.execute("INSERT INTO measurement VALUES (1, '2025-01-10')")
    build_observation_periods(con, "TEST-RUN")
    build_observation_periods(con, "TEST-RUN")
    assert con.execute("SELECT COUNT(*) FROM observation_period").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM observation_period_provenance").fetchone()[0] == 1
