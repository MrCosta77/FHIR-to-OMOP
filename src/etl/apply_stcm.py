import os
import sys
import duckdb
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

def apply_stcm_mappings():
    print("⚙️ STARTING STCM APPLICATION (APPLYING AI MAPPINGS TO CLINICAL TABLES)")
    print("-" * 50)
    
    # Dicionário com a tabela e as respetivas colunas (ID do conceito, Valor original)
    tables_to_update = {
        "condition_occurrence": ("condition_concept_id", "condition_source_value"),
        "drug_exposure": ("drug_concept_id", "drug_source_value"),
        "measurement": ("measurement_concept_id", "measurement_source_value"),
        "observation": ("observation_concept_id", "observation_source_value"),
        "procedure_occurrence": ("procedure_concept_id", "procedure_source_value")
    }

    with duckdb.connect(DB_PATH) as con:
        for table, (concept_col, source_val_col) in tables_to_update.items():
            print(f"⏳ Applying STCM mappings to {table.upper()}...")
            
            # Atualizar os registos órfãos (concept_id = 0) com as decisões da IA gravadas na STCM
            con.execute(f"""
                UPDATE {table}
                SET {concept_col} = stcm.target_concept_id
                FROM source_to_concept_map stcm
                WHERE {table}.{source_val_col} = stcm.source_code
                  AND {table}.{concept_col} = 0
            """)
            
        print("✅ All STCM mappings successfully applied to clinical tables!")

if __name__ == "__main__":
    apply_stcm_mappings()