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
DOMAIN_TO_TABLE = {v['domain']: k for k, v in TABLE_SCHEMAS.items()}

def apply_stcm_mappings(db_path=DB_PATH):
    print("⚕️ STARTING STCM APPLICATION (Cross-Domain Routing Enabled)")
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

                # 3. Cross-Domain Routing (OMOP Magic)
                for target_domain, target_table in DOMAIN_TO_TABLE.items():
                    if target_domain == expected_domain:
                        continue

                    target_schema = TABLE_SCHEMAS[target_table]
                    
                    source_cols = {row[0] for row in con.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{source_table}'").fetchall()}
                    target_cols = {row[0] for row in con.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{target_table}'").fetchall()}
                    
                    sel_person = "s.person_id," if "person_id" in source_cols else "NULL AS person_id,"
                    sel_date = f"s.{source_schema['date']} AS event_date," if source_schema['date'] in source_cols else "NULL AS event_date,"
                    sel_dt = f"s.{source_schema['dt']} AS event_datetime," if source_schema['dt'] in source_cols else "NULL AS event_datetime,"
                    sel_type = f"s.{source_schema['type']} AS type_concept_id," if source_schema['type'] in source_cols else "0 AS type_concept_id,"
                    sel_visit = "s.visit_occurrence_id" if "visit_occurrence_id" in source_cols else "NULL AS visit_occurrence_id"

                    con.execute("DROP TABLE IF EXISTS temp_cross_routing")
                    con.execute(f"""
                        CREATE TEMPORARY TABLE temp_cross_routing AS
                        SELECT 
                            s.{id_col} AS route_id,
                            {sel_person}
                            approved.target_concept_id AS mapped_concept_id,
                            {sel_date}
                            {sel_dt}
                            {sel_type}
                            s.{source_val_col} AS source_value,
                            {sel_visit}
                        FROM temp_approved approved
                        JOIN concept c ON approved.target_concept_id = c.concept_id
                        JOIN {source_table} s ON (
                            (approved.target_id IS NULL AND s.{source_val_col} = approved.source_code)
                            OR s.{id_col} = approved.target_id
                        )
                        WHERE s.{concept_col} = 0
                          AND c.domain_id = '{target_domain}'
                          AND c.standard_concept = 'S'
                          AND (c.invalid_reason IS NULL OR c.invalid_reason = '')
                          AND CURRENT_DATE BETWEEN COALESCE(TRY_CAST(c.valid_start_date AS DATE), TRY_STRPTIME(CAST(c.valid_start_date AS VARCHAR), '%Y%m%d')::DATE) AND COALESCE(TRY_CAST(c.valid_end_date AS DATE), TRY_STRPTIME(CAST(c.valid_end_date AS VARCHAR), '%Y%m%d')::DATE)
                    """)
                    
                    routed_count = con.execute("SELECT COUNT(*) FROM temp_cross_routing").fetchone()[0]
                    if routed_count > 0:
                        ins_cols = [target_schema['id'], target_schema['concept'], target_schema['source'], target_schema['source_concept']]
                        sel_cols = ["route_id", "mapped_concept_id", "source_value", "0"]
                        
                        if "person_id" in target_cols:
                            ins_cols.append("person_id")
                            sel_cols.append("person_id")
                        if target_schema['date'] in target_cols:
                            ins_cols.append(target_schema['date'])
                            sel_cols.append("event_date")
                        if target_schema['dt'] in target_cols:
                            ins_cols.append(target_schema['dt'])
                            sel_cols.append("event_datetime")
                        if target_schema['type'] in target_cols:
                            ins_cols.append(target_schema['type'])
                            sel_cols.append("type_concept_id")
                        if "visit_occurrence_id" in target_cols:
                            ins_cols.append("visit_occurrence_id")
                            sel_cols.append("visit_occurrence_id")
                            
                        con.execute(f"""
                            INSERT INTO {target_table} ({', '.join(ins_cols)})
                            SELECT {', '.join(sel_cols)} FROM temp_cross_routing
                        """)
                        con.execute(f"""
                            DELETE FROM {source_table}
                            WHERE {id_col} IN (SELECT route_id FROM temp_cross_routing)
                        """)
                        print(f"🔄 Routed {routed_count} events from '{source_table}' -> '{target_table}' (Domain: {target_domain})")

                con.execute("DROP TABLE IF EXISTS temp_approved")
                con.execute("DROP TABLE IF EXISTS temp_cross_routing")

            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise

if __name__ == "__main__":
    apply_stcm_mappings()
