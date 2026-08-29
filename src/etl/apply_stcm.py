import os
import sys
import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

# Mapeamento da Tabela Clínica -> (Coluna do ID, Coluna do Source Value, Domínio OMOP Esperado)
TARGET_MAPPINGS = {
    'condition_occurrence': ('condition_occurrence_id', 'condition_concept_id', 'condition_source_value', 'Condition', 'CMF_SYNTHEA_CONDITION'),
    'drug_exposure': ('drug_exposure_id', 'drug_concept_id', 'drug_source_value', 'Drug', 'CMF_SYNTHEA_DRUG'),
    'measurement': ('measurement_id', 'measurement_concept_id', 'measurement_source_value', 'Measurement', 'CMF_SYNTHEA_MEASUREMENT'),
    'observation': ('observation_id', 'observation_concept_id', 'observation_source_value', 'Observation', 'CMF_SYNTHEA_OBSERVATION'),
    'procedure_occurrence': ('procedure_occurrence_id', 'procedure_concept_id', 'procedure_source_value', 'Procedure', 'CMF_SYNTHEA_PROCEDURE'),
    'device_exposure': ('device_exposure_id', 'device_concept_id', 'device_source_value', 'Device', 'CMF_SYNTHEA_DEVICE')
}

def apply_stcm_mappings(db_path=DB_PATH):
    print("⚙️ STARTING STCM APPLICATION (Approved mappings only)")
    print("-" * 50)
    
    with duckdb.connect(db_path) as con:
        has_event_binding = bool(con.execute("""
            SELECT COUNT(*) FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = 'source_event_binding'
        """).fetchone()[0])
        for table, (
            id_col,
            concept_col,
            source_val_col,
            expected_domain,
            source_vocabulary,
        ) in TARGET_MAPPINGS.items():
            
            # A Magia da Governança: 
            # Cruzamos a STCM com a tabela 'concept' para garantir que o 'domain_id' 
            # do mapeamento da IA corresponde estritamente à tabela onde o vamos inserir.
            before = con.execute(f"""
                SELECT COUNT(*) FROM {table} WHERE {concept_col} <> 0
            """).fetchone()[0]
            event_binding_union = """
                    UNION
                    SELECT DISTINCT stcm.source_code, stcm.target_concept_id,
                                    binding.target_id
                    FROM source_to_concept_map stcm
                    JOIN source_event_binding binding
                      ON binding.target_table = ?
                     AND binding.source_vocabulary_id = stcm.source_vocabulary_id
                     AND binding.source_code = stcm.source_code
                     AND binding.active
                    JOIN mapping_provenance p
                      ON p.mapping_decision_id = binding.mapping_decision_id
                     AND p.target_table = binding.target_table
                     AND p.target_id = binding.target_id
                     AND p.source_vocabulary_id = stcm.source_vocabulary_id
                     AND p.source_code = stcm.source_code
                     AND p.assigned_concept_id = stcm.target_concept_id
                     AND p.reviewed_by = 'Approved_by_Human'
                    WHERE stcm.invalid_reason IS NULL
                      AND CURRENT_DATE BETWEEN stcm.valid_start_date
                                           AND stcm.valid_end_date
            """ if has_event_binding else ""
            query = f"""
                UPDATE {table}
                SET {concept_col} = approved.target_concept_id
                FROM (
                    SELECT DISTINCT stcm.source_code, stcm.target_concept_id,
                                    NULL::BIGINT AS target_id
                    FROM source_to_concept_map stcm
                    JOIN mapping_provenance p
                      ON p.target_table = ?
                     AND p.source_value = stcm.source_code
                     AND p.assigned_concept_id = stcm.target_concept_id
                     AND p.reviewed_by = 'Approved_by_Human'
                    WHERE stcm.source_vocabulary_id IN (?, 'CMF_SYNTHEA')
                      AND stcm.invalid_reason IS NULL
                      AND CURRENT_DATE BETWEEN stcm.valid_start_date
                                           AND stcm.valid_end_date
                    {event_binding_union}
                ) approved
                JOIN concept c ON approved.target_concept_id = c.concept_id
                WHERE (
                      (approved.target_id IS NULL
                       AND {table}.{source_val_col} = approved.source_code)
                   OR {table}.{id_col} = approved.target_id
                )
                  AND {table}.{concept_col} = 0
                  AND c.domain_id = '{expected_domain}'
                  AND c.standard_concept = 'S'
                  AND (c.invalid_reason IS NULL OR c.invalid_reason = '')
                  AND CURRENT_DATE BETWEEN
                      COALESCE(
                          TRY_CAST(c.valid_start_date AS DATE),
                          TRY_STRPTIME(CAST(c.valid_start_date AS VARCHAR), '%Y%m%d')::DATE
                      )
                      AND COALESCE(
                          TRY_CAST(c.valid_end_date AS DATE),
                          TRY_STRPTIME(CAST(c.valid_end_date AS VARCHAR), '%Y%m%d')::DATE
                      )
            """
            
            parameters = [table, source_vocabulary]
            if has_event_binding:
                parameters.append(table)
            con.execute(query, parameters)
            
            # Para reportar o impacto visualmente no terminal
            after = con.execute(f"""
                SELECT COUNT(*) FROM {table} WHERE {concept_col} <> 0
            """).fetchone()[0]
            mapped_count = after - before
            
            print(f"✅ Applied {mapped_count} approved STCM mappings to '{table}' (Domain locked: {expected_domain})")

if __name__ == "__main__":
    apply_stcm_mappings()
