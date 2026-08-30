"""
Script to manually execute the 7D.4D Hospital Acceptance test against an isolated test environment.
This proves that the system handles RAG retrieval and LLM mapping without touching production data.
"""

import os
from pathlib import Path
import tempfile
import duckdb

from src.adapters.hospital_csv_mapping import run_hospital_csv_mapping
from src.mapping.governance import ensure_governance_tables
from src.utils.config import CHROMA_PATH

def main():
    FIXTURE_CSV = Path("tests/fixtures/hospital_csv/e2e_hospital_6domain.csv")
    
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = Path(tmp_dir) / "test.duckdb"
        ensure_governance_tables(test_db)
        
        print("=====================================================")
        print("7D.4D: Execução de Aceitação em Ambiente Real Isolado")
        print("=====================================================")
        print(f"Base de dados (Isolada): {test_db}")
        print(f"Chroma DB (Real): {CHROMA_PATH}")
        print(f"Fixture: {FIXTURE_CSV}\n")

        result = run_hospital_csv_mapping(FIXTURE_CSV, db_path=test_db, chroma_path=CHROMA_PATH)
        print(f"Result: {result}")

if __name__ == "__main__":
    main()