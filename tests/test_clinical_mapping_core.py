import ast
import json
from pathlib import Path

import pytest

from src.clinical_mapping_core import (
    Candidate,
    DecisionKind,
    MappingRequest,
    ModelProvenance,
    parse_mapping_decision,
    render_mapping_prompt,
)

CORE_DIR = Path(__file__).resolve().parents[1] / "src" / "clinical_mapping_core"


def _payload(decision="SELECT", concept_id=1004):
    return json.dumps({
        "decision": decision,
        "selected_concept_id": concept_id,
        "confidence": 0.94,
        "reason": "The action and anatomy match the candidate.",
        "clinical_signals": ["procedure action", "anatomy"],
    })


def test_core_parses_a_typed_decision_bound_to_retrieved_candidates():
    decision = parse_mapping_decision(_payload(), [1004, 2004])

    assert decision.decision is DecisionKind.SELECT
    assert decision.selected_concept_id == 1004
    assert decision.to_dict()["clinical_signals"] == ["procedure action", "anatomy"]

    with pytest.raises(ValueError, match="retrieved candidate"):
        parse_mapping_decision(_payload(concept_id=9999), [1004, 2004])


def test_core_models_abstention_as_a_normal_typed_result():
    decision = parse_mapping_decision(_payload("ABSTAIN", None), [1004])

    assert decision.decision is DecisionKind.ABSTAIN
    assert decision.selected_concept_id is None


def test_core_renders_a_stable_prompt_without_adapter_or_storage_objects():
    request = MappingRequest(
        source_value="legacy appendectomy",
        target_domain="Procedure",
        target_vocabulary="SNOMED",
        candidates=(Candidate(1004, "Appendectomy"),),
    )

    prompt = render_mapping_prompt(
        request,
        role="clinical procedure terminology specialist",
        guidance="Check action and anatomy.",
    )

    assert "Target domain: Procedure" in prompt
    assert '"concept_id": 1004' in prompt
    assert "Never invent an ID" in prompt


def test_model_provenance_attaches_only_portable_decision_metadata():
    decision = parse_mapping_decision(_payload(), [1004])
    provenance = ModelProvenance(
        model_name="local-model",
        prompt_version="mapping-json-v2",
        model_digest="sha256:test",
        generation_parameters={"temperature": 0.0},
        index_signature="index-test",
    )

    metadata = provenance.decision_metadata(decision)

    assert metadata["selected_concept_id"] == 1004
    assert metadata["model_name"] == "local-model"
    assert metadata["model_digest"] == "sha256:test"
    assert metadata["generation_parameters"] == {"temperature": 0.0}


def test_core_import_boundary_excludes_runtime_frameworks_and_project_adapters():
    forbidden_roots = {
        "chromadb", "duckdb", "ollama", "pandas", "streamlit",
        "src.mapping", "src.security", "src.utils",
    }
    imported = set()
    for path in CORE_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

    violations = sorted(
        module
        for module in imported
        if any(module == root or module.startswith(f"{root}.") for root in forbidden_roots)
    )
    assert violations == []
