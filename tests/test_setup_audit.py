from dataclasses import replace
from datetime import date

import duckdb

import src.utils.setup_audit as setup_audit


def test_cdm_source_uses_profile_metadata(monkeypatch, tmp_path):
    database = tmp_path / "metadata.duckdb"
    settings = replace(
        setup_audit.SETTINGS,
        cdm_source_name="Example Hospital OMOP",
        cdm_source_abbreviation="EXH-OMOP",
        cdm_holder="Example Hospital",
        cdm_source_description="Governed hospital EHR extract.",
        cdm_source_release_date="2026-09-01",
        cdm_source_documentation_reference="https://hospital.example/data",
        cdm_etl_reference="https://hospital.example/etl",
    )
    monkeypatch.setattr(setup_audit, "DB_PATH", str(database))
    monkeypatch.setattr(setup_audit, "SETTINGS", settings)

    setup_audit.setup_audit_tables()

    with duckdb.connect(str(database), read_only=True) as con:
        row = con.execute("""
            SELECT cdm_source_name, cdm_source_abbreviation, cdm_holder,
                   source_description, source_documentation_reference,
                   cdm_etl_reference, source_release_date
            FROM cdm_source
        """).fetchone()
    assert row == (
        "Example Hospital OMOP",
        "EXH-OMOP",
        "Example Hospital",
        "Governed hospital EHR extract.",
        "https://hospital.example/data",
        "https://hospital.example/etl",
        date(2026, 9, 1),
    )
