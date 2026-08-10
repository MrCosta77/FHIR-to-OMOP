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
    
    # FASE 2: Extração Base (FHIR -> OMOP)
    {"name": "3. Extract Visits", "script": "src/etl/visit.py"},
    {"name": "4. Extract Conditions", "script": "src/etl/condition.py"},
    {"name": "5. Extract Medications", "script": "src/etl/drug.py"},
    {"name": "6. Extract Measurements", "script": "src/etl/measurement.py"},
    {"name": "7. Extract Observations", "script": "src/etl/observation.py"},
    
    # FASE 3: Tabelas Derivadas e Ligações
    {"name": "8. Build Observation Periods", "script": "src/etl/observation_period.py"},
    {"name": "9. Link Events to Visits", "script": "src/etl/link_visits.py"},
    
    # FASE 4: Enriquecimento Semântico com Inteligência Artificial
    {"name": "10. AI Semantic Mapping (Conditions)", "script": "src/mapping/llm_condition.py"}
]

def run_step(step_name, script_path):
    """Executa um script Python individualmente e mede o tempo."""
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
        return True # Retorna True para não quebrar o pipeline inteiro se o ficheiro ainda não existir
        
    try:
        # sys.executable garante que usamos o Python do teu .venv e não o global do sistema
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
        success = run_step(step["name"], step["script"])
        if not success:
            print("\n🛑 PIPELINE HALTED due to errors. Please fix the issue and run again.")
            sys.exit(1)
            
    total_elapsed = time.time() - total_start_time
    minutes = int(total_elapsed // 60)
    seconds = int(total_elapsed % 60)
    
    print(f"\n{'='*70}")
    print(f"🎉 PIPELINE FULLY COMPLETED IN {minutes}m {seconds}s! 🎉")
    print("Database is now clean, linked, domain-routed, and AI-mapped.")
    print(f"{'='*70}\n")

if __name__ == "__main__":
    main()