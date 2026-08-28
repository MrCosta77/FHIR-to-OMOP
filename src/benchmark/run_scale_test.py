"""Generate an isolated Synthea population and run the complete OMOP pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYNTHEA_ROOT = PROJECT_ROOT / "synthea"
DEFAULT_ROOT = PROJECT_ROOT / "benchmark_results" / "scale"
CLINICAL_TABLES = (
    "person", "visit_occurrence", "condition_occurrence", "drug_exposure",
    "measurement", "observation", "procedure_occurrence", "device_exposure",
)


def _git_commit():
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def synthea_command(output_root: Path, population: int, seed: int) -> list[str]:
    if population <= 0:
        raise ValueError("Population must be positive.")
    launcher = SYNTHEA_ROOT / "run_synthea.bat"
    return [
        os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(launcher),
        "-s", str(seed), "-p", str(population),
        f"--exporter.baseDirectory={output_root.resolve().as_posix()}",
        "--exporter.fhir.export=true",
        "--exporter.csv.export=false",
        "--exporter.hospital.fhir.export=true",
        "--exporter.practitioner.fhir.export=true",
    ]


def _run_logged(command, cwd, log_path, *, env=None, timeout=7200):
    started = time.perf_counter()
    completed = subprocess.run(
        command, cwd=cwd, env=env, text=True, capture_output=True,
        timeout=timeout,
    )
    elapsed = time.perf_counter() - started
    log_path.write_text(
        completed.stdout + "\n--- STDERR ---\n" + completed.stderr,
        encoding="utf-8",
    )
    if completed.returncode:
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}; see {log_path}"
        )
    return elapsed


def run_scale_test(output_root: Path, population: int, seed: int) -> dict:
    output_root = output_root.resolve()
    if output_root.exists():
        raise ValueError(f"Scale output already exists; refusing overwrite: {output_root}")
    output_root.mkdir(parents=True)
    generated_root = output_root / "synthea-output"
    database = output_root / "omop_scale.duckdb"
    generated_log = output_root / "synthea.log"
    pipeline_log = output_root / "pipeline.log"
    git_commit = _git_commit()

    generation_seconds = _run_logged(
        synthea_command(generated_root, population, seed),
        SYNTHEA_ROOT,
        generated_log,
    )
    fhir_dir = generated_root / "fhir"
    bundles = sorted(fhir_dir.glob("*.json"))
    if not bundles:
        raise RuntimeError("Synthea produced no FHIR bundles.")

    environment = os.environ.copy()
    environment.update({
        "CMF_FHIR_DIR": str(fhir_dir),
        "CMF_DB_PATH": str(database),
        "CMF_RUNS_DIR": str(output_root / "runs"),
        "CMF_MANIFESTS_DIR": str(output_root / "manifests"),
        "CMF_DATA_CLASSIFICATION": "SYNTHETIC",
    })
    pipeline_seconds = _run_logged(
        [sys.executable, "main.py"], PROJECT_ROOT, pipeline_log,
        env=environment,
    )

    with duckdb.connect(str(database), read_only=True) as con:
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in CLINICAL_TABLES
        }
        run = con.execute("""
            SELECT run_id, status, git_commit, completed_at
            FROM etl_run ORDER BY completed_at DESC LIMIT 1
        """).fetchone()
        unresolved = {
            "condition_occurrence": con.execute(
                "SELECT COUNT(*) FROM condition_occurrence WHERE condition_concept_id = 0"
            ).fetchone()[0],
            "procedure_occurrence": con.execute(
                "SELECT COUNT(*) FROM procedure_occurrence WHERE procedure_concept_id = 0"
            ).fetchone()[0],
            "observation": con.execute(
                "SELECT COUNT(*) FROM observation WHERE observation_concept_id = 0"
            ).fetchone()[0],
            "device_exposure": con.execute(
                "SELECT COUNT(*) FROM device_exposure WHERE device_concept_id = 0"
            ).fetchone()[0],
        }

    total_seconds = generation_seconds + pipeline_seconds
    return {
        "benchmark": "synthea-isolated-scale-test",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {"requested_population": population, "seed": seed},
        "provenance": {
            "git_commit": git_commit,
            "python": sys.version,
            "database": str(database),
            "fhir_directory": str(fhir_dir),
        },
        "execution": {
            "generation_seconds": generation_seconds,
            "pipeline_seconds": pipeline_seconds,
            "total_seconds": total_seconds,
            "patients_per_pipeline_second": counts["person"] / pipeline_seconds,
            "database_bytes": database.stat().st_size,
            "fhir_bundle_count": len(bundles),
            "synthea_log_sha256": hashlib.sha256(generated_log.read_bytes()).hexdigest(),
            "pipeline_log_sha256": hashlib.sha256(pipeline_log.read_bytes()).hexdigest(),
        },
        "etl_run": {
            "run_id": run[0], "status": run[1], "git_commit": run[2],
            "completed_at": str(run[3]),
        },
        "table_counts": counts,
        "unresolved": unresolved,
        "published_database_untouched": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", type=int, default=250)
    parser.add_argument("--seed", type=int, default=6062026)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_root = args.output_root or DEFAULT_ROOT / f"p{args.population}-{stamp}"
    report = run_scale_test(output_root, args.population, args.seed)
    report_path = output_root / "scale_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {report_path}")
    print(
        f"patients={report['table_counts']['person']}; "
        f"pipeline_seconds={report['execution']['pipeline_seconds']:.1f}; "
        f"status={report['etl_run']['status']}"
    )


if __name__ == "__main__":
    main()
