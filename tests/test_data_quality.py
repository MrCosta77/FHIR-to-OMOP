import duckdb
import pytest
import os
from pathlib import Path

# Setup paths (pointing to the root data folder)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.path.join(PROJECT_ROOT, "data", "omop_clinical.duckdb")

@pytest.fixture(scope="module")
def db_connection():
    """Establishes a connection to DuckDB for all tests to use."""
    con = duckdb.connect(DB_PATH)
    yield con
    con.close()

def test_person_ids_are_unique(db_connection):
    """Data Quality: Ensure no duplicate patients exist in the PERSON table."""
    result = db_connection.execute("""
        SELECT person_id, COUNT(*) 
        FROM person 
        GROUP BY person_id 
        HAVING COUNT(*) > 1
    """).fetchall()
    
    assert len(result) == 0, f"Found {len(result)} duplicate person_ids!"

def test_condition_person_fk_integrity(db_connection):
    """Data Quality: Ensure every condition belongs to a valid person."""
    result = db_connection.execute("""
        SELECT COUNT(*) 
        FROM condition_occurrence c
        LEFT JOIN person p ON c.person_id = p.person_id
        WHERE p.person_id IS NULL
    """).fetchone()[0]
    
    assert result == 0, f"Found {result} orphan conditions without a valid person_id!"

def test_drug_person_fk_integrity(db_connection):
    """Data Quality: Ensure every drug exposure belongs to a valid person."""
    result = db_connection.execute("""
        SELECT COUNT(*) 
        FROM drug_exposure d
        LEFT JOIN person p ON d.person_id = p.person_id
        WHERE p.person_id IS NULL
    """).fetchone()[0]
    
    assert result == 0, f"Found {result} orphan drug records without a valid person_id!"

def test_no_future_dates_in_conditions(db_connection):
    """Data Quality: Ensure no clinical events occur in the future."""
    result = db_connection.execute("""
        SELECT COUNT(*) 
        FROM condition_occurrence
        WHERE condition_start_date > CURRENT_DATE
    """).fetchone()[0]
    
    assert result == 0, f"Found {result} condition records with future dates!"

def test_person_source_value_is_unique(db_connection):
    """
    Data Quality: Ensure no duplicate patients exist in the PERSON table based on source value.
    This test would have caught the 'ghost patients' bug from the previous version.
    """
    # Look for source_values that appear more than once
    duplicates = db_connection.execute("""
        SELECT person_source_value, COUNT(*) 
        FROM person 
        GROUP BY person_source_value 
        HAVING COUNT(*) > 1
    """).fetchall()
    
    # The test fails if the duplicate list is not empty
    assert len(duplicates) == 0, f"❌ Integrity Failure: Found duplicate patients: {duplicates}"

def test_measurement_person_fk(db_connection):
    """
    Data Quality: Ensure referential integrity (Foreign Key) for the MEASUREMENT table.
    All lab tests must belong to a valid person_id.
    """
    # Look for measurements whose person_id does not exist in the person table
    orphans = db_connection.execute("""
        SELECT COUNT(*) 
        FROM measurement m
        LEFT JOIN person p ON m.person_id = p.person_id
        WHERE p.person_id IS NULL
    """).fetchone()[0]
    
    # The test fails if the orphan count is greater than zero
    assert orphans == 0, f"❌ Integrity Failure: Found {orphans} orphan measurements without a valid associated patient."


def test_measurement_units_have_explicit_mapping_outcome(db_connection):
    """A provided unit must resolve to a Standard Unit or explicit concept 0."""
    missing_outcome = db_connection.execute("""
        SELECT COUNT(*)
        FROM measurement
        WHERE unit_source_value IS NOT NULL
          AND unit_concept_id IS NULL
    """).fetchone()[0]
    assert missing_outcome == 0


def test_nonzero_measurement_units_are_valid_standard_units(db_connection):
    invalid = db_connection.execute("""
        SELECT COUNT(*)
        FROM measurement m
        LEFT JOIN concept c ON m.unit_concept_id = c.concept_id
        WHERE m.unit_concept_id <> 0
          AND (
              c.concept_id IS NULL
              OR c.domain_id <> 'Unit'
              OR c.standard_concept <> 'S'
              OR c.invalid_reason IS NOT NULL
          )
    """).fetchone()[0]
    assert invalid == 0

def test_visit_person_fk(db_connection):
    """
    Data Quality: Ensure referential integrity (Foreign Key) for the VISIT_OCCURRENCE table.
    All visits must belong to a valid person_id.
    """
    orphans = db_connection.execute("""
        SELECT COUNT(*) 
        FROM visit_occurrence v
        LEFT JOIN person p ON v.person_id = p.person_id
        WHERE p.person_id IS NULL
    """).fetchone()[0]
    
    assert orphans == 0, f"❌ Integrity Failure: Found {orphans} orphan visits without a valid associated patient."

def test_observation_person_fk(db_connection):
    """Verifica se todas as observações pertencem a um doente válido na tabela Person."""
    result = db_connection.execute("""
        SELECT COUNT(*) FROM observation o
        LEFT JOIN person p ON o.person_id = p.person_id
        WHERE p.person_id IS NULL
    """).fetchone()[0]
    assert result == 0, f"Found {result} observations linked to non-existent persons."

def test_procedure_person_fk(db_connection):
    """Verifica se todos os procedimentos pertencem a um doente válido na tabela Person."""
    result = db_connection.execute("""
        SELECT COUNT(*) FROM procedure_occurrence po
        LEFT JOIN person p ON po.person_id = p.person_id
        WHERE p.person_id IS NULL
    """).fetchone()[0]
    assert result == 0, f"Found {result} procedures linked to non-existent persons."
