import json

import duckdb

from src.adapters.fhir_coding import (
    LOINC_URI,
    RXNORM_URI,
    SNOMED_URI,
    SourceCoding,
    replace_fhir_source_codings,
    select_source_coding,
)
from src.adapters.fhir_semantics import (
    extract_fhir_publication_exclusions,
    fhir_datetime,
    is_publishable_fhir_resource,
    medication_request_period,
    replace_fhir_publication_exclusions,
)
from src.etl.condition import extract_conditions
from src.etl.drug import extract_drugs
from src.etl.measurement import extract_measurements
from src.etl.observation import extract_observation_candidates
from src.etl.procedure import extract_procedures
from src.mapping.governance import decision_id_for


def test_source_coding_identity_requires_system_and_code():
    try:
        SourceCoding("", "123", "Example")
    except ValueError as exc:
        assert "system and code" in str(exc)
    else:
        raise AssertionError("A code without a system must fail closed")


def test_preferred_system_is_selected_without_relabelling_fallback():
    local = {"system": "https://hospital.example/codes", "code": "830020009", "display": "Local procedure"}
    snomed = {"system": SNOMED_URI, "code": "73761001", "display": "Colonoscopy"}

    selected = select_source_coding([local, snomed], preferred_systems=[SNOMED_URI])
    assert selected == SourceCoding(SNOMED_URI, "73761001", "Colonoscopy")

    fallback = select_source_coding([local], preferred_systems=[SNOMED_URI])
    assert fallback.code == "830020009"
    assert fallback.athena_vocabulary_id is None
    assert fallback.source_vocabulary_id.startswith("FHIR_")


def test_uri_normalization_is_safe_and_known_vocabularies_are_explicit():
    coding = SourceCoding(f"{LOINC_URI}/", "8867-4", "Heart rate", "2.77")
    assert coding.system_uri == LOINC_URI
    assert coding.athena_vocabulary_id == "LOINC"
    assert coding.source_vocabulary_id == "LOINC"
    assert coding.version == "2.77"


def test_system_paths_and_source_codes_remain_case_sensitive():
    upper = SourceCoding("https://hospital.example/CodeSystem/A", "ABC", "A")
    lower = SourceCoding("https://hospital.example/CodeSystem/a", "abc", "a")
    assert upper.system_uri != lower.system_uri
    assert upper.source_vocabulary_id != lower.source_vocabulary_id
    assert decision_id_for(
        "R1", "measurement", "Shared", 300,
        source_vocabulary_id="FHIR_LOCAL", source_code="ABC",
    ) != decision_id_for(
        "R1", "measurement", "Shared", 300,
        source_vocabulary_id="FHIR_LOCAL", source_code="abc",
    )


def test_event_sidecar_replaces_only_the_rebuilt_target_table():
    con = duckdb.connect(":memory:")
    replace_fhir_source_codings(con, "condition_occurrence", [
        ("condition_occurrence", 1, "Condition/1", None, SNOMED_URI, "SNOMED", "123", "Example", None, "RUN-1")
    ])
    replace_fhir_source_codings(con, "measurement", [
        ("measurement", 2, "Observation/2", "component[0]", LOINC_URI, "LOINC", "8867-4", "Heart rate", None, "RUN-1")
    ])
    replace_fhir_source_codings(con, "condition_occurrence", [
        ("condition_occurrence", 3, "Condition/3", None, SNOMED_URI, "SNOMED", "456", "Other", None, "RUN-2")
    ])

    assert con.execute(
        "SELECT target_id FROM fhir_event_source_coding WHERE target_table='condition_occurrence'"
    ).fetchall() == [(3,)]
    assert con.execute(
        "SELECT target_id, component_path FROM fhir_event_source_coding WHERE target_table='measurement'"
    ).fetchall() == [(2, "component[0]")]


