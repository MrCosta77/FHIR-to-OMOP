import json

import duckdb

from src.etl import condition, measurement, observation
from src.utils import setup_audit, setup_cdm_schema
from src.utils.helpers import stable_event_id


def _entry(resource_type, resource_id, code, value=None):
    resource = {
        "resourceType": resource_type,
        "id": resource_id,
        "subject": {"reference": "Patient/patient-1"},
        "code": {
            "coding": [{
                "system": (
                    "http://snomed.info/sct"
                    if resource_type == "Condition"
                    else "http://loinc.org"
                ),
                "code": code,
                "display": code,
            }]
        },
    }
    if resource_type == "Condition":
        resource["onsetDateTime"] = "2026-01-01T10:00:00Z"
    else:
        resource["status"] = "final"
        resource["effectiveDateTime"] = "2026-01-01T10:00:00Z"
        resource.update(value or {"valueString": "reported"})
    return {"fullUrl": f"{resource_type}/{resource_id}", "resource": resource}


def _install_routes(con):
    con.executemany(
        """
        INSERT INTO concept (
            concept_id, concept_name, domain_id, vocabulary_id,
            concept_class_id, standard_concept, concept_code,
            valid_start_date, valid_end_date, invalid_reason
        ) VALUES (?, ?, ?, ?, 'Test', ?, ?, DATE '2000-01-01',
                  DATE '2099-12-31', NULL)
        """,
        [
            (2001, "Condition source", "Condition", "SNOMED", None, "C-OBS"),
            (2002, "Observed finding", "Observation", "SNOMED", "S", "OBS-TARGET"),
            (2003, "Device source", "Observation", "LOINC", None, "O-DEVICE"),
            (2004, "Device target", "Device", "SNOMED", "S", "DEVICE-TARGET"),
        ],
    )
    con.executemany(
        """
        INSERT INTO concept_relationship (
            concept_id_1, concept_id_2, relationship_id,
            valid_start_date, valid_end_date, invalid_reason
        ) VALUES (?, ?, 'Maps to', DATE '2000-01-01',
                  DATE '2099-12-31', NULL)
        """,
        [(2001, 2002), (2003, 2004)],
    )


def test_cross_domain_ownership_prevents_loss_and_duplicate_fallbacks(
    tmp_path, monkeypatch
):
    database = tmp_path / "routing.duckdb"
    fhir_directory = tmp_path / "fhir"
    fhir_directory.mkdir()
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            _entry("Condition", "condition-unmapped", "C-UNMAPPED"),
            _entry("Condition", "condition-observation", "C-OBS"),
            _entry(
                "Observation",
                "numeric-unmapped",
                "O-NUMERIC",
                {"valueQuantity": {"value": 3.2, "code": "mg"}},
            ),
            _entry("Observation", "text-unmapped", "O-TEXT"),
            _entry("Observation", "device-route", "O-DEVICE"),
        ],
    }
    (fhir_directory / "routing.json").write_text(
        json.dumps(bundle), encoding="utf-8"
    )

    for module in (setup_cdm_schema, setup_audit, condition, measurement, observation):
        monkeypatch.setattr(module, "DB_PATH", str(database))
    for module in (condition, measurement, observation):
        monkeypatch.setattr(module, "FHIR_DIR", str(fhir_directory))

    setup_cdm_schema.create_omop_skeleton()
    setup_audit.setup_audit_tables()
    with duckdb.connect(str(database)) as con:
        _install_routes(con)

    condition.run_condition_etl()
    measurement.run_measurement_etl()
    observation.run_observation_etl()

    ids = {
        name: stable_event_id(f"{resource_type}/{name}")
        for resource_type, name in (
            ("Condition", "condition-unmapped"),
            ("Condition", "condition-observation"),
            ("Observation", "numeric-unmapped"),
            ("Observation", "text-unmapped"),
            ("Observation", "device-route"),
        )
    }
    with duckdb.connect(str(database), read_only=True) as con:
        condition_ids = {
            row[0] for row in con.execute(
                "SELECT condition_occurrence_id FROM condition_occurrence"
            ).fetchall()
        }
        measurement_ids = {
            row[0] for row in con.execute(
                "SELECT measurement_id FROM measurement"
            ).fetchall()
        }
        observation_rows = dict(con.execute(
            "SELECT observation_id, observation_concept_id FROM observation"
        ).fetchall())
        quarantine = con.execute(
            """
            SELECT target_id, reason_code, reason_detail
            FROM etl_quarantine
            WHERE active AND reason_code = 'UNSUPPORTED_CROSS_DOMAIN_ROUTE'
            """
        ).fetchall()

    assert ids["condition-unmapped"] in condition_ids
    assert ids["condition-unmapped"] not in observation_rows
    assert ids["condition-observation"] not in condition_ids
    assert observation_rows[ids["condition-observation"]] == 2002
    assert ids["numeric-unmapped"] in measurement_ids
    assert ids["numeric-unmapped"] not in observation_rows
    assert observation_rows[ids["text-unmapped"]] == 0
    assert ids["device-route"] not in measurement_ids
    assert ids["device-route"] not in observation_rows
    assert quarantine == [
        (
            ids["device-route"],
            "UNSUPPORTED_CROSS_DOMAIN_ROUTE",
            "FHIR Observation maps to unsupported OMOP domain Device",
        )
    ]
