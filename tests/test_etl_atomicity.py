import duckdb
import pytest

from src.adapters.fhir_coding import RXNORM_URI, SourceCoding
from src.adapters.fhir_records import CodedFHIRPeriodRecord
from src.etl import condition, drug, measurement, observation, person, procedure, visit


@pytest.mark.parametrize(
    ("module", "runner", "target_table"),
    [
        (person, person.run_person_etl, "person"),
        (visit, visit.run_visit_etl, "visit_occurrence"),
        (condition, condition.run_condition_etl, "condition_occurrence"),
        (drug, drug.run_drug_etl, "drug_exposure"),
        (measurement, measurement.run_measurement_etl, "measurement"),
        (observation, observation.run_observation_etl, "observation"),
        (procedure, procedure.run_procedure_etl, "procedure_occurrence"),
    ],
    ids=[
        "person", "visit", "condition", "drug", "measurement",
        "observation", "procedure",
    ],
)
def test_base_etl_rolls_back_target_drop_on_schema_failure(
    monkeypatch, tmp_path, module, runner, target_table
):
    database = tmp_path / f"{target_table}.duckdb"
    with duckdb.connect(str(database)) as con:
        con.execute(f'CREATE TABLE "{target_table}" (marker VARCHAR)')
        con.execute(
            f'INSERT INTO "{target_table}" VALUES (?)',
            ["published-before-run"],
        )

    monkeypatch.setattr(module, "DB_PATH", str(database))
    monkeypatch.setattr(module, "FHIR_DIR", str(tmp_path))
    monkeypatch.setattr(module.glob, "glob", lambda _: [])

    def fail_schema_creation(*args, **kwargs):
        raise RuntimeError("fault injected after target-table drop")

    monkeypatch.setattr(module, "create_table_sql", fail_schema_creation)

    with pytest.raises(RuntimeError, match="fault injected"):
        runner()

    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute(
            f'SELECT marker FROM "{target_table}"'
        ).fetchall() == [("published-before-run",)]


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

    record = CodedFHIRPeriodRecord(
        event_id=1001,
        person_id=2001,
        coding=SourceCoding(RXNORM_URI, "314076", "Lisinopril 10 MG"),
        start_date="2026-01-01",
        start_datetime="2026-01-01T10:00:00",
        end_date="2026-01-01",
        end_datetime="2026-01-01T10:00:00",
        source_event_key="MedicationRequest/1001",
    )
    monkeypatch.setattr(drug, "DB_PATH", str(database))
    monkeypatch.setattr(drug.glob, "glob", lambda _: ["bundle.json"])
    monkeypatch.setattr(drug, "extract_drugs", lambda _: [record])
    monkeypatch.setattr(
        drug, "extract_fhir_publication_exclusions", lambda *args: []
    )

    def fail_after_rebuild(*args, **kwargs):
        raise RuntimeError("fault injected after target-table rebuild")

    monkeypatch.setattr(drug, "replace_fhir_source_codings", fail_after_rebuild)

    with pytest.raises(RuntimeError, match="fault injected"):
        drug.run_drug_etl()

    with duckdb.connect(str(database), read_only=True) as con:
        assert con.execute("SELECT marker FROM drug_exposure").fetchall() == [
            ("published-before-run",)
        ]
