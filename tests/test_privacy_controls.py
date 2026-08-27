import json

import duckdb
import pytest

from src.security.privacy import (
    PrivacyError,
    assert_local_llm_endpoint,
    audit_security_event,
    authorize_actor,
    redact_direct_identifiers,
    validate_privacy_runtime,
)


def test_llm_endpoint_is_strictly_loopback_only():
    assert assert_local_llm_endpoint("http://localhost:11434/api/generate") == "localhost"
    assert assert_local_llm_endpoint("http://127.0.0.1:11434/api/generate") == "127.0.0.1"
    assert assert_local_llm_endpoint("http://[::1]:11434/api/generate") == "::1"
    with pytest.raises(PrivacyError, match="External LLM endpoint"):
        assert_local_llm_endpoint("https://example.org/v1/chat")


def test_direct_identifiers_are_redacted_without_destroying_clinical_values():
    source = (
        "glucose 104 mg/dL; email ana@example.org; phone +351 912 345 678; "
        "MRN: HOSP-9912; Patient/abc-123; 192.168.1.4"
    )
    redacted, categories = redact_direct_identifiers(source)
    assert "glucose 104 mg/dL" in redacted
    assert "ana@example.org" not in redacted
    assert "HOSP-9912" not in redacted
    assert "Patient/abc-123" not in redacted
    assert set(categories) == {
        "EMAIL", "PHONE", "IP_ADDRESS", "LOCAL_IDENTIFIER",
        "FHIR_PATIENT_REFERENCE",
    }


def test_phi_runtime_requires_explicit_approval_and_retention():
    endpoint = "http://localhost:11434/api/generate"
    with pytest.raises(PrivacyError, match="CMF_PHI_ENABLED"):
        validate_privacy_runtime(endpoint, {"CMF_DATA_CLASSIFICATION": "PHI"})
    environment = {
        "CMF_DATA_CLASSIFICATION": "PHI",
        "CMF_PHI_ENABLED": "true",
        "CMF_PHI_POLICY_APPROVED_BY": "Hospital DPO",
        "CMF_PHI_RETENTION_DAYS": "30",
    }
    result = validate_privacy_runtime(endpoint, environment)
    assert result == {
        "classification": "PHI", "llm_host": "localhost",
        "phi_enabled": True, "retention_days": 30,
        "approved_by": "Hospital DPO",
    }


def test_phi_role_access_requires_matching_authenticated_allowlisted_identity():
    environment = {
        "CMF_DATA_CLASSIFICATION": "PHI",
        "CMF_PHI_ENABLED": "true",
        "CMF_PHI_POLICY_APPROVED_BY": "Hospital DPO",
        "CMF_PHI_RETENTION_DAYS": "30",
        "CMF_AUTHENTICATED_USER": "Dr Reviewer",
        "CMF_REVIEWER_ALLOWLIST": "Dr Reviewer,Dr Other",
        "CMF_ADJUDICATOR_ALLOWLIST": "Dr Adjudicator",
    }
    assert authorize_actor("Dr Reviewer", "reviewer", environment) == "Dr Reviewer"
    with pytest.raises(PrivacyError, match="matching authenticated"):
        authorize_actor("Dr Other", "reviewer", environment)
    with pytest.raises(PrivacyError, match="not authorized"):
        authorize_actor("Dr Reviewer", "adjudicator", environment)


def test_security_audit_is_metadata_only_and_rejects_identifiers():
    with duckdb.connect(":memory:") as con:
        audit_security_event(
            con, "TEST", "system", "ALLOWED",
            {"target_table": "measurement", "result_count": 2},
            run_id="RUN-test",
        )
        stored = con.execute("""
            SELECT event_type, actor, outcome, details_json
            FROM security_audit_log
        """).fetchone()
        assert stored[:3] == ("TEST", "system", "ALLOWED")
        assert json.loads(stored[3]) == {
            "result_count": 2, "target_table": "measurement"
        }
        with pytest.raises(PrivacyError, match="detail keys"):
            audit_security_event(
                con, "TEST", "system", "BLOCKED", {"source_value": "raw"}
            )
        with pytest.raises(PrivacyError, match="Direct identifiers"):
            audit_security_event(
                con, "TEST", "system", "BLOCKED",
                {"note": "contact ana@example.org"},
            )
