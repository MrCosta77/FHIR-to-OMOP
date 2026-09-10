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
    parse_mapping_decision_fail_safe,
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


@pytest.mark.parametrize("confidence", [True, "0.94", -0.1, 1.1, float("nan")])
def test_core_rejects_invalid_or_uncalibrated_confidence(confidence):
    payload = json.loads(_payload())
    payload["confidence"] = confidence

    with pytest.raises(ValueError, match="confidence"):
        parse_mapping_decision(json.dumps(payload), [1004])


def test_core_rejects_inconsistent_abstention_payload():
    with pytest.raises(ValueError, match="selected_concept_id=null"):
        parse_mapping_decision(_payload(decision="ABSTAIN", concept_id=1004), [1004])


@pytest.mark.parametrize(
    "content",
    [
        "{not-json",
        _payload(concept_id=9999),
        json.dumps({
            "decision": "SELECT",
            "selected_concept_id": 1004,
            "confidence": 85,
            "reason": "Invalid confidence scale.",
            "clinical_signals": [],
        }),
    ],
)
def test_fail_safe_parser_converts_contract_errors_to_technical_abstention(content):
    decision, error = parse_mapping_decision_fail_safe(content, [1004])

    assert decision.decision is DecisionKind.ABSTAIN
    assert decision.selected_concept_id is None
    assert decision.confidence == 0.0
    assert decision.reason == "INVALID_LLM_RESPONSE"
    assert error
    assert content not in error


def test_fail_safe_parser_preserves_valid_decision_without_error():
    decision, error = parse_mapping_decision_fail_safe(_payload(), [1004])

    assert decision.decision is DecisionKind.SELECT
    assert decision.selected_concept_id == 1004
    assert error is None


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
    assert "Missing detail should lower confidence" in prompt
    assert "unrelated distractors" in prompt
    assert "Use ABSTAIN" in prompt
    assert "only a proposal requiring human review" in prompt
    assert "Never invent an ID" in prompt
    assert "MUST be a decimal" in prompt

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
