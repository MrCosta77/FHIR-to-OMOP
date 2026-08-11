import os
import sys
import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

def evaluate_accuracy():
    print("\n" + "📊"*30)
    print("      AI MAPPING ACCURACY EVALUATION (RAG + FEW-SHOT)")
    print("📊"*30 + "\n")

    with duckdb.connect(DB_PATH) as con:
        # Verifica se a tabela ground_truth existe
        try:
            con.execute("SELECT 1 FROM lis_noise_ground_truth LIMIT 1")
        except duckdb.CatalogException:
            print("❌ ERROR: 'lis_noise_ground_truth' table not found.")
            print("Please run the pipeline with SIMULATE_LIS_NOISE=true first.")
            return

        # 1. Total de exames corrompidos (Baseline)
        total_corrupted = con.execute("SELECT COUNT(*) FROM lis_noise_ground_truth").fetchone()[0]
        
        # 2. Quantos é que a IA tentou mapear (Target ID diferente de 0)
        total_mapped_by_ai = con.execute("""
            SELECT COUNT(*) 
            FROM measurement m
            JOIN lis_noise_ground_truth gt ON m.measurement_id = gt.measurement_id
            WHERE m.measurement_concept_id != 0
        """).fetchone()[0]

        # 3. Quantos é que a IA mapeou CORRETAMENTE (Match exato com a verdade)
        total_correct = con.execute("""
            SELECT COUNT(*) 
            FROM measurement m
            JOIN lis_noise_ground_truth gt ON m.measurement_id = gt.measurement_id
            WHERE m.measurement_concept_id = gt.true_concept_id
        """).fetchone()[0]

        # Prevenção de divisão por zero
        if total_corrupted == 0:
            print("⚠️ Simulation table is empty.")
            return

        coverage = (total_mapped_by_ai / total_corrupted) * 100
        recall = (total_correct / total_corrupted) * 100
        precision = (total_correct / total_mapped_by_ai) * 100 if total_mapped_by_ai > 0 else 0

        print(f"🎯 Total Terms Corrupted (Ground Truth): {total_corrupted}")
        print(f"🤖 Total Terms Mapped by AI: {total_mapped_by_ai}")
        print(f"✅ Total EXACT Matches: {total_correct}\n")
        
        print("-" * 50)
        print(f"📈 COVERAGE  (Mapped / Total Corrupted): {coverage:.1f}%")
        print(f"📈 PRECISION (Correct / Total Mapped):   {precision:.1f}%")
        print(f"📈 RECALL    (Correct / Total Corrupted):{recall:.1f}%")
        print("-" * 50)
        
        print("\n💡 GLOSSARY:")
        print(" - Coverage: How often the AI gave an answer instead of refusing (0).")
        print(" - Precision: When the AI gave an answer, how often was it strictly correct?")
        print(" - Recall: Overall, how much of the broken dataset did we successfully recover?")

if __name__ == "__main__":
    evaluate_accuracy()