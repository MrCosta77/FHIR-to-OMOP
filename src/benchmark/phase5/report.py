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

from src.benchmark.evaluate_dirty_hospital import load_cases, validate_reference_concepts, deterministic_prediction, score_predictions, validate_cases
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

def _git_commit() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True,
            capture_output=True, text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _arm_report(cases, predictions, performance, thresholds) -> dict:
    scored = score_predictions(cases, predictions)
    return {
        "performance": performance,
        "extended_metrics": _extended_metrics(cases, predictions, thresholds),
        **scored,
    }


def public_summary(report: dict) -> dict:
    return {
        "benchmark": report["benchmark"],
        "evaluator": report["evaluator"],
        "generated_at": report["generated_at"],
        "protocol": {
            "protocol_version": report["protocol"]["protocol_version"],
            "fixture_sha256": report["protocol"]["fixture_sha256"],
            "policy": report["protocol"]["policy"],
        },
        "selection": report["selection"],
        "provenance": {
            key: value for key, value in report["provenance"].items()
            if key != "database"
        },
        "index_preparation": report["index_preparation"],
        "arms": {
            name: {
                "metrics": arm["metrics"],
                "by_domain": arm["by_domain"],
                "extended_metrics": arm["extended_metrics"],
                "performance": arm["performance"],
            }
            for name, arm in report["arms"].items()
        },
        "clinical_validation_required": True,
        "curation_status": "PROVISIONAL_TECHNICAL",
    }


