"""Governed handoff from successful hospital ingestion to mapping review."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from src.adapters.event_binding import EventBindingError, bind_pre_ingestion_decision
from src.adapters.source_identity import (
    SourceIdentityClaim,
    SourceIdentityError,
    resolve_source_identity,
)
from src.mapping.governance import ensure_governance_tables
from src.security.privacy import (
    audit_security_event,
    authorize_actor,
    redact_direct_identifiers,
)


RECEIPT_SCHEMA_VERSION = "cmf-ingestion-receipt-v1"
REPORT_SCHEMA_VERSION = "cmf-ingestion-handoff-report-v1"
MAX_RECEIPTS_PER_BATCH = 10_000
RUN_ID_PATTERN = re.compile(r"^RUN-[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
RECEIPT_FIELDS = {
    "schema_version",
    "ingestion_run_id",
    "input_manifest_sha256",
    "source_adapter",
    "source_system",
    "source_code",
    "source_record_key",
    "target_table",
    "target_id",
}


class IngestionHandoffError(ValueError):
    """Raised when an ingestion receipt or batch cannot be trusted."""


def _canonical_json(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _safe_reason(reason: str) -> str:
    reason = (reason or "").strip()
    if not reason:
        raise IngestionHandoffError("A handoff reason is required")
    _redacted, categories = redact_direct_identifiers(reason)
    if categories:
        raise IngestionHandoffError("Handoff reason contains a direct identifier")
    if len(reason) > 500:
        raise IngestionHandoffError("Handoff reason exceeds 500 characters")
    return reason


@dataclass(frozen=True, slots=True)
class IngestionReceipt:
    """Proof that one source record produced one concrete OMOP event."""

    schema_version: str
    ingestion_run_id: str
    input_manifest_sha256: str
    source_adapter: str
    source_system: str
    source_code: str
    source_record_key: str
    target_table: str
    target_id: int

    def __post_init__(self) -> None:
        if self.schema_version != RECEIPT_SCHEMA_VERSION:
            raise IngestionHandoffError("Unsupported ingestion receipt schema")
        if not RUN_ID_PATTERN.fullmatch(self.ingestion_run_id or ""):
            raise IngestionHandoffError("ingestion_run_id is not canonical")
        if not re.fullmatch(r"[0-9a-f]{64}", self.input_manifest_sha256 or ""):
            raise IngestionHandoffError(
                "input_manifest_sha256 must be a lowercase SHA-256 digest"
            )
        if (
            isinstance(self.target_id, bool)
            or not isinstance(self.target_id, int)
            or self.target_id <= 0
        ):
            raise IngestionHandoffError("target_id must be a positive integer")
        # Reuse the canonical source identity and governed target contract.
        self.source_identity_claim()

    def source_identity_claim(self) -> SourceIdentityClaim:
        return SourceIdentityClaim(
            source_adapter=self.source_adapter,
            source_system=self.source_system,
            source_code=self.source_code,
            target_table=self.target_table,
            source_record_key=self.source_record_key,
        )

    @property
    def receipt_id(self) -> str:
        payload = {field: getattr(self, field) for field in sorted(RECEIPT_FIELDS)}
        return hashlib.sha256(_canonical_json(payload)).hexdigest()

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "IngestionReceipt":
        if not isinstance(payload, Mapping):
            raise IngestionHandoffError("Each ingestion receipt must be an object")
        fields = set(payload)
        if fields != RECEIPT_FIELDS:
            missing = bool(RECEIPT_FIELDS - fields)
            unknown = bool(fields - RECEIPT_FIELDS)
            category = (
                "missing required and unknown"
                if missing and unknown
                else "missing required" if missing else "unknown"
            )
            raise IngestionHandoffError(
                f"Invalid ingestion receipt fields: {category} fields"
            )
        try:
            return cls(**{field: payload[field] for field in RECEIPT_FIELDS})
        except TypeError as exc:
            raise IngestionHandoffError("Invalid ingestion receipt value types") from exc


@dataclass(frozen=True, slots=True)
class ReceiptOutcome:
    """Privacy-safe processing result for one receipt."""

    receipt_id: str
    status: str
    binding_id: str | None = None
    failure_code: str | None = None

    def as_dict(self) -> dict:
        return {
            "receipt_id": self.receipt_id,
            "status": self.status,
            "binding_id": self.binding_id,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class IngestionHandoffReport:
    """Metadata-only batch result; never contains source codes or record keys."""

    batch_id: str
    ingestion_run_id: str
    outcomes: tuple[ReceiptOutcome, ...]

    @property
    def counts(self) -> dict[str, int]:
        values = {"BOUND": 0, "ALREADY_BOUND": 0, "FAILED": 0}
        for outcome in self.outcomes:
            values[outcome.status] += 1
        return values

    def as_dict(self) -> dict:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "batch_id": self.batch_id,
            "ingestion_run_id": self.ingestion_run_id,
            "receipt_count": len(self.outcomes),
            "counts": self.counts,
            "outcomes": [outcome.as_dict() for outcome in self.outcomes],
            "report_contains_phi": False,
            "source_values_included": False,
            "source_record_keys_included": False,
        }


def parse_ingestion_receipts(payload: object) -> tuple[IngestionReceipt, ...]:
    """Parse a strict JSON-compatible receipt array without accepting extra fields."""
    if not isinstance(payload, list):
        raise IngestionHandoffError("Ingestion receipt input must be a JSON array")
    if not payload:
        raise IngestionHandoffError("At least one ingestion receipt is required")
    if len(payload) > MAX_RECEIPTS_PER_BATCH:
        raise IngestionHandoffError("Ingestion receipt batch exceeds the maximum size")
    return tuple(IngestionReceipt.from_mapping(item) for item in payload)


def load_ingestion_receipts(path: Path) -> tuple[IngestionReceipt, ...]:
    """Load a UTF-8 JSON receipt array without logging its clinical metadata."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IngestionHandoffError("Unable to read a valid UTF-8 receipt file") from exc
    return parse_ingestion_receipts(payload)


