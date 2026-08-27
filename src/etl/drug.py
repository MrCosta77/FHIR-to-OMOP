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
from src.omop.cdm54 import create_table_sql
from src.mapping.governance import current_run_id

def generate_drug_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_drugs(file_path):
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        bundle = json.load(f)
        
        if bundle.get('resourceType') != 'Bundle':
            return records

        # Pass 1: Build a dictionary of Medication resources (UUID -> RxNorm details)
        medications = {}
        for entry in bundle.get('entry', []):
            res = entry.get('resource', {})
            if res.get('resourceType') == 'Medication':
                med_id = "urn:uuid:" + res.get('id', '')
                code = None
                display = "Unknown"
                codings = res.get('code', {}).get('coding', [])
                for c in codings:
                    if c.get('system') == 'http://www.nlm.nih.gov/research/umls/rxnorm':
                        code = c.get('code')
                        display = c.get('display', '')
                        break
                if not code and codings:
                    code = codings[0].get('code')
                    display = codings[0].get('display', '')
                
                if code:
                    medications[med_id] = {'code': code, 'display': display}

        # Pass 2: Process MedicationRequests
        for entry in bundle.get('entry', []):
            res = entry.get('resource', {})
            if res.get('resourceType') == 'MedicationRequest':
                patient_ref = res.get('subject', {}).get('reference', '')
                person_id = stable_person_id(patient_ref)
                if not person_id:
                    continue
                
                code = None
                display = "Unknown"
                
                # Try inline coding first
                med_cc = res.get('medicationCodeableConcept', {})
                codings = med_cc.get('coding', [])
                for c in codings:
                    if c.get('system') == 'http://www.nlm.nih.gov/research/umls/rxnorm':
                        code = c.get('code')
                        display = c.get('display', '')
                        break
                        
                # If not inline, use two-pass medicationReference lookup
                if not code:
                    med_ref = res.get('medicationReference', {}).get('reference', '')
                    if med_ref in medications:
                        code = medications[med_ref]['code']
                        display = medications[med_ref]['display']
                        
                if not code:
                    continue
                    
                start_date = res.get('authoredOn', '')[:10]
                if not start_date:
                    continue

                # A forma mais segura de identificar um recurso num Bundle FHIR é o seu 'fullUrl'
                full_url = entry.get('fullUrl', '')
                if full_url:
                    base_string = full_url
                else:
                    # Fallback à prova de bala: transformar o recurso inteiro numa string e fazer o hash
                    base_string = json.dumps(res, sort_keys=True)
                    
                drug_id = generate_drug_id(base_string)
                
                records.append((
                    drug_id,
                    person_id,
                    code,
                    display,
                    start_date
                ))
                
    return records

def run_drug_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP DRUG) [PRODUCTION]")
    print("-" * 50)
    
    print("🔍 Extracting medications from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))
    
    all_records = []
    for f in fhir_files:
        all_records.extend(extract_drugs(f))
        
    print(f"📊 Extracted {len(all_records)} raw medication records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")
    
    with duckdb.connect(DB_PATH) as con:
        con.execute("DROP TABLE IF EXISTS drug_exposure")
        
        con.execute(create_table_sql("drug_exposure"))
        
        con.execute("DROP TABLE IF EXISTS stg_drug")
        con.execute("""
            CREATE TEMPORARY TABLE stg_drug (
                drug_exposure_id BIGINT,
                person_id BIGINT,
                rxnorm_code VARCHAR,
                display_text VARCHAR,
                start_date DATE
            )
        """)
        
        con.executemany("INSERT INTO stg_drug VALUES (?, ?, ?, ?, ?)", all_records)
        
        con.execute("""
            INSERT INTO drug_exposure (
                drug_exposure_id, person_id, drug_concept_id,
                drug_exposure_start_date, drug_exposure_start_datetime,
                drug_exposure_end_date, drug_exposure_end_datetime,
                drug_type_concept_id, drug_source_value, drug_source_concept_id
            )
            SELECT 
                stg.drug_exposure_id,
                stg.person_id,
                CASE 
                    WHEN c_std.domain_id = 'Drug' THEN COALESCE(c_std.concept_id::INTEGER, 0)
                    ELSE 0 
                END AS drug_concept_id,
                stg.start_date,
                stg.start_date::TIMESTAMP,
                stg.start_date,
                stg.start_date::TIMESTAMP,
                32817 AS drug_type_concept_id,
                stg.display_text AS drug_source_value,
                COALESCE(c_src.concept_id::INTEGER, 0) AS drug_source_concept_id
            FROM stg_drug stg
            LEFT JOIN concept c_src 
                ON stg.rxnorm_code = c_src.concept_code 
                AND c_src.vocabulary_id = 'RxNorm'
                AND c_src.invalid_reason IS NULL -- Evita duplicados de conceitos descontinuados
            LEFT JOIN concept_relationship cr 
                ON c_src.concept_id = cr.concept_id_1 
                AND cr.relationship_id = 'Maps to'
                AND cr.invalid_reason IS NULL
            LEFT JOIN concept c_std 
                ON cr.concept_id_2 = c_std.concept_id 
                AND c_std.standard_concept = 'S'
                AND c_std.invalid_reason IS NULL
            -- QUALIFY garante que, mesmo que haja múltiplos mapeamentos, só levamos 1 linha por ID
            QUALIFY ROW_NUMBER() OVER (PARTITION BY stg.drug_exposure_id ORDER BY c_std.concept_id DESC) = 1
        """)

        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by, run_id
            )
            SELECT 
                'drug_exposure',
                drug_exposure_id,
                drug_source_value,
                drug_source_value,
                drug_concept_id,
                'deterministic_maps_to',
                1.0,
                'N/A',
                'Athena_v5.4',
                'System',
                ?
            FROM drug_exposure
            WHERE drug_concept_id != 0
            AND NOT EXISTS (
                SELECT 1 FROM mapping_provenance p
                WHERE p.target_table = 'drug_exposure'
                  AND p.target_id = drug_exposure.drug_exposure_id
                  AND p.mapping_method = 'deterministic_maps_to'
                  AND COALESCE(p.run_id, '') = COALESCE(?, '')
            )
        """, [current_run_id(), current_run_id()])
        
        mapped_count = con.execute("SELECT COUNT(*) FROM drug_exposure WHERE drug_concept_id != 0").fetchone()[0]
        unmapped_count = con.execute("SELECT COUNT(*) FROM drug_exposure WHERE drug_concept_id = 0").fetchone()[0]
        
    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped (OMOP Standard): {mapped_count} medications")
    print(f" - Sent to AI Fallback Queue (ID 0): {unmapped_count} medications")

if __name__ == "__main__":
    run_drug_etl()
