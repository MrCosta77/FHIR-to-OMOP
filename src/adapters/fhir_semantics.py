"""Clinical publication rules for FHIR R4 resources mapped to OMOP."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

PUBLISHABLE_RESOURCE_STATUSES = {
    "Observation": frozenset({"final", "amended", "corrected"}),
    "Procedure": frozenset({"completed"}),
    "MedicationRequest": frozenset({"active", "on-hold", "completed", "stopped"}),
}

EXCLUDED_CONDITION_VERIFICATION_STATUSES = frozenset({
    "entered-in-error",
    "refuted",
})


def _coding_codes(codeable_concept: object) -> set[str]:
    if not isinstance(codeable_concept, dict):
        return set()
    return {
        str(coding.get("code", "")).strip().casefold()
        for coding in codeable_concept.get("coding", [])
        if isinstance(coding, dict) and str(coding.get("code", "")).strip()
    }


def is_publishable_fhir_resource(resource: dict) -> bool:
    """Return whether a valid FHIR event is eligible for OMOP publication.

    This is a publication policy, not structural FHIR validation. Resources in
    draft, cancelled, erroneous, refuted, or otherwise non-final states remain
    valid FHIR but must not become asserted OMOP clinical facts.
    """
    resource_type = str(resource.get("resourceType", "")).strip()
    if resource_type == "Condition":
        verification = _coding_codes(resource.get("verificationStatus"))
        return not bool(verification & EXCLUDED_CONDITION_VERIFICATION_STATUSES)

    allowed = PUBLISHABLE_RESOURCE_STATUSES.get(resource_type)
    if allowed is None:
        return True
    status = str(resource.get("status", "")).strip().casefold()
    return status in allowed


def fhir_publication_exclusion_reason(resource: dict) -> str | None:
    """Return a metadata-only reason code when an event cannot be published."""
    resource_type = str(resource.get("resourceType", "")).strip()
    if resource_type == "Condition":
        excluded = sorted(
            _coding_codes(resource.get("verificationStatus"))
            & EXCLUDED_CONDITION_VERIFICATION_STATUSES
        )
        if excluded:
            return f"FHIR_CONDITION_VERIFICATION_{excluded[0].upper().replace('-', '_')}"
        return None

    allowed = PUBLISHABLE_RESOURCE_STATUSES.get(resource_type)
    if allowed is None:
        return None
    status = str(resource.get("status", "")).strip().casefold()
    if status in allowed:
        return None
    normalized = status.upper().replace("-", "_") if status else "MISSING"
    return f"FHIR_{resource_type.upper()}_STATUS_{normalized}"


def extract_fhir_publication_exclusions(
    file_path: str | Path,
    resource_types: set[str] | frozenset[str],
) -> list[tuple[str, str, str, str]]:
    """Extract only non-PHI metadata for valid-but-nonpublishable resources."""
    with Path(file_path).open(encoding="utf-8") as handle:
        bundle = json.load(handle)
    if bundle.get("resourceType") != "Bundle":
        return []

    exclusions = []
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        resource_type = str(resource.get("resourceType", "")).strip()
        if resource_type not in resource_types:
            continue
        reason = fhir_publication_exclusion_reason(resource)
        if reason is None:
            continue
        status = str(resource.get("status", "")).strip()
        if resource_type == "Condition":
            status = ",".join(sorted(_coding_codes(resource.get("verificationStatus"))))
        source_event_key = str(entry.get("fullUrl", "")).strip()
        if not source_event_key:
            canonical = json.dumps(resource, sort_keys=True, separators=(",", ":"))
            source_event_key = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
        exclusions.append((resource_type, source_event_key, status, reason))
    return exclusions


def replace_fhir_publication_exclusions(
    con,
    source_adapter: str,
    rows: list[tuple[str, str, str, str]],
    *,
    run_id: str | None,
) -> None:
    """Atomically replace one adapter's valid-but-excluded event inventory."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS fhir_publication_exclusion (
            source_adapter VARCHAR NOT NULL,
            resource_type VARCHAR NOT NULL,
            source_event_key VARCHAR NOT NULL,
            source_status VARCHAR,
            reason_code VARCHAR NOT NULL,
            run_id VARCHAR,
            excluded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (source_adapter, resource_type, source_event_key)
        )
    """)
    con.execute(
        "DELETE FROM fhir_publication_exclusion WHERE source_adapter = ?",
        [source_adapter],
    )
    if rows:
        con.executemany("""
            INSERT INTO fhir_publication_exclusion (
                source_adapter, resource_type, source_event_key,
                source_status, reason_code, run_id
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, [
            (source_adapter, resource_type, key, status, reason, run_id)
            for resource_type, key, status, reason in rows
        ])


def fhir_datetime(value: object) -> tuple[str, str] | None:
    """Convert a precise FHIR date/dateTime to OMOP date and UTC datetime text."""
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None
        return text, f"{text}T00:00:00"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed.date().isoformat(), parsed.isoformat(timespec="microseconds")


def medication_request_period(resource: dict) -> tuple[str, str, str, str] | None:
    """Derive a prescription interval without pretending it is administration.

    FHIR ``authoredOn`` is the order-authoring instant. It is used only when a
    dispense validity start is unavailable. Because OMOP requires an exposure
    end date, an absent validity end conservatively falls back to the start.
    """
    validity = resource.get("dispenseRequest", {}).get("validityPeriod", {})
    if not isinstance(validity, dict):
        validity = {}
    start = fhir_datetime(validity.get("start") or resource.get("authoredOn"))
    if start is None:
        return None
    end = fhir_datetime(validity.get("end"))
    if end is None:
        end = start
    return start[0], start[1], end[0], end[1]
