"""Fail-closed continuity for deterministic pseudonymization identifiers."""

from __future__ import annotations

import os
import sys
import threading
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
TRANSACTION_RETRY_ATTEMPTS = 3
_KEY_CONTINUITY_LOCK = threading.RLock()


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


def _verify_manifest_identity(existing, settings: RuntimeSettings) -> None:
    expected = (settings.phi_key_version, settings.phi_key_fingerprint)
    if tuple(existing) != expected:
        raise KeyContinuityError(
            "Pseudonymization key identity differs from the published database; "
            "a governed identifier migration is required."
        )


def _ensure_key_continuity_transaction(con, settings, env) -> str:
    """Register or verify the singleton manifest in one ACID transaction."""
    con.execute("BEGIN TRANSACTION")
    try:
        existing = con.execute("""
            SELECT key_version, key_fingerprint
            FROM cmf_pseudonymization_key_manifest
            WHERE singleton_id = 1
        """).fetchone()
        if existing:
            _verify_manifest_identity(existing, settings)
            con.execute("COMMIT")
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

        inserted = con.execute("""
            INSERT INTO cmf_pseudonymization_key_manifest (
                singleton_id, key_version, key_fingerprint, data_classification
            ) VALUES (1, ?, ?, ?)
            ON CONFLICT (singleton_id) DO NOTHING
            RETURNING singleton_id
        """, [
            settings.phi_key_version,
            settings.phi_key_fingerprint,
            settings.data_classification,
        ]).fetchone()

        if not inserted:
            winner = con.execute("""
                SELECT key_version, key_fingerprint
                FROM cmf_pseudonymization_key_manifest
                WHERE singleton_id = 1
            """).fetchone()
            if not winner:
                raise RuntimeError(
                    "Concurrent key registration completed without a visible manifest"
                )
            _verify_manifest_identity(winner, settings)
            outcome = "VERIFIED"
        else:
            outcome = "BOOTSTRAPPED" if populated else "REGISTERED"

        con.execute("COMMIT")
        return outcome
    except Exception:
        con.execute("ROLLBACK")
        raise


def _ensure_key_continuity_locked(
    database_path=DB_PATH,
    settings: RuntimeSettings = SETTINGS,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Run continuity registration while the process-level lock is held."""
    env = os.environ if environ is None else environ
    for attempt in range(TRANSACTION_RETRY_ATTEMPTS):
        try:
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
                return _ensure_key_continuity_transaction(con, settings, env)
        except (duckdb.TransactionException, duckdb.ConstraintException) as exc:
            if (
                isinstance(exc, duckdb.ConstraintException)
                and 'Duplicate key "singleton_id: 1"' not in str(exc)
            ):
                raise
            if attempt + 1 == TRANSACTION_RETRY_ATTEMPTS:
                raise KeyContinuityError(
                    "Concurrent pseudonymization key registration did not "
                    "stabilize; retry the pipeline."
                ) from None
    raise AssertionError("unreachable")


def ensure_key_continuity(
    database_path=DB_PATH,
    settings: RuntimeSettings = SETTINGS,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Register a key identity exactly once or reject an incompatible database."""
    with _KEY_CONTINUITY_LOCK:
        return _ensure_key_continuity_locked(database_path, settings, environ)


def main() -> None:
    outcome = ensure_key_continuity()
    print(f"Pseudonymization key continuity: {outcome}")


if __name__ == "__main__":
    main()
