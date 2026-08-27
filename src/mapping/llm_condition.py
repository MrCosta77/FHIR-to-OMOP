"""Governed local-LLM adapter for OMOP Condition mappings."""

from src.mapping.semantic_mapper import run_semantic_mapping


def run_condition_ai_mapping():
    return run_semantic_mapping("condition_occurrence")


if __name__ == "__main__":
    run_condition_ai_mapping()