def input_manifest_sha256(stored_manifest: str) -> str:
    """Return the receipt digest for the exact UTF-8 etl_run manifest value."""
    return hashlib.sha256(stored_manifest.encode("utf-8")).hexdigest()


def _validate_batch(receipts: Sequence[IngestionReceipt]) -> tuple[str, str, str]:
    if not receipts:
        raise IngestionHandoffError("At least one ingestion receipt is required")
    if len(receipts) > MAX_RECEIPTS_PER_BATCH:
        raise IngestionHandoffError("Ingestion receipt batch exceeds the maximum size")
    run_ids = {receipt.ingestion_run_id for receipt in receipts}
    manifest_digests = {receipt.input_manifest_sha256 for receipt in receipts}
    if len(run_ids) != 1 or len(manifest_digests) != 1:
        raise IngestionHandoffError(
            "A handoff batch must reference exactly one ingestion run and manifest"
        )
    record_keys = [receipt.source_record_key for receipt in receipts]
    targets = [(receipt.target_table, receipt.target_id) for receipt in receipts]
    if len(record_keys) != len(set(record_keys)):
        raise IngestionHandoffError("A handoff batch contains duplicate source records")
    if len(targets) != len(set(targets)):
        raise IngestionHandoffError("A handoff batch contains duplicate OMOP events")
    receipt_ids = sorted(receipt.receipt_id for receipt in receipts)
    batch_id = str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        "cmf:ingestion-handoff:" + "|".join(receipt_ids),
    ))
    return next(iter(run_ids)), next(iter(manifest_digests)), batch_id


