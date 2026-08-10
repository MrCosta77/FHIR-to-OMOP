import os
import sys
import duckdb
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

def run_observation_period_etl():
    print("⚙️ STARTING ETL PIPELINE (OMOP DERIVED -> OBSERVATION_PERIOD) [PRODUCTION]")
    print("-" * 50)
    
    print("🔌 Connecting to DuckDB to calculate longitudinal observation periods...")
    
    with duckdb.connect(DB_PATH) as con:
        con.execute("DROP TABLE IF EXISTS observation_period")
        
        # Tabela com os campos estritos exigidos pelo OMOP CDM v5.4
        con.execute("""
            CREATE TABLE observation_period (
                observation_period_id BIGINT PRIMARY KEY,
                person_id BIGINT,
                observation_period_start_date DATE,
                observation_period_end_date DATE,
                period_type_concept_id INTEGER
            )
        """)
        
        # A Magia Analítica: Encontrar o primeiro e último evento de cada doente 
        # juntando todas as tabelas clínicas que já construímos!
        con.execute("""
            INSERT INTO observation_period
            SELECT 
                -- Como há apenas 1 período por doente, o ID do doente é a chave perfeita e segura!
                person_id AS observation_period_id,
                person_id,
                MIN(start_date) AS observation_period_start_date,
                MAX(end_date) AS observation_period_end_date,
                32817 AS period_type_concept_id -- 32817 = "EHR"
            FROM (
                SELECT person_id, visit_start_date AS start_date, visit_end_date AS end_date FROM visit_occurrence
                UNION ALL
                SELECT person_id, condition_start_date, condition_end_date FROM condition_occurrence
                UNION ALL
                SELECT person_id, drug_exposure_start_date, drug_exposure_end_date FROM drug_exposure
                UNION ALL
                SELECT person_id, measurement_date, measurement_date FROM measurement
                UNION ALL
                SELECT person_id, observation_date, observation_date FROM observation
            ) combined_events
            WHERE start_date IS NOT NULL
            GROUP BY person_id
        """)
        
        mapped_count = con.execute("SELECT COUNT(*) FROM observation_period").fetchone()[0]
        
    print("\n✅ ETL Complete!")
    print(f" - Successfully derived Observation Periods for {mapped_count} patients.")

if __name__ == "__main__":
    run_observation_period_etl()