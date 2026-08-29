from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
import tracemalloc
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import duckdb
import ollama
import onnxruntime

from src.benchmark.evaluate_dirty_hospital import load_cases, validate_reference_concepts, deterministic_prediction, score_predictions, validate_cases, deterministic_prediction, score_predictions, validate_cases
from src.mapping.mapping_service import get_versioned_collection
from src.utils.config import SETTINGS
from src.mapping.semantic_mapper import (
    DECISION_SCHEMA,
    GENERATION_PARAMETERS,
    OLLAMA_TIMEOUT,
    PROMPT_VERSION,
    _model_digest,
    _response_content,
    build_prompt,
    parse_llm_decision,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_FIXTURE = PROJECT_ROOT / "benchmarks" / "dirty_hospital" / "cases.jsonl"
DEFAULT_PROTOCOL = PROJECT_ROOT / "benchmarks" / "dirty_hospital" / "phase5_protocol.json"
DEFAULT_DATABASE = SETTINGS.db_path
DEFAULT_CHROMA = SETTINGS.chroma_path
EVALUATOR_VERSION = "1.0.0"
DOMAIN_TARGETS = {
    "Condition": "condition_occurrence",
    "Drug": "drug_exposure",
    "Measurement": "measurement",
    "Observation": "observation",
    "Procedure": "procedure_occurrence",
}