def test_event_sidecar_replacement_is_scoped_by_source_adapter():
    con = duckdb.connect(":memory:")
    replace_fhir_source_codings(
        con,
        "measurement",
        [("measurement", 99, "Legacy/99", None, LOINC_URI, "LOINC", "99", "Legacy", None, "R0")],
    )
    replace_fhir_source_codings(
        con,
        "measurement",
        [("measurement", 1, "Observation/1", None, LOINC_URI, "LOINC", "1", "Lab", None, "R1")],
        source_adapter="FHIR_R4_Observation",
    )
    replace_fhir_source_codings(
        con,
        "measurement",
        [("measurement", 2, "Procedure/2", None, SNOMED_URI, "SNOMED", "2", "Procedure", None, "R1")],
        source_adapter="FHIR_R4_Procedure",
    )
    replace_fhir_source_codings(
        con,
        "measurement",
        [("measurement", 3, "Observation/3", None, LOINC_URI, "LOINC", "3", "Lab", None, "R2")],
        source_adapter="FHIR_R4_Observation",
    )

    assert con.execute("""
        SELECT target_id, source_adapter
        FROM fhir_event_source_coding
        ORDER BY target_id
    """).fetchall() == [
        (2, "FHIR_R4_Procedure"),
        (3, "FHIR_R4_Observation"),
    ]


def test_condition_keeps_local_system_without_calling_it_snomed(tmp_path):
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Condition",
                    "id": "condition-local",
                    "subject": {"reference": "Patient/patient-1"},
                    "code": {
                        "coding": [{
                            "system": "https://hospital.example/codes",
                            "code": "830020009",
                            "display": "Local procedure-like label",
                        }]
                    },
                    "onsetDateTime": "2026-01-01T10:00:00Z",
                }
            }
        ],
    }
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    records = extract_conditions(path)
    assert len(records) == 1
    assert records[0][2] == "830020009"
    assert records[0][8] == "https://hospital.example/codes"
    assert records[0][9] is None
    assert records[0][10].startswith("FHIR_")


def test_medication_reference_uses_fhir_reference_identity(tmp_path):
    bundle = {
        "resourceType": "Bundle",
        "entry": [
            {
                "resource": {
                    "resourceType": "Medication",
                    "id": "medication-1",
                    "code": {
                        "coding": [{
                            "system": RXNORM_URI,
                            "code": "314076",
                            "display": "Lisinopril 10 MG",
                        }]
                    },
                }
            },
            {
                "fullUrl": "https://hospital.example/fhir/MedicationRequest/request-1",
                "resource": {
                    "resourceType": "MedicationRequest",
                    "status": "active",
                    "id": "request-1",
                    "subject": {"reference": "Patient/patient-1"},
                    "medicationReference": {"reference": "Medication/medication-1"},
                    "authoredOn": "2026-01-01T10:00:00Z",
                },
            },
        ],
    }
    path = tmp_path / "medication.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    records = extract_drugs(path)
    assert len(records) == 1
    assert records[0][2:4] == ("314076", "Lisinopril 10 MG")
    assert records[0][5] == "2026-01-01T10:00:00.000000"
    assert records[0][8] == RXNORM_URI
    assert records[0][9:11] == ("RxNorm", "RxNorm")


def test_procedure_keeps_local_system_without_snomed_relabelling(tmp_path):
    bundle = {
        "resourceType": "Bundle",
        "entry": [{
            "fullUrl": "Procedure/local-1",
            "resource": {
                "resourceType": "Procedure",
                "status": "completed",
                "subject": {"reference": "Patient/patient-1"},
                "code": {"coding": [{
                    "system": "https://hospital.example/procedures",
                    "code": "73761001",
                    "display": "Local procedure",
                }]},
                "performedDateTime": "2026-01-01T10:00:00Z",
            },
        }],
    }
    path = tmp_path / "procedure.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    records = extract_procedures(path)
    assert len(records) == 1
    assert records[0][2] == "73761001"
    assert records[0][5] == "2026-01-01T10:00:00.000000"
    assert records[0][8] == "https://hospital.example/procedures"
    assert records[0][9] is None
    assert records[0][10].startswith("FHIR_")


