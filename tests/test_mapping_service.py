import duckdb

from src.etl.apply_stcm import apply_stcm_mappings
from src.mapping.mapping_service import (
    get_few_shot_prompt,
    get_versioned_collection,
    reconcile_resolved_proposals,
    record_mapping_proposal,
    selected_candidate,
)


def _create_stcm(con):
    con.execute("""
        CREATE TABLE source_to_concept_map (
            source_code VARCHAR, source_concept_id INTEGER,
            source_vocabulary_id VARCHAR, source_code_description VARCHAR,
            target_concept_id INTEGER, target_vocabulary_id VARCHAR,
            valid_start_date DATE, valid_end_date DATE, invalid_reason VARCHAR
        )
    """)


def _create_provenance(con):
    con.execute("CREATE SEQUENCE seq_provenance_id START 1")
    con.execute("""
        CREATE TABLE mapping_provenance (
            provenance_id BIGINT DEFAULT nextval('seq_provenance_id'),
            target_table VARCHAR, target_id BIGINT, source_value VARCHAR,
            normalized_value VARCHAR, assigned_concept_id INTEGER,
            mapping_method VARCHAR, score DOUBLE, model_name VARCHAR,
            vocabulary_version VARCHAR, reviewed_by VARCHAR,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def test_candidate_parser_uses_exact_id_and_cosine_score():
    results = {
        "ids": [["12", "123"]],
        "documents": [["Wrong", "Correct"]],
        "distances": [[0.1, 0.2]],
    }

    assert selected_candidate(results, "123") == (123, "Correct", 0.2, 0.8)
    assert selected_candidate(results, "The answer is 999") is None


def test_few_shot_examples_have_stable_order():
    with duckdb.connect(":memory:") as con:
        _create_provenance(con)
        con.executemany("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, reviewed_by
            ) VALUES ('measurement', ?, ?, ?, ?, 'Approved_by_Human')
        """, [(2, "Zulu", "Z", 2), (1, "Alpha", "A", 1)])

        prompt = get_few_shot_prompt(con, "measurement", "LOINC", 3)

    assert prompt.index("'Alpha'") < prompt.index("'Zulu'")


def test_stale_chroma_index_is_rebuilt(monkeypatch, tmp_path):
    class FakeCollection:
        def __init__(self, metadata, count):
            self.metadata = metadata
            self._count = count
            self.added = []

        def count(self):
            return self._count

        def add(self, ids, documents):
            self.added.extend(zip(ids, documents))
            self._count += len(ids)

        def modify(self, metadata):
            self.metadata = metadata

    class FakeClient:
        def __init__(self):
            self.stale = FakeCollection({"index_signature": "old"}, 1)
            self.created = None
            self.deleted = []

        def get_collection(self, name):
            return self.stale

        def delete_collection(self, name):
            self.deleted.append(name)

        def create_collection(self, name, metadata):
            self.created = FakeCollection(metadata, 0)
            return self.created

    fake_client = FakeClient()
    monkeypatch.setattr(
        "src.mapping.mapping_service.chromadb.PersistentClient",
        lambda path: fake_client,
    )
    with duckdb.connect(":memory:") as con:
        con.execute("""
            CREATE TABLE concept (
                concept_id VARCHAR, concept_name VARCHAR, vocabulary_id VARCHAR,
                domain_id VARCHAR, standard_concept VARCHAR,
                invalid_reason VARCHAR, valid_start_date VARCHAR,
                valid_end_date VARCHAR
            )
        """)
        con.execute("""
            INSERT INTO concept VALUES (
                '300', 'Test measurement', 'LOINC', 'Measurement', 'S',
                NULL, '19700101', '20991231'
            )
        """)

        collection = get_versioned_collection(
            con, str(tmp_path), "measurement"
        )

    assert fake_client.deleted == ["loinc_measurements"]
    assert collection.metadata["index_schema_version"] == "omop-rag-index-v1"
    assert collection.metadata["build_complete"] is True
    assert collection.added == [("300", "Test measurement")]


