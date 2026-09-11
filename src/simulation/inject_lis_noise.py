import random
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.mapping.governance import current_run_id
from src.utils.config import DB_PATH, LIS_NOISE_RATIO, SIMULATE_LIS_NOISE


def _fallback_run_id() -> str:
    return datetime.now(UTC).strftime("SIM-%Y%m%dT%H%M%SZ")


def _ensure_ground_truth_history(con) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS lis_noise_ground_truth_history (
            simulation_run_id VARCHAR NOT NULL,
            measurement_id BIGINT NOT NULL,
            true_concept_id INTEGER,
            true_source_value VARCHAR,
            corrupted_source_value VARCHAR,
            archived_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (simulation_run_id, measurement_id)
        )
    """)


def _archive_current_ground_truth(con) -> None:
    exists = con.execute("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = 'lis_noise_ground_truth'
    """).fetchone()[0]
    if not exists:
        return
    has_run_id = con.execute("""
        SELECT COUNT(*) FROM information_schema.columns
        WHERE table_schema = 'main' AND table_name = 'lis_noise_ground_truth'
          AND column_name = 'simulation_run_id'
    """).fetchone()[0]
    if has_run_id:
        con.execute("""
            INSERT INTO lis_noise_ground_truth_history (
                simulation_run_id, measurement_id, true_concept_id,
                true_source_value, corrupted_source_value
            )
            SELECT simulation_run_id, measurement_id, true_concept_id,
                   true_source_value, corrupted_source_value
            FROM lis_noise_ground_truth
            ON CONFLICT (simulation_run_id, measurement_id) DO NOTHING
        """)
        return
    has_etl_run = con.execute("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = 'etl_run'
    """).fetchone()[0]
    previous = None
    if has_etl_run:
        previous = con.execute("""
            SELECT run_id FROM etl_run
            WHERE status = 'SUCCESS'
            ORDER BY completed_at DESC NULLS LAST, started_at DESC
            LIMIT 1
        """).fetchone()
    legacy_run_id = previous[0] if previous else "LEGACY-UNTRACKED"
    con.execute("""
        INSERT INTO lis_noise_ground_truth_history (
            simulation_run_id, measurement_id, true_concept_id,
            true_source_value, corrupted_source_value
        )
        SELECT ?, measurement_id, true_concept_id,
               true_source_value, corrupted_source_value
        FROM lis_noise_ground_truth
        ON CONFLICT (simulation_run_id, measurement_id) DO NOTHING
    """, [legacy_run_id])


def inject_lis_noise(con, *, run_id: str, noise_ratio: float) -> int:
    """Replace the current synthetic snapshot while preserving prior evidence."""
    run_id = str(run_id or "").strip()
    if not run_id:
        raise ValueError("A simulation run ID is required.")
    if not 0.0 <= float(noise_ratio) <= 1.0:
        raise ValueError("LIS noise ratio must be between 0 and 1.")
    generator = random.Random(42)
    total_measurements = con.execute(
        "SELECT COUNT(*) FROM measurement WHERE measurement_concept_id != 0"
    ).fetchone()[0]
    limit = int(total_measurements * float(noise_ratio))
    if limit == 0:
        return 0
    candidates = con.execute("""
        SELECT measurement_id, measurement_concept_id, measurement_source_value
        FROM measurement
        WHERE measurement_concept_id != 0
        ORDER BY measurement_id
        LIMIT ?
    """, [limit]).fetchall()

    noise_map = {
        "Glucose [Mass/volume] in Blood": [
            "Glu (Blood)", "GLUCOSE RANDOM", "Blood sugar lvl", "GLUC-B",
        ],
        "Hemoglobin [Mass/volume] in Blood": [
            "HGB", "Hb blood test", "Haemoglobin", "HB",
        ],
        "Leukocytes [#/volume] in Blood by Automated count": [
            "WBC count", "White blood cells", "Leukocytes Auto", "WBC",
        ],
        "Erythrocytes [#/volume] in Blood by Automated count": [
            "RBC count", "Red blood cells", "RBC",
        ],
        "Cholesterol [Mass/volume] in Serum or Plasma": [
            "CHOL", "Cholesterol total", "Lipids: Chol",
        ],
        "Triglycerides [Mass/volume] in Serum or Plasma": [
            "TRIG", "Triglycerides", "TG",
        ],
        "Creatinine [Mass/volume] in Serum or Plasma": [
            "CREA", "Creatinine serum", "Creat",
        ],
    }
    updates = []
    ground_truth = []
    for measurement_id, true_concept, true_text in candidates:
        clean_text = true_text.split("(")[0].strip() if true_text else "Lab"
        if clean_text in noise_map:
            messy_text = generator.choice(noise_map[clean_text])
        else:
            messy_text = (
                clean_text.replace("[Mass/volume]", "")
                .replace("in Blood", "")
                .strip()
                .upper()
                + " (LEGACY)"
            )
        ground_truth.append(
            (run_id, measurement_id, true_concept, true_text, messy_text)
        )
        updates.append((messy_text, measurement_id))

    con.execute("BEGIN TRANSACTION")
    try:
        _ensure_ground_truth_history(con)
        _archive_current_ground_truth(con)
        con.execute("DROP TABLE IF EXISTS lis_noise_ground_truth")
        con.execute("""
            CREATE TABLE lis_noise_ground_truth (
                simulation_run_id VARCHAR NOT NULL,
                measurement_id BIGINT PRIMARY KEY,
                true_concept_id INTEGER,
                true_source_value VARCHAR,
                corrupted_source_value VARCHAR
            )
        """)
        con.executemany(
            "INSERT INTO lis_noise_ground_truth VALUES (?, ?, ?, ?, ?)",
            ground_truth,
        )
        con.executemany("""
            UPDATE measurement
            SET measurement_concept_id = 0,
                measurement_source_concept_id = 0,
                measurement_source_value = ?
            WHERE measurement_id = ?
        """, updates)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return len(updates)


def run_noise_injection():
    # 1. O Portão de Segurança (Só corre se o utilizador pedir explicitamente)
    if not SIMULATE_LIS_NOISE:
        print("🧪 LIS NOISE SIMULATION: Disabled.")
        print("⏭️  Skipping noise injection. Set CMF_SIMULATE_LIS_NOISE=true to enable it.")
        return

    print("🧪 STARTING SIMULATION: INJECTING LEGACY LIS NOISE")
    print("-" * 50)

    with duckdb.connect(DB_PATH) as con:
        count = inject_lis_noise(
            con,
            run_id=current_run_id() or _fallback_run_id(),
            noise_ratio=LIS_NOISE_RATIO,
        )
        if count == 0:
            print("⚠️ No valid measurements found to corrupt.")
            return
        print(f"✅ Injected legacy laboratory noise into {count} measurement records.")
        print("✅ Ground truth strictly saved to 'lis_noise_ground_truth' table.")

if __name__ == "__main__":
    run_noise_injection()
