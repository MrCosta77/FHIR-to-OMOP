"""Portable value contracts with no database, model, or framework dependency."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class DecisionKind(str, Enum):
    """The only outcomes a governed mapping model may return."""

    SELECT = "SELECT"
    ABSTAIN = "ABSTAIN"


@dataclass(frozen=True, slots=True)
class Candidate:
    """One terminology concept made available to the decision model."""

    concept_id: int
    concept_name: str

    def __post_init__(self) -> None:
        if isinstance(self.concept_id, bool) or not isinstance(self.concept_id, int):
            raise ValueError("Candidate concept_id must be an integer")
        if not isinstance(self.concept_name, str) or not self.concept_name.strip():
            raise ValueError("Candidate concept_name must be non-empty")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Candidate:
        return cls(
            concept_id=value["concept_id"],
            concept_name=value["concept_name"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {"concept_id": self.concept_id, "concept_name": self.concept_name}


@dataclass(frozen=True, slots=True)
class MappingRequest:
    """Model-facing request independent of FHIR, OMOP storage, and retrieval."""

    source_value: str
    target_domain: str
    target_vocabulary: str
    candidates: tuple[Candidate, ...]
    context: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.source_value, str):
            raise ValueError("Mapping source_value must be text")
        if not isinstance(self.target_domain, str) or not self.target_domain.strip():
            raise ValueError("Mapping target_domain must be non-empty")
        if not isinstance(self.target_vocabulary, str) or not self.target_vocabulary.strip():
            raise ValueError("Mapping target_vocabulary must be non-empty")
        if not self.candidates:
            raise ValueError("Mapping request requires at least one candidate")
        context_keys = []
        for item in self.context:
            if (
                not isinstance(item, tuple) or len(item) != 2
                or not all(isinstance(value, str) for value in item)
                or not item[0].strip() or not item[1].strip()
            ):
                raise ValueError("Mapping context requires non-empty text key/value pairs")
            context_keys.append(item[0])
        if len(context_keys) != len(set(context_keys)):
            raise ValueError("Mapping context keys must be unique")


@dataclass(frozen=True, slots=True)
class MappingDecision:
    """Validated SELECT/ABSTAIN result returned by a decision model."""

    decision: DecisionKind
    selected_concept_id: int | None
    confidence: float
    reason: str
    clinical_signals: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.decision is DecisionKind.ABSTAIN and self.selected_concept_id is not None:
            raise ValueError("ABSTAIN requires selected_concept_id=null")
        if self.decision is DecisionKind.SELECT and (
            isinstance(self.selected_concept_id, bool)
            or not isinstance(self.selected_concept_id, int)
        ):
            raise ValueError("SELECT requires an integer selected_concept_id")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise ValueError("Mapping confidence must be numeric")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("Mapping confidence must be between 0 and 1")
        if not isinstance(self.reason, str) or not self.reason.strip() or len(self.reason) > 300:
            raise ValueError("Mapping reason must contain 1 to 300 characters")
        if len(self.clinical_signals) > 6 or any(
            not isinstance(item, str) or not item.strip() or len(item) > 100
            for item in self.clinical_signals
        ):
            raise ValueError("Mapping clinical_signals violate the governed contract")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "selected_concept_id": self.selected_concept_id,
            "confidence": float(self.confidence),
            "reason": self.reason,
            "clinical_signals": list(self.clinical_signals),
        }


@dataclass(frozen=True, slots=True)
class ModelProvenance:
    """Portable provenance attached to one validated model decision."""

    model_name: str
    prompt_version: str
    model_digest: str | None
    generation_parameters: Mapping[str, Any]
    index_signature: str | None

    def decision_metadata(self, decision: MappingDecision) -> dict[str, Any]:
        return {
            **decision.to_dict(),
            "model_name": self.model_name,
            "prompt_version": self.prompt_version,
            "model_digest": self.model_digest,
            "generation_parameters": dict(self.generation_parameters),
            "index_signature": self.index_signature,
        }
