import os
import sys
import time
import subprocess
from pathlib import Path

# Ensure the script runs from the project root
PROJECT_ROOT = Path(__file__).resolve().parent

# Exact execution order of the pipeline dependencies
PIPELINE_STEPS = [
    # --- PHASE 1: Initialization & Vocabularies ---
    {"name": "1. Setup Vocabularies", "script": "src/utils/setup_vocab.py"},
    {"name": "2. Setup Audit/Provenance", "script": "src/utils/setup_audit.py"},
    {"name": "2b. Build OMOP DDL Skeleton", "script": "src/utils/setup_cdm_schema.py"},
    
    # --- PHASE 2: Base Extraction (FHIR -> OMOP) ---
    {"name": "3. Extract Persons", "script": "src/etl/person.py"},
    {"name": "4. Extract Visits", "script": "src/etl/visit.py"},
    {"name": "5. Extract Conditions", "script": "src/etl/condition.py"},
    {"name": "6. Extract Medications", "script": "src/etl/drug.py"},
    {"name": "7. Extract Measurements", "script": "src/etl/measurement.py"},
    {"name": "8. Extract Observations", "script": "src/etl/observation.py"},
    {"name": "9. Extract Procedures", "script": "src/etl/procedure.py"},
    
    # --- PHASE 3: Derived Tables, Linkage & Simulation ---
    {"name": "10. Build Observation Periods", "script": "src/etl/observation_period.py"},
    {"name": "11. Link Events to Visits", "script": "src/etl/link_visits.py"},
    {"name": "11b. Inject Legacy LIS Noise", "script": "src/simulation/inject_lis_noise.py"},

    # --- PHASE 4: AI Semantic Mapping ---
    {"name": "12. AI Semantic Mapping (Conditions)", "script": "src/mapping/llm_condition.py"},
    {"name": "13. AI Semantic Mapping (Drugs)", "script": "src/mapping/llm_drug.py"},
    {"name": "14. AI Semantic Mapping (Measurements)", "script": "src/mapping/llm_measurement.py"},
        
    # --- PHASE 5: Apply AI Mappings ---
    {"name": "15. Apply STCM Mappings", "script": "src/etl/apply_stcm.py"},
        
    # --- PHASE 6: Validation & Analytics ---
    {"name": "16. Run Quality Gate (Tests)", "script": "tests/test_data_quality.py", "is_pytest": True},
    {"name": "17. Generate RWE Analytics Report", "script": "src/analytics/rwe_cohort_discovery.py"}
] # <-- THE MISSING BRACKET WAS HERE!

def run_step(step):
    """Executes a Python script individually and measures execution time."""
    step_name = step["name"]
    script_path = step["script"]
    
    print(f"\n{'='*70}")
    print(f"🚀 RUNNING: {step_name}")
    print(f"📄 Script:  {script_path}")
    print(f"{'='*70}\n")
    
    start_time = time.time()
    
    # Check if the file exists before attempting to run it
    full_path = os.path.join(PROJECT_ROOT, script_path)
    if not os.path.exists(full_path):
        print(f"❌ ERROR: File not found -> {full_path}")
        print("⏭️ Skipping this step (check your filenames if this was unexpected).")
        return False 
        
    try:
        # sys.executable ensures we use the isolated .venv Python, not the global system one
        if step.get("is_pytest"):
            subprocess.run([sys.executable, "-X", "utf8", "-m", "pytest", script_path, "-v", "--disable-warnings"], cwd=PROJECT_ROOT, check=True)
        else:
            subprocess.run([sys.executable, "-X", "utf8", script_path], cwd=PROJECT_ROOT, check=True)
        
        elapsed = time.time() - start_time
        print(f"\n✅ SUCCESS: '{step_name}' completed in {elapsed:.1f} seconds.")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ CRITICAL ERROR: Pipeline failed at '{step_name}' (Exit Code: {e.returncode}).")
        return False

def main():
    print("\n" + "🏥"*30)
    print("      CLINICAL MAPPING FRAMEWORK - FULL ORCHESTRATOR")
    print("🏥"*30 + "\n")
    
    total_start_time = time.time()
    
    for step in PIPELINE_STEPS:
        success = run_step(step)
        if not success:
            print("\n🛑 PIPELINE HALTED due to errors. Please fix the issue and run again.")
            sys.exit(1)
            
    total_elapsed = time.time() - total_start_time
    minutes = int(total_elapsed // 60)
    seconds = int(total_elapsed % 60)
    
    print(f"\n{'='*70}")
    print(f"🎉 PIPELINE FULLY COMPLETED IN {minutes}m {seconds}s! 🎉")
    print(
        "Database is clean, linked, domain-routed, and tested; "
        "LLM candidates remain gated until human approval."
    )
    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()
