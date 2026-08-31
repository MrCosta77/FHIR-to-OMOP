from pathlib import Path

import duckdb
import pytest

from src.adapters.hospital_csv import load_hospital_csv
from src.adapters.source_identity import (
    SourceIdentityClaim,
    SourceIdentityError,
    claim_hospital_csv_identity,
    deactivate_source_system,
    register_source_system,
    resolve_source_identity,
    validate_source_registration,
)
from src.security.privacy import PrivacyError

FIXTURE = Path(__file__).parent / "fixtures" / "hospital_csv" / "golden_hospital.csv"


def _register(con, vocabulary="CMF_HOSP_LIS"):
    return register_source_system(
        con,
        "hospital-csv-v1",
        "LIS_LOCAL",
        vocabulary,
        actor="Source Administrator",
        reason="Approved local terminology registration",
    )


def test_golden_csv_claim_resolves_through_one_explicit_registry_entry():
    record = load_hospital_csv(FIXTURE)[0]
    claim = claim_hospital_csv_identity(record)

    assert claim.source_system == "LIS_LOCAL"
    assert claim.source_code == "GLU_BLD"
    assert claim.target_table == "measurement"
    assert len(claim.source_record_key) == 64

    with duckdb.connect(":memory:") as con:
        _register(con)
        resolved = resolve_source_identity(con, claim)

        assert resolved.source_vocabulary_id == "CMF_HOSP_LIS"
        assert con.execute("SELECT COUNT(*) FROM mapping_decision").fetchone()[0] == 0


def test_registration_is_idempotent_atomic_and_forbids_silent_remapping(monkeypatch):
    with duckdb.connect(":memory:") as con:
        first = _register(con)
        second = _register(con)

        assert first == second
        assert con.execute("""
            SELECT COUNT(*) FROM source_identity_registry WHERE active
        """).fetchone()[0] == 1
        with pytest.raises(SourceIdentityError, match="different active vocabulary"):
            _register(con, "CMF_HOSP_LIS2")

    with duckdb.connect(":memory:") as con:
        def fail_audit(*_args, **_kwargs):
            raise RuntimeError("audit unavailable")

        monkeypatch.setattr(
            "src.adapters.source_identity.audit_security_event", fail_audit
        )
        with pytest.raises(RuntimeError, match="audit unavailable"):
            _register(con)
        assert con.execute(
            "SELECT COUNT(*) FROM source_identity_registry"
        ).fetchone()[0] == 0


def test_explicit_deactivation_allows_new_registration_and_preserves_history():
    with duckdb.connect(":memory:") as con:
        _register(con)
        deactivated = deactivate_source_system(
            con,
            "hospital-csv-v1",
            "LIS_LOCAL",
            actor="Source Administrator",
            reason="Terminology migration approved",
        )
        replacement = _register(con, "CMF_HOSP_LIS2")

        assert deactivated.source_vocabulary_id == "CMF_HOSP_LIS"
        assert replacement.source_vocabulary_id == "CMF_HOSP_LIS2"
        assert con.execute("""
            SELECT source_vocabulary_id, active
            FROM source_identity_registry ORDER BY source_vocabulary_id
        """).fetchall() == [
            ("CMF_HOSP_LIS", False),
            ("CMF_HOSP_LIS2", True),
        ]


def test_unregistered_or_ambiguous_identity_fails_closed():
    claim = claim_hospital_csv_identity(load_hospital_csv(FIXTURE)[0])
    with duckdb.connect(":memory:") as con:
        with pytest.raises(SourceIdentityError, match="not registered"):
            resolve_source_identity(con, claim)
        _register(con)
        con.execute("""
            INSERT INTO source_identity_registry (
                source_adapter, source_system, source_vocabulary_id,
                registered_by, registration_reason
            ) VALUES (
                'hospital-csv-v1', 'LIS_LOCAL', 'CMF_HOSP_LIS2',
                'manual-test', 'deliberate ambiguity test'
            )
        """)
        with pytest.raises(SourceIdentityError, match="ambiguous"):
            resolve_source_identity(con, claim)


@pytest.mark.parametrize(
    ("adapter", "system", "vocabulary", "message"),
    [
        ("Hospital CSV", "LIS_LOCAL", "CMF_HOSP_LIS", "canonical adapter"),
        ("hospital-csv-v1", "lis local", "CMF_HOSP_LIS", "uppercase system"),
        ("hospital-csv-v1", "LIS_LOCAL", "CMF_SYNTHEA_LIS", "reserved"),
        ("hospital-csv-v1", "LIS_LOCAL", "CMF_HOSPITAL_VERY_LONG", "at most 20"),
    ],
)
def test_registration_contract_rejects_noncanonical_or_reserved_values(
    adapter, system, vocabulary, message
):
    with pytest.raises(SourceIdentityError, match=message):
        validate_source_registration(adapter, system, vocabulary)


def test_record_claim_requires_source_system_code_and_rejects_identifiers(tmp_path):
    missing = tmp_path / "missing.csv"
    missing.write_text(
        "schema_version,record_id,domain,source_value\n"
        "hospital-csv-v1,row-1,measurement,glucose\n",
        encoding="utf-8",
    )
    with pytest.raises(SourceIdentityError, match="source_system is required"):
        claim_hospital_csv_identity(load_hospital_csv(missing)[0])

    with pytest.raises(SourceIdentityError, match="direct-identifier"):
        SourceIdentityClaim(
            source_adapter="hospital-csv-v1",
            source_system="LIS_LOCAL",
            source_code="MRN: ABC-12345",
            target_table="measurement",
            source_record_key="a" * 64,
        )


def test_phi_registration_requires_authenticated_allowlisted_source_admin():
    base_environment = {
        "CMF_DATA_CLASSIFICATION": "PHI",
        "CMF_PHI_ENABLED": "true",
        "CMF_PHI_POLICY_APPROVED_BY": "Hospital DPO",
        "CMF_PHI_RETENTION_DAYS": "30",
        "CMF_AUTHENTICATED_USER": "Source Administrator",
    }
    with duckdb.connect(":memory:") as con:
        with pytest.raises(PrivacyError, match="not authorized"):
            register_source_system(
                con,
                "hospital-csv-v1",
                "LIS_LOCAL",
                "CMF_HOSP_LIS",
                actor="Source Administrator",
                reason="Approved registration",
                environ=base_environment,
            )

    allowed = {
        **base_environment,
        "CMF_SOURCE_ADMIN_ALLOWLIST": "Source Administrator",
    }
    with duckdb.connect(":memory:") as con:
        registered = register_source_system(
            con,
            "hospital-csv-v1",
            "LIS_LOCAL",
            "CMF_HOSP_LIS",
            actor="Source Administrator",
            reason="Approved registration",
            environ=allowed,
        )
        assert registered.source_vocabulary_id == "CMF_HOSP_LIS"
