import hashlib

def normalise_fhir_reference(ref: str) -> str:
    """Reduce any FHIR reference form to the bare resource UUID."""
    return ref.split('/')[-1].replace('urn:uuid:', '').strip()

def stable_person_id(source_id: str) -> int:
    """Generates a highly stable, collision-resistant BIGINT from any FHIR ID format."""
    clean_id = normalise_fhir_reference(source_id)
    return int(hashlib.sha256(clean_id.encode('utf-8')).hexdigest(), 16) % (2**62)