"""Governed local-LLM adapter for OMOP Measurement mappings."""

from src.mapping.semantic_mapper import run_semantic_mapping


def run_measurement_ai_mapping():
    return run_semantic_mapping("measurement")


if __name__ == "__main__":
    run_measurement_ai_mapping()