def _validate_successful_ingestion_run(con, run_id: str, manifest_digest: str) -> None:
    rows = con.execute("""
        SELECT status, input_manifest FROM etl_run WHERE run_id = ?
    """, [run_id]).fetchall()
    if len(rows) != 1 or rows[0][0] != "SUCCESS":
        raise IngestionHandoffError(
            "Ingestion receipt must reference one successful ETL run"
        )
    if input_manifest_sha256(rows[0][1]) != manifest_digest:
        raise IngestionHandoffError("Ingestion input manifest digest does not match")


def _existing_binding(con, receipt: IngestionReceipt, source_vocabulary_id: str):
    row = con.execute("""
        SELECT binding_id, source_adapter, source_system, source_vocabulary_id,
               source_code, target_table, target_id, active,
               ingestion_run_id, input_manifest_sha256, handoff_batch_id
        FROM source_event_binding WHERE source_record_key = ?
    """, [receipt.source_record_key]).fetchone()
    if not row:
        return None
    expected = (
        receipt.source_adapter,
        receipt.source_system,
        source_vocabulary_id,
        receipt.source_code,
        receipt.target_table,
        receipt.target_id,
        True,
    )
    if row[1:8] != expected:
        raise IngestionHandoffError("Receipt conflicts with an existing event binding")
    return row[0], row[8], row[9], row[10]


def _attest_binding(
    con,
    binding_id,
    receipt,
    *,
    ingestion_run_id,
    manifest_digest,
    batch_id,
    actor,
    outcome,
):
    lineage = con.execute("""
        SELECT ingestion_run_id, input_manifest_sha256, handoff_batch_id
        FROM source_event_binding WHERE binding_id = ?
    """, [binding_id]).fetchone()
    stored_run_id, stored_manifest_digest, stored_batch_id = lineage
    if (
        (stored_run_id is None) != (stored_manifest_digest is None)
        or (
            stored_run_id is not None
            and (stored_run_id, stored_manifest_digest)
            != (ingestion_run_id, manifest_digest)
        )
    ):
        raise IngestionHandoffError(
            "Existing binding has conflicting ingestion lineage"
        )
    if stored_run_id is None or stored_batch_id is None:
        con.execute("""
            UPDATE source_event_binding
            SET ingestion_run_id = ?, input_manifest_sha256 = ?,
                handoff_batch_id = ?
            WHERE binding_id = ?
        """, [ingestion_run_id, manifest_digest, batch_id, binding_id])
    audit_security_event(
        con,
        "INGESTION_RECEIPT_ATTESTATION",
        actor,
        outcome,
        {
            "batch_id": batch_id,
            "binding_id": binding_id,
            "receipt_id": receipt.receipt_id,
            "target_table": receipt.target_table,
        },
        run_id=ingestion_run_id,
    )


def _bind_one(
    con,
    receipt: IngestionReceipt,
    *,
    ingestion_run_id,
    manifest_digest,
    batch_id,
    actor,
    reason,
    environ,
):
    claim = receipt.source_identity_claim()
    identity = resolve_source_identity(con, claim)
    existing = _existing_binding(
        con, receipt, identity.source_vocabulary_id
    )
    if existing:
        existing_binding_id = existing[0]
        _attest_binding(
            con,
            existing_binding_id,
            receipt,
            ingestion_run_id=ingestion_run_id,
            manifest_digest=manifest_digest,
            batch_id=batch_id,
            actor=actor,
            outcome="ATTESTED_EXISTING",
        )
        return ReceiptOutcome(
            receipt_id=receipt.receipt_id,
            status="ALREADY_BOUND",
            binding_id=existing_binding_id,
        )
    decisions = con.execute("""
        SELECT mapping_decision_id FROM mapping_decision
        WHERE source_adapter = ? AND source_record_key = ?
          AND target_table = ?
          AND status IN ('PRE_INGESTION', 'PRE_INGESTION_LOW_CONFIDENCE')
          AND NOT COALESCE(publication_eligible, FALSE)
          AND llm_decision = 'SELECT'
        ORDER BY mapping_decision_id
    """, [
        receipt.source_adapter, receipt.source_record_key, receipt.target_table,
    ]).fetchall()
    if not decisions:
        raise IngestionHandoffError("Receipt has no bindable SELECT decision")
    if len(decisions) != 1:
        raise IngestionHandoffError("Receipt has multiple bindable SELECT decisions")
    binding = bind_pre_ingestion_decision(
        con,
        identity,
        decisions[0][0],
        receipt.target_id,
        actor=actor,
        reason=reason,
        environ=environ,
        manage_transaction=False,
    )
    _attest_binding(
        con,
        binding.binding_id,
        receipt,
        ingestion_run_id=ingestion_run_id,
        manifest_digest=manifest_digest,
        batch_id=batch_id,
        actor=actor,
        outcome="ATTESTED_NEW",
    )
    return ReceiptOutcome(
        receipt_id=receipt.receipt_id,
        status="BOUND",
        binding_id=binding.binding_id,
    )


