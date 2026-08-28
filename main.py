"""Fail-closed, run-identified and atomically published ETL orchestrator."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from src.utils.config import SETTINGS, load_settings

PROJECT_ROOT = SETTINGS.project_root


def resolve_runtime_paths(environment=None):
    settings = load_settings(environment)
    return {
        "published_db": settings.db_path,
        "fhir_dir": settings.fhir_dir,
        "runs_dir": settings.runs_dir,
        "manifests_dir": settings.manifests_dir,
    }


_RUNTIME_PATHS = resolve_runtime_paths()
PUBLISHED_DB = _RUNTIME_PATHS["published_db"]
FHIR_INPUT_DIR = _RUNTIME_PATHS["fhir_dir"]
RUNS_DIR = _RUNTIME_PATHS["runs_dir"]
MANIFESTS_DIR = _RUNTIME_PATHS["manifests_dir"]

PIPELINE_STEPS = [
    {"name": "0. Validate External Prerequisites", "script": "src/quality/preflight.py"},
    {"name": "1. Setup Vocabularies", "script": "src/utils/setup_vocab.py"},
    {"name": "2. Setup Audit/Provenance", "script": "src/utils/setup_audit.py"},
    {"name": "2b. Build OMOP DDL Skeleton", "script": "src/utils/setup_cdm_schema.py"},
    {
        "name": "2c. Validate FHIR Input Contract",
        "script": "src/quality/validate_fhir.py",
        "args": [str(FHIR_INPUT_DIR)],
    },
    {"name": "3. Extract Persons", "script": "src/etl/person.py"},
    {"name": "4. Extract Visits", "script": "src/etl/visit.py"},
    {"name": "5. Extract Conditions", "script": "src/etl/condition.py"},
    {"name": "6. Extract Medications", "script": "src/etl/drug.py"},
    {"name": "7. Extract Measurements", "script": "src/etl/measurement.py"},
    {"name": "8. Extract Observations", "script": "src/etl/observation.py"},
    {"name": "9. Extract Procedures", "script": "src/etl/procedure.py"},
    {"name": "10. Link Events to Visits", "script": "src/etl/link_visits.py"},
    {"name": "11. Build Observation Periods", "script": "src/etl/observation_period.py"},
    {"name": "11b. Inject Legacy LIS Noise", "script": "src/simulation/inject_lis_noise.py"},
    {"name": "12. AI Semantic Mapping (Conditions)", "script": "src/mapping/llm_condition.py"},
    {"name": "13. AI Semantic Mapping (Drugs)", "script": "src/mapping/llm_drug.py"},
    {"name": "14. AI Semantic Mapping (Measurements)", "script": "src/mapping/llm_measurement.py"},
    {"name": "14b. AI Semantic Mapping (Procedures)", "script": "src/mapping/llm_procedure.py"},
    {"name": "14c. AI Semantic Mapping (Observations)", "script": "src/mapping/llm_observation.py"},
    {"name": "14d. AI Semantic Mapping (Devices)", "script": "src/mapping/llm_device.py"},
    {"name": "15. Apply Approved Mappings", "script": "src/etl/apply_stcm.py"},
    {"name": "15b. Build Condition and Drug Eras", "script": "src/etl/eras.py"},
    {
        "name": "16. Run Complete Quality Gate",
        "script": "tests",
        "is_pytest": True,
    },
    {"name": "17. Generate RWE Analytics Report", "script": "src/analytics/rwe_cohort_discovery.py"},
]


def utc_now():
    return datetime.now(timezone.utc)


def new_run_id():
    return utc_now().strftime("RUN-%Y%m%dT%H%M%SZ-") + os.urandom(4).hex()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_manifest():
    roots = [
        FHIR_INPUT_DIR,
        SETTINGS.vocab_dir,
    ]
    files = []
    for root in roots:
        if root.is_dir():
            for path in sorted(item for item in root.iterdir() if item.is_file()):
                try:
                    manifest_path = path.relative_to(PROJECT_ROOT)
                except ValueError:
                    manifest_path = path
                files.append({
                    "path": str(manifest_path).replace("\\", "/"),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                })
    return files


def git_commit():
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT,
        check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def write_manifest(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    # OneDrive/antivirus can briefly hold a newly written file on Windows.
    # Preserve atomic replacement while tolerating only transient lock errors.
    for attempt in range(10):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.2 * (attempt + 1))


def persist_run(database_path, manifest):
    import duckdb

    from src.mapping.governance import ensure_governance_tables

    with duckdb.connect(str(database_path)) as con:
        ensure_governance_tables(con)
        con.execute("""
            INSERT INTO etl_run (
                run_id, status, started_at, completed_at, git_commit,
                input_manifest, configuration_manifest, step_manifest,
                error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status,
                completed_at = EXCLUDED.completed_at,
                step_manifest = EXCLUDED.step_manifest,
                error_message = EXCLUDED.error_message
        """, [
            manifest["run_id"], manifest["status"], manifest["started_at"],
            manifest.get("completed_at"), manifest["git_commit"],
            json.dumps(manifest["inputs"], sort_keys=True),
            json.dumps(manifest["configuration"], sort_keys=True),
            json.dumps(manifest["steps"], sort_keys=True),
            manifest.get("error_message"),
        ])


def prepare_staging_database(run_id):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    staging = RUNS_DIR / f"{run_id}.staging.duckdb"
    if staging.exists():
        raise FileExistsError(f"Staging database already exists: {staging}")
    if PUBLISHED_DB.exists():
        shutil.copy2(PUBLISHED_DB, staging)
    return staging


def run_step(step, environment):
    script_path = PROJECT_ROOT / step["script"]
    if not script_path.exists():
        raise FileNotFoundError(f"Required pipeline step not found: {script_path}")
    command = [sys.executable, "-X", "utf8"]
    if step.get("is_pytest"):
        run_id = environment.get("CMF_RUN_ID", "untracked")
        pytest_temp = RUNS_DIR / f"{run_id}.pytest"
        command.extend([
            "-m", "pytest", str(script_path), "-v", "--disable-warnings",
            f"--basetemp={pytest_temp}",
        ])
    else:
        command.append(str(script_path))
    command.extend(step.get("args", []))
    started = time.monotonic()
    subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True)
    return round(time.monotonic() - started, 3)


def main():
    run_id = new_run_id()
    staging_db = prepare_staging_database(run_id)
    manifest_path = MANIFESTS_DIR / f"{run_id}.json"
    started = utc_now()
    manifest = {
        "run_id": run_id,
        "status": "RUNNING",
        "started_at": started.isoformat(),
        "git_commit": git_commit(),
        "inputs": input_manifest(),
        "configuration": {
            "database_publish_path": str(PUBLISHED_DB),
            "python": sys.version,
            "runtime": SETTINGS.manifest(),
        },
        "steps": [],
    }
    environment = os.environ.copy()
    environment["CMF_RUN_ID"] = run_id
    environment["CMF_DB_PATH"] = str(staging_db)
    environment["CMF_REQUIRE_INTEGRATION"] = "1"
    write_manifest(manifest_path, manifest)
    persist_run(staging_db, manifest)

    try:
        for step in PIPELINE_STEPS:
            step_record = {"name": step["name"], "script": step["script"], "status": "RUNNING"}
            manifest["steps"].append(step_record)
            write_manifest(manifest_path, manifest)
            try:
                step_record["duration_seconds"] = run_step(step, environment)
                step_record["status"] = "SUCCESS"
            except Exception as exc:
                step_record["status"] = "FAILED"
                step_record["error"] = str(exc)
                raise
            finally:
                write_manifest(manifest_path, manifest)

        manifest["status"] = "SUCCESS"
        manifest["completed_at"] = utc_now().isoformat()
        persist_run(staging_db, manifest)
        PUBLISHED_DB.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging_db, PUBLISHED_DB)
        write_manifest(manifest_path, manifest)
        elapsed = (utc_now() - started).total_seconds()
        print(f"Pipeline {run_id} published atomically in {elapsed:.1f}s")
    except Exception as exc:
        manifest["status"] = "FAILED"
        manifest["completed_at"] = utc_now().isoformat()
        manifest["error_message"] = str(exc)
        try:
            persist_run(staging_db, manifest)
        finally:
            write_manifest(manifest_path, manifest)
        print(
            f"Pipeline {run_id} failed. The published database was not changed. "
            f"Forensic staging data: {staging_db}",
            file=sys.stderr,
        )
        raise


if __name__ == "__main__":
    main()
