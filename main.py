import sys
import time

# Import all our ETL and Mapping modules
from src.etl.person import run_person_etl
from src.etl.condition import run_condition_etl
from src.mapping.llm_condition import run_semantic_mapping as run_condition_mapping
from src.etl.drug import run_drug_etl
from src.mapping.llm_drug import run_semantic_mapping_drugs
from src.etl.measurement import run_measurement_etl
from src.mapping.llm_measurement import run_semantic_mapping_measurement

def main():
    print("="*60)
    print("🏥 CLINICAL DATA PIPELINE: FHIR TO OMOP CDM")
    print("="*60)
    
    start_time = time.time()

    # 1. Base Demographics (Must run first for Foreign Keys)
    print("\n[PHASE 1: DEMOGRAPHICS]")
    run_person_etl()
    
    # 2. Conditions (Diagnoses)
    print("\n[PHASE 2: CONDITIONS]")
    run_condition_etl()
    run_condition_mapping()
    
    # 3. Medications (Prescriptions)
    print("\n[PHASE 3: MEDICATIONS]")
    run_drug_etl()
    run_semantic_mapping_drugs()
    
    # 4. Measurements (Labs & Vitals)
    print("\n[PHASE 4: MEASUREMENTS & LABS]")
    run_measurement_etl()
    run_semantic_mapping_measurement()
    
    elapsed = (time.time() - start_time) / 60
    print("="*60)
    print(f"✅ FULL PIPELINE EXECUTED SUCCESSFULLY IN {elapsed:.1f} MINUTES.")
    print("="*60)

if __name__ == "__main__":
    main()