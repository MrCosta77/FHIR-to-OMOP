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

def generate_procedure_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_procedures(file_path):
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        bundle = json.load(f)
        
        if bundle.get('resourceType') != 'Bundle':
            return records
            
        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            
            # Capturar Procedimentos
            if resource.get('resourceType') == 'Procedure':
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(patient_ref)
                
                if not person_id: continue
                    
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
                    
                if not code: continue
                    
                # FHIR procedures can have performedDateTime or performedPeriod
                date = resource.get('performedDateTime', '')[:10]
                if not date:
                    date = resource.get('performedPeriod', {}).get('start', '')[:10]
                if not date: continue
                    
                full_url = entry.get('fullUrl', '')
                base_string = full_url if full_url else json.dumps(resource, sort_keys=True)
                procedure_id = generate_procedure_id(base_string)
                    
                records.append((procedure_id, person_id, code, display, date))
                
    return records

def run_procedure_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP PROCEDURE) [PRODUCTION]")
    print("-" * 50)
    
    print("🔍 Extracting procedures from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))
    
    all_records = []
    for f in fhir_files:
        all_records.extend(extract_procedures(f))
        
    print(f"📊 Extracted {len(all_records)} raw procedure records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")
    
    with duckdb.connect(DB_PATH) as con:
        con.execute("DROP TABLE IF EXISTS procedure_occurrence")
        
        # OMOP 5.4 Procedure Occurrence Table
        con.execute("""
            CREATE TABLE procedure_occurrence (
                procedure_occurrence_id BIGINT PRIMARY KEY,
                person_id BIGINT,
                procedure_concept_id INTEGER,
                procedure_date DATE,
                procedure_datetime TIMESTAMP,
                procedure_type_concept_id INTEGER,
                modifier_concept_id INTEGER,
                quantity INTEGER,
                provider_id BIGINT,
                visit_occurrence_id BIGINT,
                visit_detail_id BIGINT,
                procedure_source_value VARCHAR,
                procedure_source_concept_id INTEGER,
                modifier_source_value VARCHAR
            )
        """)
        
        con.execute("DROP TABLE IF EXISTS stg_procedure")
        con.execute("""
            CREATE TEMPORARY TABLE stg_procedure (
                procedure_occurrence_id BIGINT,
                person_id BIGINT,
                code VARCHAR,
                display_text VARCHAR,
                date DATE
            )
        """)
        
        con.executemany("INSERT INTO stg_procedure VALUES (?, ?, ?, ?, ?)", all_records)
        
        # DOMAIN ROUTING ESTRITO
        con.execute("""
            INSERT INTO procedure_occurrence (
                procedure_occurrence_id, person_id, procedure_concept_id,
                procedure_date, procedure_datetime,
                procedure_type_concept_id,
                procedure_source_value, procedure_source_concept_id
            )
            SELECT 
                stg.procedure_occurrence_id,
                stg.person_id,
                CASE 
                    WHEN c_std.domain_id = 'Procedure' THEN COALESCE(c_std.concept_id::INTEGER, 0)
                    ELSE 0 
                END AS procedure_concept_id,
                stg.date,
                stg.date::TIMESTAMP,
                32817 AS procedure_type_concept_id,
                stg.display_text AS procedure_source_value,
                COALESCE(c_src.concept_id::INTEGER, 0) AS procedure_source_concept_id
            FROM stg_procedure stg
            LEFT JOIN concept c_src 
                ON stg.code = c_src.concept_code 
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
            QUALIFY ROW_NUMBER() OVER (PARTITION BY stg.procedure_occurrence_id ORDER BY c_std.concept_id DESC) = 1
        """)
        
        # Registar na Auditoria
        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by
            )
            SELECT 
                'procedure_occurrence',
                procedure_occurrence_id,
                procedure_source_value,
                procedure_source_value,
                procedure_concept_id,
                'deterministic_maps_to',
                1.0,
                'N/A',
                'Athena_v5.4',
                'System'
            FROM procedure_occurrence
            WHERE procedure_concept_id != 0
            AND procedure_occurrence_id NOT IN (
                SELECT target_id FROM mapping_provenance WHERE target_table = 'procedure_occurrence'
            )
        """)
        
        mapped_count = con.execute("SELECT COUNT(*) FROM procedure_occurrence WHERE procedure_concept_id != 0").fetchone()[0]
        unmapped_count = con.execute("SELECT COUNT(*) FROM procedure_occurrence WHERE procedure_concept_id = 0").fetchone()[0]
        
    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped (OMOP Standard): {mapped_count} procedures")
    print(f" - Sent to AI Fallback Queue (ID 0): {unmapped_count} procedures")

if __name__ == "__main__":
    run_procedure_etl()