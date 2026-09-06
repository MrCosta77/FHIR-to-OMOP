"""Fail-closed continuity for deterministic pseudonymization identifiers."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, SETTINGS, RuntimeSettings

CLINICAL_ID_TABLES = (
    "person",
    "visit_occurrence",
    "condition_occurrence",
    "drug_exposure",
    "measurement",
    "observation",
    "procedure_occurrence",
    "device_exposure",
)


class KeyContinuityError(RuntimeError):
    """Raised when a pseudonymization key would change published identities."""


def _has_existing_clinical_ids(con) -> bool:
    existing = {
        row[0]
        for row in con.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'main'
        """).fetchall()
    }
    return any(
        con.execute(f'SELECT EXISTS (SELECT 1 FROM "{table}" LIMIT 1)').fetchone()[0]
        for table in CLINICAL_ID_TABLES
        if table in existing
    )


def ensure_key_continuity(
    database_path=DB_PATH,
    settings: RuntimeSettings = SETTINGS,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Register a new key identity or reject an incompatible existing database."""
    env = os.environ if environ is None else environ
    with duckdb.connect(str(database_path)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS cmf_pseudonymization_key_manifest (
                singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                key_version VARCHAR NOT NULL,
                key_fingerprint VARCHAR NOT NULL,
                data_classification VARCHAR NOT NULL,
                registered_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        existing = con.execute("""
            SELECT key_version, key_fingerprint
            FROM cmf_pseudonymization_key_manifest
            WHERE singleton_id = 1
        """).fetchone()
        if existing:
            expected = (settings.phi_key_version, settings.phi_key_fingerprint)
            if tuple(existing) != expected:
                raise KeyContinuityError(
                    "Pseudonymization key identity differs from the published database; "
                    "a governed identifier migration is required."
                )
            return "VERIFIED"

        populated = _has_existing_clinical_ids(con)
        bootstrap_approved = (
            env.get("CMF_PHI_KEY_BOOTSTRAP_APPROVED", "").strip().casefold()
            == "true"
        )
        if settings.profile == "hospital" and populated and not bootstrap_approved:
            raise KeyContinuityError(
                "Existing hospital data has no pseudonymization key manifest; "
                "institutional bootstrap approval is required."
            )
        con.execute("""
            INSERT INTO cmf_pseudonymization_key_manifest (
                singleton_id, key_version, key_fingerprint, data_classification
            ) VALUES (1, ?, ?, ?)
        """, [
            settings.phi_key_version,
            settings.phi_key_fingerprint,
            settings.data_classification,
        ])
        return "BOOTSTRAPPED" if populated else "REGISTERED"


def main() -> None:
    outcome = ensure_key_continuity()
    print(f"Pseudonymization key continuity: {outcome}")


if __name__ == "__main__":
    main()
