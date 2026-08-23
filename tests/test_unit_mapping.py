from src.utils.unit_mapping import UCUM_SYSTEM, canonical_ucum_code


def test_exact_ucum_code_is_preserved():
    assert canonical_ucum_code(UCUM_SYSTEM, "mg/dL") == "mg/dL"
    assert canonical_ucum_code(UCUM_SYSTEM, "pH") == "pH"


def test_reviewed_fhir_alias_is_normalized():
    assert canonical_ucum_code(UCUM_SYSTEM, "U/L") == "[U]/L"
    assert canonical_ucum_code(UCUM_SYSTEM, "ng/dl") == "ng/dL"
    assert canonical_ucum_code(UCUM_SYSTEM, "{score}") == "[score]"


def test_non_ucum_or_missing_code_is_not_guessed():
    assert canonical_ucum_code("http://example.test/units", "mg/dL") is None
    assert canonical_ucum_code(UCUM_SYSTEM, None) is None
