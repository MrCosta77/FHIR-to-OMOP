import os
import sys
import json
import glob
import duckdb
import hashlib
from pathlib import Path

# Setup paths so Python can find the 'src' folder
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

# Clean, centralized configuration import (using FHIR_DIR as defined in config.py)
from src.utils.config import DB_PATH, FHIR_DIR

# OMOP Standard Visit Concepts
# FHIR Encounter class codes mapped to OMOP Concept IDs
VISIT_MAPPING = {
    "AMB": 9202,    # Outpatient Visit
    "WELL": 9202,   # Outpatient Visit (Wellness is mapped to Outpatient)
    "IMP": 9201,    # Inpatient Visit
    "EMER": 9203,   # Emergency Room Visit
    "URGENT": 9203  # Emergency Room Visit
}

DEFAULT_VISIT_CONCEPT_ID = 0  # Unmapped visit

def generate_numeric_id(uuid_string):
    """Generates a stable numeric ID from a FHIR UUID using SHA-256."""
    if not uuid_string:
        return None
    clean_uuid = uuid_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_uuid.encode('utf-8')).hexdigest()[:15], 16)

def run_visit_etl():
    """Extracts FHIR Encounters and loads them into OMOP VISIT_OCCURRENCE."""
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP VISIT_OCCURRENCE)")
    print("-" * 50)
    
    print("🔍 Extracting encounters from FHIR JSON files...")
    if not os.path.exists(FHIR_DIR):
        print(f"⚠️ Warning: FHIR data directory not found at {FHIR_DIR}")
        print("Please check your folder structure.")
        return
        
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))
    
    visit_records = []
    
    for file_path in fhir_files:
        with open(file_path, 'r', encoding='utf-8') as f:
            bundle = json.load(f)
            
            # Skip if not a Bundle
            if bundle.get('resourceType') != 'Bundle':
                continue
                
            for entry in bundle.get('entry', []):
                resource = entry.get('resource', {})
                
                if resource.get('resourceType') == 'Encounter':
                    # 1. Get Foreign Key (Patient ID)
                    patient_ref = resource.get('subject', {}).get('reference', '')
                    person_id = generate_numeric_id(patient_ref)
                    
                    if not person_id:
                        continue
                        
                    # 2. Get Primary Key (Visit ID)
                    encounter_id = resource.get('id', '')
                    visit_occurrence_id = generate_numeric_id(encounter_id)
                    
                    # 3. Get Dates
                    period = resource.get('period', {})
                    start_date = period.get('start', '')[:10]  # YYYY-MM-DD
                    end_date = period.get('end', '')[:10]
                    
                    if not start_date:
                        continue
                    if not end_date:
                        end_date = start_date # Fallback if visit was single day
                        
                    # 4. Map Visit Type (Encounter Class)
                    fhir_class_code = resource.get('class', {}).get('code', '').upper()
                    visit_concept_id = VISIT_MAPPING.get(fhir_class_code, DEFAULT_VISIT_CONCEPT_ID)
                    
                    # 32035 means "EHR" (Electronic Health Record) - standard for origin tracking
                    visit_type_concept_id = 32035 
                    
                    visit_records.append((
                        visit_occurrence_id,
                        person_id,
                        visit_concept_id,
                        start_date,
                        start_date, # start_datetime
                        end_date,
                        end_date,   # end_datetime
                        visit_type_concept_id,
                        0,          # provider_id
                        0,          # care_site_id
                        fhir_class_code, # visit_source_value
                        0           # visit_source_concept_id
                    ))

    print(f"📊 Extracted {len(visit_records)} raw visit records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")
    
    with duckdb.connect(DB_PATH) as con:
        # Create table if it doesn't exist
        con.execute("""
            CREATE TABLE IF NOT EXISTS visit_occurrence (
                visit_occurrence_id BIGINT PRIMARY KEY,
                person_id BIGINT,
                visit_concept_id INTEGER,
                visit_start_date DATE,
                visit_start_datetime TIMESTAMP,
                visit_end_date DATE,
                visit_end_datetime TIMESTAMP,
                visit_type_concept_id INTEGER,
                provider_id BIGINT,
                care_site_id BIGINT,
                visit_source_value VARCHAR,
                visit_source_concept_id INTEGER
            )
        """)
        
        # Clear existing data to maintain idempotency
        con.execute("DELETE FROM visit_occurrence")
        
        # Bulk Insert
        if visit_records:
            con.executemany("""
                INSERT INTO visit_occurrence (
                    visit_occurrence_id, person_id, visit_concept_id,
                    visit_start_date, visit_start_datetime, visit_end_date,
                    visit_end_datetime, visit_type_concept_id, provider_id,
                    care_site_id, visit_source_value, visit_source_concept_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, visit_records)
        
        # Diagnostics
        mapped_count = con.execute("SELECT COUNT(*) FROM visit_occurrence WHERE visit_concept_id != 0").fetchone()[0]
        unmapped_count = con.execute("SELECT COUNT(*) FROM visit_occurrence WHERE visit_concept_id = 0").fetchone()[0]
        
    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped to OMOP Standards: {mapped_count} visits")
    print(f" - Failed to map (Unknown Type): {unmapped_count} visits")

if __name__ == "__main__":
    run_visit_etl()