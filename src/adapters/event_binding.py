"""Atomic binding of resolved hospital identities to concrete OMOP events."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from src.adapters.source_identity import (
    ResolvedSourceIdentity,
    SourceIdentityError,
    resolve_source_identity,
)
from src.mapping.governance import ensure_governance_tables
from src.omop.mapping_targets import TARGETS
from src.security.privacy import (
    audit_security_event,
    authorize_actor,
    redact_direct_identifiers,
)


class EventBindingError(ValueError):
    """Raised when a pre-ingestion decision cannot be safely bound."""


@dataclass(frozen=True, slots=True)
class EventBinding:
    """Verified relationship between one external record, decision and event."""

    binding_id: str
    mapping_decision_id: str
    target_table: str
    target_id: int
    source_vocabulary_id: str
    source_code: str
    decision_status: str


def _safe_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise EventBindingError("A binding reason is required")
    _redacted, categories = redact_direct_identifiers(reason)
    if categories:
        raise EventBindingError("Binding reason contains a direct identifier")
    if len(reason) > 500:
        raise EventBindingError("Binding reason exceeds 500 characters")
    return reason


def _binding_id(source_adapter: str, source_record_key: str) -> str:
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"cmf:source-event-binding:{source_adapter}|{source_record_key}",
    ))


def _bind_uncommitted(
    con,
    identity: ResolvedSourceIdentity,
    mapping_decision_id: str,
    target_id: int,
    *,
    actor: str,
    reason: str,
    environ=None,
) -> EventBinding:
    actor = authorize_actor(actor, "source_admin", environ)
    reason = _safe_reason(reason)
    if isinstance(target_id, bool) or not isinstance(target_id, int) or target_id <= 0:
        raise EventBindingError("target_id must be a positive OMOP event identifier")
    current_identity = resolve_source_identity(con, identity.claim)
    if current_identity.source_vocabulary_id != identity.source_vocabulary_id:
        raise SourceIdentityError("Resolved source identity is stale or no longer active")

    claim = identity.claim
    expected_binding_id = _binding_id(claim.source_adapter, claim.source_record_key)
    existing = con.execute("""
        SELECT binding_id, mapping_decision_id, target_table, target_id,
               source_vocabulary_id, source_code, active
        FROM source_event_binding
        WHERE source_record_key = ?
    """, [claim.source_record_key]).fetchone()
    if existing:
        exact = existing[:6] == (
            expected_binding_id,
            mapping_decision_id,
            claim.target_table,
            target_id,
            identity.source_vocabulary_id,
            claim.source_code,
        ) and bool(existing[6])
        if not exact:
            raise EventBindingError("Source record already has a conflicting event binding")
        status = con.execute("""
            SELECT status FROM mapping_decision
            WHERE mapping_decision_id = ? AND publication_eligible
        """, [mapping_decision_id]).fetchone()
        if not status or status[0] not in {"PENDING", "LOW_CONFIDENCE"}:
            raise EventBindingError("Existing binding is not in a reviewable decision state")
        outcome = "IDEMPOTENT"
        decision_status = status[0]
    else:
        decision = con.execute("""
            SELECT target_table, source_adapter, source_record_key, status,
                   COALESCE(publication_eligible, FALSE), llm_decision,
                   assigned_concept_id
            FROM mapping_decision WHERE mapping_decision_id = ?
        """, [mapping_decision_id]).fetchone()
        if not decision:
            raise EventBindingError("Unknown mapping decision")
        if decision[:3] != (
            claim.target_table, claim.source_adapter, claim.source_record_key
        ):
            raise EventBindingError("Decision does not belong to this source identity")
        if decision[4]:
            raise EventBindingError("Decision is already publication-eligible")
        if decision[3] not in {
            "PRE_INGESTION", "PRE_INGESTION_LOW_CONFIDENCE"
        }:
            raise EventBindingError("Decision is not a bindable pre-ingestion proposal")
        if decision[5] != "SELECT" or int(decision[6]) <= 0:
            raise EventBindingError("Only a SELECT proposal can be bound for review")
        rejected = con.execute("""
            SELECT COUNT(*) FROM scoped_mapping_rejection_policy
            WHERE target_table = ? AND source_vocabulary_id = ?
              AND source_code = ? AND assigned_concept_id = ? AND active
        """, [
            claim.target_table, identity.source_vocabulary_id,
            claim.source_code, int(decision[6]),
        ]).fetchone()[0]
        if rejected:
            raise EventBindingError(
                "Proposal is blocked by active source-scoped rejection policy"
            )

        config = TARGETS[claim.target_table]
        events = con.execute(f"""
            SELECT {config['concept_column']},
                   {config['source_concept_column']},
                   {config['source_column']}
            FROM {claim.target_table}
            WHERE {config['id_column']} = ?
        """, [target_id]).fetchall()
        if len(events) != 1:
            raise EventBindingError("target_id must identify exactly one OMOP event")
        concept_id, source_concept_id, event_source_code = events[0]
        if int(concept_id or 0) != 0:
            raise EventBindingError("OMOP event is already mapped")
        if source_concept_id not in {None, 0}:
            raise EventBindingError("OMOP event already has a source concept")
        if event_source_code != claim.source_code:
            raise EventBindingError(
                "OMOP event source value does not exactly match the registered source code"
            )

        provenance = con.execute("""
            SELECT COUNT(*), MIN(publication_eligible), MAX(publication_eligible)
            FROM mapping_provenance
            WHERE mapping_decision_id = ? AND source_adapter = ?
              AND source_record_key = ?
        """, [
            mapping_decision_id, claim.source_adapter, claim.source_record_key,
        ]).fetchone()
        if provenance[0] != 1 or bool(provenance[1]) or bool(provenance[2]):
            raise EventBindingError("Decision requires one non-publishable provenance row")

        decision_status = (
            "PENDING" if decision[3] == "PRE_INGESTION" else "LOW_CONFIDENCE"
        )
        review_status = (
            "Pending_Human_Review"
            if decision_status == "PENDING"
            else "Below_Confidence_Threshold"
        )
        con.execute("""
            INSERT INTO source_event_binding (
                binding_id, source_adapter, source_record_key, source_system,
                source_vocabulary_id, source_code, target_table, target_id,
                mapping_decision_id, bound_by, binding_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            expected_binding_id, claim.source_adapter, claim.source_record_key,
            claim.source_system, identity.source_vocabulary_id,
            claim.source_code, claim.target_table, target_id,
            mapping_decision_id, actor, reason,
        ])
        con.execute("""
            UPDATE mapping_decision
            SET source_system = ?, source_vocabulary_id = ?, source_code = ?,
                publication_eligible = TRUE, status = ?
            WHERE mapping_decision_id = ?
              AND status IN ('PRE_INGESTION', 'PRE_INGESTION_LOW_CONFIDENCE')
              AND NOT publication_eligible
        """, [
            claim.source_system, identity.source_vocabulary_id,
            claim.source_code, decision_status, mapping_decision_id,
        ])
        con.execute("""
            UPDATE mapping_provenance
            SET target_id = ?, source_system = ?, source_vocabulary_id = ?,
                source_code = ?, publication_eligible = TRUE, reviewed_by = ?
            WHERE mapping_decision_id = ? AND source_adapter = ?
              AND source_record_key = ? AND NOT publication_eligible
        """, [
            target_id, claim.source_system, identity.source_vocabulary_id,
            claim.source_code, review_status, mapping_decision_id,
            claim.source_adapter, claim.source_record_key,
        ])
        outcome = "BOUND_FOR_REVIEW"

    audit_security_event(
        con,
        "SOURCE_EVENT_BINDING",
        actor,
        outcome,
        {
            "binding_id": expected_binding_id,
            "target_table": claim.target_table,
            "target_id": target_id,
            "decision_status": decision_status,
            "vocabulary_id": identity.source_vocabulary_id,
        },
    )
    return EventBinding(
        binding_id=expected_binding_id,
        mapping_decision_id=mapping_decision_id,
        target_table=claim.target_table,
        target_id=target_id,
        source_vocabulary_id=identity.source_vocabulary_id,
        source_code=claim.source_code,
        decision_status=decision_status,
    )


def bind_pre_ingestion_decision(
    con,
    identity: ResolvedSourceIdentity,
    mapping_decision_id: str,
    target_id: int,
    *,
    actor: str,
    reason: str,
    environ=None,
    manage_transaction=True,
) -> EventBinding:
    """Atomically bind one SELECT proposal to a verified, unmapped OMOP event."""
    ensure_governance_tables(con)
    if not manage_transaction:
        return _bind_uncommitted(
            con, identity, mapping_decision_id, target_id,
            actor=actor, reason=reason, environ=environ,
        )
    con.execute("BEGIN TRANSACTION")
    try:
        binding = _bind_uncommitted(
            con, identity, mapping_decision_id, target_id,
            actor=actor, reason=reason, environ=environ,
        )
        con.execute("COMMIT")
        return binding
    except Exception:
        con.execute("ROLLBACK")
        raise
