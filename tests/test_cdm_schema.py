from pathlib import Path

import duckdb

from src.omop.cdm54 import (
    SPEC_PATH,
    ensure_complete_cdm_schema,
    load_table_specs,
    verify_specification,
)


def test_pinned_ohdsi_specification_has_expected_dimensions():
    assert verify_specification()
    specs = load_table_specs()
    assert len(specs) == 39
    assert sum(map(len, specs.values())) == 432


def test_schema_upgrade_adds_missing_contract_without_deleting_data(tmp_path):
    db_path = tmp_path / "omop.duckdb"
    with duckdb.connect(str(db_path)) as con:
        con.execute("CREATE TABLE visit_occurrence (visit_occurrence_id BIGINT)")
        con.execute("INSERT INTO visit_occurrence VALUES (123)")
        ensure_complete_cdm_schema(con)
        assert con.execute(
            "SELECT visit_occurrence_id FROM visit_occurrence"
        ).fetchall() == [(123,)]
        tables = {
            row[0]
            for row in con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert set(load_table_specs()) <= tables
        columns = {
            row[1]
            for row in con.execute(
                "PRAGMA table_info('visit_occurrence')"
            ).fetchall()
        }
        assert "preceding_visit_occurrence_id" in columns


def test_pinned_specification_is_exempt_from_eol_normalization():
    attributes = (SPEC_PATH.parents[2] / ".gitattributes").read_text(encoding="utf-8")
    assert "OMOP_CDMv5.4_Field_Level.csv -text" in attributes


def test_dqd_runner_explicitly_uses_cdm_54_checks():
    analytics = SPEC_PATH.parents[2] / "src" / "analytics"
    runner = (analytics / "run_dqd_tests.R").read_text(
        encoding="utf-8"
    )
    worker = (analytics / "run_dqd_worker.R").read_text(encoding="utf-8")
    assert worker.count('cdmVersion = "5.4"') == 2
    assert 'run_worker("base"' in runner
    assert 'run_worker("future_high"' in runner
    assert "src.quality.merge_dqd_results" in runner
