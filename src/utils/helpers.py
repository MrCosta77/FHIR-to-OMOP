import hashlib

def normalise_fhir_reference(ref: str) -> str:
    """Reduce any FHIR reference form to the bare resource UUID."""
    value = str(ref or "").split("?", 1)[0].split("#", 1)[0].rstrip("/")
    parts = value.split("/")
    if "_history" in parts:
        history_index = parts.index("_history")
        value = parts[history_index - 1] if history_index else ""
    else:
        value = parts[-1]
    return value.replace('urn:uuid:', '').strip()

def stable_person_id(source_id: str) -> int:
    """Generates a highly stable, collision-resistant BIGINT from any FHIR ID format."""
    clean_id = normalise_fhir_reference(source_id)
    return int(hashlib.sha256(clean_id.encode('utf-8')).hexdigest(), 16) % (2**62)


def stable_event_id(source_id: str) -> int:
    """Match the deterministic BIGINT used by the clinical event ETLs."""
    clean_id = normalise_fhir_reference(source_id)
    return int(hashlib.sha256(clean_id.encode("utf-8")).hexdigest()[:15], 16)