def _failure_code(exc: Exception) -> str:
    if isinstance(exc, SourceIdentityError):
        return "SOURCE_IDENTITY_REJECTED"
    if isinstance(exc, EventBindingError):
        return "EVENT_BINDING_REJECTED"
    return "RECEIPT_REJECTED"


def process_ingestion_handoff(
    con,
    receipts: Sequence[IngestionReceipt],
    *,
    actor: str,
    reason: str,
    environ=None,
) -> IngestionHandoffReport:
    """Bind valid receipts independently and report expected failures without PHI."""
    ensure_governance_tables(con)
    actor = authorize_actor(actor, "source_admin", environ)
    reason = _safe_reason(reason)
    receipts = tuple(receipts)
    run_id, manifest_digest, batch_id = _validate_batch(receipts)
    _validate_successful_ingestion_run(con, run_id, manifest_digest)

    # Audit authorization before reading or binding any receipt in the batch.
    con.execute("BEGIN TRANSACTION")
    try:
        audit_security_event(
            con,
            "INGESTION_HANDOFF_BATCH",
            actor,
            "AUTHORIZED",
            {
                "batch_id": batch_id,
                "ingestion_run_id": run_id,
                "receipt_count": len(receipts),
                "target_tables": sorted({r.target_table for r in receipts}),
            },
            run_id=run_id,
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise

    outcomes = []
    expected_errors = (IngestionHandoffError, SourceIdentityError, EventBindingError)
    for receipt in receipts:
        con.execute("BEGIN TRANSACTION")
        try:
            outcome = _bind_one(
                con,
                receipt,
                ingestion_run_id=run_id,
                manifest_digest=manifest_digest,
                batch_id=batch_id,
                actor=actor,
                reason=reason,
                environ=environ,
            )
            con.execute("COMMIT")
        except expected_errors as exc:
            con.execute("ROLLBACK")
            outcome = ReceiptOutcome(
                receipt_id=receipt.receipt_id,
                status="FAILED",
                failure_code=_failure_code(exc),
            )
        except Exception:
            con.execute("ROLLBACK")
            raise
        outcomes.append(outcome)
    return IngestionHandoffReport(
        batch_id=batch_id,
        ingestion_run_id=run_id,
        outcomes=tuple(outcomes),
    )


def main() -> int:
    """Process a local receipt file and print only the metadata-safe report."""
    from src.utils.config import DB_PATH

    parser = argparse.ArgumentParser(
        description="Bind successful hospital ingestion receipts to governed review."
    )
    parser.add_argument("path", type=Path)
    parser.add_argument("--database", type=Path, default=DB_PATH)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()

    import duckdb

    receipts = load_ingestion_receipts(args.path)
    with duckdb.connect(str(args.database)) as con:
        report = process_ingestion_handoff(
            con,
            receipts,
            actor=args.actor,
            reason=args.reason,
        )
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    return 2 if report.counts["FAILED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
