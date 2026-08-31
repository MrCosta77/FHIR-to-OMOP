import json
from pathlib import Path

import pytest

from src.adapters.hospital_csv import (
    HospitalCSVError,
    load_hospital_csv,
    summarize_hospital_csv,
)
from src.clinical_mapping_core import Candidate, render_mapping_prompt

FIXTURE = Path(__file__).parent / "fixtures" / "hospital_csv" / "golden_hospital.csv"
HEADER = (
    "schema_version,record_id,domain,source_value,source_system,source_code,"
    "unit,specimen,route,dose,event_date"
)


def _write_csv(path, rows, header=HEADER):
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")


def test_semicolon_hospital_fixture_routes_four_synthetic_domains():
    records = load_hospital_csv(FIXTURE)

    assert len(records) == 4
    assert [record.record_id for record in records] == [
        "lab-001", "proc-001", "drug-001", "obs-001",
    ]
    assert records[0].target_table == "measurement"
    assert records[0].target_domain == "Measurement"
    assert records[0].target_vocabulary == "LOINC"
    assert dict(records[0].context) == {
        "source_system": "LIS_LOCAL",
        "source_code": "GLU_BLD",
        "unit": "mg/dL",
        "specimen": "blood",
        "event_date": "2026-01-15",
    }


def test_csv_record_builds_core_request_without_exposing_record_id():
    record = load_hospital_csv(FIXTURE)[0]

    prepared = record.prepare_mapping_request(
        [Candidate(300, "Glucose [Mass/volume] in Blood")]
    )
    prompt = render_mapping_prompt(
        prepared.request,
        role="laboratory terminology specialist",
        guidance="Check analyte, specimen and unit.",
    )

    assert prepared.target_table == "measurement"
    assert prepared.redaction_categories == ()
    assert "lab-001" not in prompt
    assert '"specimen": "blood"' in prompt
    assert '"unit": "mg/dL"' in prompt
    assert '"concept_id": 300' in prompt


def test_csv_summary_is_metadata_only():
    records = load_hospital_csv(FIXTURE)

    summary = summarize_hospital_csv(records)
    serialized = json.dumps(summary)

    assert summary["record_count"] == 4
    assert summary["by_target_table"] == {
        "drug_exposure": 1,
        "measurement": 1,
        "observation": 1,
        "procedure_occurrence": 1,
    }
    assert "glicose sangue" not in serialized
    assert "GLU_BLD" not in serialized


def test_prompt_bound_csv_values_are_redacted_before_core_request(tmp_path):
    path = tmp_path / "phi-like.csv"
    raw_email = "ana@example.org"
    raw_identifier = "MRN: ABC-12345"
    _write_csv(path, [
        f"hospital-csv-v1,row-1,condition,asma reported by {raw_email},EHR,"
        f"{raw_identifier},,,,,2026-01-15"
    ])

    record = load_hospital_csv(path)[0]
    prepared = record.prepare_mapping_request([Candidate(317009, "Asthma")])
    prompt = render_mapping_prompt(
        prepared.request,
        role="clinical terminology specialist",
        guidance="Check condition meaning.",
    )

    assert raw_email not in prompt
    assert raw_identifier not in prompt
    assert "[REDACTED_EMAIL]" in prompt
    assert "[REDACTED_LOCAL_IDENTIFIER]" in prompt
    assert prepared.redaction_categories == ("EMAIL", "LOCAL_IDENTIFIER")


def test_non_allowlisted_columns_fail_closed_before_values_are_used(tmp_path):
    path = tmp_path / "unknown-column.csv"
    _write_csv(
        path,
        ["hospital-csv-v1,row-1,condition,asthma,EHR,ASTHMA,,,,,2026-01-15,Ana"],
        header=HEADER + ",patient_name",
    )

    with pytest.raises(HospitalCSVError, match="non-allowlisted.*patient_name"):
        load_hospital_csv(path)


def test_duplicate_record_ids_are_rejected(tmp_path):
    path = tmp_path / "duplicates.csv"
    row = "hospital-csv-v1,row-1,device,legacy implant,EHR,DEV1,,,,,2026-01-15"
    _write_csv(path, [row, row])

    with pytest.raises(HospitalCSVError, match="Duplicate record_id"):
        load_hospital_csv(path)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (
            "hospital-csv-v0,row-1,condition,asthma,EHR,ASTHMA,,,,,2026-01-15",
            "unsupported schema_version",
        ),
        (
            "hospital-csv-v1,row-1,unknown,asthma,EHR,ASTHMA,,,,,2026-01-15",
            "unsupported domain",
        ),
        (
            "hospital-csv-v1,row-1,condition,,EHR,ASTHMA,,,,,2026-01-15",
            "empty source_value",
        ),
        (
            "hospital-csv-v1,row-1,condition,asthma,EHR,ASTHMA,,,,,15/01/2026",
            "ISO YYYY-MM-DD",
        ),
    ],
)
def test_invalid_contract_rows_fail_closed(tmp_path, row, message):
    path = tmp_path / "invalid.csv"
    _write_csv(path, [row])

    with pytest.raises(HospitalCSVError, match=message):
        load_hospital_csv(path)
