import duckdb
import pytest

from src.analytics.rwe_cohort_discovery import average_observation_years


def _observation_period_table(con):
    con.execute("""
        CREATE TABLE observation_period (
            person_id BIGINT,
            observation_period_start_date DATE,
            observation_period_end_date DATE
        )
    """)


def test_average_observation_time_is_absent_for_empty_population():
    with duckdb.connect(":memory:") as con:
        _observation_period_table(con)
        assert average_observation_years(con) is None


def test_average_observation_time_uses_days_and_aggregates_per_person():
    with duckdb.connect(":memory:") as con:
        _observation_period_table(con)
        con.executemany(
            "INSERT INTO observation_period VALUES (?, ?, ?)",
            [
                (1, "2025-12-31", "2026-01-01"),
                (1, "2026-02-01", "2026-02-01"),
                (2, "2026-01-01", "2026-01-04"),
            ],
        )

        years = average_observation_years(con)

    # Person 1 has 3 inclusive days; person 2 has 4. Mean = 3.5 days.
    assert years == pytest.approx(3.5 / 365.25)
