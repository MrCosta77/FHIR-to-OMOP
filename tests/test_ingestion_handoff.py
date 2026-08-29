import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path

import duckdb
import pytest

from src.adapters.hospital_csv import load_hospital_csv
from src.adapters.ingestion_handoff import (
    IngestionHandoffError,
    IngestionReceipt,
    RECEIPT_SCHEMA_VERSION,
    load_ingestion_receipts,
    main as handoff_main,
    process_ingestion_handoff,
)
from src.adapters.source_identity import (
    SourceIdentityError,
    claim_hospital_csv_identity,
    register_source_system,
)
from src.mapping.governance import ensure_governance_tables, register_decision
from src.security.privacy import PrivacyError


FIXTURE = Path(__file__).parent / "fixtures" / "hospital_csv" / "golden_hospital.csv"
INGESTION_RUN_ID = "RUN-hospital-ingestion"
INPUT_MANIFEST = '[{"sha256": "abc", "size": 123}]'
INPUT_MANIFEST_DIGEST = hashlib.sha256(INPUT_MANIFEST.encode("utf-8")).hexdigest()


def _database(path, *, run_status="SUCCESS"):
    with duckdb.connect(str(path)) as con:
        ensure_governance_tables(con)
        con.execute("""
            INSERT INTO etl_run (
                run_id, status, started_at, completed_at, git_commit,
                input_manifest, configuration_manifest, step_manifest
            ) VALUES (?, ?, now(), now(), 'abc123', ?, '{}', '[]')
        """, [INGESTION_RUN_ID, run_status, INPUT_MANIFEST])
        con.execute("""
            CREATE TABLE measurement (
                measurement_id BIGINT,
                measurement_concept_id INTEGER,
                measurement_source_concept_id INTEGER,
                measurement_source_value VARCHAR
            )
        """)


def _record(record_id):
    return replace(load_hospital_csv(FIXTURE)[0], record_id=record_id)


def _prepare_decision(con, record, *, concept_id=300, llm_decision="SELECT"):
    claim = claim_hospital_csv_identity(record)
    register_source_system(
        con,
        claim.source_adapter,
        claim.source_system,
        "CMF_HOSP_LIS",
        actor="Source Administrator",
        reason="Approved test source",
    )
    status = "PRE_INGESTION" if llm_decision == "SELECT" else "ABSTAINED"
    decision_id = register_decision(
        con,
        claim.target_table,
        record.source_value,
        concept_id,
        "Candidate",
        "llm_rag_json",
        0.94,
        "test-model",
        "v-test",
        status,
        run_id="RUN-mapping",
        llm_decision=llm_decision,
        llm_confidence=0.94 if llm_decision == "SELECT" else 0.0,
        source_adapter=claim.source_adapter,
        source_record_key=claim.source_record_key,
        publication_eligible=False,
    )
    con.execute("""
        INSERT INTO mapping_provenance (
            target_table, target_id, source_value, normalized_value,
            assigned_concept_id, mapping_method, score, model_name,
            vocabulary_version, reviewed_by, run_id, mapping_decision_id,
            source_adapter, source_record_key, publication_eligible
        ) VALUES (?, ?, ?, 'Candidate', ?, 'llm_rag_json', 0.94,
                  'test-model', 'v-test', ?, 'RUN-mapping', ?, ?, ?, FALSE)
    """, [
        claim.target_table,
        -int(claim.source_record_key[:15], 16),
        record.source_value,
        concept_id,
        (
            "Pre_Ingestion_Proposal"
            if llm_decision == "SELECT"
            else "Pre_Ingestion_Abstention"
        ),
        decision_id,
        claim.source_adapter,
        claim.source_record_key,
    ])
    return claim, decision_id


def _receipt(record, target_id, *, manifest_digest=INPUT_MANIFEST_DIGEST):
    claim = claim_hospital_csv_identity(record)
    return IngestionReceipt(
        schema_version=RECEIPT_SCHEMA_VERSION,
        ingestion_run_id=INGESTION_RUN_ID,
        input_manifest_sha256=manifest_digest,
        source_adapter=claim.source_adapter,
        source_system=claim.source_system,
        source_code=claim.source_code,
        source_record_key=claim.source_record_key,
        target_table=claim.target_table,
        target_id=target_id,
    )


def test_receipt_contract_is_strict_and_file_loader_rejects_unknown_fields(tmp_path):
    receipt = _receipt(_record("lab-contract"), 101)
    payload = {field: getattr(receipt, field) for field in (
        "schema_version", "ingestion_run_id", "input_manifest_sha256",
        "source_adapter", "source_system", "source_code",
        "source_record_key", "target_table", "target_id",
    )}
    path = tmp_path / "receipts.json"
    path.write_text(json.dumps([payload]), encoding="utf-8")
    assert load_ingestion_receipts(path) == (receipt,)

    payload["patient_name"] = "Forbidden"
    path.write_text(json.dumps([payload]), encoding="utf-8")
    with pytest.raises(IngestionHandoffError, match="unknown"):
        load_ingestion_receipts(path)
    with pytest.raises(IngestionHandoffError, match="positive"):
        replace(receipt, target_id=True)
    with pytest.raises(SourceIdentityError, match="direct-identifier"):
        replace(receipt, source_code="patient@example.org")


