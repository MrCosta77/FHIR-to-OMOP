from types import SimpleNamespace

import duckdb
import pytest

from src.security.key_continuity import KeyContinuityError, ensure_key_continuity


def _settings(*, profile="hospital", version="hospital-v1", fingerprint="abc123"):
    return SimpleNamespace(
        profile=profile,
        phi_key_version=version,
        phi_key_fingerprint=fingerprint,
        data_classification="PHI" if profile == "hospital" else "SYNTHETIC",
    )


def test_new_database_registers_and_then_verifies_key(tmp_path):
    database = tmp_path / "new.duckdb"

    assert ensure_key_continuity(database, _settings(), {}) == "REGISTERED"
    assert ensure_key_continuity(database, _settings(), {}) == "VERIFIED"


def test_existing_key_mismatch_fails_closed(tmp_path):
    database = tmp_path / "mismatch.duckdb"
    ensure_key_continuity(database, _settings(), {})

    with pytest.raises(KeyContinuityError, match="migration is required"):
        ensure_key_continuity(
            database,
            _settings(version="hospital-v2", fingerprint="different"),
            {},
        )


def test_populated_legacy_hospital_database_requires_explicit_bootstrap(tmp_path):
    database = tmp_path / "legacy.duckdb"
    with duckdb.connect(str(database)) as con:
        con.execute("CREATE TABLE person (person_id BIGINT)")
        con.execute("INSERT INTO person VALUES (1)")

    with pytest.raises(KeyContinuityError, match="bootstrap approval"):
        ensure_key_continuity(database, _settings(), {})

    assert ensure_key_continuity(
        database,
        _settings(),
        {"CMF_PHI_KEY_BOOTSTRAP_APPROVED": "true"},
    ) == "BOOTSTRAPPED"


def test_populated_synthetic_database_bootstraps_without_phi_approval(tmp_path):
    database = tmp_path / "synthetic.duckdb"
    with duckdb.connect(str(database)) as con:
        con.execute("CREATE TABLE person (person_id BIGINT)")
        con.execute("INSERT INTO person VALUES (1)")

    assert ensure_key_continuity(
        database, _settings(profile="development"), {}
    ) == "BOOTSTRAPPED"
