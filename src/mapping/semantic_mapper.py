"""Governed multidomain retrieval and structured local-LLM adjudication."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb
import ollama

from src.clinical_mapping_core import (
    DECISION_SCHEMA,
    PROMPT_VERSION,
    Candidate,
    MappingRequest,
    ModelProvenance,
    parse_mapping_decision,
    render_mapping_prompt,
)
from src.mapping.governance import current_run_id
from src.mapping.mapping_service import (
    TARGETS,
    MappingSourceTerm,
    get_few_shot_prompt,
    get_versioned_collection,
    reconcile_resolved_proposals,
    record_mapping_abstention,
    record_mapping_proposal,
    selected_candidate,
)
from src.security.privacy import (
    audit_security_event,
    redact_direct_identifiers,
    validate_privacy_runtime,
)
from src.utils.config import CHROMA_PATH, DB_PATH, MODEL_NAME, OLLAMA_TIMEOUT, OLLAMA_URL

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATION_PARAMETERS = {"temperature": 0.0, "seed": 0, "num_predict": 512}

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
    """Compatibility facade for the extracted fail-closed core contract."""
    return parse_mapping_decision(content, candidate_ids).to_dict()


def build_prompt(target_table: str, source_value: str, candidates: list[dict], few_shot="") -> str:
    config = TARGETS[target_table]
    prompt = DOMAIN_PROMPTS[target_table]
    request = MappingRequest(
        source_value=source_value,
        target_domain=config["domain"],
        target_vocabulary=config["vocabulary"],
        candidates=tuple(Candidate.from_mapping(candidate) for candidate in candidates),
    )
    return render_mapping_prompt(
        request,
        role=prompt["role"],
        guidance=prompt["guidance"],
        few_shot=few_shot,
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


def _unmapped_terms(con, target_table: str) -> list[MappingSourceTerm]:
    config = TARGETS[target_table]
    has_fhir_identity = bool(con.execute("""
        SELECT COUNT(*) FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_name = 'fhir_event_source_coding'
    """).fetchone()[0])
    if has_fhir_identity:
        return [
            MappingSourceTerm(*row)
            for row in con.execute(f"""
                SELECT DISTINCT event.{config['source_column']},
                       coding.source_system_uri,
                       coding.source_vocabulary_id,
                       coding.source_code
                FROM {target_table} event
                LEFT JOIN fhir_event_source_coding coding
                  ON coding.target_table = ?
                 AND coding.target_id = event.{config['id_column']}
                WHERE event.{config['concept_column']} = 0
                  AND event.{config['source_column']} IS NOT NULL
                ORDER BY LOWER(TRIM(event.{config['source_column']})),
                         COALESCE(coding.source_vocabulary_id, ''),
                         COALESCE(coding.source_code, '')
            """, [target_table]).fetchall()
        ]
    return [
        MappingSourceTerm(row[0])
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
        timeout=OLLAMA_TIMEOUT,
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
        provenance = ModelProvenance(
            model_name=MODEL_NAME,
            prompt_version=PROMPT_VERSION,
            model_digest=digest,
            generation_parameters=GENERATION_PARAMETERS,
            index_signature=index_signature,
        )

        for position, source_term in enumerate(terms, start=1):
            source_value = source_term.source_value
            search = collection.query(query_texts=[source_value], n_results=5)
            ids = search.get("ids", [[]])[0]
            documents = search.get("documents", [[]])[0]
            if not ids:
                raise RuntimeError(f"No retrieval candidates for {source_value!r}")
            candidates = [
                {"concept_id": int(concept_id), "concept_name": documents[index]}
                for index, concept_id in enumerate(ids)
            ]
            prompt_input = source_value
            if source_term.is_scoped:
                prompt_input = (
                    f"{source_value}\nSource terminology: "
                    f"{source_term.source_vocabulary_id}; "
                    f"system={source_term.source_system}; "
                    f"code={source_term.source_code}"
                )
            prompt_source, redaction_categories = redact_direct_identifiers(
                prompt_input
            )
            prompt = build_prompt(target_table, prompt_source, candidates, few_shot)
            response = client.chat(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                format=DECISION_SCHEMA,
                options=GENERATION_PARAMETERS,
            )
            decision_contract = parse_mapping_decision(_response_content(response), ids)
            sanitized_reason, reason_categories = redact_direct_identifiers(
                decision_contract.reason
            )
            sanitized_signals = []
            signal_categories = []
            for signal in decision_contract.clinical_signals:
                sanitized, categories = redact_direct_identifiers(signal)
                sanitized_signals.append(sanitized)
                signal_categories.extend(categories)
            decision_contract = replace(
                decision_contract,
                reason=sanitized_reason,
                clinical_signals=tuple(sanitized_signals),
            )
            decision = decision_contract.to_dict()
            redaction_categories = sorted(set(
                redaction_categories + reason_categories + signal_categories
            ))
            metadata = provenance.decision_metadata(decision_contract)
            if decision["decision"] == "ABSTAIN":
                status, event_count = record_mapping_abstention(
                    con,
                    target_table,
                    source_value,
                    metadata,
                    source_term=source_term,
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
                    source_term=source_term,
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
