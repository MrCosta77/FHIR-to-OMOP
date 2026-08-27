"""Governed local-LLM adapter for OMOP Observation mappings."""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.mapping.semantic_mapper import run_semantic_mapping


def run_observation_ai_mapping():
    return run_semantic_mapping("observation")


if __name__ == "__main__":
    run_observation_ai_mapping()
