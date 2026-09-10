import duckdb

from src.mapping.retrieval_queue import (
    record_retrieval_suggestion,
    retrieval_suggestion_queue,
)


def _record(con, score=0.91):
    return record_retrieval_suggestion(
        con,
        run_id="RUN-test",
        target_table="measurement",
        source_value="HGB",
        candidate_concept_id=3000963,
        candidate_concept_name="Hemoglobin [Mass/volume] in Blood",
        retrieval_score=score,
        candidate_rank=1,
        alias_id="LIS-HGB",
        lexicon_version="lis-aliases-v1",
        lexicon_sha256="a" * 64,
        model_name="qwen-test",
        prompt_version="prompt-test",
        llm_confidence=0.75,
        llm_reason="Insufficient source detail.",
        affected_events=12,
    )


def test_strong_retrieval_candidate_is_separate_and_nonpublishable():
    with duckdb.connect(":memory:") as con:
        assert _record(con) is True
        queue = retrieval_suggestion_queue(con)
        assert len(queue) == 1
        assert queue[0]["candidate_concept_id"] == 3000963
        assert queue[0]["retrieval_score"] == 0.91
        tables = {
            row[0] for row in con.execute(
                "SELECT table_name FROM information_schema.tables"
            ).fetchall()
        }
        assert "mapping_decision" not in tables
        assert "mapping_provenance" not in tables


def test_weak_retrieval_candidate_does_not_enter_queue():
    with duckdb.connect(":memory:") as con:
        assert _record(con, score=0.79) is False
        assert retrieval_suggestion_queue(con) == []


def test_queue_deduplicates_same_semantic_suggestion_across_runs():
    with duckdb.connect(":memory:") as con:
        _record(con)
        record_retrieval_suggestion(
            con,
            run_id="RUN-new",
            target_table="measurement",
            source_value=" hgb ",
            candidate_concept_id=3000963,
            candidate_concept_name="Hemoglobin [Mass/volume] in Blood",
            retrieval_score=0.95,
            candidate_rank=1,
            alias_id="LIS-HGB",
            lexicon_version="lis-aliases-v1",
            lexicon_sha256="a" * 64,
            model_name="qwen-test",
            prompt_version="prompt-test",
            llm_confidence=0.80,
            llm_reason="Insufficient source detail.",
            affected_events=12,
        )

        queue = retrieval_suggestion_queue(con)
        assert len(queue) == 1
        assert queue[0]["run_id"] == "RUN-new"
