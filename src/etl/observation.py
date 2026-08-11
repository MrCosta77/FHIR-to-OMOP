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

def generate_observation_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_observation_candidates(file_path):
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        bundle = json.load(f)
        
        if bundle.get('resourceType') != 'Bundle':
            return records
            
        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            
            # 1. Caçar as "Condições" que são na verdade Observações Sociais (as nossas 1082 fugitivas)
            if resource.get('resourceType') == 'Condition':
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
                    
                date = resource.get('onsetDateTime', '')[:10]
                if not date: date = '1900-01-01'
                    
                full_url = entry.get('fullUrl', '')
                base_string = full_url if full_url else json.dumps(resource, sort_keys=True)
                obs_id = generate_observation_id(base_string)
                    
                records.append((obs_id, person_id, code, display, date))
                
            # 2. Caçar Observações Categóricas (ex: Fumar, questionários) que o measurement.py ignorou
            elif resource.get('resourceType') == 'Observation':
                # Ignoramos as que têm valor numérico (o measurement.py já tratou dessas)
                if 'valueQuantity' in resource:
                    continue
                    
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(patient_ref)
                if not person_id: continue
                
                code = None
                display = "Unknown"
                codings = resource.get('code', {}).get('coding', [])
                for c in codings:
                    if c.get('system') in ['http://loinc.org', 'http://snomed.info/sct']:
                        code = c.get('code')
                        display = c.get('display', '')
                        break
                if not code: continue
                
                date = resource.get('effectiveDateTime', '')[:10]
                if not date: continue
                
                full_url = entry.get('fullUrl', '')
                base_string = full_url if full_url else json.dumps(resource, sort_keys=True)
                obs_id = generate_observation_id(base_string)
                
                records.append((obs_id, person_id, code, display, date))
                
    return records

def run_observation_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP OBSERVATION) [PRODUCTION]")
    print("-" * 50)
    
    print("🔍 Extracting potential observation candidates from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))
    
    all_records = []
    for f in fhir_files:
        all_records.extend(extract_observation_candidates(f))
        
    print(f"📊 Extracted {len(all_records)} raw candidates (mixed domains).")
    print("🔌 Connecting to DuckDB for strict domain-routed insertion...")
    
    with duckdb.connect(DB_PATH) as con:
        con.execute("DROP TABLE IF EXISTS observation")
        
        con.execute("""
            CREATE TABLE observation (
                observation_id BIGINT PRIMARY KEY,
                person_id BIGINT,
                observation_concept_id INTEGER,
                observation_date DATE,
                observation_datetime TIMESTAMP,
                observation_type_concept_id INTEGER,
                value_as_number DOUBLE,
                value_as_string VARCHAR,
                value_as_concept_id INTEGER,
                qualifier_concept_id INTEGER,
                unit_concept_id INTEGER,
                provider_id BIGINT,
                visit_occurrence_id BIGINT,
                visit_detail_id BIGINT,
                observation_source_value VARCHAR,
                observation_source_concept_id INTEGER,
                unit_source_value VARCHAR,
                qualifier_source_value VARCHAR,
                value_source_value VARCHAR,
                observation_event_id BIGINT,
                obs_event_field_concept_id INTEGER
            )
        """)
        
        con.execute("DROP TABLE IF EXISTS stg_observation")
        con.execute("""
            CREATE TEMPORARY TABLE stg_observation (
                observation_id BIGINT,
                person_id BIGINT,
                code VARCHAR,
                display_text VARCHAR,
                date DATE
            )
        """)
        
        con.executemany("INSERT INTO stg_observation VALUES (?, ?, ?, ?, ?)", all_records)
        
        # DOMAIN ROUTING ESTRITO: O INNER JOIN garante que apenas os conceitos de Observação entram
        con.execute("""
            INSERT INTO observation (
                observation_id, person_id, observation_concept_id,
                observation_date, observation_datetime,
                observation_type_concept_id,
                observation_source_value, observation_source_concept_id
            )
            SELECT 
                stg.observation_id,
                stg.person_id,
                c_std.concept_id::INTEGER AS observation_concept_id,
                stg.date,
                stg.date::TIMESTAMP,
                32817 AS observation_type_concept_id,
                stg.display_text AS observation_source_value,
                COALESCE(c_src.concept_id::INTEGER, 0) AS observation_source_concept_id
            FROM stg_observation stg
            LEFT JOIN concept c_src 
                ON stg.code = c_src.concept_code 
                AND c_src.vocabulary_id IN ('SNOMED', 'LOINC')
                AND c_src.invalid_reason IS NULL
            LEFT JOIN concept_relationship cr 
                ON c_src.concept_id = cr.concept_id_1 
                AND cr.relationship_id = 'Maps to'
                AND cr.invalid_reason IS NULL
            INNER JOIN concept c_std 
                ON cr.concept_id_2 = c_std.concept_id 
                AND c_std.standard_concept = 'S'
                AND c_std.invalid_reason IS NULL
                AND c_std.domain_id = 'Observation' -- 🚨 O FILTRO MÁGICO 🚨
            QUALIFY ROW_NUMBER() OVER (PARTITION BY stg.observation_id ORDER BY c_std.concept_id DESC) = 1
        """)

        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by
            )
            SELECT 
                'observation',
                observation_id,
                observation_source_value,
                observation_source_value,
                observation_concept_id,
                'deterministic_maps_to',
                1.0,
                'N/A',
                'Athena_v5.4',
                'System'
            FROM observation
            WHERE observation_concept_id != 0
            AND observation_id NOT IN (
                SELECT target_id FROM mapping_provenance WHERE target_table = 'observation'
            )
        """)

        con.execute("""
            UPDATE observation
            SET observation_concept_id = stcm.target_concept_id
            FROM source_to_concept_map stcm
            WHERE observation.observation_source_value = stcm.source_code
              AND observation.observation_concept_id = 0;
        """)
        
        mapped_count = con.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
        
    print("\n✅ ETL Complete!")
    print(f" - Successfully routed & mapped to OMOP Observation: {mapped_count} records")

if __name__ == "__main__":
    run_observation_etl()