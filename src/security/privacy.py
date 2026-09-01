"""Fail-closed local-LLM, PHI redaction, access, retention and audit controls."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import unicodedata
import uuid
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = PROJECT_ROOT / "config" / "privacy_policy.json"
SENSITIVE_AUDIT_KEYS = {
    "source", "source_value", "prompt", "response", "patient", "name",
    "email", "phone", "address", "identifier", "mrn", "nif", "nhs",
}
DIRECT_IDENTIFIER_PATTERNS = (
    ("EMAIL", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("PHONE", re.compile(r"(?<![\w-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}(?![\w-])")),
    ("US_SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("IBAN", re.compile(r"(?i)\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")),
    ("IP_ADDRESS", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("LOCAL_IDENTIFIER", re.compile(
        r"(?i)\b(?:MRN|NHS|NIF|NISS|SNS|CC|CITIZEN[ _-]?CARD)"
        r"\s*[:#-]?\s*[A-Z0-9-]{4,}\b"
    )),
    ("FHIR_PATIENT_REFERENCE", re.compile(r"(?i)\bPatient/[A-Za-z0-9.-]+\b")),
)


class PrivacyError(ValueError):
    """Raised when a privacy or access control fails closed."""


def canonical_actor_key(actor: str) -> str:
    """Normalize a governed identity without treating accents as new people."""
    decomposed = unicodedata.normalize("NFKD", (actor or "").strip())
    without_accents = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(without_accents.casefold().split())


def load_policy(path: Path = POLICY_PATH) -> dict:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if policy.get("schema_version") != "1.0.0":
        raise PrivacyError("Unsupported privacy policy schema version.")
    return policy


def _environment(environ=None):
    return os.environ if environ is None else environ


def data_classification(environ=None) -> str:
    env = _environment(environ)
    policy = load_policy()
    classification = env.get(
        "CMF_DATA_CLASSIFICATION", policy["default_data_classification"]
    ).strip().upper()
    if classification not in policy["allowed_data_classifications"]:
        raise PrivacyError(f"Unsupported data classification: {classification}")
    return classification


def assert_local_llm_endpoint(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PrivacyError("The LLM endpoint must be an absolute local HTTP(S) URL.")
    hostname = parsed.hostname.casefold()
    is_local = hostname == "localhost"
    if not is_local:
        try:
            is_local = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_local = False
    if not is_local:
        raise PrivacyError(f"External LLM endpoint is forbidden: {hostname}")
    return hostname


def validate_privacy_runtime(llm_endpoint: str, environ=None) -> dict:
    env = _environment(environ)
    classification = data_classification(env)
    hostname = assert_local_llm_endpoint(llm_endpoint)
    result = {
        "classification": classification,
        "llm_host": hostname,
        "phi_enabled": False,
        "retention_days": None,
        "approved_by": None,
    }
    if classification != "PHI":
        return result
    if env.get("CMF_PHI_ENABLED", "").strip().casefold() != "true":
        raise PrivacyError("PHI is classified but CMF_PHI_ENABLED is not true.")
    approved_by = env.get("CMF_PHI_POLICY_APPROVED_BY", "").strip()
    if not approved_by:
        raise PrivacyError("PHI requires named institutional policy approval.")
    try:
        retention_days = int(env.get("CMF_PHI_RETENTION_DAYS", ""))
    except ValueError as exc:
        raise PrivacyError("PHI requires an integer retention period.") from exc
    if retention_days <= 0:
        raise PrivacyError("PHI retention days must be positive.")
    result.update({
        "phi_enabled": True,
        "retention_days": retention_days,
        "approved_by": approved_by,
    })
    return result


def redact_direct_identifiers(value: str) -> tuple[str, list[str]]:
    redacted = str(value)
    categories = []
    for category, pattern in DIRECT_IDENTIFIER_PATTERNS:
        redacted, count = pattern.subn(f"[REDACTED_{category}]", redacted)
        if count:
            categories.append(category)
    return redacted, categories


def authorize_actor(actor: str, role: str, environ=None) -> str:
    env = _environment(environ)
    actor = (actor or "").strip()
    if not actor:
        raise PrivacyError("A named actor is required.")
    if role not in {"reviewer", "adjudicator", "source_admin"}:
        raise PrivacyError(f"Unsupported governed role: {role}")
    if data_classification(env) != "PHI":
        return actor
    validate_privacy_runtime(
        env.get("CMF_OLLAMA_URL", "http://localhost:11434/api/generate"), env
    )
    authenticated = env.get("CMF_AUTHENTICATED_USER", "").strip()
    if not authenticated or authenticated.casefold() != actor.casefold():
        raise PrivacyError("PHI access requires a matching authenticated identity.")
    allowlist_keys = {
        "reviewer": "CMF_REVIEWER_ALLOWLIST",
        "adjudicator": "CMF_ADJUDICATOR_ALLOWLIST",
        "source_admin": "CMF_SOURCE_ADMIN_ALLOWLIST",
    }
    allowlist_key = allowlist_keys[role]
    allowed = {
        item.strip().casefold()
        for item in env.get(allowlist_key, "").split(",") if item.strip()
    }
    if actor.casefold() not in allowed:
        raise PrivacyError(f"Authenticated actor is not authorized as {role}.")
    return authenticated


def ensure_security_audit_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS security_audit_log (
            security_event_id VARCHAR PRIMARY KEY,
            run_id VARCHAR,
            event_type VARCHAR NOT NULL,
            actor VARCHAR NOT NULL,
            outcome VARCHAR NOT NULL,
            details_json VARCHAR NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)


def audit_security_event(
    con, event_type: str, actor: str, outcome: str, details: dict, *, run_id=None
):
    """Write metadata-only audit data and reject direct identifiers or raw content."""
    unsafe_keys = {
        str(key).strip().casefold() for key in details
        if str(key).strip().casefold() in SENSITIVE_AUDIT_KEYS
    }
    if unsafe_keys:
        raise PrivacyError(f"Sensitive audit detail keys are forbidden: {sorted(unsafe_keys)}")
    serialized = json.dumps(details, ensure_ascii=False, sort_keys=True)
    _, categories = redact_direct_identifiers(serialized)
    if categories:
        raise PrivacyError("Direct identifiers are forbidden in security audit details.")
    ensure_security_audit_table(con)
    con.execute("""
        INSERT INTO security_audit_log (
            security_event_id, run_id, event_type, actor, outcome, details_json
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, [str(uuid.uuid4()), run_id, event_type, actor, outcome, serialized])
