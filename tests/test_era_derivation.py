from datetime import date

import duckdb
import pytest

from src.etl.eras import derive_eras


def _fixture_connection():
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE condition_occurrence (
            condition_occurrence_id BIGINT,
            person_id BIGINT,
            condition_concept_id BIGINT,
            condition_start_date DATE,
            condition_end_date DATE
        );
        INSERT INTO condition_occurrence VALUES
            (1, 1, 100, DATE '2020-01-01', DATE '2020-01-01'),
            (2, 1, 100, DATE '2020-01-20', DATE '2020-01-20'),
            (3, 1, 100, DATE '2020-03-01', DATE '2020-03-01'),
            (4, 1, 0,   DATE '2020-01-05', DATE '2020-01-05');

        CREATE TABLE drug_exposure (
            drug_exposure_id BIGINT,
            person_id BIGINT,
            drug_concept_id BIGINT,
            drug_exposure_start_date DATE,
            drug_exposure_end_date DATE,
            days_supply INTEGER
        );
        INSERT INTO drug_exposure VALUES
            (10, 1, 1000, DATE '2020-01-01', DATE '2020-01-01', NULL),
            (11, 1, 1001, DATE '2020-01-20', DATE '2020-01-20', NULL),
            (12, 1, 1001, DATE '2020-04-01', DATE '2020-04-01', NULL);

        CREATE TABLE concept (
            concept_id VARCHAR,
            domain_id VARCHAR,
            concept_class_id VARCHAR,
            standard_concept VARCHAR,
            invalid_reason VARCHAR
        );
        INSERT INTO concept VALUES
            ('10', 'Drug', 'Ingredient', 'S', NULL),
            ('20', 'Drug', 'Ingredient', 'S', NULL);

        CREATE TABLE concept_ancestor (
            ancestor_concept_id VARCHAR,
            descendant_concept_id VARCHAR
        );
        INSERT INTO concept_ancestor VALUES
            ('10', '1000'), ('20', '1000'), ('10', '1001');
    """)
    return con


def test_condition_eras_apply_thirty_day_persistence_window():
    con = _fixture_connection()
    derive_eras(con)
    rows = con.execute("""
        SELECT condition_era_start_date, condition_era_end_date,
               condition_occurrence_count
        FROM condition_era
        ORDER BY condition_era_start_date
    """).fetchall()
    assert rows == [
        (date(2020, 1, 1), date(2020, 1, 20), 2),
        (date(2020, 3, 1), date(2020, 3, 1), 1),
    ]
    con.close()


def test_drug_eras_expand_products_to_ingredients_and_are_idempotent():
    con = _fixture_connection()
    first_counts = derive_eras(con)
    first_rows = con.execute("SELECT * FROM drug_era ORDER BY drug_era_id").fetchall()
    second_counts = derive_eras(con)
    second_rows = con.execute("SELECT * FROM drug_era ORDER BY drug_era_id").fetchall()

    assert first_counts == second_counts == (2, 3)
    assert first_rows == second_rows
    assert con.execute("""
        SELECT drug_exposure_count, gap_days
        FROM drug_era
        WHERE drug_concept_id = 10
        ORDER BY drug_era_start_date
    """).fetchall() == [(2, 18), (1, 0)]
    assert con.execute("""
        SELECT COUNT(*) FROM drug_era WHERE drug_concept_id = 20
    """).fetchone()[0] == 1
    con.close()


def test_missing_ingredient_aborts_without_replacing_published_eras():
    con = _fixture_connection()
    derive_eras(con)
    before = con.execute("SELECT * FROM drug_era ORDER BY drug_era_id").fetchall()
    con.execute("""
        INSERT INTO drug_exposure VALUES
            (99, 1, 9999, DATE '2020-05-01', DATE '2020-05-01', NULL)
    """)

    with pytest.raises(ValueError, match="without a current Standard Ingredient"):
        derive_eras(con)

    after = con.execute("SELECT * FROM drug_era ORDER BY drug_era_id").fetchall()
    assert after == before
    con.close()
