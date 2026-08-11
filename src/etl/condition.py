import os
import sys
import json
import glob
import duckdb
import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, FHIR_DIR
from src.utils.helpers import stable_person_id

def generate_condition_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_conditions(file_path):
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        bundle = json.load(f)
        
        if bundle.get('resourceType') != 'Bundle':
            return records
            
        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            
            if resource.get('resourceType') == 'Condition':
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(patient_ref)
                
                if not person_id:
                    continue
                    
                code = None
                display = "Unknown"
                codings = resource.get('code', {}).get('coding', [])
                
                for c in codings:
                    if c.get('system') == 'http://snomed.info/sct':
                        code = c.get('code')
                        display = c.get('display', '')
                        break
                        
                if not code and codings:
                    code = codings[0].get('code')
                    display = codings[0].get('display', '')
                    
                if not code:
                    continue
                    
                start_date = resource.get('onsetDateTime', '')[:10]
                if not start_date:
                    continue 
                    
                end_date = resource.get('abatementDateTime', '')[:10]
                if not end_date:
                    end_date = start_date
                    
                # A forma mais segura de identificar um recurso num Bundle FHIR
                full_url = entry.get('fullUrl', '')
                if full_url:
                    base_string = full_url
                else:
                    base_string = json.dumps(resource, sort_keys=True)
                    
                condition_id = generate_condition_id(base_string)
                    
                records.append((
                    condition_id,
                    person_id,
                    code,
                    display,
                    start_date,
                    end_date
                ))
    return records

def run_condition_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP CONDITION) [PRODUCTION]")
    print("-" * 50)
    
    print("🔍 Extracting conditions from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))
    
    all_records = []
    for f in fhir_files:
        all_records.extend(extract_conditions(f))
        
    print(f"📊 Extracted {len(all_records)} raw condition records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")
    
    with duckdb.connect(DB_PATH) as con:
        # FORÇA A ELIMINAÇÃO DA TABELA ANTIGA PARA ATUALIZAR O SCHEMA
        con.execute("DROP TABLE IF EXISTS condition_occurrence")
        
        # Create persistent condition_occurrence table
        con.execute("""
            CREATE TABLE condition_occurrence (
                condition_occurrence_id BIGINT PRIMARY KEY,
                person_id BIGINT,
                condition_concept_id INTEGER,
                condition_start_date DATE,
                condition_start_datetime TIMESTAMP,
                condition_end_date DATE,
                condition_end_datetime TIMESTAMP,
                condition_type_concept_id INTEGER,
                stop_reason VARCHAR,
                provider_id BIGINT,
                visit_occurrence_id BIGINT,
                visit_detail_id BIGINT,
                condition_source_value VARCHAR,
                condition_source_concept_id INTEGER,
                condition_status_source_value VARCHAR,
                condition_status_concept_id INTEGER
            )
        """)
        
        # Temporary staging table
        con.execute("DROP TABLE IF EXISTS stg_condition")
        con.execute("""
            CREATE TEMPORARY TABLE stg_condition (
                condition_occurrence_id BIGINT,
                person_id BIGINT,
                snomed_code VARCHAR,
                display_text VARCHAR,
                start_date DATE,
                end_date DATE
            )
        """)
        
        con.executemany("INSERT INTO stg_condition VALUES (?, ?, ?, ?, ?, ?)", all_records)
        
        con.execute("DELETE FROM condition_occurrence")
        
        # THE OMOP TRIPLE-JOIN: Source -> Relationship -> Standard
        con.execute("""
            INSERT INTO condition_occurrence (
                condition_occurrence_id, person_id, condition_concept_id,
                condition_start_date, condition_start_datetime,
                condition_end_date, condition_end_datetime,
                condition_type_concept_id, condition_source_value,
                condition_source_concept_id
            )
            SELECT 
                stg.condition_occurrence_id,
                stg.person_id,
                CASE 
                    WHEN c_std.domain_id = 'Condition' THEN COALESCE(c_std.concept_id::INTEGER, 0)
                    ELSE 0 
                END AS condition_concept_id,
                stg.start_date,
                stg.start_date::TIMESTAMP,
                stg.end_date,
                stg.end_date::TIMESTAMP,
                32817 AS condition_type_concept_id, 
                stg.display_text AS condition_source_value, 
                COALESCE(c_src.concept_id::INTEGER, 0) AS condition_source_concept_id
            FROM stg_condition stg
            LEFT JOIN concept c_src 
                ON stg.snomed_code = c_src.concept_code 
                AND c_src.vocabulary_id = 'SNOMED'
                AND c_src.invalid_reason IS NULL
            LEFT JOIN concept_relationship cr 
                ON c_src.concept_id = cr.concept_id_1 
                AND cr.relationship_id = 'Maps to'
                AND cr.invalid_reason IS NULL
            LEFT JOIN concept c_std 
                ON cr.concept_id_2 = c_std.concept_id 
                AND c_std.standard_concept = 'S'
                AND c_std.invalid_reason IS NULL
            QUALIFY ROW_NUMBER() OVER (PARTITION BY stg.condition_occurrence_id ORDER BY c_std.concept_id DESC) = 1
        """)

        con.execute("""
            DELETE FROM condition_occurrence
            WHERE condition_occurrence_id IN (
                SELECT stg.condition_occurrence_id
                FROM stg_condition stg
                JOIN concept c_src ON stg.snomed_code = c_src.concept_code AND c_src.vocabulary_id = 'SNOMED'
                JOIN concept_relationship cr ON c_src.concept_id = cr.concept_id_1 AND cr.relationship_id = 'Maps to'
                JOIN concept c_std ON cr.concept_id_2 = c_std.concept_id AND c_std.standard_concept = 'S'
                WHERE c_std.domain_id != 'Condition'
            )
        """)
        
        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by
            )
            SELECT 
                'condition_occurrence',
                condition_occurrence_id,
                condition_source_value,
                condition_source_value, -- Sem normalização nesta fase
                condition_concept_id,
                'deterministic_maps_to',
                1.0, -- Confiança total
                'N/A',
                'Athena_v5.4',
                'System'
            FROM condition_occurrence
            WHERE condition_concept_id != 0
            -- Evita duplicar registos se correres o script várias vezes
            AND condition_occurrence_id NOT IN (
                SELECT target_id FROM mapping_provenance WHERE target_table = 'condition_occurrence'
            )
        """)

        con.execute("""
            UPDATE condition_occurrence
            SET condition_concept_id = stcm.target_concept_id
            FROM source_to_concept_map stcm
            WHERE condition_occurrence.condition_source_value = stcm.source_code
              AND condition_occurrence.condition_concept_id = 0;
        """)
        
        mapped_count = con.execute("SELECT COUNT(*) FROM condition_occurrence WHERE condition_concept_id != 0").fetchone()[0]
        unmapped_count = con.execute("SELECT COUNT(*) FROM condition_occurrence WHERE condition_concept_id = 0").fetchone()[0]
        
    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped (OMOP Standard): {mapped_count} conditions")
    print(f" - Sent to AI Fallback / Observation Queue (ID 0): {unmapped_count} conditions")

if __name__ == "__main__":
    run_condition_etl()