"""Executable golden-path contract from one FHIR bundle to OMOP CDM 5.4."""

import os
from pathlib import Path

import duckdb

from src.etl import (
    condition,
    drug,
    eras,
    link_visits,
    measurement,
    observation,
    observation_period,
    person,
    procedure,
    visit,
)
from src.utils import setup_audit, setup_cdm_schema


GOLDEN = Path(__file__).parent / "fixtures" / "golden_fhir_bundle.json"


def _point_pipeline_at(monkeypatch, database, fhir_directory):
    database_modules = (
        setup_cdm_schema,
        setup_audit,
        person,
        visit,
        condition,
        drug,
        measurement,
        observation,
        procedure,
        observation_period,
        link_visits,
        eras,
    )
    for module in database_modules:
        monkeypatch.setattr(module, "DB_PATH", str(database))
    for module in (
        person, visit, condition, drug, measurement, observation, procedure,
        link_visits,
    ):
        monkeypatch.setattr(module, "FHIR_DIR", str(fhir_directory))


def _install_fixture_vocabulary(con):
    concepts = (
        # concept_id, name, domain, vocabulary, class, standard, code
        (1001, "Hypertensive disorder", "Condition", "SNOMED", "Clinical Finding", "S", "38341003"),
        (1002, "Lisinopril", "Drug", "RxNorm", "Ingredient", "S", "314076"),
        (1003, "Heart rate", "Measurement", "LOINC", "Clinical Observation", "S", "8867-4"),
        (1004, "Colonoscopy", "Procedure", "SNOMED", "Procedure", "S", "73761001"),
        (1005, "per minute", "Unit", "UCUM", "Unit", "S", "/min"),
        (1006, "Tobacco smoking status", "Observation", "LOINC", "Clinical Observation", "S", "72166-2"),
    )
    con.executemany(
        """
        INSERT INTO concept (
            concept_id, concept_name, domain_id, vocabulary_id,
            concept_class_id, standard_concept, concept_code,
            valid_start_date, valid_end_date, invalid_reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?, DATE '2000-01-01', DATE '2099-12-31', NULL)
        """,
        concepts,
    )
    con.executemany(
        """
        INSERT INTO concept_relationship (
            concept_id_1, concept_id_2, relationship_id,
            valid_start_date, valid_end_date, invalid_reason
        ) VALUES (?, ?, 'Maps to', DATE '2000-01-01', DATE '2099-12-31', NULL)
        """,
        [(concept_id, concept_id) for concept_id in (1001, 1002, 1003, 1004, 1006)],
    )
    con.execute(
        """
        INSERT INTO concept_ancestor (
            ancestor_concept_id, descendant_concept_id,
            min_levels_of_separation, max_levels_of_separation
        ) VALUES (1002, 1002, 0, 0)
        """
    )


def test_golden_bundle_loads_through_derived_omop_tables(tmp_path, monkeypatch):
    database = tmp_path / "golden.duckdb"
    fhir_directory = tmp_path / "fhir"
    fhir_directory.mkdir()
    (fhir_directory / "golden.json").write_bytes(GOLDEN.read_bytes())
    _point_pipeline_at(monkeypatch, database, fhir_directory)

    setup_cdm_schema.create_omop_skeleton()
    setup_audit.setup_audit_tables()
    with duckdb.connect(str(database)) as con:
        _install_fixture_vocabulary(con)

    person.run_person_etl()
    visit.run_visit_etl()
    condition.run_condition_etl()
    drug.run_drug_etl()
    measurement.run_measurement_etl()
    observation.run_observation_etl()
    procedure.run_procedure_etl()
    link_visits.link_events_to_visits()
    observation_period.run_observation_period_etl()
    eras.run_era_etl()

    with duckdb.connect(str(database), read_only=True) as con:
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "person",
                "visit_occurrence",
                "condition_occurrence",
                "drug_exposure",
                "measurement",
                "observation",
                "procedure_occurrence",
                "observation_period",
                "condition_era",
                "drug_era",
            )
        }
        assert counts == {
            "person": 1,
            "visit_occurrence": 1,
            "condition_occurrence": 1,
            "drug_exposure": 1,
            "measurement": 1,
            "observation": 1,
            "procedure_occurrence": 1,
            "observation_period": 1,
            "condition_era": 1,
            "drug_era": 1,
        }

        assert con.execute(
            "SELECT condition_concept_id FROM condition_occurrence"
        ).fetchone()[0] == 1001
        assert con.execute(
            "SELECT drug_concept_id FROM drug_exposure"
        ).fetchone()[0] == 1002
        assert con.execute(
            "SELECT measurement_concept_id, unit_concept_id FROM measurement"
        ).fetchone() == (1003, 1005)
        assert con.execute(
            "SELECT procedure_concept_id FROM procedure_occurrence"
        ).fetchone()[0] == 1004
        assert con.execute(
            "SELECT observation_concept_id FROM observation"
        ).fetchone()[0] == 1006

        unlinked = con.execute(
            """
            SELECT SUM(missing) FROM (
                SELECT COUNT(*) FILTER (WHERE visit_occurrence_id IS NULL) missing
                FROM condition_occurrence
                UNION ALL
                SELECT COUNT(*) FILTER (WHERE visit_occurrence_id IS NULL)
                FROM drug_exposure
                UNION ALL
                SELECT COUNT(*) FILTER (WHERE visit_occurrence_id IS NULL)
                FROM measurement
                UNION ALL
                SELECT COUNT(*) FILTER (WHERE visit_occurrence_id IS NULL)
                FROM procedure_occurrence
            )
            """
        ).fetchone()[0]
        assert unlinked == 0
        linkage = con.execute("""
            SELECT link_method, link_status, COUNT(*)
            FROM event_visit_linkage GROUP BY 1, 2
        """).fetchall()
        assert linkage == [("FHIR_REFERENCE", "LINKED", 5)]
        assert con.execute("""
            SELECT derivation_method FROM observation_period_provenance
        """).fetchone()[0] == "FHIR_ENCOUNTER_COVERAGE"
        provenance = con.execute(
            "SELECT run_id FROM mapping_provenance ORDER BY provenance_id"
        ).fetchall()
        assert len(provenance) == 5
        assert {row[0] for row in provenance} == {
            os.environ.get("CMF_RUN_ID") or None
        }
