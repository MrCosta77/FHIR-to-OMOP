import duckdb

from src.etl.link_visits import link_events_in_connection, replace_fhir_event_contexts
from src.utils.helpers import stable_event_id


def _connection():
    con = duckdb.connect(":memory:")
    con.execute("""
        CREATE TABLE visit_occurrence (
            visit_occurrence_id BIGINT, person_id BIGINT,
            visit_start_date DATE, visit_end_date DATE
        );
        CREATE TABLE condition_occurrence (
            condition_occurrence_id BIGINT, person_id BIGINT,
            condition_start_date DATE, visit_occurrence_id BIGINT
        );
    """)
    return con


def test_reference_precedes_temporal_fallback_and_ambiguity_is_quarantined():
    con = _connection()
    first_visit = stable_event_id("encounter-a")
    referenced_visit = stable_event_id("encounter-b")
    only_visit = stable_event_id("encounter-c")
    con.executemany(
        "INSERT INTO visit_occurrence VALUES (?, ?, ?, ?)",
        [
            (first_visit, 1, "2025-01-10", "2025-01-10"),
            (referenced_visit, 1, "2025-01-10", "2025-01-10"),
            (only_visit, 2, "2025-01-10", "2025-01-10"),
        ],
    )
    con.executemany(
        "INSERT INTO condition_occurrence VALUES (?, ?, ?, NULL)",
        [
            (101, 1, "2025-01-10"),  # explicit reference wins over two temporal candidates
            (102, 2, "2025-01-10"),  # exactly one temporal fallback candidate
            (103, 1, "2025-01-10"),  # ambiguous without a reference
            (104, 2, "2025-01-10"),  # invalid explicit reference must not fall back
            (105, 2, "2025-01-11"),  # explicit reference outside the visit date
        ],
    )
    replace_fhir_event_contexts(
        con,
        [
            ("condition_occurrence", 101, "Condition/101", "Encounter/encounter-b", referenced_visit),
            ("condition_occurrence", 102, "Condition/102", None, None),
            ("condition_occurrence", 103, "Condition/103", None, None),
            ("condition_occurrence", 104, "Condition/104", "Encounter/missing", stable_event_id("missing")),
            ("condition_occurrence", 105, "Condition/105", "Encounter/encounter-c", only_visit),
        ],
    )

    results = link_events_in_connection(con, "TEST-RUN")

    links = dict(con.execute(
        "SELECT condition_occurrence_id, visit_occurrence_id FROM condition_occurrence ORDER BY 1"
    ).fetchall())
    assert links[101] == referenced_visit
    assert links[102] == only_visit
    assert links[103] is None
    assert links[104] is None
    assert links[105] is None
    assert results["condition_occurrence"]["FHIR_REFERENCE"] == 1
    assert results["condition_occurrence"]["TEMPORAL_FALLBACK"] == 1
    assert results["condition_occurrence"]["QUARANTINED"] == 3

    reasons = {row[0] for row in con.execute(
        "SELECT reason_code FROM etl_quarantine WHERE active ORDER BY reason_code"
    ).fetchall()}
    assert reasons == {
        "VISIT_REFERENCE_DATE_OUTSIDE",
        "VISIT_REFERENCE_NOT_FOUND",
        "VISIT_TEMPORAL_AMBIGUOUS",
    }


def test_linkage_is_idempotent_for_a_run():
    con = _connection()
    visit_id = stable_event_id("encounter-a")
    con.execute("INSERT INTO visit_occurrence VALUES (?, 1, '2025-01-10', '2025-01-10')", [visit_id])
    con.execute("INSERT INTO condition_occurrence VALUES (1, 1, '2025-01-10', NULL)")
    replace_fhir_event_contexts(con, [("condition_occurrence", 1, "Condition/1", None, None)])
    link_events_in_connection(con, "TEST-RUN")
    link_events_in_connection(con, "TEST-RUN")
    assert con.execute("SELECT COUNT(*) FROM event_visit_linkage").fetchone()[0] == 1
    assert con.execute("SELECT visit_occurrence_id FROM condition_occurrence").fetchone()[0] == visit_id
