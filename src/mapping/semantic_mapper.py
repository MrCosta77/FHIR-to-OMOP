"""Governed multidomain retrieval and structured local-LLM adjudication."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import ollama

from src.mapping.mapping_service import (
    TARGETS,
    get_few_shot_prompt,
    get_versioned_collection,
    reconcile_resolved_proposals,
    record_mapping_abstention,
    record_mapping_proposal,
    selected_candidate,
)
from src.mapping.governance import current_run_id
from src.security.privacy import (
    audit_security_event,
    redact_direct_identifiers,
    validate_privacy_runtime,
)
from src.utils.config import CHROMA_PATH, DB_PATH, MODEL_NAME, OLLAMA_URL


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_VERSION = "mapping-json-v2"
GENERATION_PARAMETERS = {"temperature": 0.0, "seed": 0, "num_predict": 512}
OLLAMA_TIMEOUT_SECONDS = 120.0
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

DOMAIN_PROMPTS = {
    "condition_occurrence": {
        "label": "SNOMED CT Condition",
        "role": "clinical terminology specialist",
        "guidance": "Use only disorder/condition meaning and clinically relevant qualifiers.",
    },
    "drug_exposure": {
        "label": "RxNorm Drug",
        "role": "clinical pharmacist",
        "guidance": "Check ingredient, strength, dose form and route; abstain when these conflict.",
    },
    "measurement": {
        "label": "LOINC Measurement",
        "role": "laboratory terminology specialist",
        "guidance": "Check analyte, specimen, property, timing, method and units; abstain on ambiguity.",
    },
    "procedure_occurrence": {
        "label": "SNOMED Procedure",
        "role": "clinical procedure terminology specialist",
        "guidance": "Check action, anatomy, approach and intent; do not select observations or devices.",
    },
    "observation": {
        "label": "Standard Observation",
        "role": "clinical observation terminology specialist",
        "guidance": "Check the observed meaning and value context; do not select measurements, conditions or procedures.",
    },
    "device_exposure": {
        "label": "SNOMED Device",
        "role": "medical device terminology specialist",
        "guidance": "Select only a concrete device concept; do not select implantation procedures or device findings.",
    },
}


def parse_llm_decision(content: str, candidate_ids) -> dict:
    """Validate the exact fail-closed JSON contract returned by the local LLM."""
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
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("LLM confidence must be between 0 and 1")
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
    payload["confidence"] = float(confidence)
    payload["reason"] = reason.strip()
    payload["clinical_signals"] = [item.strip() for item in signals]
    return payload


def build_prompt(target_table: str, source_value: str, candidates: list[dict], few_shot="") -> str:
    config = TARGETS[target_table]
    prompt = DOMAIN_PROMPTS[target_table]
    return (
        f"You are a {prompt['role']} mapping dirty hospital data to OMOP.\n"
        f"Target domain: {config['domain']}. Target vocabulary: {config['vocabulary']}.\n"
        f"{prompt['guidance']}\n"
        "You may select only a concept_id from the supplied candidates. "
        "If no candidate is clinically safe, use ABSTAIN. Never invent an ID.\n"
        "Keep reason under 300 characters and provide at most 6 concise clinical signals.\n"
        f"{few_shot}"
        f"Source value: {json.dumps(source_value, ensure_ascii=False)}\n"
        f"Candidates: {json.dumps(candidates, ensure_ascii=False)}\n"
        "Return only JSON matching the supplied schema."
    )


def _response_content(response) -> str:
    message = getattr(response, "message", None)
    if message is not None:
        return getattr(message, "content", None) or message["content"]
    return response["message"]["content"]


def _model_digest(client, model_name: str) -> str | None:
    try:
        listing = client.list()
        models = getattr(listing, "models", None) or listing.get("models", [])
        for model in models:
            name = getattr(model, "model", None) or getattr(model, "name", None)
            if name is None and isinstance(model, dict):
                name = model.get("model") or model.get("name")
            if name == model_name:
                digest = getattr(model, "digest", None)
                if digest is None and isinstance(model, dict):
                    digest = model.get("digest")
                return digest
    except Exception:
        return None
    return None


def _unmapped_terms(con, target_table: str) -> list[str]:
    config = TARGETS[target_table]
    return [
        row[0]
        for row in con.execute(f"""
            SELECT DISTINCT {config['source_column']}
            FROM {target_table}
            WHERE {config['concept_column']} = 0
              AND {config['source_column']} IS NOT NULL
            ORDER BY LOWER(TRIM({config['source_column']}))
        """).fetchall()
    ]


def run_semantic_mapping(
    target_table: str,
    *,
    db_path=DB_PATH,
    chroma_path=CHROMA_PATH,
    client=None,
) -> dict:
    """Run one governed domain adapter without publishing any mapping."""
    if target_table not in DOMAIN_PROMPTS:
        raise ValueError(f"Unsupported semantic mapping target: {target_table}")
    privacy = validate_privacy_runtime(OLLAMA_URL)
    client = client or ollama.Client(
        host=OLLAMA_URL.rsplit("/api/", 1)[0],
        timeout=OLLAMA_TIMEOUT_SECONDS,
    )
    config = TARGETS[target_table]
    result = {"target_table": target_table, "terms": 0, "proposals": 0, "abstentions": 0}
    print(f"STARTING GOVERNED LOCAL-LLM MAPPING: {target_table}")
    with duckdb.connect(str(db_path)) as con:
        retired = reconcile_resolved_proposals(con, target_table)
        terms = _unmapped_terms(con, target_table)
        result["retired"] = retired
        result["terms"] = len(terms)
        if not terms:
            print(f"No unmapped {config['domain']} terms found.")
            return result

        collection = get_versioned_collection(con, str(chroma_path), target_table)
        if collection.count() == 0:
            raise ValueError(f"{config['collection']} vector store is empty")
        few_shot = get_few_shot_prompt(
            con, target_table, DOMAIN_PROMPTS[target_table]["label"], 3
        )
        digest = _model_digest(client, MODEL_NAME)
        index_signature = (collection.metadata or {}).get("index_signature")

        for position, source_value in enumerate(terms, start=1):
            search = collection.query(query_texts=[source_value], n_results=5)
            ids = search.get("ids", [[]])[0]
            documents = search.get("documents", [[]])[0]
            if not ids:
                raise RuntimeError(f"No retrieval candidates for {source_value!r}")
            candidates = [
                {"concept_id": int(concept_id), "concept_name": documents[index]}
                for index, concept_id in enumerate(ids)
            ]
            prompt_source, redaction_categories = redact_direct_identifiers(source_value)
            prompt = build_prompt(target_table, prompt_source, candidates, few_shot)
            response = client.chat(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                format=DECISION_SCHEMA,
                options=GENERATION_PARAMETERS,
            )
            decision = parse_llm_decision(_response_content(response), ids)
            decision["reason"], reason_categories = redact_direct_identifiers(
                decision["reason"]
            )
            sanitized_signals = []
            signal_categories = []
            for signal in decision["clinical_signals"]:
                sanitized, categories = redact_direct_identifiers(signal)
                sanitized_signals.append(sanitized)
                signal_categories.extend(categories)
            decision["clinical_signals"] = sanitized_signals
            redaction_categories = sorted(set(
                redaction_categories + reason_categories + signal_categories
            ))
            metadata = {
                **decision,
                "prompt_version": PROMPT_VERSION,
                "model_digest": digest,
                "generation_parameters": GENERATION_PARAMETERS,
                "index_signature": index_signature,
            }
            if decision["decision"] == "ABSTAIN":
                status, event_count = record_mapping_abstention(
                    con, target_table, source_value, metadata
                )
                result["abstentions"] += 1
            else:
                match = selected_candidate(
                    search, str(decision["selected_concept_id"]),
                    (collection.metadata or {}).get("distance_metric", "cosine"),
                )
                if match is None:
                    raise ValueError("Validated candidate could not be reconciled with retrieval")
                concept_id, concept_name, distance, retrieval_score = match
                governed_score = min(retrieval_score, decision["confidence"])
                status, event_count = record_mapping_proposal(
                    con, target_table, source_value,
                    (concept_id, concept_name, distance, governed_score), metadata,
                )
                result["proposals"] += 1
            audit_security_event(
                con,
                "LOCAL_LLM_MAPPING_DECISION",
                "LOCAL_MAPPING_ENGINE",
                status,
                {
                    "target_table": target_table,
                    "model": MODEL_NAME,
                    "decision": decision["decision"],
                    "redaction_categories": redaction_categories,
                    "data_classification": privacy["classification"],
                },
                run_id=current_run_id(),
            )
            print(
                f"[{position}/{len(terms)}] {source_value!r}: {status}; "
                f"events={event_count}"
            )
    return result
