import os
import sys
import duckdb
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

def evaluate_accuracy():
    print("📊 EVALUATING AI MAPPING ACCURACY (RAG + FEW-SHOT)")
    print("-" * 50)

    with duckdb.connect(DB_PATH) as con:
        # Check if noise simulation was run
        try:
            total_corrupted = con.execute("SELECT COUNT(*) FROM lis_noise_ground_truth").fetchone()[0]
            if total_corrupted == 0:
                raise ValueError("Ground truth table is empty.")
        except Exception:
            print("❌ ERROR: No Ground Truth table found.")
            print("Please run the pipeline with noise enabled first:")
            print('$env:SIMULATE_LIS_NOISE="true"; python main.py')
            return

        # Evaluate Precision and Recall by joining STCM-mapped measurements with ground truth
        query = """
            SELECT 
                COUNT(*) as total_corrupted,
                SUM(CASE WHEN m.measurement_concept_id != 0 THEN 1 ELSE 0 END) as total_mapped,
                SUM(CASE WHEN m.measurement_concept_id = g.true_concept_id THEN 1 ELSE 0 END) as correct_matches
            FROM lis_noise_ground_truth g
            JOIN measurement m ON g.measurement_id = m.measurement_id
        """
        
        result = con.execute(query).fetchone()
        total = result[0]
        mapped = result[1]
        correct = result[2]
        
        # Scientific Model Evaluation Formulas
        coverage = (mapped / total) * 100 if total else 0
        precision = (correct / mapped) * 100 if mapped else 0
        recall = (correct / total) * 100 if total else 0
        
        print(f"Total Simulated/Corrupted Records: {total}")
        print(f"Total Mapped by AI: {mapped}")
        print(f"Strictly Correct Matches: {correct}\n")
        
        print("🏆 FINAL PERFORMANCE METRICS:")
        print(f" - Coverage  : {coverage:.2f}% (Proportion of dirty terms the AI attempted to map)")
        print(f" - Precision : {precision:.2f}% (Proportion of AI mappings that were exactly correct)")
        print(f" - Recall    : {recall:.2f}% (Proportion of total corrupted records successfully resolved)\n")

if __name__ == "__main__":
    evaluate_accuracy()