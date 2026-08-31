import json

import duckdb
import pytest

from src.adapters.hospital_csv_mapping import run_hospital_csv_mapping
from src.mapping.governance import (
    blinded_review_queue,
    bootstrap_identity_administrator,
    ensure_governance_tables,
    register_governed_actor,
    submit_blinded_review,
)
from src.mapping.mapping_service import record_external_mapping_decision
from src.utils.config import MODEL_NAME


class _FakeCollection:
    metadata = {"distance_metric": "cosine", "index_signature": "csv-index-test"}

    def count(self):
        return 1

    def query(self, query_texts, n_results):
        assert n_results == 5
        assert "ana@example.org" not in query_texts[0]
        assert "MRN: ABC-12345" not in query_texts[0]
        return {
            "ids": [["1004"]],
            "documents": [["Appendectomy"]],
            "distances": [[0.05]],
        }


def test_external_persistence_contract_fails_closed_before_database_access():
    key = "a" * 64
    with pytest.raises(ValueError, match="source adapter"):
        record_external_mapping_decision(
            None, "measurement", "term", key, None,
            {"decision": "ABSTAIN"}, source_adapter="",
        )
    with pytest.raises(ValueError, match="SHA-256"):
        record_external_mapping_decision(
            None, "measurement", "term", "not-a-digest", None,
            {"decision": "ABSTAIN"}, source_adapter="hospital-csv-v1",
        )
    with pytest.raises(ValueError, match="SELECT requires"):
        record_external_mapping_decision(
            None, "measurement", "term", key, None,
            {"decision": "SELECT"}, source_adapter="hospital-csv-v1",
        )

class _FakeOllama:
    def __init__(self, decision="SELECT"):
        self.decision = decision
        self.prompts = []

    def list(self):
        return {"models": [{"model": MODEL_NAME, "digest": "sha256:csv-model"}]}

    def chat(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        selected = 1004 if self.decision == "SELECT" else None
        return {"message": {"content": json.dumps({
            "decision": self.decision,
            "selected_concept_id": selected,
            "confidence": 0.94,
            "reason": "The procedure meaning matches." if selected else "Insufficient evidence.",
            "clinical_signals": ["procedure action"],
        })}}


def _database(path):
    with duckdb.connect(str(path)) as con:
        ensure_governance_tables(con)
        bootstrap_identity_administrator(
            con, "Test Identity Administrator",
            "One-time hospital CSV test identity bootstrap.",
        )
        register_governed_actor(
            con, "Reviewer One", {"reviewer"}, "Test Identity Administrator",
            "Explicit governed hospital CSV test identity.",
        )
        con.execute(
            "CREATE TABLE vocabulary (vocabulary_id VARCHAR, vocabulary_version VARCHAR)"
        )
        con.execute("INSERT INTO vocabulary VALUES ('None', 'v-test')")
        con.execute("""
            CREATE TABLE concept (
                concept_id INTEGER, vocabulary_id VARCHAR, domain_id VARCHAR,
                standard_concept VARCHAR, invalid_reason VARCHAR,
                valid_start_date VARCHAR, valid_end_date VARCHAR
            )
        """)
        con.execute("""
            INSERT INTO concept VALUES
            (1004, 'SNOMED', 'Procedure', 'S', NULL, '20200101', '20991231')
        """)
        con.execute("""
            CREATE TABLE source_to_concept_map (
                source_code VARCHAR, source_concept_id INTEGER,
                source_vocabulary_id VARCHAR, source_code_description VARCHAR,
                target_concept_id INTEGER, target_vocabulary_id VARCHAR,
                valid_start_date DATE, valid_end_date DATE, invalid_reason VARCHAR
            )
        """)


def _csv(path):
    path.write_text(
        "schema_version,record_id,domain,source_value,source_system,source_code\n"
        "hospital-csv-v1,row-private-1,procedure,legacy appendectomy contact "
        "ana@example.org,EHR,MRN: ABC-12345\n",
        encoding="utf-8",
    )


def test_csv_runner_retrieves_calls_local_llm_and_persists_non_publishable_proposal(
    monkeypatch, tmp_path
):
    database = tmp_path / "hospital.duckdb"
    source = tmp_path / "hospital.csv"
    _database(database)
    _csv(source)
    monkeypatch.setattr(
        "src.adapters.hospital_csv_mapping.get_versioned_collection",
        lambda con, path, target: _FakeCollection(),
    )
    client = _FakeOllama()

    first = run_hospital_csv_mapping(
        source, db_path=database, chroma_path=tmp_path, client=client
    )
    second = run_hospital_csv_mapping(
        source, db_path=database, chroma_path=tmp_path, client=client
    )

    assert first == {
        "source_adapter": "hospital-csv-v1",
        "records": 1,
        "proposals": 1,
        "abstentions": 0,
        "persisted": 1,
    }
    assert second["persisted"] == 0
    assert "row-private-1" not in client.prompts[0]
    assert "ana@example.org" not in client.prompts[0]
    assert "[REDACTED_EMAIL]" in client.prompts[0]
    with duckdb.connect(str(database), read_only=True) as con:
        decision = con.execute("""
            SELECT source_value, source_adapter, LENGTH(source_record_key),
                   publication_eligible, status
            FROM mapping_decision
        """).fetchone()
        assert decision == (
            "legacy appendectomy contact [REDACTED_EMAIL]",
            "hospital-csv-v1",
            64,
            False,
            "PRE_INGESTION",
        )
        assert con.execute("SELECT COUNT(*) FROM mapping_provenance").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0


def test_pre_ingestion_csv_proposal_cannot_enter_clinical_review_or_stcm(
    monkeypatch, tmp_path
):
    database = tmp_path / "blocked-publication.duckdb"
    source = tmp_path / "hospital.csv"
    _database(database)
    _csv(source)
    monkeypatch.setattr(
        "src.adapters.hospital_csv_mapping.get_versioned_collection",
        lambda con, path, target: _FakeCollection(),
    )
    run_hospital_csv_mapping(
        source, db_path=database, chroma_path=tmp_path, client=_FakeOllama()
    )

    with duckdb.connect(str(database)) as con:
        decision_id = con.execute(
            "SELECT mapping_decision_id FROM mapping_decision"
        ).fetchone()[0]
        assert blinded_review_queue(con, "Reviewer One") == []
        with pytest.raises(ValueError, match="Pre-ingestion proposals"):
            submit_blinded_review(
                con, decision_id, "APPROVE", "Reviewer One", "Verified"
            )
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0


def test_csv_abstention_is_persisted_and_never_reviewable(monkeypatch, tmp_path):
    database = tmp_path / "abstention.duckdb"
    source = tmp_path / "hospital.csv"
    _database(database)
    _csv(source)
    monkeypatch.setattr(
        "src.adapters.hospital_csv_mapping.get_versioned_collection",
        lambda con, path, target: _FakeCollection(),
    )

    result = run_hospital_csv_mapping(
        source,
        db_path=database,
        chroma_path=tmp_path,
        client=_FakeOllama("ABSTAIN"),
    )

    assert result["abstentions"] == 1
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("""
            SELECT status, assigned_concept_id, publication_eligible
            FROM mapping_decision
        """).fetchone() == ("ABSTAINED", 0, False)
        assert con.execute(
            "SELECT reviewed_by FROM mapping_provenance"
        ).fetchone()[0] == "LLM_ABSTAIN"
