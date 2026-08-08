import os
import json
import duckdb
import sys
from datetime import datetime
from pathlib import Path

# Setup paths dynamically
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FHIR_DIR = os.path.join(PROJECT_ROOT, "synthea", "output", "fhir")
DB_PATH = os.path.join(PROJECT_ROOT, "data", "omop_clinical.duckdb")

sys.path.append(str(PROJECT_ROOT))
from src.utils.helpers import stable_person_id

def get_omop_gender_id(gender_string):
    mapping = {'male': 8507, 'female': 8532}
    return mapping.get(gender_string.lower(), 0)

def extract_person_data(patient_file):
    """Reads a FHIR bundle and extracts baseline demographic data."""
    file_path = os.path.join(FHIR_DIR, patient_file)
    with open(file_path, 'r', encoding='utf-8') as f:
        fhir_data = json.load(f)
        
    for entry in fhir_data.get('entry', []):
        resource = entry.get('resource', {})
        
        if resource.get('resourceType') == 'Patient':
            patient_source_id = resource.get('id', 'unknown')
            person_id = stable_person_id(patient_source_id)
            gender_source = resource.get('gender', 'unknown')
            gender_concept_id = get_omop_gender_id(gender_source)
            
            birth_date = resource.get('birthDate')
            if birth_date:
                dt = datetime.strptime(birth_date, '%Y-%m-%d')
                yob, mob, dob = dt.year, dt.month, dt.day
            else:
                yob, mob, dob = 0, 0, 0
            
            return {
                'person_id': person_id,
                'gender_concept_id': gender_concept_id,
                'year_of_birth': yob, 'month_of_birth': mob, 'day_of_birth': dob,
                'person_source_value': patient_source_id,
                'gender_source_value': gender_source
            }
    return None

def run_person_etl():
    """Main execution block for Person ETL."""
    print("⚙️ STARTING ETL (FHIR -> OMOP PERSON)\n" + "-"*50)
    
    json_files = [f for f in os.listdir(FHIR_DIR) if f.endswith('.json')]
    persons = [extract_person_data(f) for f in json_files if extract_person_data(f)]

    if not persons:
        print("❌ No valid FHIR patient files found.")
        return

    try:
        with duckdb.connect(DB_PATH) as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS person (
                    person_id BIGINT PRIMARY KEY,
                    gender_concept_id INTEGER,
                    year_of_birth INTEGER, month_of_birth INTEGER, day_of_birth INTEGER,
                    person_source_value VARCHAR, gender_source_value VARCHAR
                )
            """)
            
            for p in persons:
                con.execute("""
                    INSERT INTO person VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (person_id) DO UPDATE SET
                        gender_concept_id = EXCLUDED.gender_concept_id,
                        year_of_birth = EXCLUDED.year_of_birth,
                        month_of_birth = EXCLUDED.month_of_birth,
                        day_of_birth = EXCLUDED.day_of_birth
                """, (p['person_id'], p['gender_concept_id'], p['year_of_birth'], 
                      p['month_of_birth'], p['day_of_birth'], p['person_source_value'], p['gender_source_value']))
            
            count = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
            print(f"✅ PERSON table updated! Total structured patients: {count}")
    except Exception as e:
        print(f"❌ Database error: {e}")

if __name__ == "__main__":
    run_person_etl()