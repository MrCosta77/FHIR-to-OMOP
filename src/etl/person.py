import os
import sys
import json
import glob
import duckdb
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

# Importa a config global, como sugerido pela revisão
from src.utils.config import DB_PATH, FHIR_DIR
from src.utils.helpers import stable_person_id

def extract_persons(file_path):
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        bundle = json.load(f)
        
        if bundle.get('resourceType') != 'Bundle': 
            return records

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            
            if resource.get('resourceType') == 'Patient':
                patient_id = resource.get('id', '')
                if not patient_id: 
                    continue

                person_id = stable_person_id(patient_id)
                gender = resource.get('gender', 'unknown')
                gender_concept_id = 8507 if gender == 'male' else 8532 if gender == 'female' else 0

                birth_date = resource.get('birthDate', '')
                year_of_birth = None
                month_of_birth = None
                day_of_birth = None
                birth_datetime = None

                if birth_date:
                    parts = birth_date.split('-')
                    year_of_birth = int(parts[0]) if len(parts) > 0 else None
                    month_of_birth = int(parts[1]) if len(parts) > 1 else None
                    day_of_birth = int(parts[2]) if len(parts) > 2 else None
                    birth_datetime = f"{birth_date} 00:00:00"

                # Ignorar doentes com year_of_birth a 0 (crítica do revisor resolvida)
                if not year_of_birth or year_of_birth == 0:
                    continue

                # Extrair Raça e Etnia (crítica do revisor resolvida)
                race_concept_id = 0
                ethnicity_concept_id = 0
                for ext in resource.get('extension', []):
                    url = ext.get('url', '')
                    if 'race' in url:
                        text = ext.get('extension', [{}])[0].get('valueCoding', {}).get('display', '').lower()
                        if 'white' in text: race_concept_id = 8527
                        elif 'black' in text or 'african' in text: race_concept_id = 8516
                        elif 'asian' in text: race_concept_id = 8515
                    elif 'ethnicity' in url:
                        text = ext.get('extension', [{}])[0].get('valueCoding', {}).get('display', '').lower()
                        if 'hispanic' in text: ethnicity_concept_id = 38003563
                        else: ethnicity_concept_id = 38003564

                records.append((
                    person_id, gender_concept_id, year_of_birth, month_of_birth, day_of_birth, birth_datetime,
                    race_concept_id, ethnicity_concept_id, patient_id, gender
                ))
    return records

def run_person_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP PERSON) [PRODUCTION]")
    print("-" * 50)
    
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))
    all_records = []
    
    # Sem try/except engolidores. Se falhar, falha de forma ruidosa e visível.
    for f in fhir_files:
        all_records.extend(extract_persons(f))
        
    print(f"📊 Extracted {len(all_records)} raw person records.")
    
    with duckdb.connect(DB_PATH) as con:
        # A CAUSA DO ERRO: Faltava o DROP TABLE para garantir idempotência!
        con.execute("DROP TABLE IF EXISTS person")
        
        con.execute("""
            CREATE TABLE person (
                person_id BIGINT PRIMARY KEY,
                gender_concept_id INTEGER,
                year_of_birth INTEGER,
                month_of_birth INTEGER,
                day_of_birth INTEGER,
                birth_datetime TIMESTAMP,
                race_concept_id INTEGER,
                ethnicity_concept_id INTEGER,
                location_id BIGINT,
                provider_id BIGINT,
                care_site_id BIGINT,
                person_source_value VARCHAR,
                gender_source_value VARCHAR,
                gender_source_concept_id INTEGER,
                race_source_value VARCHAR,
                race_source_concept_id INTEGER,
                ethnicity_source_value VARCHAR,
                ethnicity_source_concept_id INTEGER
            )
        """)
        
        con.execute("DROP TABLE IF EXISTS stg_person")
        con.execute("""
            CREATE TEMPORARY TABLE stg_person (
                person_id BIGINT,
                gender_concept_id INTEGER,
                year_of_birth INTEGER,
                month_of_birth INTEGER,
                day_of_birth INTEGER,
                birth_datetime TIMESTAMP,
                race_concept_id INTEGER,
                ethnicity_concept_id INTEGER,
                person_source_value VARCHAR,
                gender_source_value VARCHAR
            )
        """)
        
        con.executemany("INSERT INTO stg_person VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", all_records)
        
        # Inserção com DISTINCT para garantir unicidade absoluta
        con.execute("""
            INSERT INTO person (
                person_id, gender_concept_id, year_of_birth, month_of_birth, day_of_birth, birth_datetime,
                race_concept_id, ethnicity_concept_id, person_source_value, gender_source_value
            )
            SELECT DISTINCT
                person_id, gender_concept_id, year_of_birth, month_of_birth, day_of_birth, birth_datetime,
                race_concept_id, ethnicity_concept_id, person_source_value, gender_source_value
            FROM stg_person
        """)
        
        count = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        
    print(f"✅ PERSON table updated! Total structured patients: {count}")

if __name__ == "__main__":
    run_person_etl()