"""Derive OMOP CONDITION_ERA and DRUG_ERA from published event tables."""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.omop.cdm54 import ensure_table_columns
from src.utils.config import DB_PATH

PERSISTENCE_DAYS = 30


def _condition_era_sql() -> str:
    return f"""
        CREATE TEMP TABLE next_condition_era AS
        WITH normalized AS (
            SELECT
                condition_occurrence_id,
                person_id,
                condition_concept_id,
                condition_start_date AS start_date,
                GREATEST(
                    COALESCE(condition_end_date, condition_start_date),
                    condition_start_date
                ) AS end_date
            FROM condition_occurrence
            WHERE condition_concept_id <> 0
        ),
        ordered AS (
            SELECT *,
                MAX(end_date) OVER (
                    PARTITION BY person_id, condition_concept_id
                    ORDER BY start_date, end_date, condition_occurrence_id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS prior_max_end
            FROM normalized
        ),
        marked AS (
            SELECT *,
                CASE
                    WHEN prior_max_end IS NULL
                      OR start_date > prior_max_end + INTERVAL {PERSISTENCE_DAYS} DAY
                    THEN 1 ELSE 0
                END AS starts_new_era
            FROM ordered
        ),
        grouped AS (
            SELECT *,
                SUM(starts_new_era) OVER (
                    PARTITION BY person_id, condition_concept_id
                    ORDER BY start_date, end_date, condition_occurrence_id
                    ROWS UNBOUNDED PRECEDING
                ) AS era_number
            FROM marked
        ),
        aggregated AS (
            SELECT
                person_id,
                condition_concept_id,
                MIN(start_date) AS era_start_date,
                MAX(end_date) AS era_end_date,
                COUNT(*) AS occurrence_count
            FROM grouped
            GROUP BY person_id, condition_concept_id, era_number
        )
        SELECT
            CAST(
                '0x' || SUBSTR(SHA256(
                    'condition_era|' || person_id::VARCHAR || '|' ||
                    condition_concept_id::VARCHAR || '|' || era_start_date::VARCHAR
                ), 1, 15)
                AS BIGINT
            ) AS condition_era_id,
            person_id,
            condition_concept_id,
            era_start_date AS condition_era_start_date,
            era_end_date AS condition_era_end_date,
            occurrence_count AS condition_occurrence_count
        FROM aggregated
    """


def _drug_era_sql() -> str:
    return f"""
        CREATE TEMP TABLE next_drug_era AS
        WITH ingredient_exposures AS (
            SELECT DISTINCT
                exposure.drug_exposure_id,
                exposure.person_id,
                ingredient.concept_id::BIGINT AS ingredient_concept_id,
                exposure.drug_exposure_start_date AS start_date,
                GREATEST(
                    COALESCE(
                        exposure.drug_exposure_end_date,
                        CASE
                            WHEN exposure.days_supply > 0
                            THEN exposure.drug_exposure_start_date
                                 + CAST(exposure.days_supply AS INTEGER)
                            ELSE exposure.drug_exposure_start_date
                        END
                    ),
                    exposure.drug_exposure_start_date
                ) AS end_date
            FROM drug_exposure exposure
            JOIN concept_ancestor ancestor
              ON ancestor.descendant_concept_id = exposure.drug_concept_id::VARCHAR
            JOIN concept ingredient
              ON ingredient.concept_id = ancestor.ancestor_concept_id
             AND ingredient.domain_id = 'Drug'
             AND ingredient.concept_class_id = 'Ingredient'
             AND ingredient.standard_concept = 'S'
             AND ingredient.invalid_reason IS NULL
            WHERE exposure.drug_concept_id <> 0
        ),
        ordered AS (
            SELECT *,
                MAX(end_date) OVER (
                    PARTITION BY person_id, ingredient_concept_id
                    ORDER BY start_date, end_date, drug_exposure_id
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS prior_max_end
            FROM ingredient_exposures
        ),
        marked AS (
            SELECT *,
                CASE
                    WHEN prior_max_end IS NULL
                      OR start_date > prior_max_end + INTERVAL {PERSISTENCE_DAYS} DAY
                    THEN 1 ELSE 0
                END AS starts_new_era,
                CASE
                    WHEN prior_max_end IS NULL
                      OR start_date <= prior_max_end
                      OR start_date > prior_max_end + INTERVAL {PERSISTENCE_DAYS} DAY
                    THEN 0
                    ELSE GREATEST(DATE_DIFF('day', prior_max_end, start_date) - 1, 0)
                END AS uncovered_days
            FROM ordered
        ),
        grouped AS (
            SELECT *,
                SUM(starts_new_era) OVER (
                    PARTITION BY person_id, ingredient_concept_id
                    ORDER BY start_date, end_date, drug_exposure_id
                    ROWS UNBOUNDED PRECEDING
                ) AS era_number
            FROM marked
        ),
        aggregated AS (
            SELECT
                person_id,
                ingredient_concept_id,
                MIN(start_date) AS era_start_date,
                MAX(end_date) AS era_end_date,
                COUNT(*) AS exposure_count,
                SUM(uncovered_days) AS gap_days
            FROM grouped
            GROUP BY person_id, ingredient_concept_id, era_number
        )
        SELECT
            CAST(
                '0x' || SUBSTR(SHA256(
                    'drug_era|' || person_id::VARCHAR || '|' ||
                    ingredient_concept_id::VARCHAR || '|' || era_start_date::VARCHAR
                ), 1, 15)
                AS BIGINT
            ) AS drug_era_id,
            person_id,
            ingredient_concept_id AS drug_concept_id,
            era_start_date AS drug_era_start_date,
            era_end_date AS drug_era_end_date,
            exposure_count AS drug_exposure_count,
            gap_days
        FROM aggregated
    """


