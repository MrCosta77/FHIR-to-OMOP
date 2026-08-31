"""Build, seal, publish and verify privacy-safe immutable ETL run reports."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from src.quality.validate_dqd import DEFAULT_POLICY, validate_dqd_file

REPORT_SCHEMA_VERSION = "cmf-run-report-v1"
REPORT_BUILDER_VERSION = "1.0.0"
CLINICAL_TARGETS = {
    "condition_occurrence": "condition_concept_id",
    "drug_exposure": "drug_concept_id",
    "measurement": "measurement_concept_id",
    "observation": "observation_concept_id",
    "procedure_occurrence": "procedure_concept_id",
    "device_exposure": "device_concept_id",
}
SAFE_RUNTIME_KEYS = {
    "profile", "model_name", "similarity_threshold",
    "data_classification", "simulate_lis_noise", "require_integration",
    "include_dqd",
}
REQUIRED_REPORT_KEYS = {
    "schema_version", "report_builder_version", "generated_at", "run",
    "configuration", "inputs", "pipeline", "quality_gates", "mapping",
    "database", "privacy", "readiness",
}


def canonical_json(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def summarize_inputs(inputs: list[dict]) -> dict:
    return {
        "file_count": len(inputs),
        "total_bytes": sum(int(item.get("size") or 0) for item in inputs),
        "manifest_sha256": hashlib.sha256(canonical_json(inputs)).hexdigest(),
        "individual_paths_included": False,
    }


def parse_junit(path: Path) -> dict:
    path = Path(path)
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    duration = 0.0
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.attrib.get(key, 0))
        duration += float(suite.attrib.get("time", 0.0))
    return {
        "status": "PASSED" if totals["failures"] == totals["errors"] == 0 else "FAILED",
        **totals,
        "duration_seconds": round(duration, 3),
        "junit_sha256": sha256_file(path),
    }


def dqd_evidence(
    *, required: bool, result_path: Path | None, policy_path: Path = DEFAULT_POLICY
) -> dict:
    if not required:
        return {"required": False, "status": "NOT_RUN", "reason": "profile_policy"}
    if result_path is None or not Path(result_path).is_file():
        raise FileNotFoundError("A run-linked DQD result is required but missing.")
    summary = validate_dqd_file(Path(result_path), Path(policy_path))
    return {
        "required": True,
        "status": "PASSED",
        "summary": summary,
        "result_sha256": sha256_file(Path(result_path)),
        "policy_sha256": sha256_file(Path(policy_path)),
    }


def _table_exists(con, table: str) -> bool:
    return bool(con.execute("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = 'main' AND table_name = ?
    """, [table]).fetchone()[0])


def mapping_metrics(con, run_id: str) -> dict:
    domains = {}
    for table, concept_column in CLINICAL_TARGETS.items():
        if not _table_exists(con, table):
            domains[table] = {"status": "MISSING"}
            continue
        total, unresolved = con.execute(f"""
            SELECT COUNT(*), COUNT(*) FILTER (WHERE {concept_column} = 0)
            FROM {table}
        """).fetchone()
        domains[table] = {
            "total_rows": total,
            "mapped_rows": total - unresolved,
            "unresolved_rows": unresolved,
            "coverage": round((total - unresolved) / total, 6) if total else None,
        }

    decisions = []
    if _table_exists(con, "mapping_decision"):
        decisions = [
            {"target_table": row[0], "status": row[1], "count": row[2]}
            for row in con.execute("""
                SELECT target_table, status, COUNT(*)
                FROM mapping_decision WHERE run_id = ?
                GROUP BY target_table, status ORDER BY target_table, status
            """, [run_id]).fetchall()
        ]
    review_count = 0
    if _table_exists(con, "clinical_mapping_review") and _table_exists(con, "mapping_decision"):
        review_count = con.execute("""
            SELECT COUNT(*) FROM clinical_mapping_review r
            JOIN mapping_decision d USING (mapping_decision_id)
            WHERE d.run_id = ? AND COALESCE(r.active, TRUE)
        """, [run_id]).fetchone()[0]
    adjudication_count = 0
    if _table_exists(con, "clinical_mapping_adjudication") and _table_exists(con, "mapping_decision"):
        adjudication_count = con.execute("""
            SELECT COUNT(*) FROM clinical_mapping_adjudication a
            JOIN mapping_decision d USING (mapping_decision_id)
            WHERE d.run_id = ?
        """, [run_id]).fetchone()[0]
    return {
        "domains": domains,
        "decision_counts": decisions,
        "clinical_review_count": review_count,
        "clinical_adjudication_count": adjudication_count,
        "source_values_included": False,
        "reviewer_identities_included": False,
    }


def _safe_steps(steps: list[dict]) -> list[dict]:
    return [
        {
            "name": step.get("name"),
            "script": step.get("script"),
            "status": step.get("status"),
            "duration_seconds": step.get("duration_seconds"),
        }
        for step in steps
    ]


