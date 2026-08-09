import os
import json
import duckdb
import sys
from pathlib import Path

# Setup dynamic paths relative to this script
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FHIR_DIR = os.path.join(PROJECT_ROOT, "synthea", "output", "fhir")
DB_PATH = os.path.join(PROJECT_ROOT, "data", "omop_clinical.duckdb")

# Centralized imports
sys.path.append(str(PROJECT_ROOT))
from src.utils.helpers import stable_person_id

def extract_conditions(patient_file):
    """Reads a FHIR bundle and extracts clinical conditions."""
    file_path = os.path.join(FHIR_DIR, patient_file)
    with open(file_path, 'r', encoding='utf-8') as f:
        fhir_data = json.load(f)
        
    conditions = []
    
    for entry in fhir_data.get('entry', []):
        resource = entry.get('resource', {})
        
        if resource.get('resourceType') == 'Condition':
            # Extract stable Patient ID
            subject_ref = resource.get('subject', {}).get('reference', '')
            patient_source_id = subject_ref.replace('urn:uuid:', '')
            person_id = stable_person_id(patient_source_id)
            
            # Extract Condition Data
            code_block = resource.get('code', {}).get('coding', [{}])[0]
            snomed_code = code_block.get('code', '0')
            condition_text = code_block.get('display', 'Unknown')
            start_date = resource.get('onsetDateTime', '1900-01-01')[:10] 
            
            conditions.append((person_id, snomed_code, condition_text, start_date))
            
    return conditions

def run_condition_etl():
    """Main execution block for Condition ETL."""
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP CONDITION) [PRODUCTION]\n" + "-"*50)

    print("🔍 Extracting conditions from FHIR JSON files...")
    json_files = [f for f in os.listdir(FHIR_DIR) if f.endswith('.json')]
    all_conditions = []

    for file in json_files:
        all_conditions.extend(extract_conditions(file))

    print(f"📊 Extracted {len(all_conditions)} raw condition records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")

    try:
        with duckdb.connect(DB_PATH) as con:
            # Create staging table
            con.execute("DROP TABLE IF EXISTS stg_condition")
            con.execute("""
                CREATE TEMPORARY TABLE stg_condition (
                    person_id BIGINT,
                    snomed_code VARCHAR,
                    condition_text VARCHAR,
                    start_date DATE
                )
            """)
            
            con.executemany("INSERT INTO stg_condition VALUES (?, ?, ?, ?)", all_conditions)
            
            # Ensure target table exists
            con.execute("""
                CREATE TABLE IF NOT EXISTS condition_occurrence (
                    condition_occurrence_id BIGINT PRIMARY KEY,
                    person_id BIGINT,
                    condition_concept_id INTEGER,
                    condition_start_date DATE,
                    condition_source_value VARCHAR,
                    condition_source_concept_id INTEGER
                )
            """)
            
            # Idempotency: Clear existing records to prevent duplication
            con.execute("DELETE FROM condition_occurrence")
            
            # Insertion with strict OMOP semantic separation
            con.execute("""
                INSERT INTO condition_occurrence 
                SELECT 
                    ROW_NUMBER() OVER () AS condition_occurrence_id,
                    stg.person_id,
                    COALESCE(c.concept_id, 0) AS condition_concept_id,
                    stg.start_date AS condition_start_date,
                    stg.condition_text AS condition_source_value,
                    COALESCE(sc.concept_id, 0) AS condition_source_concept_id
                FROM stg_condition stg
                
                -- 1. O Mapeamento Analítico (Standard)
                LEFT JOIN concept c 
                    ON stg.snomed_code = c.concept_code 
                    AND c.vocabulary_id = 'SNOMED'
                    AND c.domain_id = 'Condition'
                    AND c.standard_concept = 'S'
                    
                -- 2. O Mapeamento de Auditoria (Source)
                LEFT JOIN concept sc 
                    ON stg.snomed_code = sc.concept_code 
                    AND sc.vocabulary_id = 'SNOMED'
            """)
            
            mapped = con.execute("SELECT COUNT(*) FROM condition_occurrence WHERE condition_concept_id != 0").fetchone()[0]
            unmapped = con.execute("SELECT COUNT(*) FROM condition_occurrence WHERE condition_concept_id = 0").fetchone()[0]
            
            print("\n✅ ETL Complete!")
            print(f" - Successfully mapped (Exact Match): {mapped} conditions")
            print(f" - Sent to AI Fallback Queue (ID 0): {unmapped} conditions")

    except Exception as e:
        print(f"❌ Database error: {e}")

if __name__ == "__main__":
    run_condition_etl()