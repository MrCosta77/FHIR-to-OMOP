"""Fail-closed decision parsing and deterministic prompt rendering."""

from __future__ import annotations

import json
import math

from src.clinical_mapping_core.contracts import (
    DecisionKind,
    MappingDecision,
    MappingRequest,
)

PROMPT_VERSION = "mapping-json-v4"
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision", "selected_concept_id", "confidence", "reason",
        "clinical_signals",
    ],
    "properties": {
        "decision": {"type": "string", "enum": ["SELECT", "ABSTAIN"]},
        "selected_concept_id": {"type": ["integer", "null"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reason": {"type": "string", "minLength": 1, "maxLength": 300},
        "clinical_signals": {
            "type": "array",
            "maxItems": 6,
            "items": {"type": "string", "minLength": 1, "maxLength": 100},
        },
    },
}


def parse_mapping_decision(content: str, candidate_ids) -> MappingDecision:
    """Validate the exact JSON contract and bind SELECT to supplied candidates."""
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("LLM response is not valid JSON") from exc
    required = {
        "decision", "selected_concept_id", "confidence", "reason",
        "clinical_signals",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError("LLM response does not match the decision schema")
    if payload["decision"] not in {"SELECT", "ABSTAIN"}:
        raise ValueError("LLM decision must be SELECT or ABSTAIN")
    confidence = payload["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("LLM confidence must be numeric")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise ValueError("LLM confidence must be finite and between 0 and 1")
    reason = payload["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("LLM reason must be non-empty")
    if len(reason) > 300:
        raise ValueError("LLM reason exceeds the schema limit")
    signals = payload["clinical_signals"]
    if (
        not isinstance(signals, list) or len(signals) > 6
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 100
            for item in signals
        )
    ):
        raise ValueError("LLM clinical_signals must be a list of non-empty strings")

    allowed_ids = {int(value) for value in candidate_ids}
    selected = payload["selected_concept_id"]
    if payload["decision"] == "ABSTAIN":
        if selected is not None:
            raise ValueError("ABSTAIN requires selected_concept_id=null")
    elif (
        isinstance(selected, bool) or not isinstance(selected, int)
        or selected not in allowed_ids
    ):
        raise ValueError("SELECT must use exactly one retrieved candidate ID")

    return MappingDecision(
        decision=DecisionKind(payload["decision"]),
        selected_concept_id=selected,
        confidence=float(confidence),
        reason=reason.strip(),
        clinical_signals=tuple(item.strip() for item in signals),
    )


def parse_mapping_decision_fail_safe(
    content: str, candidate_ids
) -> tuple[MappingDecision, str | None]:
    """Convert a strict contract failure into an explicit technical abstention.

    The parser remains fail-closed. This operational boundary prevents one
    malformed local-model response from terminating a batch, while returning
    only a controlled validator message and never the raw model response.
    """
    try:
        return parse_mapping_decision(content, candidate_ids), None
    except ValueError as exc:
        return (
            MappingDecision(
                decision=DecisionKind.ABSTAIN,
                selected_concept_id=None,
                confidence=0.0,
                reason="INVALID_LLM_RESPONSE",
                clinical_signals=(),
            ),
            str(exc),
        )


def render_mapping_prompt(
    request: MappingRequest,
    *,
    role: str,
    guidance: str,
    few_shot: str = "",
) -> str:
    """Render a stable model prompt from a portable mapping request."""
    candidates = [candidate.to_dict() for candidate in request.candidates]
    context_line = ""
    if request.context:
        context = dict(sorted(request.context))
        context_line = f"Context: {json.dumps(context, ensure_ascii=False)}\n"
    return (
        f"You are a {role} mapping dirty hospital data to OMOP.\n"
        f"Target domain: {request.target_domain}. "
        f"Target vocabulary: {request.target_vocabulary}.\n"
        f"{guidance}\n"
        "Select a candidate when it directly represents the known source meaning, "
        "including a recognizable synonym, abbreviation, translation, formatting "
        "variation, or legacy label. Missing detail should lower confidence; it "
        "does not by itself require ABSTAIN. Evaluate each candidate independently: "
        "unrelated distractors elsewhere in the list do not make a good candidate "
        "ambiguous. Use ABSTAIN when no supplied candidate represents the known "
        "meaning or when known attributes explicitly conflict. Every SELECT is only "
        "a proposal requiring human review. You may select only a supplied "
        "concept_id; Never invent an ID.\n"
        "The confidence field MUST be a decimal between 0.0 and 1.0 (e.g., 0.85, never 85).\n"
        "Keep reason under 300 characters and provide at most 6 concise clinical signals.\n"
        f"{few_shot}"
        f"Source value: {json.dumps(request.source_value, ensure_ascii=False)}\n"
        f"{context_line}"
        f"Candidates: {json.dumps(candidates, ensure_ascii=False)}\n"
        "Return only JSON matching the supplied schema."
    )
