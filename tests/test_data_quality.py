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