def build_run_report(
    manifest: dict,
    database_path: Path,
    junit_path: Path,
    *,
    dqd_required: bool = False,
    dqd_path: Path | None = None,
    dqd_policy_path: Path = DEFAULT_POLICY,
) -> dict:
    if manifest.get("status") != "SUCCESS":
        raise ValueError("Immutable success reports require a successful run manifest.")
    run_id = str(manifest["run_id"])
    database_path = Path(database_path)
    pytest_result = parse_junit(junit_path)
    if pytest_result["status"] != "PASSED":
        raise ValueError("The pytest evidence does not show a passing quality gate.")
    dqd_result = dqd_evidence(
        required=dqd_required, result_path=dqd_path, policy_path=dqd_policy_path
    )
    with duckdb.connect(str(database_path), read_only=True) as con:
        mapping = mapping_metrics(con, run_id)
    runtime = manifest.get("configuration", {}).get("runtime", {})
    safe_runtime = {key: runtime.get(key) for key in sorted(SAFE_RUNTIME_KEYS)}
    steps = _safe_steps(manifest.get("steps", []))
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_builder_version": REPORT_BUILDER_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "run": {
            "run_id": run_id,
            "status": "SUCCESS",
            "started_at": manifest.get("started_at"),
            "completed_at": manifest.get("completed_at"),
            "git_commit": manifest.get("git_commit"),
        },
        "configuration": safe_runtime,
        "inputs": summarize_inputs(manifest.get("inputs", [])),
        "pipeline": {
            "step_count": len(steps),
            "successful_steps": sum(step["status"] == "SUCCESS" for step in steps),
            "total_duration_seconds": round(sum(
                float(step.get("duration_seconds") or 0.0) for step in steps
            ), 3),
            "steps": steps,
        },
        "quality_gates": {"pytest": pytest_result, "dqd": dqd_result},
        "mapping": mapping,
        "database": {
            "bytes": database_path.stat().st_size,
            "sha256": sha256_file(database_path),
        },
        "privacy": {
            "report_contains_phi": False,
            "report_contains_source_values": False,
            "report_contains_reviewer_identities": False,
        },
        "readiness": {
            "classification": "TECHNICAL_EVIDENCE",
            "deployment_authorized": False,
            "clinical_validation_required": True,
        },
    }


def validate_report_contract(report: dict) -> None:
    missing = REQUIRED_REPORT_KEYS - set(report)
    if missing:
        raise ValueError(f"Run report is missing required sections: {sorted(missing)}")
    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        raise ValueError("Unsupported run report schema version.")
    if not str(report.get("run", {}).get("run_id", "")).startswith("RUN-"):
        raise ValueError("Run report has an invalid run ID.")
    if report["run"].get("status") != "SUCCESS":
        raise ValueError("Immutable publication reports require SUCCESS status.")
    gates = report.get("quality_gates", {})
    if gates.get("pytest", {}).get("status") != "PASSED":
        raise ValueError("Immutable publication reports require a passing pytest gate.")
    dqd = gates.get("dqd", {})
    if dqd.get("status") not in {"PASSED", "NOT_RUN"}:
        raise ValueError("Run report contains an invalid DQD state.")
    if dqd.get("required") and dqd.get("status") != "PASSED":
        raise ValueError("A required DQD gate must pass before report publication.")
    privacy = report.get("privacy", {})
    if any(privacy.get(key) is not False for key in (
        "report_contains_phi", "report_contains_source_values",
        "report_contains_reviewer_identities",
    )):
        raise ValueError("Immutable run reports must remain metadata-only.")
    readiness = report.get("readiness", {})
    if readiness.get("deployment_authorized") is not False:
        raise ValueError("Technical run reports cannot authorize deployment.")
    if readiness.get("clinical_validation_required") is not True:
        raise ValueError("Clinical validation must remain explicitly required.")


def seal_report(report: dict) -> dict:
    validate_report_contract(report)
    sealed = copy.deepcopy(report)
    sealed.pop("integrity", None)
    payload_hash = hashlib.sha256(canonical_json(sealed)).hexdigest()
    sealed["integrity"] = {
        "algorithm": "SHA-256",
        "canonicalization": "sorted-keys-compact-json-without-integrity",
        "payload_sha256": payload_hash,
    }
    return sealed


def verify_report(report: dict) -> bool:
    integrity = report.get("integrity", {})
    expected = integrity.get("payload_sha256")
    payload = copy.deepcopy(report)
    payload.pop("integrity", None)
    actual = hashlib.sha256(canonical_json(payload)).hexdigest()
    return bool(expected) and expected == actual


def stage_immutable_report(report: dict, reports_dir: Path) -> tuple[Path, Path]:
    sealed = seal_report(report)
    digest = sealed["integrity"]["payload_sha256"]
    run_id = sealed["run"]["run_id"]
    reports_dir = Path(reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    destination = reports_dir / f"{run_id}-{digest[:16]}.json"
    if destination.exists():
        raise FileExistsError(f"Immutable run report already exists: {destination}")
    temporary = reports_dir / f".{run_id}-{uuid.uuid4().hex}.tmp"
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(sealed, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return temporary, destination


def publish_staged_report(temporary: Path, destination: Path) -> Path:
    for attempt in range(10):
        try:
            os.link(temporary, destination)
            temporary.unlink()
            return destination
        except PermissionError:
            if attempt == 9:
                raise
            time.sleep(0.2 * (attempt + 1))
    raise RuntimeError("Immutable report publication retries were exhausted.")


def write_immutable_report(report: dict, reports_dir: Path) -> Path:
    temporary, destination = stage_immutable_report(report, reports_dir)
    try:
        return publish_staged_report(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def load_and_verify_report(path: Path) -> dict:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    if report.get("schema_version") != REPORT_SCHEMA_VERSION or not verify_report(report):
        raise ValueError("Run report integrity verification failed.")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = load_and_verify_report(args.report)
    print(
        f"Verified {report['run']['run_id']}: "
        f"{report['integrity']['payload_sha256']}"
    )


if __name__ == "__main__":
    main()
