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

def generate_measurement_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_measurements(file_path):
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        bundle = json.load(f)
        
        if bundle.get('resourceType') != 'Bundle':
            return records
            
        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            
            if resource.get('resourceType') == 'Observation':
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(patient_ref)
                
                if not person_id:
                    continue
                    
                code = None
                display = "Unknown"
                codings = resource.get('code', {}).get('coding', [])
                
                for c in codings:
                    if c.get('system') == 'http://loinc.org':
                        code = c.get('code')
                        display = c.get('display', '')
                        break
                        
                if not code:
                    continue
                    
                date = resource.get('effectiveDateTime', '')[:10]
                if not date:
                    continue
                    
                value = None
                unit = None
                
                if 'valueQuantity' in resource:
                    value = resource['valueQuantity'].get('value')
                    unit = resource['valueQuantity'].get('unit')
                
                if value is None:
                    continue
                    
                # Identificador Único Universal do Bundle FHIR
                full_url = entry.get('fullUrl', '')
                if full_url:
                    base_string = full_url
                else:
                    # Fallback de segurança: transformar o JSON do lab resource numa string
                    base_string = json.dumps(resource, sort_keys=True)
                    
                measurement_id = generate_measurement_id(base_string)
                
                records.append((
                    measurement_id,
                    person_id,
                    code,
                    display,
                    float(value),
                    unit,
                    date
                ))
    return records

def run_measurement_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP MEASUREMENT) [PRODUCTION]")
    print("-" * 50)
    
    print("🔍 Extracting laboratory results from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))
    
    all_records = []
    for f in fhir_files:
        all_records.extend(extract_measurements(f))
        
    print(f"📊 Extracted {len(all_records)} raw measurement records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")
    
    with duckdb.connect(DB_PATH) as con:
        con.execute("DROP TABLE IF EXISTS measurement")
        
        con.execute("""
            CREATE TABLE measurement (
                measurement_id BIGINT PRIMARY KEY,
                person_id BIGINT,
                measurement_concept_id INTEGER,
                measurement_date DATE,
                measurement_datetime TIMESTAMP,
                measurement_time VARCHAR,
                measurement_type_concept_id INTEGER,
                operator_concept_id INTEGER,
                value_as_number DOUBLE,
                value_as_concept_id INTEGER,
                unit_concept_id INTEGER,
                range_low DOUBLE,
                range_high DOUBLE,
                provider_id BIGINT,
                visit_occurrence_id BIGINT,
                visit_detail_id BIGINT,
                measurement_source_value VARCHAR,
                measurement_source_concept_id INTEGER,
                unit_source_value VARCHAR,
                value_source_value VARCHAR
            )
        """)
        
        con.execute("DROP TABLE IF EXISTS stg_measurement")
        con.execute("""
            CREATE TEMPORARY TABLE stg_measurement (
                measurement_id BIGINT,
                person_id BIGINT,
                loinc_code VARCHAR,
                display_text VARCHAR,
                value DOUBLE,
                unit VARCHAR,
                date DATE
            )
        """)
        
        con.executemany("INSERT INTO stg_measurement VALUES (?, ?, ?, ?, ?, ?, ?)", all_records)
        
        con.execute("""
            INSERT INTO measurement (
                measurement_id, person_id, measurement_concept_id,
                measurement_date, measurement_datetime,
                measurement_type_concept_id, value_as_number,
                measurement_source_value, measurement_source_concept_id,
                unit_source_value
            )
            SELECT 
                stg.measurement_id,
                stg.person_id,
                CASE 
                    WHEN c_std.domain_id = 'Measurement' THEN COALESCE(c_std.concept_id::INTEGER, 0)
                    ELSE 0 
                END AS measurement_concept_id,
                stg.date,
                stg.date::TIMESTAMP,
                32817 AS measurement_type_concept_id,
                stg.value AS value_as_number,
                stg.display_text AS measurement_source_value,
                COALESCE(c_src.concept_id::INTEGER, 0) AS measurement_source_concept_id,
                stg.unit AS unit_source_value
            FROM stg_measurement stg
            LEFT JOIN concept c_src 
                ON stg.loinc_code = c_src.concept_code 
                AND c_src.vocabulary_id = 'LOINC'
                AND c_src.invalid_reason IS NULL
            LEFT JOIN concept_relationship cr 
                ON c_src.concept_id = cr.concept_id_1 
                AND cr.relationship_id = 'Maps to'
                AND cr.invalid_reason IS NULL
            LEFT JOIN concept c_std 
                ON cr.concept_id_2 = c_std.concept_id 
                AND c_std.standard_concept = 'S'
                AND c_std.invalid_reason IS NULL
            -- O escudo contra o 'merge-inflation' garantindo apenas 1 conceito por registo
            QUALIFY ROW_NUMBER() OVER (PARTITION BY stg.measurement_id ORDER BY c_std.concept_id DESC) = 1
        """)

        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by
            )
            SELECT 
                'measurement',
                measurement_id,
                measurement_source_value,
                measurement_source_value,
                measurement_concept_id,
                'deterministic_maps_to',
                1.0,
                'N/A',
                'Athena_v5.4',
                'System'
            FROM measurement
            WHERE measurement_concept_id != 0
            AND measurement_id NOT IN (
                SELECT target_id FROM mapping_provenance WHERE target_table = 'measurement'
            )
        """)

        mapped_count = con.execute("SELECT COUNT(*) FROM measurement WHERE measurement_concept_id != 0").fetchone()[0]
        unmapped_count = con.execute("SELECT COUNT(*) FROM measurement WHERE measurement_concept_id = 0").fetchone()[0]
        
    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped (Clean Data): {mapped_count} records")
    print(f" - Sent to AI Fallback Queue (ID 0): {unmapped_count} records")

if __name__ == "__main__":
    run_measurement_etl()