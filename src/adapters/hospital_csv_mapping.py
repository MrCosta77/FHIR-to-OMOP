"""Governed local-LLM mapping runner for validated hospital CSV records."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import duckdb
import ollama

from src.adapters.hospital_csv import SCHEMA_VERSION, load_hospital_csv
from src.clinical_mapping_core import (
    Candidate,
    DECISION_SCHEMA,
    ModelProvenance,
    PROMPT_VERSION,
    parse_mapping_decision,
    render_mapping_prompt,
)
from src.mapping.governance import current_run_id, ensure_governance_tables
from src.mapping.mapping_service import (
    get_few_shot_prompt,
    get_versioned_collection,
    record_external_mapping_decision,
    selected_candidate,
)
from src.mapping.semantic_mapper import (
    DOMAIN_PROMPTS,
    GENERATION_PARAMETERS,
    OLLAMA_TIMEOUT_SECONDS,
    _model_digest,
    _response_content,
)
from src.security.privacy import (
    audit_security_event,
    redact_direct_identifiers,
    validate_privacy_runtime,
)
from src.utils.config import CHROMA_PATH, DB_PATH, MODEL_NAME, OLLAMA_URL


def run_hospital_csv_mapping(
    path, *, db_path=DB_PATH, chroma_path=CHROMA_PATH, client=None
):
    """Retrieve and persist governed proposals; never publish CSV mappings."""
    records = load_hospital_csv(path)
    privacy = validate_privacy_runtime(OLLAMA_URL)
    client = client or ollama.Client(
        host=OLLAMA_URL.rsplit("/api/", 1)[0], timeout=OLLAMA_TIMEOUT_SECONDS
    )
    result = {
        "source_adapter": SCHEMA_VERSION,
        "records": len(records),
        "proposals": 0,
        "abstentions": 0,
        "persisted": 0,
    }
    with duckdb.connect(str(db_path)) as con:
        ensure_governance_tables(con)
        runtime = {}
        for record in records:
            if record.target_table not in runtime:
                collection = get_versioned_collection(
                    con, str(chroma_path), record.target_table
                )
                if collection.count() == 0:
                    raise ValueError(f"{record.target_table} vector store is empty")
                domain_prompt = DOMAIN_PROMPTS[record.target_table]
                runtime[record.target_table] = (
                    collection,
                    get_few_shot_prompt(
                        con, record.target_table, domain_prompt["label"], 3
                    ),
                    ModelProvenance(
                        model_name=MODEL_NAME,
                        prompt_version=PROMPT_VERSION,
                        model_digest=_model_digest(client, MODEL_NAME),
                        generation_parameters=GENERATION_PARAMETERS,
                        index_signature=(collection.metadata or {}).get(
                            "index_signature"
                        ),
                    ),
                )
            collection, few_shot, provenance = runtime[record.target_table]
            retrieval_text, retrieval_categories = record.prepare_retrieval_text()
            search = collection.query(query_texts=[retrieval_text], n_results=5)
            ids = search.get("ids", [[]])[0]
            documents = search.get("documents", [[]])[0]
            if not ids:
                raise RuntimeError(
                    f"No retrieval candidates for record {record.source_record_key}"
                )
            prepared = record.prepare_mapping_request(
                Candidate(int(concept_id), documents[index])
                for index, concept_id in enumerate(ids)
            )
            prompt_config = DOMAIN_PROMPTS[record.target_table]
            prompt = render_mapping_prompt(
                prepared.request,
                role=prompt_config["role"],
                guidance=prompt_config["guidance"],
                few_shot=few_shot,
            )
            response = client.chat(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                format=DECISION_SCHEMA,
                options=GENERATION_PARAMETERS,
            )
            decision = parse_mapping_decision(_response_content(response), ids)
            reason, reason_categories = redact_direct_identifiers(decision.reason)
            signals = []
            signal_categories = []
            for signal in decision.clinical_signals:
                sanitized, categories = redact_direct_identifiers(signal)
                signals.append(sanitized)
                signal_categories.extend(categories)
            decision = replace(
                decision, reason=reason, clinical_signals=tuple(signals)
            )
            match = None
            if decision.decision.value == "SELECT":
                match = selected_candidate(
                    search,
                    str(decision.selected_concept_id),
                    (collection.metadata or {}).get("distance_metric", "cosine"),
                )
                if match is None:
                    raise ValueError(
                        "Validated candidate could not be reconciled with retrieval"
                    )
                concept_id, concept_name, distance, retrieval_score = match
                match = (
                    concept_id,
                    concept_name,
                    distance,
                    min(retrieval_score, float(decision.confidence)),
                )
                result["proposals"] += 1
            else:
                result["abstentions"] += 1
            status, persisted = record_external_mapping_decision(
                con,
                record.target_table,
                prepared.request.source_value,
                record.source_record_key,
                match,
                provenance.decision_metadata(decision),
                source_adapter=SCHEMA_VERSION,
            )
            result["persisted"] += persisted
            categories = sorted(
                set(
                    retrieval_categories
                    + prepared.redaction_categories
                    + tuple(reason_categories)
                    + tuple(signal_categories)
                )
            )
            audit_security_event(
                con,
                "HOSPITAL_CSV_LOCAL_LLM_DECISION",
                "LOCAL_MAPPING_ENGINE",
                status,
                {
                    "target_table": record.target_table,
                    "model": MODEL_NAME,
                    "decision": decision.decision.value,
                    "redaction_categories": categories,
                    "data_classification": privacy["classification"],
                    "publication_eligible": False,
                },
                run_id=current_run_id(),
            )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run governed local-LLM proposals for hospital-csv-v1 without publication."
        )
    )
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(run_hospital_csv_mapping(args.path), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