def test_proposal_is_event_level_and_threshold_controls_stcm():
    with duckdb.connect(":memory:") as con:
        _create_stcm(con)
        _create_provenance(con)
        con.execute("CREATE TABLE vocabulary (vocabulary_id VARCHAR, vocabulary_version VARCHAR)")
        con.execute("INSERT INTO vocabulary VALUES ('None', 'v-test')")
        con.execute("""
            CREATE TABLE measurement (
                measurement_id BIGINT, measurement_concept_id INTEGER,
                measurement_source_value VARCHAR
            )
        """)
        con.execute("INSERT INTO measurement VALUES (2, 0, 'Legacy'), (1, 0, 'Legacy'), (3, 0, 'Weak')")
        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, assigned_concept_id,
                mapping_method, reviewed_by
            ) VALUES (
                'measurement', 0, 'Legacy', 300,
                'llm_rag_few_shot', 'Pending_Human_Review'
            )
        """)

        status, count = record_mapping_proposal(
            con, "measurement", "Legacy", (300, "Candidate", 0.05, 0.95)
        )
        weak_status, _ = record_mapping_proposal(
            con, "measurement", "Weak", (301, "Weak candidate", 0.50, 0.50)
        )

        assert (status, count) == ("Pending_Human_Review", 2)
        assert weak_status == "Below_Confidence_Threshold"
        assert con.execute("SELECT COUNT(*) FROM source_to_concept_map").fetchone()[0] == 0
        assert con.execute("""
            SELECT target_id, reviewed_by FROM mapping_provenance
            ORDER BY target_id
        """).fetchall() == [
            (0, "Superseded_Legacy_Placeholder"),
            (1, "Pending_Human_Review"),
            (2, "Pending_Human_Review"),
            (3, "Below_Confidence_Threshold"),
        ]
        assert con.execute("""
            SELECT status FROM mapping_decision
            WHERE target_table = 'measurement' AND source_value = 'Legacy'
        """).fetchone()[0] == "PENDING"


def test_deterministic_mapping_supersedes_pending_event_proposal():
    with duckdb.connect(":memory:") as con:
        _create_provenance(con)
        con.execute("""
            CREATE TABLE measurement (
                measurement_id BIGINT, measurement_concept_id INTEGER,
                measurement_source_value VARCHAR
            )
        """)
        con.execute("INSERT INTO measurement VALUES (1, 300, 'Legacy')")
        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value,
                assigned_concept_id, mapping_method, reviewed_by
            ) VALUES (
                'measurement', 1, 'Legacy', 301,
                'llm_rag_few_shot', 'Pending_Human_Review'
            )
        """)

        retired = reconcile_resolved_proposals(con, "measurement")

        assert retired == 1
        assert con.execute("""
            SELECT reviewed_by FROM mapping_provenance
        """).fetchone()[0] == "Superseded_By_Deterministic_Mapping"


def test_stcm_application_requires_human_approval(tmp_path):
    db_path = tmp_path / "gate.duckdb"
    with duckdb.connect(str(db_path)) as con:
        _create_stcm(con)
        _create_provenance(con)
        con.execute("""
            CREATE TABLE concept (
                concept_id INTEGER, domain_id VARCHAR,
                standard_concept VARCHAR, invalid_reason VARCHAR,
                valid_start_date VARCHAR, valid_end_date VARCHAR
            )
        """)
        con.execute("""
            INSERT INTO concept VALUES (
                300, 'Measurement', 'S', NULL, '19700101', '20991231'
            )
        """)
        con.execute("""
            INSERT INTO source_to_concept_map VALUES (
                'Legacy', 0, 'CMF_SYNTHEA_MEASUREMENT', 'Legacy',
                300, 'LOINC', '2020-01-01', '2099-12-31', NULL
            )
        """)
        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by
            ) VALUES (
                'measurement', 1, 'Legacy', 'Candidate', 300,
                'llm_rag_few_shot', 0.95, 'test', 'v-test',
                'Pending_Human_Review'
            )
        """)
        for table, id_col, concept_col, source_col in [
            ("condition_occurrence", "condition_occurrence_id", "condition_concept_id", "condition_source_value"),
            ("drug_exposure", "drug_exposure_id", "drug_concept_id", "drug_source_value"),
            ("measurement", "measurement_id", "measurement_concept_id", "measurement_source_value"),
            ("observation", "observation_id", "observation_concept_id", "observation_source_value"),
            ("procedure_occurrence", "procedure_occurrence_id", "procedure_concept_id", "procedure_source_value"),
        ]:
            con.execute(
                f"CREATE TABLE {table} ({id_col} BIGINT, {concept_col} INTEGER, {source_col} VARCHAR)"
            )
        con.execute("INSERT INTO measurement VALUES (1, 0, 'Legacy')")

    apply_stcm_mappings(db_path)
    with duckdb.connect(str(db_path)) as con:
        assert con.execute("SELECT measurement_concept_id FROM measurement").fetchone()[0] == 0
        con.execute("""
            UPDATE mapping_provenance SET reviewed_by = 'Approved_by_Human'
        """)

    apply_stcm_mappings(db_path)
    with duckdb.connect(str(db_path), read_only=True) as con:
        assert con.execute("SELECT measurement_concept_id FROM measurement").fetchone()[0] == 300
