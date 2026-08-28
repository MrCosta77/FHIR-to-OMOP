"""Governed source-system identity contract for pre-ingestion hospital data."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.mapping.governance import ensure_governance_tables
from src.omop.mapping_targets import TARGETS
from src.security.privacy import (
    audit_security_event,
    authorize_actor,
    redact_direct_identifiers,
)

if TYPE_CHECKING:
    from src.adapters.hospital_csv import HospitalCSVRecord


SOURCE_ADAPTER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,49}$")
SOURCE_SYSTEM_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._:-]{0,99}$")
SOURCE_VOCABULARY_PATTERN = re.compile(r"^CMF_[A-Z0-9_]{1,16}$")
MAX_SOURCE_CODE_LENGTH = 50


class SourceIdentityError(ValueError):
    """Raised when a source identity is missing, ambiguous or unsafe."""


def _require_safe_metadata(value: str, *, field: str) -> str:
    value = (value or "").strip()
    if not value:
        raise SourceIdentityError(f"{field} is required for source identity")
    _redacted, categories = redact_direct_identifiers(value)
    if categories:
        raise SourceIdentityError(
            f"{field} matches a direct-identifier pattern: {', '.join(categories)}"
        )
    if any(character in value for character in ("\x00", "\r", "\n")):
        raise SourceIdentityError(f"{field} contains a forbidden control character")
    return value


@dataclass(frozen=True, slots=True)
class SourceIdentityClaim:
    """One record's unambiguous pre-ingestion identity claim."""

    source_adapter: str
    source_system: str
    source_code: str
    target_table: str
    source_record_key: str

    def __post_init__(self) -> None:
        _require_safe_metadata(self.source_adapter, field="source_adapter")
        if not SOURCE_ADAPTER_PATTERN.fullmatch(self.source_adapter):
            raise SourceIdentityError("source_adapter is not a canonical adapter ID")
        _require_safe_metadata(self.source_system, field="source_system")
        if not SOURCE_SYSTEM_PATTERN.fullmatch(self.source_system):
            raise SourceIdentityError(
                "source_system must be a canonical uppercase system code"
            )
        source_code = _require_safe_metadata(self.source_code, field="source_code")
        if len(source_code) > MAX_SOURCE_CODE_LENGTH:
            raise SourceIdentityError(
                f"source_code exceeds the OMOP limit of {MAX_SOURCE_CODE_LENGTH} characters"
            )
        if self.target_table not in TARGETS:
            raise SourceIdentityError("target_table is not a governed OMOP target")
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_record_key):
            raise SourceIdentityError("source_record_key must be a lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class RegisteredSourceSystem:
    """One active adapter/system to OMOP source-vocabulary registration."""

    source_adapter: str
    source_system: str
    source_vocabulary_id: str


@dataclass(frozen=True, slots=True)
class ResolvedSourceIdentity:
    """A record claim resolved through exactly one active registry entry."""

    claim: SourceIdentityClaim
    source_vocabulary_id: str


def validate_source_registration(
    source_adapter: str,
    source_system: str,
    source_vocabulary_id: str,
) -> RegisteredSourceSystem:
    """Validate canonical registry values against OMOP/STCM constraints."""
    source_adapter = (source_adapter or "").strip()
    source_system = (source_system or "").strip()
    source_vocabulary_id = (source_vocabulary_id or "").strip()
    if not SOURCE_ADAPTER_PATTERN.fullmatch(source_adapter):
        raise SourceIdentityError("source_adapter is not a canonical adapter ID")
    if not SOURCE_SYSTEM_PATTERN.fullmatch(source_system):
        raise SourceIdentityError(
            "source_system must be a canonical uppercase system code"
        )
    _require_safe_metadata(source_system, field="source_system")
    if not SOURCE_VOCABULARY_PATTERN.fullmatch(source_vocabulary_id):
        raise SourceIdentityError(
            "source_vocabulary_id must use CMF_, uppercase characters and at most 20 characters"
        )
    if source_vocabulary_id.startswith("CMF_SYNTHEA"):
        raise SourceIdentityError(
            "Hospital source identities cannot reuse reserved CMF_SYNTHEA vocabularies"
        )
    return RegisteredSourceSystem(
        source_adapter=source_adapter,
        source_system=source_system,
        source_vocabulary_id=source_vocabulary_id,
    )


def claim_hospital_csv_identity(record: HospitalCSVRecord) -> SourceIdentityClaim:
    """Extract the required local system and code from one validated CSV row."""
    context = dict(record.context)
    return SourceIdentityClaim(
        source_adapter="hospital-csv-v1",
        source_system=context.get("source_system", ""),
        source_code=context.get("source_code", ""),
        target_table=record.target_table,
        source_record_key=record.source_record_key,
    )


def _register_source_system_uncommitted(
    con,
    source_adapter: str,
    source_system: str,
    source_vocabulary_id: str,
    *,
    actor: str,
    reason: str,
    environ=None,
) -> RegisteredSourceSystem:
    """Register or safely reactivate one source identity without silent remapping."""
    registration = validate_source_registration(
        source_adapter, source_system, source_vocabulary_id
    )
    actor = authorize_actor(actor, "source_admin", environ)
    reason = _require_safe_metadata(reason, field="registration_reason")
    active = con.execute("""
        SELECT source_vocabulary_id
        FROM source_identity_registry
        WHERE source_adapter = ? AND source_system = ? AND active
        ORDER BY source_vocabulary_id
    """, [registration.source_adapter, registration.source_system]).fetchall()
    if len(active) > 1:
        raise SourceIdentityError("Source registry contains multiple active identities")
    if active:
        if active[0][0] != registration.source_vocabulary_id:
            raise SourceIdentityError(
                "Source system already has a different active vocabulary; "
                "deactivate it explicitly before registering another"
            )
        outcome = "IDEMPOTENT"
    else:
        existing = con.execute("""
            SELECT COUNT(*) FROM source_identity_registry
            WHERE source_adapter = ? AND source_system = ?
              AND source_vocabulary_id = ?
        """, [
            registration.source_adapter,
            registration.source_system,
            registration.source_vocabulary_id,
        ]).fetchone()[0]
        if existing:
            con.execute("""
                UPDATE source_identity_registry
                SET active = TRUE, registered_by = ?, registration_reason = ?,
                    registered_at = now(), deactivated_by = NULL,
                    deactivation_reason = NULL, deactivated_at = NULL
                WHERE source_adapter = ? AND source_system = ?
                  AND source_vocabulary_id = ? AND NOT active
            """, [
                actor,
                reason,
                registration.source_adapter,
                registration.source_system,
                registration.source_vocabulary_id,
            ])
            outcome = "REACTIVATED"
        else:
            con.execute("""
                INSERT INTO source_identity_registry (
                    source_adapter, source_system, source_vocabulary_id,
                    registered_by, registration_reason
                ) VALUES (?, ?, ?, ?, ?)
            """, [
                registration.source_adapter,
                registration.source_system,
                registration.source_vocabulary_id,
                actor,
                reason,
            ])
            outcome = "REGISTERED"
    audit_security_event(
        con,
        "SOURCE_IDENTITY_REGISTRATION",
        actor,
        outcome,
        {
            "adapter_id": registration.source_adapter,
            "system_code": registration.source_system,
            "vocabulary_id": registration.source_vocabulary_id,
        },
    )
    return registration


def register_source_system(
    con,
    source_adapter: str,
    source_system: str,
    source_vocabulary_id: str,
    *,
    actor: str,
    reason: str,
    environ=None,
    manage_transaction=True,
) -> RegisteredSourceSystem:
    """Atomically register a source identity and its audit event."""
    ensure_governance_tables(con)
    if not manage_transaction:
        return _register_source_system_uncommitted(
            con,
            source_adapter,
            source_system,
            source_vocabulary_id,
            actor=actor,
            reason=reason,
            environ=environ,
        )
    con.execute("BEGIN TRANSACTION")
    try:
        registration = _register_source_system_uncommitted(
            con,
            source_adapter,
            source_system,
            source_vocabulary_id,
            actor=actor,
            reason=reason,
            environ=environ,
        )
        con.execute("COMMIT")
        return registration
    except Exception:
        con.execute("ROLLBACK")
        raise


def _deactivate_source_system_uncommitted(
    con,
    source_adapter: str,
    source_system: str,
    *,
    actor: str,
    reason: str,
    environ=None,
) -> RegisteredSourceSystem:
    """Deactivate exactly one active registration while retaining its history."""
    source_adapter = (source_adapter or "").strip()
    source_system = (source_system or "").strip()
    actor = authorize_actor(actor, "source_admin", environ)
    reason = _require_safe_metadata(reason, field="deactivation_reason")
    rows = con.execute("""
        SELECT source_vocabulary_id FROM source_identity_registry
        WHERE source_adapter = ? AND source_system = ? AND active
    """, [source_adapter, source_system]).fetchall()
    if len(rows) != 1:
        raise SourceIdentityError(
            "Source system must have exactly one active registration to deactivate"
        )
    registration = validate_source_registration(
        source_adapter, source_system, rows[0][0]
    )
    con.execute("""
        UPDATE source_identity_registry
        SET active = FALSE, deactivated_by = ?, deactivation_reason = ?,
            deactivated_at = now()
        WHERE source_adapter = ? AND source_system = ?
          AND source_vocabulary_id = ? AND active
    """, [
        actor,
        reason,
        registration.source_adapter,
        registration.source_system,
        registration.source_vocabulary_id,
    ])
    audit_security_event(
        con,
        "SOURCE_IDENTITY_DEACTIVATION",
        actor,
        "DEACTIVATED",
        {
            "adapter_id": registration.source_adapter,
            "system_code": registration.source_system,
            "vocabulary_id": registration.source_vocabulary_id,
        },
    )
    return registration


def deactivate_source_system(
    con,
    source_adapter: str,
    source_system: str,
    *,
    actor: str,
    reason: str,
    environ=None,
    manage_transaction=True,
) -> RegisteredSourceSystem:
    """Atomically deactivate a source identity and retain its audit event."""
    ensure_governance_tables(con)
    if not manage_transaction:
        return _deactivate_source_system_uncommitted(
            con,
            source_adapter,
            source_system,
            actor=actor,
            reason=reason,
            environ=environ,
        )
    con.execute("BEGIN TRANSACTION")
    try:
        registration = _deactivate_source_system_uncommitted(
            con,
            source_adapter,
            source_system,
            actor=actor,
            reason=reason,
            environ=environ,
        )
        con.execute("COMMIT")
        return registration
    except Exception:
        con.execute("ROLLBACK")
        raise


def resolve_source_identity(
    con, claim: SourceIdentityClaim
) -> ResolvedSourceIdentity:
    """Resolve a claim only when exactly one matching registry entry is active."""
    ensure_governance_tables(con)
    rows = con.execute("""
        SELECT source_vocabulary_id FROM source_identity_registry
        WHERE source_adapter = ? AND source_system = ? AND active
        ORDER BY source_vocabulary_id
    """, [claim.source_adapter, claim.source_system]).fetchall()
    if not rows:
        raise SourceIdentityError("Source identity is not registered or is inactive")
    if len(rows) > 1:
        raise SourceIdentityError("Source identity is ambiguous: multiple entries are active")
    return ResolvedSourceIdentity(
        claim=claim,
        source_vocabulary_id=rows[0][0],
    )
