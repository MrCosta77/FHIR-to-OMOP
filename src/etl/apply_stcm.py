import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

TABLE_SCHEMAS = {
    'condition_occurrence': {'domain': 'Condition', 'id': 'condition_occurrence_id', 'concept': 'condition_concept_id', 'date': 'condition_start_date', 'dt': 'condition_start_datetime', 'type': 'condition_type_concept_id', 'source': 'condition_source_value', 'source_concept': 'condition_source_concept_id'},
    'drug_exposure': {'domain': 'Drug', 'id': 'drug_exposure_id', 'concept': 'drug_concept_id', 'date': 'drug_exposure_start_date', 'dt': 'drug_exposure_start_datetime', 'type': 'drug_type_concept_id', 'source': 'drug_source_value', 'source_concept': 'drug_source_concept_id'},
    'measurement': {'domain': 'Measurement', 'id': 'measurement_id', 'concept': 'measurement_concept_id', 'date': 'measurement_date', 'dt': 'measurement_datetime', 'type': 'measurement_type_concept_id', 'source': 'measurement_source_value', 'source_concept': 'measurement_source_concept_id'},
    'observation': {'domain': 'Observation', 'id': 'observation_id', 'concept': 'observation_concept_id', 'date': 'observation_date', 'dt': 'observation_datetime', 'type': 'observation_type_concept_id', 'source': 'observation_source_value', 'source_concept': 'observation_source_concept_id'},
    'procedure_occurrence': {'domain': 'Procedure', 'id': 'procedure_occurrence_id', 'concept': 'procedure_concept_id', 'date': 'procedure_date', 'dt': 'procedure_datetime', 'type': 'procedure_type_concept_id', 'source': 'procedure_source_value', 'source_concept': 'procedure_source_concept_id'},
    'device_exposure': {'domain': 'Device', 'id': 'device_exposure_id', 'concept': 'device_concept_id', 'date': 'device_exposure_start_date', 'dt': 'device_exposure_start_datetime', 'type': 'device_type_concept_id', 'source': 'device_source_value', 'source_concept': 'device_source_concept_id'},
}
def apply_stcm_mappings(db_path=DB_PATH):
    print("⚕️ STARTING DOMAIN-SAFE STCM APPLICATION")
    print("-" * 50)

    with duckdb.connect(db_path) as con:
        con.execute('BEGIN TRANSACTION')
        try:
            has_event_binding = bool(con.execute("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = 'main' AND table_name = 'source_event_binding'
            """).fetchone()[0])
            has_fhir_coding = bool(con.execute("""
                SELECT COUNT(*) FROM information_schema.tables
                WHERE table_schema = 'main'
                  AND table_name = 'fhir_event_source_coding'
            """).fetchone()[0])

            for source_table, source_schema in TABLE_SCHEMAS.items():
                expected_domain = source_schema['domain']
                id_col = source_schema['id']
                concept_col = source_schema['concept']
                source_val_col = source_schema['source']
                source_vocabulary = 'CMF_SYNTHEA_' + expected_domain.upper()

                before = con.execute(f"SELECT COUNT(*) FROM {source_table} WHERE {concept_col} <> 0").fetchone()[0]

                # 1. Create a temporary table of approved mappings for this source_table
                con.execute("DROP TABLE IF EXISTS temp_approved")

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
                          AND CURRENT_DATE BETWEEN COALESCE(TRY_CAST(stcm.valid_start_date AS DATE), TRY_STRPTIME(CAST(stcm.valid_start_date AS VARCHAR), '%Y%m%d')::DATE) AND COALESCE(TRY_CAST(stcm.valid_end_date AS DATE), TRY_STRPTIME(CAST(stcm.valid_end_date AS VARCHAR), '%Y%m%d')::DATE)
                """ if has_event_binding else ""

                fhir_coding_union = """
                        UNION
                        SELECT DISTINCT stcm.source_code, stcm.target_concept_id,
                                        coding.target_id
                        FROM source_to_concept_map stcm
                        JOIN fhir_event_source_coding coding
                          ON coding.target_table = ?
                         AND coding.source_vocabulary_id = stcm.source_vocabulary_id
                         AND coding.source_code = stcm.source_code
                        JOIN mapping_provenance p
                          ON p.target_table = coding.target_table
                         AND p.target_id = coding.target_id
                         AND p.source_vocabulary_id = stcm.source_vocabulary_id
                         AND p.source_code = stcm.source_code
                         AND p.assigned_concept_id = stcm.target_concept_id
                         AND p.reviewed_by = 'Approved_by_Human'
                        WHERE stcm.invalid_reason IS NULL
                          AND CURRENT_DATE BETWEEN COALESCE(TRY_CAST(stcm.valid_start_date AS DATE), TRY_STRPTIME(CAST(stcm.valid_start_date AS VARCHAR), '%Y%m%d')::DATE) AND COALESCE(TRY_CAST(stcm.valid_end_date AS DATE), TRY_STRPTIME(CAST(stcm.valid_end_date AS VARCHAR), '%Y%m%d')::DATE)
                """ if has_fhir_coding else ""

                parameters = [source_table, source_vocabulary]
                if has_event_binding:
                    parameters.append(source_table)
                if has_fhir_coding:
                    parameters.append(source_table)

                con.execute(f"""
                    CREATE TEMPORARY TABLE temp_approved AS
                    SELECT DISTINCT stcm.source_code, stcm.target_concept_id, NULL::BIGINT AS target_id
                    FROM source_to_concept_map stcm
                    JOIN mapping_provenance p
                      ON p.target_table = ?
                     AND p.source_value = stcm.source_code
                     AND p.assigned_concept_id = stcm.target_concept_id
                     AND p.reviewed_by = 'Approved_by_Human'
                    WHERE stcm.source_vocabulary_id IN (?, 'CMF_SYNTHEA')
                      AND stcm.invalid_reason IS NULL
                      AND CURRENT_DATE BETWEEN COALESCE(TRY_CAST(stcm.valid_start_date AS DATE), TRY_STRPTIME(CAST(stcm.valid_start_date AS VARCHAR), '%Y%m%d')::DATE) AND COALESCE(TRY_CAST(stcm.valid_end_date AS DATE), TRY_STRPTIME(CAST(stcm.valid_end_date AS VARCHAR), '%Y%m%d')::DATE)
                    {event_binding_union}
                    {fhir_coding_union}
                """, parameters)

                # 2. In-place Update for Matching Domain
                con.execute(f"""
                    UPDATE {source_table}
                    SET {concept_col} = approved.target_concept_id
                    FROM temp_approved approved
                    JOIN concept c ON approved.target_concept_id = c.concept_id
                    WHERE (
                          (approved.target_id IS NULL AND {source_table}.{source_val_col} = approved.source_code)
                       OR {source_table}.{id_col} = approved.target_id
                    )
                      AND {source_table}.{concept_col} = 0
                      AND c.domain_id = '{expected_domain}'
                      AND c.standard_concept = 'S'
                      AND (c.invalid_reason IS NULL OR c.invalid_reason = '')
                      AND CURRENT_DATE BETWEEN COALESCE(TRY_CAST(c.valid_start_date AS DATE), TRY_STRPTIME(CAST(c.valid_start_date AS VARCHAR), '%Y%m%d')::DATE) AND COALESCE(TRY_CAST(c.valid_end_date AS DATE), TRY_STRPTIME(CAST(c.valid_end_date AS VARCHAR), '%Y%m%d')::DATE)
                """)

                after = con.execute(f"SELECT COUNT(*) FROM {source_table} WHERE {concept_col} <> 0").fetchone()[0]
                mapped_count = after - before
                print(f"✅ Applied {mapped_count} mapped events natively in '{source_table}'")

                con.execute("DROP TABLE IF EXISTS temp_approved")

            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise

if __name__ == "__main__":
    apply_stcm_mappings()
