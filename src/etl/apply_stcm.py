import os
import sys
import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

# Mapeamento da Tabela Clínica -> (Coluna do ID, Coluna do Source Value, Domínio OMOP Esperado)
TARGET_MAPPINGS = {
    'condition_occurrence': ('condition_concept_id', 'condition_source_value', 'Condition'),
    'drug_exposure': ('drug_concept_id', 'drug_source_value', 'Drug'),
    'measurement': ('measurement_concept_id', 'measurement_source_value', 'Measurement'),
    'observation': ('observation_concept_id', 'observation_source_value', 'Observation'),
    'procedure_occurrence': ('procedure_concept_id', 'procedure_source_value', 'Procedure')
}

def apply_stcm_mappings():
    print("⚙️ STARTING STCM APPLICATION (Applying AI mappings to clinical tables)")
    print("-" * 50)
    
    with duckdb.connect(DB_PATH) as con:
        for table, (concept_col, source_val_col, expected_domain) in TARGET_MAPPINGS.items():
            
            # A Magia da Governança: 
            # Cruzamos a STCM com a tabela 'concept' para garantir que o 'domain_id' 
            # do mapeamento da IA corresponde estritamente à tabela onde o vamos inserir.
            query = f"""
                UPDATE {table}
                SET {concept_col} = stcm.target_concept_id
                FROM source_to_concept_map stcm
                JOIN concept c ON stcm.target_concept_id = c.concept_id
                WHERE {table}.{source_val_col} = stcm.source_code
                  AND {table}.{concept_col} = 0
                  AND c.domain_id = '{expected_domain}'
            """
            
            try:
                con.execute(query)
            except Exception as e:
                print(f"❌ Error applying STCM to {table}: {e}")
                continue
            
            # Para reportar o impacto visualmente no terminal
            mapped_count = con.execute(f"""
                SELECT COUNT(*) FROM {table} 
                WHERE {concept_col} != 0 
                  AND {source_val_col} IN (SELECT source_code FROM source_to_concept_map)
            """).fetchone()[0]
            
            print(f"✅ Applied {mapped_count} STCM dictionary mappings to '{table}' (Domain locked: {expected_domain})")

if __name__ == "__main__":
    apply_stcm_mappings()