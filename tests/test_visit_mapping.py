from src.etl.visit import DEFAULT_VISIT_CONCEPT_ID, VISIT_MAPPING


def test_synthea_home_and_virtual_encounters_use_standard_visit_concepts():
    assert VISIT_MAPPING["HH"] == 581476
    assert VISIT_MAPPING["VR"] == 722455


def test_unknown_encounter_class_remains_unmapped():
    assert VISIT_MAPPING.get("UNKNOWN", DEFAULT_VISIT_CONCEPT_ID) == 0
