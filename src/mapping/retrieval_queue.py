"""Non-publishable review queue for strong retrieval candidates after LLM abstention."""

from __future__ import annotations

import uuid

RETRIEVAL_SUGGESTION_THRESHOLD = 0.80


def ensure_retrieval_suggestion_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS retrieval_candidate_suggestion (
            suggestion_id VARCHAR PRIMARY KEY,
            run_id VARCHAR,
            target_table VARCHAR NOT NULL,
            source_value VARCHAR NOT NULL,
            candidate_concept_id INTEGER NOT NULL,
            candidate_concept_name VARCHAR NOT NULL,
            retrieval_score DOUBLE NOT NULL,
            candidate_rank INTEGER NOT NULL,
            alias_id VARCHAR,
            lexicon_version VARCHAR,
            lexicon_sha256 VARCHAR,
            model_name VARCHAR NOT NULL,
            prompt_version VARCHAR NOT NULL,
            llm_confidence DOUBLE NOT NULL,
            llm_reason VARCHAR NOT NULL,
            affected_events INTEGER NOT NULL,
            status VARCHAR NOT NULL DEFAULT 'PENDING',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (status IN ('PENDING', 'SUPERSEDED', 'DISMISSED')),
            CHECK (retrieval_score >= 0.0 AND retrieval_score <= 1.0),
            CHECK (candidate_rank > 0),
            CHECK (affected_events >= 0)
        )
    """)


def record_retrieval_suggestion(
    con,
    *,
    run_id,
    target_table,
    source_value,
    candidate_concept_id,
    candidate_concept_name,
    retrieval_score,
    candidate_rank,
    alias_id,
    lexicon_version,
    lexicon_sha256,
    model_name,
    prompt_version,
    llm_confidence,
    llm_reason,
    affected_events,
    threshold=RETRIEVAL_SUGGESTION_THRESHOLD,
) -> bool:
    """Record strong retrieval evidence without creating a mapping decision."""
    retrieval_score = float(retrieval_score)
    if not 0.0 <= float(threshold) <= 1.0:
        raise ValueError("Retrieval suggestion threshold must be between 0 and 1.")
    if retrieval_score < float(threshold):
        return False
    ensure_retrieval_suggestion_table(con)
    identity = "|".join([
        run_id or "UNTRACKED", target_table, source_value.strip().casefold(),
        str(int(candidate_concept_id)), alias_id or "", lexicon_version or "",
    ])
    suggestion_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"cmf:retrieval-suggestion:{identity}")
    )
    con.execute("""
        INSERT INTO retrieval_candidate_suggestion (
            suggestion_id, run_id, target_table, source_value,
            candidate_concept_id, candidate_concept_name, retrieval_score,
            candidate_rank, alias_id, lexicon_version, lexicon_sha256,
            model_name, prompt_version, llm_confidence, llm_reason,
            affected_events, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING')
        ON CONFLICT (suggestion_id) DO NOTHING
    """, [
        suggestion_id, run_id, target_table, source_value,
        int(candidate_concept_id), candidate_concept_name, retrieval_score,
        int(candidate_rank), alias_id, lexicon_version, lexicon_sha256,
        model_name, prompt_version, float(llm_confidence), llm_reason,
        int(affected_events),
    ])
    return True


def retrieval_suggestion_queue(con) -> list[dict]:
    """Return one current suggestion per semantic candidate across runs."""
    ensure_retrieval_suggestion_table(con)
    columns = [
        "suggestion_id", "run_id", "target_table", "source_value",
        "candidate_concept_id", "candidate_concept_name", "retrieval_score",
        "candidate_rank", "alias_id", "lexicon_version", "model_name",
        "prompt_version", "llm_confidence", "llm_reason", "affected_events",
    ]
    rows = con.execute("""
        SELECT suggestion_id, run_id, target_table, source_value,
               candidate_concept_id, candidate_concept_name, retrieval_score,
               candidate_rank, alias_id, lexicon_version, model_name,
               prompt_version, llm_confidence, llm_reason, affected_events
        FROM retrieval_candidate_suggestion
        WHERE status = 'PENDING'
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY target_table, LOWER(TRIM(source_value)),
                         candidate_concept_id
            ORDER BY created_at DESC, suggestion_id
        ) = 1
        ORDER BY retrieval_score DESC, affected_events DESC,
                 LOWER(TRIM(source_value))
    """).fetchall()
    return [dict(zip(columns, row, strict=True)) for row in rows]