def test_observation_components_expand_with_distinct_stable_identity(tmp_path):
    bundle = {
        "resourceType": "Bundle",
        "entry": [{
            "fullUrl": "Observation/panel-1",
            "resource": {
                "resourceType": "Observation",
                "status": "final",
                "subject": {"reference": "Patient/patient-1"},
                "code": {"coding": [{
                    "system": LOINC_URI, "code": "24323-8", "display": "Panel"
                }]},
                "effectiveDateTime": "2026-01-01T10:00:00Z",
                "component": [
                    {
                        "code": {"coding": [{
                            "system": LOINC_URI,
                            "code": "8867-4",
                            "display": "Heart rate",
                        }]},
                        "valueQuantity": {
                            "value": 72,
                            "system": "http://unitsofmeasure.org",
                            "code": "/min",
                            "unit": "beats/minute",
                        },
                    },
                    {
                        "code": {"coding": [{
                            "system": LOINC_URI,
                            "code": "72166-2",
                            "display": "Smoking status",
                        }]},
                        "valueCodeableConcept": {"coding": [{
                            "system": SNOMED_URI,
                            "code": "266919005",
                            "display": "Never smoked tobacco",
                        }]},
                    },
                ],
            },
        }],
    }
    path = tmp_path / "panel.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")

    measurement_records = extract_measurements(path)
    observation_records = extract_observation_candidates(path)

    assert len(measurement_records) == 2
    assert len(observation_records) == 3
    assert {record[16] for record in measurement_records} == {
        "component[0]", "component[1]"
    }
    assert len({record[0] for record in measurement_records}) == 2
    coded_measurement = next(
        record for record in measurement_records if record[16] == "component[1]"
    )
    assert coded_measurement[20:22] == ("266919005", "Never smoked tobacco")
    coded_observation = next(
        record for record in observation_records if record[17] == "component[1]"
    )
    assert coded_observation[21:23] == (
        "266919005", "Never smoked tobacco"
    )


def test_non_publishable_fhir_states_are_excluded(tmp_path):
    assert not is_publishable_fhir_resource({
        "resourceType": "Observation", "status": "entered-in-error"
    })
    assert not is_publishable_fhir_resource({
        "resourceType": "Procedure", "status": "not-done"
    })
    assert not is_publishable_fhir_resource({
        "resourceType": "MedicationRequest", "status": "cancelled"
    })
    assert not is_publishable_fhir_resource({
        "resourceType": "Condition",
        "verificationStatus": {"coding": [{"code": "refuted"}]},
    })

    bundle = {
        "resourceType": "Bundle",
        "entry": [{
            "resource": {
                "resourceType": "Observation",
                "status": "entered-in-error",
                "subject": {"reference": "Patient/patient-1"},
                "code": {"coding": [{
                    "system": LOINC_URI, "code": "8867-4", "display": "Heart rate"
                }]},
                "effectiveDateTime": "2026-01-01T10:00:00Z",
                "valueQuantity": {"value": 72, "code": "/min"},
            }
        }],
    }
    path = tmp_path / "excluded.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    assert extract_measurements(path) == []
    assert extract_observation_candidates(path) == []


def test_medication_request_period_preserves_time_and_validity_interval():
    assert fhir_datetime("2026-01-01T12:30:00+02:00") == (
        "2026-01-01", "2026-01-01T10:30:00.000000"
    )
    assert medication_request_period({
        "authoredOn": "2025-12-31T23:00:00Z",
        "dispenseRequest": {"validityPeriod": {
            "start": "2026-01-01T10:00:00Z",
            "end": "2026-01-31T18:00:00Z",
        }},
    }) == (
        "2026-01-01", "2026-01-01T10:00:00.000000",
        "2026-01-31", "2026-01-31T18:00:00.000000",
    )


def test_nonpublishable_event_is_audited_without_raw_clinical_content(tmp_path):
    bundle = {
        "resourceType": "Bundle",
        "entry": [{
            "fullUrl": "Observation/excluded-1",
            "resource": {
                "resourceType": "Observation",
                "status": "entered-in-error",
                "id": "excluded-1",
                "valueString": "must not be persisted",
            },
        }],
    }
    path = tmp_path / "excluded.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    rows = extract_fhir_publication_exclusions(path, {"Observation"})
    assert rows == [(
        "Observation", "Observation/excluded-1", "entered-in-error",
        "FHIR_OBSERVATION_STATUS_ENTERED_IN_ERROR",
    )]

    con = duckdb.connect(":memory:")
    replace_fhir_publication_exclusions(
        con, "FHIR_R4_Observation", rows, run_id="RUN-1"
    )
    persisted = con.execute("""
        SELECT source_event_key, source_status, reason_code, run_id
        FROM fhir_publication_exclusion
    """).fetchone()
    assert persisted == (
        "Observation/excluded-1", "entered-in-error",
        "FHIR_OBSERVATION_STATUS_ENTERED_IN_ERROR", "RUN-1",
    )
    assert "must not be persisted" not in str(persisted)