def _validate_staged_eras(con) -> None:
    unmapped_drugs = con.execute("""
        SELECT COUNT(*)
        FROM drug_exposure exposure
        WHERE exposure.drug_concept_id <> 0
          AND NOT EXISTS (
              SELECT 1
              FROM concept_ancestor ancestor
              JOIN concept ingredient
                ON ingredient.concept_id = ancestor.ancestor_concept_id
               AND ingredient.domain_id = 'Drug'
               AND ingredient.concept_class_id = 'Ingredient'
               AND ingredient.standard_concept = 'S'
               AND ingredient.invalid_reason IS NULL
              WHERE ancestor.descendant_concept_id = exposure.drug_concept_id::VARCHAR
          )
    """).fetchone()[0]
    if unmapped_drugs:
        raise ValueError(
            f"Drug era derivation found {unmapped_drugs} mapped exposures "
            "without a current Standard Ingredient ancestor."
        )

    invalid_ranges = con.execute("""
        SELECT
            (SELECT COUNT(*) FROM next_condition_era
             WHERE condition_era_start_date IS NULL
                OR condition_era_end_date IS NULL
                OR condition_era_end_date < condition_era_start_date)
          + (SELECT COUNT(*) FROM next_drug_era
             WHERE drug_era_start_date IS NULL
                OR drug_era_end_date IS NULL
                OR drug_era_end_date < drug_era_start_date
                OR gap_days < 0)
    """).fetchone()[0]
    if invalid_ranges:
        raise ValueError(f"Era derivation produced {invalid_ranges} invalid date ranges.")

    condition_uncovered = con.execute("""
        SELECT COUNT(*)
        FROM condition_occurrence occurrence
        WHERE occurrence.condition_concept_id <> 0
          AND NOT EXISTS (
              SELECT 1 FROM next_condition_era era
              WHERE era.person_id = occurrence.person_id
                AND era.condition_concept_id = occurrence.condition_concept_id
                AND occurrence.condition_start_date >= era.condition_era_start_date
                AND COALESCE(occurrence.condition_end_date, occurrence.condition_start_date)
                    <= era.condition_era_end_date
          )
    """).fetchone()[0]
    if condition_uncovered:
        raise ValueError(
            f"Condition era derivation left {condition_uncovered} mapped occurrences uncovered."
        )

    drug_uncovered = con.execute("""
        WITH expected AS (
            SELECT DISTINCT
                exposure.drug_exposure_id,
                exposure.person_id,
                ingredient.concept_id::BIGINT AS ingredient_concept_id,
                exposure.drug_exposure_start_date AS start_date,
                GREATEST(
                    COALESCE(
                        exposure.drug_exposure_end_date,
                        CASE
                            WHEN exposure.days_supply > 0
                            THEN exposure.drug_exposure_start_date
                                 + CAST(exposure.days_supply AS INTEGER)
                            ELSE exposure.drug_exposure_start_date
                        END
                    ),
                    exposure.drug_exposure_start_date
                ) AS end_date
            FROM drug_exposure exposure
            JOIN concept_ancestor ancestor
              ON ancestor.descendant_concept_id = exposure.drug_concept_id::VARCHAR
            JOIN concept ingredient
              ON ingredient.concept_id = ancestor.ancestor_concept_id
             AND ingredient.domain_id = 'Drug'
             AND ingredient.concept_class_id = 'Ingredient'
             AND ingredient.standard_concept = 'S'
             AND ingredient.invalid_reason IS NULL
            WHERE exposure.drug_concept_id <> 0
        )
        SELECT COUNT(*)
        FROM expected exposure
        WHERE NOT EXISTS (
            SELECT 1 FROM next_drug_era era
            WHERE era.person_id = exposure.person_id
              AND era.drug_concept_id = exposure.ingredient_concept_id
              AND exposure.start_date >= era.drug_era_start_date
              AND exposure.end_date <= era.drug_era_end_date
        )
    """).fetchone()[0]
    if drug_uncovered:
        raise ValueError(
            f"Drug era derivation left {drug_uncovered} exposure-ingredient pairs uncovered."
        )

    duplicate_ids = con.execute("""
        SELECT
            (SELECT COUNT(*) - COUNT(DISTINCT condition_era_id) FROM next_condition_era)
          + (SELECT COUNT(*) - COUNT(DISTINCT drug_era_id) FROM next_drug_era)
    """).fetchone()[0]
    if duplicate_ids:
        raise ValueError(f"Era derivation produced {duplicate_ids} duplicate IDs.")


