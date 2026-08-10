import os
import sys
import duckdb
import random
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

def run_noise_injection():
    print("🧪 STARTING SIMULATION: INJECTING LEGACY LIS NOISE")
    print("-" * 50)
    
    # 1. Garantir reprodutibilidade (o mesmo ruído sempre)
    random.seed(42)

    with duckdb.connect(DB_PATH) as con:
        # 2. Criar a Tabela de "Ground Truth" (A Verdade Absoluta)
        con.execute("DROP TABLE IF EXISTS lis_noise_ground_truth")
        con.execute("""
            CREATE TABLE lis_noise_ground_truth (
                measurement_id BIGINT PRIMARY KEY,
                true_concept_id INTEGER,
                true_source_value VARCHAR,
                corrupted_source_value VARCHAR
            )
        """)

        # 3. Escolher 10% das medições válidas para corromper
        total_measurements = con.execute("SELECT COUNT(*) FROM measurement WHERE measurement_concept_id != 0").fetchone()[0]
        limit = int(total_measurements * 0.10)
        
        if limit == 0:
            print("⚠️ No valid measurements found to corrupt.")
            return

        candidates = con.execute(f"""
            SELECT measurement_id, measurement_concept_id, measurement_source_value
            FROM measurement
            WHERE measurement_concept_id != 0
            LIMIT {limit}
        """).fetchall()

        # 4. Dicionário de corrupções laboratoriais comuns (abreviaturas, falta de unidades)
        noise_map = {
            "Glucose [Mass/volume] in Blood": ["Glu (Blood)", "GLUCOSE RANDOM", "Blood sugar lvl", "GLUC-B"],
            "Hemoglobin [Mass/volume] in Blood": ["HGB", "Hb blood test", "Haemoglobin", "HB"],
            "Leukocytes [#/volume] in Blood by Automated count": ["WBC count", "White blood cells", "Leukocytes Auto", "WBC"],
            "Erythrocytes [#/volume] in Blood by Automated count": ["RBC count", "Red blood cells", "RBC"],
            "Cholesterol [Mass/volume] in Serum or Plasma": ["CHOL", "Cholesterol total", "Lipids: Chol"],
            "Triglycerides [Mass/volume] in Serum or Plasma": ["TRIG", "Triglycerides", "TG"],
            "Creatinine [Mass/volume] in Serum or Plasma": ["CREA", "Creatinine serum", "Creat"],
        }

        updates = []
        ground_truth = []

        for row in candidates:
            m_id, true_concept, true_text = row
            
            # Se conhecermos o teste, usamos o nosso dicionário; senão, criamos um erro genérico
            clean_text = true_text.split('(')[0].strip() if true_text else "Lab"
            if clean_text in noise_map:
                messy_text = random.choice(noise_map[clean_text])
            else:
                # Exemplo: "Urea nitrogen [Mass/volume] in Blood" -> "UREA NITROGEN (LEGACY)"
                messy_text = clean_text.replace('[Mass/volume]', '').replace('in Blood', '').strip().upper() + " (LEGACY)"

            ground_truth.append((m_id, true_concept, true_text, messy_text))
            updates.append((messy_text, m_id))

        # 5. Guardar a Verdade
        con.executemany("""
            INSERT INTO lis_noise_ground_truth VALUES (?, ?, ?, ?)
        """, ground_truth)

        # 6. Corromper a base de dados principal (forçar a IA a trabalhar)
        con.executemany("""
            UPDATE measurement
            SET measurement_concept_id = 0,
                measurement_source_concept_id = 0,
                measurement_source_value = ?
            WHERE measurement_id = ?
        """, updates)

        print(f"✅ Injected legacy laboratory noise into {len(updates)} measurement records.")
        print("✅ Ground truth strictly saved to 'lis_noise_ground_truth' table.")

if __name__ == "__main__":
    run_noise_injection()