def test_successful_handoff_is_idempotent_and_report_is_metadata_only(tmp_path):
    database = tmp_path / "handoff.duckdb"
    _database(database)
    first = _record("lab-101")
    second = _record("lab-102")
    receipts = (_receipt(first, 101), _receipt(second, 102))

    with duckdb.connect(str(database)) as con:
        con.execute("""
            INSERT INTO measurement VALUES
            (101, 0, 0, 'GLU_BLD'), (102, 0, 0, 'GLU_BLD')
        """)
        _prepare_decision(con, first)
        _prepare_decision(con, second)

        report = process_ingestion_handoff(
            con,
            receipts,
            actor="Source Administrator",
            reason="Verified hospital ingestion receipts",
        )
        repeated = process_ingestion_handoff(
            con,
            receipts,
            actor="Source Administrator",
            reason="Verified hospital ingestion receipts",
        )
        regrouped = process_ingestion_handoff(
            con,
            (receipts[0],),
            actor="Source Administrator",
            reason="Verified hospital ingestion receipts",
        )
        con.execute("""
            INSERT INTO etl_run (
                run_id, status, started_at, completed_at, git_commit,
                input_manifest, configuration_manifest, step_manifest
            ) VALUES ('RUN-other-ingestion', 'SUCCESS', now(), now(),
                      'abc123', ?, '{}', '[]')
        """, [INPUT_MANIFEST])
        conflicting = process_ingestion_handoff(
            con,
            (replace(receipts[0], ingestion_run_id="RUN-other-ingestion"),),
            actor="Source Administrator",
            reason="Verified hospital ingestion receipts",
        )

        assert report.counts == {"BOUND": 2, "ALREADY_BOUND": 0, "FAILED": 0}
        assert repeated.counts == {
            "BOUND": 0, "ALREADY_BOUND": 2, "FAILED": 0,
        }
        assert regrouped.counts == {
            "BOUND": 0, "ALREADY_BOUND": 1, "FAILED": 0,
        }
        assert conflicting.counts == {
            "BOUND": 0, "ALREADY_BOUND": 0, "FAILED": 1,
        }
        assert conflicting.outcomes[0].failure_code == "RECEIPT_REJECTED"
        assert con.execute("SELECT COUNT(*) FROM source_event_binding").fetchone()[0] == 2
        assert con.execute("""
            SELECT COUNT(*) FROM source_event_binding
            WHERE ingestion_run_id = ? AND input_manifest_sha256 = ?
              AND handoff_batch_id = ?
        """, [
            INGESTION_RUN_ID, INPUT_MANIFEST_DIGEST, report.batch_id,
        ]).fetchone()[0] == 2
        assert con.execute("""
            SELECT COUNT(*) FROM mapping_decision
            WHERE publication_eligible AND status = 'PENDING'
        """).fetchone()[0] == 2
        assert con.execute("""
            SELECT COUNT(*) FROM security_audit_log
            WHERE event_type = 'INGESTION_HANDOFF_BATCH'
              AND outcome = 'AUTHORIZED'
        """).fetchone()[0] == 4
        assert con.execute("""
            SELECT COUNT(*) FROM security_audit_log
            WHERE event_type = 'INGESTION_RECEIPT_ATTESTATION'
        """).fetchone()[0] == 5

        serialized = json.dumps(report.as_dict(), sort_keys=True)
        assert "GLU_BLD" not in serialized
        assert first.source_record_key not in serialized
        assert report.as_dict()["report_contains_phi"] is False


def test_expected_receipt_failures_are_isolated_without_partial_record_writes(tmp_path):
    database = tmp_path / "partial.duckdb"
    _database(database)
    valid = _record("lab-valid")
    wrong_event = _record("lab-wrong")
    ambiguous = _record("lab-ambiguous")
    receipts = (
        _receipt(valid, 101),
        _receipt(wrong_event, 102),
        _receipt(ambiguous, 103),
    )

    with duckdb.connect(str(database)) as con:
        con.execute("""
            INSERT INTO measurement VALUES
            (101, 0, 0, 'GLU_BLD'),
            (102, 0, 0, 'WRONG'),
            (103, 0, 0, 'GLU_BLD')
        """)
        _prepare_decision(con, valid)
        wrong_claim, wrong_decision = _prepare_decision(con, wrong_event)
        _prepare_decision(con, ambiguous, concept_id=300)
        _prepare_decision(con, ambiguous, concept_id=301)

        report = process_ingestion_handoff(
            con,
            receipts,
            actor="Source Administrator",
            reason="Verified hospital ingestion receipts",
        )

        assert report.counts == {"BOUND": 1, "ALREADY_BOUND": 0, "FAILED": 2}
        assert [outcome.failure_code for outcome in report.outcomes] == [
            None, "EVENT_BINDING_REJECTED", "RECEIPT_REJECTED",
        ]
        assert con.execute("SELECT target_id FROM source_event_binding").fetchall() == [(101,)]
        assert con.execute("""
            SELECT status, publication_eligible FROM mapping_decision
            WHERE mapping_decision_id = ?
        """, [wrong_decision]).fetchone() == ("PRE_INGESTION", False)
        assert con.execute("""
            SELECT COUNT(*) FROM mapping_provenance
            WHERE source_record_key = ? AND publication_eligible
        """, [wrong_claim.source_record_key]).fetchone()[0] == 0