def derive_eras(con) -> tuple[int, int]:
    """Build and atomically publish both era tables on an open DuckDB connection."""
    con.execute("BEGIN TRANSACTION")
    try:
        ensure_table_columns(con, "condition_era")
        ensure_table_columns(con, "drug_era")
        con.execute("DROP TABLE IF EXISTS next_condition_era")
        con.execute("DROP TABLE IF EXISTS next_drug_era")
        con.execute(_condition_era_sql())
        con.execute(_drug_era_sql())
        _validate_staged_eras(con)

        condition_count = con.execute(
            "SELECT COUNT(*) FROM next_condition_era"
        ).fetchone()[0]
        drug_count = con.execute(
            "SELECT COUNT(*) FROM next_drug_era"
        ).fetchone()[0]

        con.execute("DELETE FROM condition_era")
        con.execute("INSERT INTO condition_era SELECT * FROM next_condition_era")
        con.execute("DELETE FROM drug_era")
        con.execute("INSERT INTO drug_era SELECT * FROM next_drug_era")
        con.execute("DROP TABLE next_condition_era")
        con.execute("DROP TABLE next_drug_era")
        con.execute("COMMIT")
        return condition_count, drug_count
    except Exception:
        con.execute("ROLLBACK")
        raise


def run_era_etl() -> None:
    print("⚙️ BUILDING OMOP CONDITION_ERA AND DRUG_ERA [DERIVED]")
    print("-" * 50)
    with duckdb.connect(DB_PATH) as con:
        condition_count, drug_count = derive_eras(con)
        ignored_conditions = con.execute(
            "SELECT COUNT(*) FROM condition_occurrence WHERE condition_concept_id = 0"
        ).fetchone()[0]
        ignored_drugs = con.execute(
            "SELECT COUNT(*) FROM drug_exposure WHERE drug_concept_id = 0"
        ).fetchone()[0]

    print("\n✅ Era derivation complete and published atomically!")
    print(f" - CONDITION_ERA rows: {condition_count}")
    print(f" - DRUG_ERA rows:      {drug_count}")
    print(f" - Unresolved conditions excluded: {ignored_conditions}")
    print(f" - Unresolved drugs excluded:      {ignored_drugs}")


if __name__ == "__main__":
    run_era_etl()
