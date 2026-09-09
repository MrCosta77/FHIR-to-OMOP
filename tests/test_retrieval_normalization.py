import json

import pytest

from src.mapping.retrieval_normalization import (
    load_lis_aliases,
    normalize_retrieval_text,
)


def test_versioned_lis_alias_expands_retrieval_without_assigning_a_concept():
    result = normalize_retrieval_text("  hGb  ", "measurement")

    assert result.original_text == "  hGb  "
    assert result.retrieval_text == "Hemoglobin [Mass/volume] in Blood"
    assert result.alias_id == "LIS-HGB"
    assert result.lexicon_version == "lis-aliases-v1"
    assert len(result.lexicon_sha256) == 64


def test_unknown_and_nonmeasurement_terms_remain_unchanged():
    unknown = normalize_retrieval_text("Unknown lab", "measurement")
    condition = normalize_retrieval_text("HGB", "condition_occurrence")

    assert unknown.retrieval_text == "Unknown lab"
    assert unknown.alias_applied is False
    assert condition.retrieval_text == "HGB"
    assert condition.lexicon_version is None


def test_alias_lexicon_rejects_duplicate_normalized_aliases(tmp_path):
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "lexicon_version": "test-v1",
        "scope": "test",
        "target_table": "measurement",
        "aliases": [
            {"alias_id": "A", "alias": "HGB", "retrieval_expansion": "Hemoglobin"},
            {"alias_id": "B", "alias": " hgb ", "retrieval_expansion": "Hemoglobin"},
        ],
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="must be unique"):
        load_lis_aliases(path)
