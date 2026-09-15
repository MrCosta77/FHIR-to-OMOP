import duckdb
import pytest

from src.analytics.text_to_sql_agent import (
    extract_sql_query,
    harden_analytics_connection,
    validate_read_only_sql,
)


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
        "SELECT * FROM read_text('secrets.txt')",
        "SELECT * FROM read_blob('secrets.bin')",
        "SELECT * FROM parquet_scan('https://example.test/data.parquet')",
        "PRAGMA database_list",
    ],
)
def test_read_only_sql_guard_rejects_unsafe_queries(query):
    with pytest.raises(ValueError):
        validate_read_only_sql(query)


def test_hardened_connection_blocks_external_file_access_and_reenable(tmp_path):
    public_fixture = tmp_path / "public.txt"
    public_fixture.write_text("safe test fixture", encoding="utf-8")
    with duckdb.connect() as con:
        assert con.execute(
            "SELECT content FROM read_text(?)", [str(public_fixture)]
        ).fetchone()[0] == "safe test fixture"

        harden_analytics_connection(con)

        with pytest.raises(duckdb.PermissionException, match="disabled"):
            con.execute(
                "SELECT content FROM read_text(?)", [str(public_fixture)]
            ).fetchall()
        with pytest.raises(duckdb.Error):
            con.execute("SET enable_external_access = true")

from src.analytics.text_to_sql_agent import (
    MODEL_NAME,
    OLLAMA_TIMEOUT,
    OLLAMA_URL,
    generate_sql_query,
)


def test_generate_sql_query_success():
    class FakeClient:
        def chat(self, *args, **kwargs):
            assert kwargs["model"] == MODEL_NAME
            assert len(kwargs["messages"]) == 2
            assert kwargs["messages"][1]["role"] == "user"
            assert "temperature" in kwargs["options"]
            return {"message": {"content": "```sql\nSELECT * FROM person\n```"}}

    query = generate_sql_query("Count all persons", client=FakeClient())
    assert query == "SELECT * FROM person"


def test_generate_sql_query_uses_validated_configured_client(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def chat(self, *args, **kwargs):
            return {"message": {"content": "SELECT 1"}}

    monkeypatch.setenv("OLLAMA_HOST", "https://untrusted.example")
    monkeypatch.setattr("src.analytics.text_to_sql_agent.ollama.Client", FakeClient)

    assert generate_sql_query("Count all persons") == "SELECT 1"
    assert captured == {
        "host": OLLAMA_URL.rsplit("/api/", 1)[0],
        "timeout": OLLAMA_TIMEOUT,
    }


def test_generate_sql_query_redacts_direct_identifiers_before_inference():
    class FakeClient:
        def chat(self, *args, **kwargs):
            prompt = kwargs["messages"][1]["content"]
            assert "Patient/abc-123" not in prompt
            assert "[REDACTED_FHIR_PATIENT_REFERENCE]" in prompt
            return {"message": {"content": "SELECT 1"}}

    assert generate_sql_query(
        "Count records for Patient/abc-123", client=FakeClient()
    ) == "SELECT 1"


def test_generate_sql_query_handles_api_errors(capsys):
    class ErrorClient:
        def chat(self, *args, **kwargs):
            raise Exception("Connection Refused")

    query = generate_sql_query("Count all persons", client=ErrorClient())
    assert query is None

    captured = capsys.readouterr()
    assert "LLM Error: Connection Refused" in captured.out
