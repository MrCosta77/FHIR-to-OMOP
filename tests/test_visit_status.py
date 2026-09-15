import json

import duckdb

from src.etl import visit
from src.utils import setup_cdm_schema


def _encounter(encounter_id, status):
    return {
        "fullUrl": f"Encounter/{encounter_id}",
        "resource": {
            "resourceType": "Encounter",
            "id": encounter_id,
            "status": status,
            "subject": {"reference": "Patient/patient-1"},
            "class": {"code": "AMB"},
            "period": {
                "start": "2026-01-01T10:00:00Z",
                "end": "2026-01-01T11:00:00Z",
            },
        },
    }


def test_entered_in_error_encounter_is_excluded_and_audited(
    monkeypatch, tmp_path
):
    database = tmp_path / "visit-status.duckdb"
    fhir_directory = tmp_path / "fhir"
    fhir_directory.mkdir()
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            _encounter("valid", "finished"),
            _encounter("invalid", "entered-in-error"),
        ],
    }
    (fhir_directory / "bundle.json").write_text(
        json.dumps(bundle), encoding="utf-8"
    )

    monkeypatch.setattr(setup_cdm_schema, "DB_PATH", str(database))
    monkeypatch.setattr(visit, "DB_PATH", str(database))
    monkeypatch.setattr(visit, "FHIR_DIR", str(fhir_directory))
    setup_cdm_schema.create_omop_skeleton()

    visit.run_visit_etl()

    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute(
            "SELECT COUNT(*) FROM visit_occurrence"
        ).fetchone()[0] == 1
        exclusion = con.execute("""
            SELECT source_adapter, resource_type, source_event_key,
                   source_status, reason_code
            FROM fhir_publication_exclusion
        """).fetchone()
    assert exclusion == (
        "FHIR_R4_Encounter",
        "Encounter",
        "Encounter/invalid",
        "entered-in-error",
        "FHIR_ENCOUNTER_STATUS_ENTERED_IN_ERROR",
    )


def test_other_encounter_statuses_are_unchanged_pending_explicit_policy():
    from src.adapters.fhir_semantics import is_publishable_fhir_resource

    for status in ("planned", "cancelled", "in-progress", "finished"):
        assert is_publishable_fhir_resource({
            "resourceType": "Encounter", "status": status
        })
