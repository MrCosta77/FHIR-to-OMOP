import pytest

from src.analytics.text_to_sql_agent import extract_sql_query, validate_read_only_sql


@pytest.mark.parametrize(
    ("raw_output", "expected"),
    [
        ("SELECT * FROM concept", "SELECT * FROM concept"),
        ("```sql\nSELECT * FROM concept\n```", "SELECT * FROM concept"),
        ("```sql\nSELECT * FROM concept```", "SELECT * FROM concept"),
        ("```sql\r\nSELECT * FROM concept\r\n```", "SELECT * FROM concept"),
        ("Here is the query:\n```SQL\nSELECT 1```", "SELECT 1"),
    ],
)
def test_extract_sql_query_handles_common_ollama_formats(raw_output, expected):
    assert extract_sql_query(raw_output) == expected


def test_unfenced_explanatory_text_remains_visible_to_fail_closed_validator():
    output = extract_sql_query("**Query:** SELECT * FROM concept")
    with pytest.raises(ValueError, match="Only SELECT"):
        validate_read_only_sql(output)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT concept_id FROM concept;",
        "WITH current_concepts AS (SELECT * FROM concept) SELECT * FROM current_concepts",
        "SELECT 'drop table concept' AS harmless_text",
        "SELECT 1 /* DELETE FROM person */",
    ],
)
def test_read_only_sql_guard_accepts_bounded_query_shapes(query):
    assert validate_read_only_sql(query) == query


@pytest.mark.parametrize(
    "query",
    [
        "",
        "DELETE FROM person",
        "WITH removed AS (DELETE FROM person RETURNING *) SELECT * FROM removed",
        "SELECT 1; SELECT 2",
        "SELECT * FROM read_csv_auto('hospital.csv')",
        "SELECT * FROM parquet_scan('https://example.test/data.parquet')",
        "PRAGMA database_list",
    ],
)
def test_read_only_sql_guard_rejects_unsafe_queries(query):
    with pytest.raises(ValueError):
        validate_read_only_sql(query)
