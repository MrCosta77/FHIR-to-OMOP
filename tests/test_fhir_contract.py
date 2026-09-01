import json
from pathlib import Path

import pytest

from src.etl.condition import extract_conditions
from src.etl.drug import extract_drugs
from src.etl.measurement import extract_measurements
from src.etl.observation import extract_observation_candidates
from src.etl.person import extract_persons
from src.etl.procedure import extract_procedures
from src.quality.validate_fhir import validate_bundle, validate_directory

GOLDEN = Path(__file__).parent / "fixtures" / "golden_fhir_bundle.json"


def test_golden_fhir_bundle_has_stable_resource_contract():
    counts = validate_bundle(GOLDEN)
    assert counts == {
        "Patient": 1, "Encounter": 1, "Condition": 1, "Observation": 2,
        "Medication": 1, "MedicationRequest": 1, "Procedure": 1,
    }


def test_directory_accepts_named_synthea_auxiliary_bundle(tmp_path):
    patient_bundle = json.loads(GOLDEN.read_text(encoding="utf-8"))
    (tmp_path / "patient.json").write_text(json.dumps(patient_bundle), encoding="utf-8")
    auxiliary = {
        "resourceType": "Bundle",
        "type": "batch",
        "entry": [{"resource": {"resourceType": "Organization", "id": "org-1"}}],
    }
    (tmp_path / "hospitalInformation123.json").write_text(
        json.dumps(auxiliary), encoding="utf-8"
    )

    counts = validate_directory(tmp_path)

    assert counts["Patient"] == 1
    assert counts["Organization"] == 1


def test_unnamed_bundle_without_patient_remains_invalid(tmp_path):
    path = tmp_path / "clinical.json"
    path.write_text(
        json.dumps({
            "resourceType": "Bundle",
            "entry": [{"resource": {"resourceType": "Organization", "id": "org-1"}}],
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bundle has no Patient"):
        validate_directory(tmp_path)


def _mutated_bundle(tmp_path, mutate):
    payload = json.loads(GOLDEN.read_text(encoding="utf-8"))
    mutate(payload)
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_unresolved_patient_reference_is_rejected(tmp_path):
    path = _mutated_bundle(tmp_path, lambda p: p["entry"][2]["resource"]["subject"].update(reference="Patient/missing"))
    with pytest.raises(ValueError, match="unresolved subject"):
        validate_bundle(path)


def test_unresolved_encounter_reference_is_rejected(tmp_path):
    path = _mutated_bundle(
        tmp_path,
        lambda p: p["entry"][2]["resource"]["encounter"].update(
            reference="Encounter/missing"
        ),
    )
    with pytest.raises(ValueError, match="unresolved Encounter reference"):
        validate_bundle(path)


def test_numeric_observation_requires_source_unit(tmp_path):
    def remove_unit(payload):
        quantity = payload["entry"][3]["resource"]["valueQuantity"]
        quantity.pop("unit")
        quantity.pop("code")
    path = _mutated_bundle(tmp_path, remove_unit)
    with pytest.raises(ValueError, match="no source unit"):
        validate_bundle(path)


def test_duplicate_resource_identity_is_rejected(tmp_path):
    path = _mutated_bundle(tmp_path, lambda p: p["entry"].append(p["entry"][0]))
    with pytest.raises(ValueError, match="duplicate (resource|fullUrl)"):
        validate_bundle(path)


def test_relative_clinical_full_url_is_rejected(tmp_path):
    path = _mutated_bundle(
        tmp_path,
        lambda p: p["entry"][2].update(fullUrl="Condition/condition-1"),
    )
    with pytest.raises(ValueError, match="absolute fullUrl namespace"):
        validate_bundle(path)


def test_golden_bundle_reconciles_to_deterministic_extractor_counts():
    extractors = {
        "person": (extract_persons, 1),
        "condition": (extract_conditions, 1),
        "drug": (extract_drugs, 1),
        # Both numeric and categorical Observation values are candidates;
        # Athena domain routing decides their final OMOP destination.
        "measurement": (extract_measurements, 2),
        "observation_candidate": (extract_observation_candidates, 3),
        "procedure": (extract_procedures, 1),
    }
    for name, (extractor, expected) in extractors.items():
        first = extractor(GOLDEN)
        second = extractor(GOLDEN)
        assert len(first) == expected, f"{name}: source-to-staging count drift"
        assert first == second, f"{name}: extraction is not idempotent"
