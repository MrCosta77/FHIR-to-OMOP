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

def extract_drugs(patient_file):
    """
    Reads a FHIR bundle and extracts MedicationRequest records, 
    resolving medicationReferences when inline coding is missing.
    """
    file_path = os.path.join(FHIR_DIR, patient_file)
    with open(file_path, 'r', encoding='utf-8') as f:
        fhir_data = json.load(f)
        
    drugs = []
    
    # PASS 1: Build a dictionary of Medication resources
    medication_dict = {}
    for entry in fhir_data.get('entry', []):
        resource = entry.get('resource', {})
        if resource.get('resourceType') == 'Medication':
            full_url = entry.get('fullUrl', '')
            coding = resource.get('code', {}).get('coding', [])
            if coding:
                medication_dict[full_url] = {
                    'code': coding[0].get('code', '0'),
                    'display': coding[0].get('display', 'Unknown')
                }

    # PASS 2: Extract Prescriptions (MedicationRequests)
    for entry in fhir_data.get('entry', []):
        resource = entry.get('resource', {})
        
        if resource.get('resourceType') == 'MedicationRequest':
            subject_ref = resource.get('subject', {}).get('reference', '')
            patient_source_id = subject_ref.replace('urn:uuid:', '')
            person_id = stable_person_id(patient_source_id)
            
            rxnorm_code = "0"
            drug_text = "Unknown"
            
            medication_cc = resource.get('medicationCodeableConcept', {})
            if medication_cc and medication_cc.get('coding'):
                rxnorm_code = medication_cc['coding'][0].get('code', '0')
                drug_text = medication_cc['coding'][0].get('display', 'Unknown')
                
            elif 'medicationReference' in resource:
                ref_url = resource['medicationReference'].get('reference', '')
                if ref_url in medication_dict:
                    rxnorm_code = medication_dict[ref_url]['code']
                    drug_text = medication_dict[ref_url]['display']
                
            start_date = resource.get('authoredOn', '1900-01-01')[:10] 
            
            drugs.append((person_id, rxnorm_code, drug_text, start_date))
            
    return drugs

def run_drug_etl():
    """Main execution block for Drug ETL."""
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP DRUG) [PRODUCTION]\n" + "-"*50)

    print("🔍 Extracting medications from FHIR JSON files...")
    json_files = [f for f in os.listdir(FHIR_DIR) if f.endswith('.json')]
    all_drugs = []

    for file in json_files:
        all_drugs.extend(extract_drugs(file))

    print(f"📊 Extracted {len(all_drugs)} raw medication records.")
    print("🔌 Connecting to DuckDB for RxNorm vocabulary mapping...")

    try:
        with duckdb.connect(DB_PATH) as con:
            con.execute("DROP TABLE IF EXISTS stg_drug")
            con.execute("""
                CREATE TEMPORARY TABLE stg_drug (
                    person_id BIGINT,
                    rxnorm_code VARCHAR,
                    drug_text VARCHAR,
                    start_date DATE
                )
            """)
            
            con.executemany("INSERT INTO stg_drug VALUES (?, ?, ?, ?)", all_drugs)
            
            con.execute("""
                CREATE TABLE IF NOT EXISTS drug_exposure (
                    drug_exposure_id BIGINT PRIMARY KEY,
                    person_id BIGINT,
                    drug_concept_id INTEGER,
                    drug_exposure_start_date DATE,
                    drug_source_value VARCHAR,
                    drug_source_concept_id INTEGER
                )
            """)
            con.execute("DELETE FROM drug_exposure")
            
            # Insertion with strict OMOP semantic separation
            con.execute("""
                INSERT INTO drug_exposure 
                SELECT 
                    ROW_NUMBER() OVER () AS drug_exposure_id,
                    stg.person_id,
                    COALESCE(c.concept_id, 0) AS drug_concept_id, 
                    stg.start_date AS drug_exposure_start_date,
                    stg.drug_text AS drug_source_value,
                    0 AS drug_source_concept_id 
                FROM stg_drug stg
                LEFT JOIN concept c 
                    ON stg.rxnorm_code = c.concept_code 
                    AND c.vocabulary_id = 'RxNorm'
                    AND c.domain_id = 'Drug'
            """)
            
            mapped = con.execute("SELECT COUNT(*) FROM drug_exposure WHERE drug_concept_id != 0").fetchone()[0]
            unmapped = con.execute("SELECT COUNT(*) FROM drug_exposure WHERE drug_concept_id = 0").fetchone()[0]
            
            print("\n✅ ETL Complete!")
            print(f" - Successfully mapped to OMOP Standards: {mapped} medications")
            print(f" - Failed to map (Unknown/Custom): {unmapped} medications")

    except Exception as e:
        print(f"❌ Database error: {e}")

if __name__ == "__main__":
    run_drug_etl()