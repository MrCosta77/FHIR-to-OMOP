"""Governed local-LLM adapter for OMOP Drug mappings."""

from src.mapping.semantic_mapper import run_semantic_mapping


def run_drug_ai_mapping():
    return run_semantic_mapping("drug_exposure")


if __name__ == "__main__":
    run_drug_ai_mapping()
