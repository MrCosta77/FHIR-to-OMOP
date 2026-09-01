"""Clinical publication rules for FHIR R4 resources mapped to OMOP."""

from __future__ import annotations

from datetime import UTC, datetime

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
