import json

import pytest

from src.etl.person import extract_persons


def _patient_bundle(extensions):
    return {
        "resourceType": "Bundle",
        "entry": [
            {
                "fullUrl": "urn:uuid:patient-1",
                "resource": {
                    "resourceType": "Patient",
                    "id": "patient-1",
                    "gender": "female",
                    "birthDate": "1980-01-02",
                    "extension": extensions,
                },
            }
        ],
    }


@pytest.mark.parametrize(
    "extensions",
    [
        [],
        None,
        [None],
        [{"url": "race", "extension": []}],
        [{"url": "ethnicity", "extension": None}],
        [{"url": "race", "extension": [{}]}],
    ],
)
def test_empty_or_malformed_demographic_extensions_remain_unmapped(tmp_path, extensions):
    bundle_path = tmp_path / "patient.json"
    bundle_path.write_text(json.dumps(_patient_bundle(extensions)), encoding="utf-8")

    records = extract_persons(bundle_path)

    assert len(records) == 1
    assert records[0].race_concept_id == 0
    assert records[0].ethnicity_concept_id == 0


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ("Hispanic or Latino", 38003563),
        ("Not Hispanic or Latino", 38003564),
        ("Non-Hispanic", 38003564),
    ],
)
def test_ethnicity_text_does_not_confuse_negative_with_positive(tmp_path, display, expected):
    extensions = [
        {
            "url": "http://hl7.org/fhir/us/core/StructureDefinition/us-core-ethnicity",
            "extension": [{"valueCoding": {"display": display}}],
        }
    ]
    bundle_path = tmp_path / "patient.json"
    bundle_path.write_text(json.dumps(_patient_bundle(extensions)), encoding="utf-8")

    record = extract_persons(bundle_path)[0]

    assert record.ethnicity_concept_id == expected
