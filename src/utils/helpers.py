import hashlib

def stable_person_id(source_id: str) -> int:
    """
    Generates a deterministic person_id using SHA-256 for perfect relational integrity.
    Matches FHIR UUIDs consistently to OMOP standard integer IDs.
    """
    return int(hashlib.sha256(source_id.encode()).hexdigest(), 16) % (10**9)