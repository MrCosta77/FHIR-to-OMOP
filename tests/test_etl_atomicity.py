import duckdb
import pytest

from src.etl import drug


def test_drug_etl_rolls_back_rebuild_after_mid_transaction_failure(
    monkeypatch, tmp_path
):
    database = tmp_path / "atomicity.duckdb"
    with duckdb.connect(str(database)) as con:
        con.execute("CREATE TABLE drug_exposure(marker VARCHAR)")
        con.execute("INSERT INTO drug_exposure VALUES ('published-before-run')")
        con.execute("""
            CREATE TABLE concept (
                concept_id BIGINT, concept_code VARCHAR, vocabulary_id VARCHAR,
                invalid_reason VARCHAR, domain_id VARCHAR, standard_concept VARCHAR
            )
        """)
        con.execute("""
            CREATE TABLE concept_relationship (
                concept_id_1 BIGINT, concept_id_2 BIGINT,
                relationship_id VARCHAR, invalid_reason VARCHAR
            )
        """)
        con.execute("""
            INSERT INTO concept VALUES
                (1, '314076', 'RxNorm', NULL, 'Drug', NULL),
                (2, '314076', 'RxNorm', NULL, 'Drug', 'S')
        """)
        con.execute("INSERT INTO concept_relationship VALUES (1, 2, 'Maps to', NULL)")

    record = (
        1001, 2001, "314076", "Lisinopril 10 MG",
        "2026-01-01", "2026-01-01T10:00:00",
        "2026-01-01", "2026-01-01T10:00:00",
        "http://www.nlm.nih.gov/research/umls/rxnorm",
        "RxNorm", "RxNorm", None, "MedicationRequest/1001",
    )
    monkeypatch.setattr(drug, "DB_PATH", str(database))
    monkeypatch.setattr(drug.glob, "glob", lambda _: ["bundle.json"])
    monkeypatch.setattr(drug, "extract_drugs", lambda _: [record])

    def fail_after_rebuild(*args, **kwargs):
        raise RuntimeError("fault injected after target-table rebuild")

    monkeypatch.setattr(drug, "replace_fhir_source_codings", fail_after_rebuild)

    with pytest.raises(RuntimeError, match="fault injected"):
        drug.run_drug_etl()

    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("SELECT marker FROM drug_exposure").fetchall() == [
            ("published-before-run",)
        ]
