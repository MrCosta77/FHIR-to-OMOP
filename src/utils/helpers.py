import hashlib
from urllib.parse import urlsplit, urlunsplit


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


def canonical_fhir_identity(ref: str) -> str:
    """Preserve an absolute source namespace while normalising local/URN IDs."""
    value = str(ref or "").strip()
    if value.casefold().startswith("urn:uuid:"):
        return value[9:].strip()
    parsed = urlsplit(value)
    if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc:
        path_parts = parsed.path.rstrip("/").split("/")
        if "_history" in path_parts:
            path_parts = path_parts[:path_parts.index("_history")]
        path = "/".join(path_parts)
        return urlunsplit((
            parsed.scheme.casefold(), parsed.netloc.casefold(), path, "", ""
        ))
    return normalise_fhir_reference(value)


def build_fhir_reference_index(bundle: dict) -> dict[str, str]:
    """Resolve relative Bundle references to their globally scoped fullUrl."""
    index: dict[str, str] = {}
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        resource_type = str(resource.get("resourceType", "")).strip()
        resource_id = str(resource.get("id", "")).strip()
        full_url = str(entry.get("fullUrl", "")).strip()
        if not resource_type or not resource_id or not full_url:
            continue
        index[full_url] = full_url
        index[f"{resource_type}/{resource_id}"] = full_url
    return index


def resolve_fhir_reference(reference: str, index: dict[str, str]) -> str:
    value = str(reference or "").strip()
    return index.get(value, value)

def stable_person_id(source_id: str) -> int:
    """Generates a highly stable, collision-resistant BIGINT from any FHIR ID format."""
    clean_id = canonical_fhir_identity(source_id)
    return int(hashlib.sha256(clean_id.encode('utf-8')).hexdigest(), 16) % (2**62)


def stable_event_id(source_id: str) -> int:
    """Match the deterministic BIGINT used by the clinical event ETLs."""
    clean_id = canonical_fhir_identity(source_id)
    return int(hashlib.sha256(clean_id.encode("utf-8")).hexdigest()[:15], 16)