@pytest.mark.parametrize("run_status", ["RUNNING", "FAILED"])
def test_handoff_requires_successful_run_and_exact_manifest(tmp_path, run_status):
    database = tmp_path / f"run-{run_status}.duckdb"
    _database(database, run_status=run_status)
    record = _record(f"lab-{run_status}")
    with duckdb.connect(str(database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        _prepare_decision(con, record)
        with pytest.raises(IngestionHandoffError, match="successful ETL run"):
            process_ingestion_handoff(
                con,
                (_receipt(record, 101),),
                actor="Source Administrator",
                reason="Verified hospital ingestion receipt",
            )
        assert con.execute("SELECT COUNT(*) FROM source_event_binding").fetchone()[0] == 0

    valid_database = tmp_path / f"manifest-{run_status}.duckdb"
    _database(valid_database)
    with duckdb.connect(str(valid_database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        _prepare_decision(con, record)
        with pytest.raises(IngestionHandoffError, match="manifest digest"):
            process_ingestion_handoff(
                con,
                (_receipt(record, 101, manifest_digest="0" * 64),),
                actor="Source Administrator",
                reason="Verified hospital ingestion receipt",
            )
        assert con.execute("SELECT COUNT(*) FROM source_event_binding").fetchone()[0] == 0


def test_batch_preflight_and_audit_fail_before_any_binding(tmp_path, monkeypatch):
    database = tmp_path / "preflight.duckdb"
    _database(database)
    record = _record("lab-preflight")
    receipt = _receipt(record, 101)
    with duckdb.connect(str(database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        _prepare_decision(con, record)
        with pytest.raises(IngestionHandoffError, match="duplicate source"):
            process_ingestion_handoff(
                con,
                (receipt, receipt),
                actor="Source Administrator",
                reason="Verified hospital ingestion receipt",
            )

        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr(
            "src.adapters.ingestion_handoff.audit_security_event", fail_audit
        )
        with pytest.raises(RuntimeError, match="audit unavailable"):
            process_ingestion_handoff(
                con,
                (receipt,),
                actor="Source Administrator",
                reason="Verified hospital ingestion receipt",
            )
        assert con.execute("SELECT COUNT(*) FROM source_event_binding").fetchone()[0] == 0


def test_phi_handoff_requires_authenticated_allowlisted_source_admin(tmp_path):
    database = tmp_path / "phi.duckdb"
    _database(database)
    record = _record("lab-phi")
    environment = {
        "CMF_DATA_CLASSIFICATION": "PHI",
        "CMF_PHI_ENABLED": "true",
        "CMF_PHI_POLICY_APPROVED_BY": "Hospital DPO",
        "CMF_PHI_RETENTION_DAYS": "30",
        "CMF_AUTHENTICATED_USER": "Source Administrator",
        "CMF_OLLAMA_URL": "http://localhost:11434/api/generate",
    }
    with duckdb.connect(str(database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        _prepare_decision(con, record)
        with pytest.raises(PrivacyError, match="not authorized"):
            process_ingestion_handoff(
                con,
                (_receipt(record, 101),),
                actor="Source Administrator",
                reason="Verified hospital ingestion receipt",
                environ=environment,
            )
        assert con.execute("SELECT COUNT(*) FROM source_event_binding").fetchone()[0] == 0


def test_cli_prints_only_metadata_safe_report(tmp_path, monkeypatch, capsys):
    database = tmp_path / "cli.duckdb"
    receipt_path = tmp_path / "receipts.json"
    _database(database)
    record = _record("lab-cli")
    receipt = _receipt(record, 101)
    receipt_path.write_text(json.dumps([asdict(receipt)]), encoding="utf-8")
    with duckdb.connect(str(database)) as con:
        con.execute("INSERT INTO measurement VALUES (101, 0, 0, 'GLU_BLD')")
        _prepare_decision(con, record)

    monkeypatch.setattr(
        "sys.argv",
        [
            "ingestion_handoff",
            str(receipt_path),
            "--database",
            str(database),
            "--actor",
            "Source Administrator",
            "--reason",
            "Verified hospital ingestion receipt",
        ],
    )
    assert handoff_main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["counts"] == {"ALREADY_BOUND": 0, "BOUND": 1, "FAILED": 0}
    serialized = json.dumps(report, sort_keys=True)
    assert "GLU_BLD" not in serialized
    assert record.source_record_key not in serialized
