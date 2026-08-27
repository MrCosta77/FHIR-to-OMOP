"""Governed local-LLM adapter for OMOP Measurement mappings."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.mapping.semantic_mapper import run_semantic_mapping


def run_measurement_ai_mapping():
    return run_semantic_mapping("measurement")


if __name__ == "__main__":
    run_measurement_ai_mapping()
