import os
import sys
import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

def setup_provenance_table():
    print("⚙️ STARTING AUDIT SETUP (MAPPING PROVENANCE)")
    print("-" * 50)
    
    with duckdb.connect(DB_PATH) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS mapping_provenance (
                target_table        VARCHAR,
                target_id           BIGINT,
                source_value        VARCHAR,
                normalized_value    VARCHAR,
                assigned_concept_id INTEGER,
                mapping_method      VARCHAR,   -- 'deterministic', 'llm_jaro_winkler', 'llm_rag'
                score               DOUBLE,    -- 1.0 para determinístico, score do Jaro/Vector para a IA
                model_name          VARCHAR,
                vocabulary_version  VARCHAR,
                reviewed_by         VARCHAR,
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Apagamos o histórico se estivermos a fazer um reset total no pipeline
        con.execute("DELETE FROM mapping_provenance")
        
    print("✅ 'mapping_provenance' table verified/created successfully!")

if __name__ == "__main__":
    setup_provenance_table()