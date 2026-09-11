import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH


def mapping_accuracy_metrics(con) -> dict[str, int | float]:
    """Measure against every ground-truth row, including quarantined events."""
    total, mapped, correct = con.execute("""
        SELECT
            COUNT(*) AS total_corrupted,
            SUM(CASE WHEN m.measurement_concept_id != 0 THEN 1 ELSE 0 END)
                AS total_mapped,
            SUM(CASE WHEN m.measurement_concept_id = g.true_concept_id THEN 1 ELSE 0 END)
                AS correct_matches
        FROM lis_noise_ground_truth g
        LEFT JOIN measurement m ON g.measurement_id = m.measurement_id
    """).fetchone()
    total = int(total or 0)
    mapped = int(mapped or 0)
    correct = int(correct or 0)
    return {
        "total_corrupted": total,
        "total_mapped": mapped,
        "correct_matches": correct,
        "coverage": (mapped / total) * 100 if total else 0.0,
        "precision": (correct / mapped) * 100 if mapped else 0.0,
        "recall": (correct / total) * 100 if total else 0.0,
    }


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
            print('$env:CMF_SIMULATE_LIS_NOISE="true"; python main.py')
            return

        metrics = mapping_accuracy_metrics(con)
        total = metrics["total_corrupted"]
        mapped = metrics["total_mapped"]
        correct = metrics["correct_matches"]
        coverage = metrics["coverage"]
        precision = metrics["precision"]
        recall = metrics["recall"]

        print(f"Total Simulated/Corrupted Records: {total}")
        print(f"Total Mapped by AI: {mapped}")
        print(f"Strictly Correct Matches: {correct}\n")

        print("🏆 FINAL PERFORMANCE METRICS:")
        print(f" - Coverage  : {coverage:.2f}% (Proportion of dirty terms the AI attempted to map)")
        print(f" - Precision : {precision:.2f}% (Proportion of AI mappings that were exactly correct)")
        print(f" - Recall    : {recall:.2f}% (Proportion of total corrupted records successfully resolved)\n")

if __name__ == "__main__":
    evaluate_accuracy()
