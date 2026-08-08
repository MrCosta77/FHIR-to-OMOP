import os
import json
import duckdb
import random
import sys
from pathlib import Path

# Setup paths based on our new modular architecture
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FHIR_DIR = os.path.join(PROJECT_ROOT, "synthea", "output", "fhir")
DB_PATH = os.path.join(PROJECT_ROOT, "data", "omop_clinical.duckdb")

# Import our reusable hash function
sys.path.append(str(PROJECT_ROOT))
from src.utils.helpers import stable_person_id

def simulate_legacy_lis(loinc_code: str, display_text: str):
    """
    Controlled Chaos: Simulates real-world messy hospital data.
    20% of the time, it strips the LOINC code and slightly alters the text,
    forcing the record into the AI fallback queue.
    """
    if random.random() < 0.20:
        # Erase the code and simulate a messy manual entry
        messy_text = display_text.upper().replace(" IN SERUM OR PLASMA", "").strip()
        return "0", messy_text
    return loinc_code, display_text

def extract_measurements(patient_file):
    """Extracts lab results and vital signs from FHIR bundles."""
    file_path = os.path.join(FHIR_DIR, patient_file)
    with open(file_path, 'r', encoding='utf-8') as f:
        fhir_data = json.load(f)
        
    measurements = []
    
    for entry in fhir_data.get('entry', []):
        resource = entry.get('resource', {})
        
        # Look for Observations (Labs and Vitals)
        if resource.get('resourceType') == 'Observation':
            
            # Extract Person ID
            subject_ref = resource.get('subject', {}).get('reference', '')
            patient_source_id = subject_ref.replace('urn:uuid:', '')
            person_id = stable_person_id(patient_source_id)
            
            # Extract the raw LOINC code and text
            code_block = resource.get('code', {}).get('coding', [{}])[0]
            raw_code = code_block.get('code', '0')
            raw_text = code_block.get('display', 'Unknown')
            
            # 🌪️ INJECT CONTROLLED CHAOS 🌪️
            source_code, source_text = simulate_legacy_lis(raw_code, raw_text)
            
            # Extract the Value and Unit
            value_qty = resource.get('valueQuantity', {})
            value_as_number = value_qty.get('value', None)
            unit_source_value = value_qty.get('unit', None)
            
            # We only want quantitative measurements for this phase
            if value_as_number is not None:
                start_date = resource.get('effectiveDateTime', '1900-01-01')[:10]
                measurements.append((person_id, source_code, source_text, start_date, value_as_number, unit_source_value))
            
    return measurements

def run_measurement_etl():
    """Main execution block for Measurement ETL."""
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP MEASUREMENT) [RWE CHAOS SIMULATION]\n" + "-"*50)
    
    json_files = [f for f in os.listdir(FHIR_DIR) if f.endswith('.json')]
    all_measurements = []

    print("🔍 Extracting laboratory results and injecting real-world noise...")
    for file in json_files:
        all_measurements.extend(extract_measurements(file))

    print(f"📊 Extracted {len(all_measurements)} quantitative measurements.")
    
    try:
        with duckdb.connect(DB_PATH) as con:
            # Staging table
            con.execute("DROP TABLE IF EXISTS stg_measurement")
            con.execute("""
                CREATE TEMPORARY TABLE stg_measurement (
                    person_id BIGINT,
                    loinc_code VARCHAR,
                    measurement_text VARCHAR,
                    start_date DATE,
                    value_as_number DOUBLE,
                    unit_source_value VARCHAR
                )
            """)
            
            con.executemany("INSERT INTO stg_measurement VALUES (?, ?, ?, ?, ?, ?)", all_measurements)
            
            # Create Target Table with proper OMOP fields
            con.execute("""
                CREATE TABLE IF NOT EXISTS measurement (
                    measurement_id BIGINT PRIMARY KEY,
                    person_id BIGINT,
                    measurement_concept_id INTEGER,
                    measurement_date DATE,
                    value_as_number DOUBLE,
                    unit_source_value VARCHAR,
                    measurement_source_value VARCHAR,
                    measurement_source_concept_id INTEGER
                )
            """)
            
            con.execute("DELETE FROM measurement")
            
            # Insertion with Semantic Rigor (LOINC code mapping)
            con.execute("""
                INSERT INTO measurement 
                SELECT 
                    ROW_NUMBER() OVER () AS measurement_id,
                    stg.person_id,
                    COALESCE(c.concept_id, 0) AS measurement_concept_id,
                    stg.start_date AS measurement_date,
                    stg.value_as_number,
                    stg.unit_source_value,
                    stg.measurement_text AS measurement_source_value,
                    0 AS measurement_source_concept_id
                FROM stg_measurement stg
                LEFT JOIN concept c 
                    ON stg.loinc_code = c.concept_code 
                    AND c.vocabulary_id = 'LOINC'
                    AND c.domain_id = 'Measurement'
                    AND c.standard_concept = 'S'
            """)
            
            mapped = con.execute("SELECT COUNT(*) FROM measurement WHERE measurement_concept_id != 0").fetchone()[0]
            unmapped = con.execute("SELECT COUNT(*) FROM measurement WHERE measurement_concept_id = 0").fetchone()[0]
            
            print("\n✅ ETL Complete!")
            print(f" - Successfully mapped (Clean Data): {mapped} records")
            print(f" - Corrupted / Unmapped (AI Queue): {unmapped} records")

    except Exception as e:
        print(f"❌ Database error: {e}")

if __name__ == "__main__":
    run_measurement_etl()