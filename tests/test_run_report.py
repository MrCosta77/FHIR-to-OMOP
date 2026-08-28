import json

import duckdb
import pytest

from src.quality.run_report import (
    build_run_report,
    dqd_evidence,
    load_and_verify_report,
    seal_report,
    stage_immutable_report,
    publish_staged_report,
    verify_report,
    write_immutable_report,
)


def _database(path):
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE condition_occurrence(condition_concept_id INTEGER)")
        con.execute("INSERT INTO condition_occurrence VALUES (1), (0)")
        for table, column in (
            ("drug_exposure", "drug_concept_id"),
            ("measurement", "measurement_concept_id"),
            ("observation", "observation_concept_id"),
            ("procedure_occurrence", "procedure_concept_id"),
            ("device_exposure", "device_concept_id"),
        ):
            con.execute(f"CREATE TABLE {table}({column} INTEGER)")
            con.execute(f"INSERT INTO {table} VALUES (1)")
        con.execute("""
            CREATE TABLE mapping_decision(
                mapping_decision_id VARCHAR, run_id VARCHAR,
                target_table VARCHAR, status VARCHAR
            )
        """)
        con.execute("INSERT INTO mapping_decision VALUES ('d1','RUN-test','condition_occurrence','PENDING_REVIEW')")


def _junit(path):
    path.write_text(
        '<testsuites><testsuite tests="3" failures="0" errors="0" skipped="1" time="1.25"/></testsuites>',
        encoding="utf-8",
    )


def _manifest():
    return {
        "run_id": "RUN-test",
        "status": "SUCCESS",
        "started_at": "2026-08-28T08:00:00+00:00",
        "completed_at": "2026-08-28T08:01:00+00:00",
        "git_commit": "abc123",
        "inputs": [{"path": "Patient/secret.json", "size": 10, "sha256": "deadbeef"}],
        "configuration": {"runtime": {
            "profile": "development", "model_name": "local",
            "similarity_threshold": 0.9, "data_classification": "SYNTHETIC",
            "ollama_url": "http://localhost:11434/api/generate",
            "simulate_lis_noise": False, "require_integration": True,
            "include_dqd": False, "db_path": "secret-path",
        }},
        "steps": [{
            "name": "quality", "script": "tests", "status": "SUCCESS",
            "duration_seconds": 1.5,
        }],
    }


def _minimal_report(run_id="RUN-test"):
    return {
        "schema_version": "cmf-run-report-v1",
        "report_builder_version": "test",
        "generated_at": "2026-08-28T08:00:00+00:00",
        "run": {"run_id": run_id, "status": "SUCCESS", "git_commit": "abc"},
        "configuration": {}, "inputs": {}, "pipeline": {}, "mapping": {},
        "quality_gates": {
            "pytest": {"status": "PASSED"},
            "dqd": {"required": False, "status": "NOT_RUN"},
        },
        "database": {"bytes": 0, "sha256": "0" * 64},
        "privacy": {
            "report_contains_phi": False,
            "report_contains_source_values": False,
            "report_contains_reviewer_identities": False,
        },
        "readiness": {
            "classification": "TECHNICAL_EVIDENCE",
            "deployment_authorized": False,
            "clinical_validation_required": True,
        },
    }


def test_report_aggregates_evidence_without_source_paths(tmp_path):
    database = tmp_path / "run.duckdb"
    junit = tmp_path / "pytest.xml"
    _database(database)
    _junit(junit)
    report = build_run_report(_manifest(), database, junit)
    serialized = json.dumps(report)
    assert report["quality_gates"]["pytest"]["tests"] == 3
    assert report["quality_gates"]["dqd"]["status"] == "NOT_RUN"
    assert report["mapping"]["domains"]["condition_occurrence"]["coverage"] == 0.5
    assert report["mapping"]["decision_counts"][0]["count"] == 1
    assert report["readiness"]["deployment_authorized"] is False
    assert "Patient/secret.json" not in serialized
    assert "secret-path" not in serialized


def test_immutable_report_is_content_addressed_and_refuses_overwrite(tmp_path):
    report = _minimal_report()
    path = write_immutable_report(report, tmp_path)
    loaded = load_and_verify_report(path)
    assert verify_report(loaded)
    assert loaded["integrity"]["payload_sha256"][:16] in path.name
    with pytest.raises(FileExistsError):
        write_immutable_report(report, tmp_path)


def test_staged_report_is_hidden_until_explicit_publication(tmp_path):
    report = _minimal_report("RUN-stage")
    temporary, destination = stage_immutable_report(report, tmp_path)
    assert temporary.exists()
    assert not destination.exists()
    publish_staged_report(temporary, destination)
    assert destination.exists()
    assert not temporary.exists()


def test_report_tampering_is_detected():
    report = seal_report(_minimal_report())
    report["run"]["status"] = "ALTERED"
    assert verify_report(report) is False


def test_required_dqd_is_validated_and_hashed(tmp_path):
    result = tmp_path / "dqd.json"
    policy = tmp_path / "policy.json"
    result.write_text(json.dumps({"CheckResults": [{
        "checkId": "ok", "isError": 0, "failed": 0,
    }]}), encoding="utf-8")
    policy.write_text(json.dumps({
        "status": "approved", "max_errors": 0, "max_failed_checks": 0,
        "allowed_failure_ids": {},
    }), encoding="utf-8")
    evidence = dqd_evidence(required=True, result_path=result, policy_path=policy)
    assert evidence["status"] == "PASSED"
    assert evidence["summary"]["total"] == 1
    assert len(evidence["result_sha256"]) == 64


def test_required_dqd_cannot_be_omitted(tmp_path):
    with pytest.raises(FileNotFoundError, match="run-linked DQD"):
        dqd_evidence(required=True, result_path=None, policy_path=tmp_path / "policy")


def test_unsafe_or_incomplete_report_cannot_be_sealed():
    unsafe = _minimal_report()
    unsafe["readiness"]["deployment_authorized"] = True
    with pytest.raises(ValueError, match="cannot authorize"):
        seal_report(unsafe)
    with pytest.raises(ValueError, match="missing required"):
        seal_report({"schema_version": "cmf-run-report-v1"})
