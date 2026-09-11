import duckdb
import pytest

from src.simulation.inject_lis_noise import inject_lis_noise


def _create_database(con):
    con.execute("""
        CREATE TABLE etl_run (
            run_id VARCHAR, status VARCHAR, started_at TIMESTAMP,
            completed_at TIMESTAMP
        );
        CREATE TABLE measurement (
            measurement_id BIGINT PRIMARY KEY,
            measurement_concept_id INTEGER,
            measurement_source_concept_id INTEGER,
            measurement_source_value VARCHAR
        );
        INSERT INTO measurement VALUES
            (1, 100, 100, 'Glucose [Mass/volume] in Blood'),
            (2, 200, 200, 'Creatinine [Mass/volume] in Serum or Plasma');
    """)


def test_reinjection_archives_prior_ground_truth_snapshot():
    with duckdb.connect(":memory:") as con:
        _create_database(con)
        assert inject_lis_noise(con, run_id="RUN-one", noise_ratio=0.5) == 1
        first = con.execute("""
            SELECT simulation_run_id, measurement_id, corrupted_source_value
            FROM lis_noise_ground_truth
        """).fetchone()

        con.execute("""
            UPDATE measurement
            SET measurement_concept_id = CASE measurement_id WHEN 1 THEN 100 ELSE 200 END,
                measurement_source_concept_id =
                    CASE measurement_id WHEN 1 THEN 100 ELSE 200 END,
                measurement_source_value = CASE measurement_id
                    WHEN 1 THEN 'Glucose [Mass/volume] in Blood'
                    ELSE 'Creatinine [Mass/volume] in Serum or Plasma'
                END
        """)
        assert inject_lis_noise(con, run_id="RUN-two", noise_ratio=1.0) == 2

        assert con.execute("""
            SELECT simulation_run_id, measurement_id, corrupted_source_value
            FROM lis_noise_ground_truth_history
        """).fetchall() == [first]
        assert con.execute("""
            SELECT DISTINCT simulation_run_id FROM lis_noise_ground_truth
        """).fetchall() == [("RUN-two",)]


def test_invalid_ratio_does_not_replace_current_snapshot():
    with duckdb.connect(":memory:") as con:
        _create_database(con)
        inject_lis_noise(con, run_id="RUN-one", noise_ratio=0.5)
        before = con.execute(
            "SELECT * FROM lis_noise_ground_truth ORDER BY measurement_id"
        ).fetchall()

        with pytest.raises(ValueError, match="between 0 and 1"):
            inject_lis_noise(con, run_id="RUN-two", noise_ratio=1.5)

        assert con.execute(
            "SELECT * FROM lis_noise_ground_truth ORDER BY measurement_id"
        ).fetchall() == before


def test_legacy_snapshot_is_migrated_to_history():
    with duckdb.connect(":memory:") as con:
        _create_database(con)
        con.execute("""
            INSERT INTO etl_run VALUES (
                'RUN-legacy', 'SUCCESS', '2026-01-01', '2026-01-01 00:01:00'
            );
            CREATE TABLE lis_noise_ground_truth (
                measurement_id BIGINT PRIMARY KEY,
                true_concept_id INTEGER,
                true_source_value VARCHAR,
                corrupted_source_value VARCHAR
            );
            INSERT INTO lis_noise_ground_truth VALUES (
                99, 999, 'Legacy truth', 'LEGACY'
            );
        """)

        inject_lis_noise(con, run_id="RUN-new", noise_ratio=0.5)

        assert con.execute("""
            SELECT simulation_run_id, measurement_id
            FROM lis_noise_ground_truth_history
        """).fetchall() == [("RUN-legacy", 99)]


def test_failed_reinjection_rolls_back_snapshot_replacement():
    with duckdb.connect(":memory:") as con:
        con.execute("""
            CREATE TABLE measurement (
                measurement_id BIGINT PRIMARY KEY,
                measurement_concept_id INTEGER CHECK (measurement_concept_id > 0),
                measurement_source_concept_id INTEGER,
                measurement_source_value VARCHAR
            );
            INSERT INTO measurement VALUES (1, 100, 100, 'Test');
            CREATE TABLE lis_noise_ground_truth (
                simulation_run_id VARCHAR NOT NULL,
                measurement_id BIGINT PRIMARY KEY,
                true_concept_id INTEGER,
                true_source_value VARCHAR,
                corrupted_source_value VARCHAR
            );
            INSERT INTO lis_noise_ground_truth VALUES (
                'RUN-old', 99, 999, 'Prior truth', 'PRIOR'
            );
        """)
        before = con.execute("SELECT * FROM lis_noise_ground_truth").fetchall()

        with pytest.raises(duckdb.ConstraintException):
            inject_lis_noise(con, run_id="RUN-new", noise_ratio=1.0)

        assert con.execute("SELECT * FROM lis_noise_ground_truth").fetchall() == before
        assert con.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_name = 'lis_noise_ground_truth_history'
        """).fetchone()[0] == 0
