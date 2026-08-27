import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from src.mapping.governance import ensure_governance_tables
from src.mapping.semantic_mapper import (
    PROMPT_VERSION,
    build_prompt,
    parse_llm_decision,
    run_semantic_mapping,
)
from src.utils.config import MODEL_NAME


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _decision(decision="SELECT", concept_id=1004):
    return json.dumps({
        "decision": decision,
        "selected_concept_id": concept_id,
        "confidence": 0.94,
        "reason": "The action and anatomy match the candidate.",
        "clinical_signals": ["procedure action", "anatomy"],
    })


def test_structured_decision_accepts_only_retrieved_candidate_ids():
    parsed = parse_llm_decision(_decision(), [1004, 2004])
    assert parsed["selected_concept_id"] == 1004

    with pytest.raises(ValueError, match="retrieved candidate"):
        parse_llm_decision(_decision(concept_id=9999), [1004, 2004])
    with pytest.raises(ValueError, match="decision schema"):
        payload = json.loads(_decision())
        payload["unexpected"] = True
        parse_llm_decision(json.dumps(payload), [1004])


def test_abstain_requires_a_null_candidate():
    parsed = parse_llm_decision(_decision("ABSTAIN", None), [1004])
    assert parsed["decision"] == "ABSTAIN"

    with pytest.raises(ValueError, match="selected_concept_id=null"):
        parse_llm_decision(_decision("ABSTAIN", 1004), [1004])


def test_procedure_prompt_is_domain_locked():
    prompt = build_prompt(
        "procedure_occurrence", "legacy appendectomy",
        [{"concept_id": 1004, "concept_name": "Appendectomy"}],
    )
    assert "Target domain: Procedure" in prompt
    assert "Never invent an ID" in prompt
    assert "observations or devices" in prompt


@pytest.mark.parametrize(
    "adapter", ["condition", "drug", "measurement", "procedure", "observation", "device"]
)
def test_adapter_can_be_imported_as_a_direct_script_from_any_working_directory(
    adapter, tmp_path,
):
    script = PROJECT_ROOT / "src" / "mapping" / f"llm_{adapter}.py"
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    subprocess.run(
        [
            sys.executable, "-c",
            "import runpy; "
            f"runpy.run_path({str(script)!r}, run_name='adapter_import_test')",
        ],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


class _FakeCollection:
    metadata = {"distance_metric": "cosine", "index_signature": "index-test"}

    def count(self):
        return 1

    def query(self, query_texts, n_results):
        assert n_results == 5
        return {
            "ids": [["1004"]],
            "documents": [["Appendectomy"]],
            "distances": [[0.05]],
        }


class _FakeOllama:
    def __init__(self, content):
        self.content = content

    def list(self):
        return {"models": [{"model": MODEL_NAME, "digest": "sha256:model-test"}]}

    def chat(self, **kwargs):
        assert kwargs["format"]["additionalProperties"] is False
        assert kwargs["options"] == {
            "temperature": 0.0, "seed": 0, "num_predict": 512,
        }
        return {"message": {"content": self.content}}


def _procedure_database(path):
    with duckdb.connect(str(path)) as con:
        ensure_governance_tables(con)
        con.execute("CREATE TABLE vocabulary (vocabulary_id VARCHAR, vocabulary_version VARCHAR)")
        con.execute("INSERT INTO vocabulary VALUES ('None', 'v-test')")
        con.execute("""
            CREATE TABLE procedure_occurrence (
                procedure_occurrence_id BIGINT,
                procedure_concept_id INTEGER,
                procedure_source_concept_id INTEGER,
                procedure_source_value VARCHAR
            )
        """)
        con.execute("INSERT INTO procedure_occurrence VALUES (1, 0, 9001, 'legacy appendectomy')")


def test_procedure_adapter_persists_governed_proposal_without_publishing(monkeypatch, tmp_path):
    database = tmp_path / "procedure.duckdb"
    _procedure_database(database)
    monkeypatch.setattr(
        "src.mapping.semantic_mapper.get_versioned_collection",
        lambda con, path, target: _FakeCollection(),
    )

    result = run_semantic_mapping(
        "procedure_occurrence", db_path=database, chroma_path=tmp_path,
        client=_FakeOllama(_decision()),
    )

    assert result["proposals"] == 1
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("SELECT procedure_concept_id FROM procedure_occurrence").fetchone()[0] == 0
        assert con.execute("""
            SELECT mapping_method, status, prompt_version, llm_decision,
                   llm_confidence, model_digest, index_signature
            FROM mapping_decision
        """).fetchone() == (
            "llm_rag_json", "PENDING", PROMPT_VERSION, "SELECT", 0.94,
            "sha256:model-test", "index-test",
        )
        assert con.execute("""
            SELECT reviewed_by FROM mapping_provenance
        """).fetchone()[0] == "Pending_Human_Review"


def test_procedure_adapter_persists_abstention_as_non_publishable(monkeypatch, tmp_path):
    database = tmp_path / "abstain.duckdb"
    _procedure_database(database)
    monkeypatch.setattr(
        "src.mapping.semantic_mapper.get_versioned_collection",
        lambda con, path, target: _FakeCollection(),
    )

    result = run_semantic_mapping(
        "procedure_occurrence", db_path=database, chroma_path=tmp_path,
        client=_FakeOllama(_decision("ABSTAIN", None)),
    )

    assert result["abstentions"] == 1
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("SELECT status, assigned_concept_id FROM mapping_decision").fetchone() == (
            "ABSTAINED", 0,
        )
        assert con.execute("SELECT reviewed_by FROM mapping_provenance").fetchone()[0] == "LLM_ABSTAIN"
        assert con.execute("SELECT procedure_concept_id FROM procedure_occurrence").fetchone()[0] == 0


@pytest.mark.parametrize(
    ("target_table", "ddl", "insert_sql"),
    [
        (
            "observation",
            """CREATE TABLE observation (
                observation_id BIGINT, observation_concept_id INTEGER,
                observation_source_concept_id INTEGER,
                observation_source_value VARCHAR
            )""",
            "INSERT INTO observation VALUES (1, 0, 9001, 'legacy social observation')",
        ),
        (
            "device_exposure",
            """CREATE TABLE device_exposure (
                device_exposure_id BIGINT, device_concept_id INTEGER,
                device_source_concept_id INTEGER, device_source_value VARCHAR
            )""",
            "INSERT INTO device_exposure VALUES (1, 0, 9002, 'legacy implant')",
        ),
    ],
)
def test_new_domain_adapters_persist_safe_abstention(
    monkeypatch, tmp_path, target_table, ddl, insert_sql,
):
    database = tmp_path / f"{target_table}.duckdb"
    with duckdb.connect(str(database)) as con:
        ensure_governance_tables(con)
        con.execute("CREATE TABLE vocabulary (vocabulary_id VARCHAR, vocabulary_version VARCHAR)")
        con.execute("INSERT INTO vocabulary VALUES ('None', 'v-test')")
        con.execute(ddl)
        con.execute(insert_sql)
    monkeypatch.setattr(
        "src.mapping.semantic_mapper.get_versioned_collection",
        lambda con, path, target: _FakeCollection(),
    )

    result = run_semantic_mapping(
        target_table, db_path=database, chroma_path=tmp_path,
        client=_FakeOllama(_decision("ABSTAIN", None)),
    )

    assert result["abstentions"] == 1
    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("SELECT target_table, status FROM mapping_decision").fetchone() == (
            target_table, "ABSTAINED",
        )
