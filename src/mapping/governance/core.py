from __future__ import annotations

import os
import uuid

TARGET_GOVERNANCE = {
    "condition_occurrence": ("CMF_SYNTHEA_CONDITION", "SNOMED", "Condition"),
    "drug_exposure": ("CMF_SYNTHEA_DRUG", "RxNorm", "Drug"),
    "measurement": ("CMF_SYNTHEA_MEASUREMENT", "LOINC", "Measurement"),
    "observation": ("CMF_SYNTHEA_OBSERVATION", "SNOMED", "Observation"),
    "procedure_occurrence": ("CMF_SYNTHEA_PROCEDURE", "SNOMED", "Procedure"),
    "device_exposure": ("CMF_SYNTHEA_DEVICE", "SNOMED", "Device"),
}

def current_run_id() -> str | None:
    return os.environ.get("CMF_RUN_ID") or None

def decision_id_for(
    run_id, target_table, source_value, concept_id, *, source_record_key=None
):
    identity_parts = [
        run_id or "UNTRACKED", target_table, source_value.strip().lower(),
        str(concept_id),
    ]
    if source_record_key:
        identity_parts.append(source_record_key)
    identity = "|".join(identity_parts)
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cmf:mapping-decision:{identity}"))

def rejection_policy_exists(
    con,
    target_table,
    source_value,
    concept_id,
    *,
    source_vocabulary_id=None,
    source_code=None,
):
    from .schema import ensure_governance_tables
    ensure_governance_tables(con)
    source_vocabulary_id = (
        source_vocabulary_id or TARGET_GOVERNANCE[target_table][0]
    )
    source_code = source_code or source_value
    return bool(con.execute("""
        SELECT COUNT(*) FROM scoped_mapping_rejection_policy
        WHERE target_table = ? AND source_vocabulary_id = ?
          AND LOWER(TRIM(source_code)) = LOWER(TRIM(?))
          AND assigned_concept_id = ? AND active
    """, [
        target_table, source_vocabulary_id, source_code, int(concept_id),
    ]).fetchone()[0])

def register_decision(
    con,
    target_table,
    source_value,
    concept_id,
    normalized_value,
    mapping_method,
    score,
    model_name,
    vocabulary_version,
    status,
    run_id=None,
    prompt_version="mapping-prompt-v1",
    llm_decision=None,
    llm_confidence=None,
    llm_reason=None,
    clinical_signals=None,
    model_digest=None,
    generation_parameters=None,
    index_signature=None,
    source_adapter=None,
    source_record_key=None,
    publication_eligible=True,
):
    from .schema import ensure_governance_tables
    ensure_governance_tables(con)
    run_id = run_id or current_run_id()
    decision_id = decision_id_for(
        run_id, target_table, source_value, concept_id,
        source_record_key=source_record_key,
    )
    con.execute("""
        INSERT INTO mapping_decision (
            mapping_decision_id, run_id, target_table, source_value,
            normalized_value, assigned_concept_id, mapping_method, score,
            model_name, prompt_version, vocabulary_version, status,
            llm_decision, llm_confidence, llm_reason, clinical_signals,
            model_digest, generation_parameters, index_signature,
            source_adapter, source_record_key, publication_eligible
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (mapping_decision_id) DO UPDATE SET
            normalized_value = EXCLUDED.normalized_value,
            score = EXCLUDED.score,
            model_name = EXCLUDED.model_name,
            prompt_version = EXCLUDED.prompt_version,
            vocabulary_version = EXCLUDED.vocabulary_version,
            llm_decision = EXCLUDED.llm_decision,
            llm_confidence = EXCLUDED.llm_confidence,
            llm_reason = EXCLUDED.llm_reason,
            clinical_signals = EXCLUDED.clinical_signals,
            model_digest = EXCLUDED.model_digest,
            generation_parameters = EXCLUDED.generation_parameters,
            index_signature = EXCLUDED.index_signature,
            source_adapter = EXCLUDED.source_adapter,
            source_record_key = EXCLUDED.source_record_key,
            publication_eligible = EXCLUDED.publication_eligible,
            status = CASE
                WHEN mapping_decision.status IN ('APPROVED', 'REJECTED')
                THEN mapping_decision.status ELSE EXCLUDED.status END
    """, [
        decision_id, run_id, target_table, source_value, normalized_value,
        int(concept_id), mapping_method, score, model_name,
        prompt_version, vocabulary_version, status, llm_decision,
        llm_confidence, llm_reason, clinical_signals, model_digest,
        generation_parameters, index_signature,
        source_adapter, source_record_key, bool(publication_eligible),
    ])
    return decision_id
