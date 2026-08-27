"""Governed local-LLM adapter for OMOP Procedure mappings."""

from src.mapping.semantic_mapper import run_semantic_mapping


def run_procedure_ai_mapping():
    return run_semantic_mapping("procedure_occurrence")


if __name__ == "__main__":
    run_procedure_ai_mapping()
