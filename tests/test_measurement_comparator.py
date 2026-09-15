import json

import duckdb
import pytest

from src.etl import measurement
from src.utils import setup_audit, setup_cdm_schema


@pytest.mark.parametrize(
    ("comparator", "expected_concept_id"),
    [
        ("<", 4171756),
        ("<=", 4171754),
        (">=", 4171755),
        (">", 4172704),
        (None, None),
    ],
)
def test_fhir_quantity_comparator_is_preserved_in_omop(
    monkeypatch, tmp_path, comparator, expected_concept_id
):
    database = tmp_path / "comparator.duckdb"
    fhir_directory = tmp_path / "fhir"
    fhir_directory.mkdir()
    quantity = {"value": 5}
    if comparator is not None:
        quantity["comparator"] = comparator
    bundle = {
        "resourceType": "Bundle",
        "entry": [{
            "fullUrl": "Observation/comparator-1",
            "resource": {
                "resourceType": "Observation",
                "status": "final",
                "subject": {"reference": "Patient/patient-1"},
                "code": {"coding": [{
                    "system": "http://loinc.org",
                    "code": "1234-5",
                    "display": "Comparator test",
                }]},
                "effectiveDateTime": "2026-01-01T10:00:00Z",
                "valueQuantity": quantity,
            },
        }],
    }
    (fhir_directory / "bundle.json").write_text(
        json.dumps(bundle), encoding="utf-8"
    )

    monkeypatch.setattr(setup_cdm_schema, "DB_PATH", str(database))
    monkeypatch.setattr(setup_audit, "DB_PATH", str(database))
    monkeypatch.setattr(measurement, "DB_PATH", str(database))
    monkeypatch.setattr(measurement, "FHIR_DIR", str(fhir_directory))
    setup_cdm_schema.create_omop_skeleton()
    setup_audit.setup_audit_tables()
    with duckdb.connect(str(database)) as con:
        con.execute("""
            INSERT INTO concept (
                concept_id, concept_name, domain_id, vocabulary_id,
                concept_class_id, standard_concept, concept_code,
                valid_start_date, valid_end_date, invalid_reason
            ) VALUES (
                1001, 'Comparator test', 'Measurement', 'LOINC',
                'Lab Test', 'S', '1234-5',
                DATE '2000-01-01', DATE '2099-12-31', NULL
            )
        """)
        con.execute("""
            INSERT INTO concept_relationship (
                concept_id_1, concept_id_2, relationship_id,
                valid_start_date, valid_end_date, invalid_reason
            ) VALUES (
                1001, 1001, 'Maps to',
                DATE '2000-01-01', DATE '2099-12-31', NULL
            )
        """)

    measurement.run_measurement_etl()

    with duckdb.connect(str(database), read_only=True) as con:
        row = con.execute("""
            SELECT operator_concept_id, value_as_number, value_source_value
            FROM measurement
        """).fetchone()
    assert row == (
        expected_concept_id,
        5.0,
        f"{comparator or ''}5",
    )


def test_unknown_fhir_quantity_comparator_fails_closed(tmp_path):
    bundle = {
        "resourceType": "Bundle",
        "entry": [{
            "fullUrl": "Observation/invalid-comparator",
            "resource": {
                "resourceType": "Observation",
                "status": "final",
                "subject": {"reference": "Patient/patient-1"},
                "code": {"coding": [{
                    "system": "http://loinc.org", "code": "1234-5"
                }]},
                "effectiveDateTime": "2026-01-01T10:00:00Z",
                "valueQuantity": {"value": 5, "comparator": "!="},
            },
        }],
    }
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported FHIR Quantity comparator"):
        measurement.extract_measurements(path)
