import sys
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.helpers import normalise_fhir_reference, stable_event_id, stable_person_id


def test_stable_person_id_handles_all_reference_formats():
    """Garante que qualquer formato de referência FHIR gera o mesmo Patient ID no OMOP."""
    base_uuid = "1234-5678-90ab-cdef"

    id1 = stable_person_id(f"urn:uuid:{base_uuid}")
    id2 = stable_person_id(f"Patient/{base_uuid}")
    id3 = stable_person_id(base_uuid)

    assert id1 == id2 == id3, "CRITICAL: Os IDs gerados não são iguais para o mesmo doente!"


def test_versioned_and_absolute_fhir_references_normalise_to_the_same_id():
    assert normalise_fhir_reference("https://hospital/fhir/Encounter/abc/_history/7") == "abc"
    assert stable_event_id("Encounter/abc") == stable_event_id("urn:uuid:abc")
