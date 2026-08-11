import os
import sys
import time
import subprocess
from pathlib import Path

# Garante que o script corre a partir da raiz do projeto
PROJECT_ROOT = Path(__file__).resolve().parent

# A ordem exata de dependências do pipeline
PIPELINE_STEPS = [
    # FASE 1: Inicialização e Vocabulários
    {"name": "1. Setup Vocabularies", "script": "src/utils/setup_vocab.py"},
    {"name": "2. Setup Audit/Provenance", "script": "src/utils/setup_audit.py"},
    {"name": "2b. Build OMOP DDL Skeleton", "script": "src/utils/setup_cdm_schema.py"},
    
    # FASE 2: Extração Base (FHIR -> OMOP)
    {"name": "3. Extract Persons", "script": "src/etl/person.py"},
    {"name": "4. Extract Visits", "script": "src/etl/visit.py"},
    {"name": "5. Extract Conditions", "script": "src/etl/condition.py"},
    {"name": "6. Extract Medications", "script": "src/etl/drug.py"},
    {"name": "7. Extract Measurements", "script": "src/etl/measurement.py"},
    {"name": "8. Extract Observations", "script": "src/etl/observation.py"},
    {"name": "9. Extract Procedures", "script": "src/etl/procedure.py"},
    
    # FASE 3: Tabelas Derivadas, Ligações e Simulação
    {"name": "10. Build Observation Periods", "script": "src/etl/observation_period.py"},
    {"name": "11. Link Events to Visits", "script": "src/etl/link_visits.py"},
    {"name": "11b. Inject Legacy LIS Noise", "script": "src/simulation/inject_lis_noise.py"},
    
    # FASE 4: Enriquecimento Semântico com Inteligência Artificial
    {"name": "12. AI Semantic Mapping (Conditions)", "script": "src/mapping/llm_condition.py"},
    {"name": "13. AI Semantic Mapping (Drugs)", "script": "src/mapping/llm_drug.py"},
    {"name": "14. AI Semantic Mapping (Measurements)", "script": "src/mapping/llm_measurement.py"},
    
    # FASE 5: Controlo de Qualidade e Analytics
    {"name": "15. Run Quality Gate (Tests)", "script": "tests/test_data_quality.py", "is_pytest": True},
    {"name": "16. Generate RWE Analytics Report", "script": "src/analytics/rwe_cohort_discovery.py"}
]

def run_step(step):
    """Executa um script Python individualmente e mede o tempo."""
    step_name = step["name"]
    script_path = step["script"]
    
    print(f"\n{'='*70}")
    print(f"🚀 RUNNING: {step_name}")
    print(f"📄 Script:  {script_path}")
    print(f"{'='*70}\n")
    
    start_time = time.time()
    
    # Verifica se o ficheiro existe antes de tentar correr
    full_path = os.path.join(PROJECT_ROOT, script_path)
    if not os.path.exists(full_path):
        print(f"❌ ERROR: File not found -> {full_path}")
        print("⏭️ Skipping this step (check your filenames if this was unexpected).")
        return False 
        
    try:
        # sys.executable garante que usamos o Python do teu .venv e não o global do sistema
        if step.get("is_pytest"):
            subprocess.run([sys.executable, "-m", "pytest", script_path, "-v", "--disable-warnings"], cwd=PROJECT_ROOT, check=True)
        else:
            subprocess.run([sys.executable, script_path], cwd=PROJECT_ROOT, check=True)
        
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
    print("Database is now clean, linked, domain-routed, AI-mapped, and tested.")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()