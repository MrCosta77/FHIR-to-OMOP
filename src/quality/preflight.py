"""Fail-fast checks for external inputs and services required by the pipeline."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import FHIR_DIR, MODEL_NAME, OLLAMA_URL, VOCAB_DIR
from src.security.privacy import PrivacyError, validate_privacy_runtime


ATHENA_FILES = (
    "CONCEPT.csv",
    "CONCEPT_RELATIONSHIP.csv",
    "VOCABULARY.csv",
    "DOMAIN.csv",
    "CONCEPT_CLASS.csv",
    "CONCEPT_ANCESTOR.csv",
)


def _ollama_models(url=OLLAMA_URL, timeout=3):
    tags_url = url.rsplit("/api/", 1)[0] + "/api/tags"
    with urllib.request.urlopen(tags_url, timeout=timeout) as response:
        payload = json.load(response)
    return {
        model.get("name") or model.get("model")
        for model in payload.get("models", [])
    }


def find_rscript(candidates=None):
    discovered = shutil.which("Rscript")
    if discovered:
        return Path(discovered)
    if candidates is None:
        program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
        candidates = sorted(
            program_files.glob("R/R-*/bin/Rscript.exe"), reverse=True
        )
    return next((Path(path) for path in candidates if Path(path).is_file()), None)


def collect_failures(
    fhir_dir=FHIR_DIR,
    vocab_dir=VOCAB_DIR,
    require_ollama=True,
    require_dqd=False,
    llm_url=OLLAMA_URL,
):
    failures = []
    try:
        validate_privacy_runtime(llm_url)
    except PrivacyError as exc:
        failures.append(f"Privacy preflight failed: {exc}")
    fhir_path = Path(fhir_dir)
    if not fhir_path.is_dir():
        failures.append(f"FHIR directory is missing: {fhir_path}")
    elif not any(fhir_path.glob("*.json")):
        failures.append(f"FHIR directory contains no JSON bundles: {fhir_path}")

    vocabulary_path = Path(vocab_dir)
    missing_vocab = [name for name in ATHENA_FILES if not (vocabulary_path / name).is_file()]
    if missing_vocab:
        failures.append(
            f"Athena vocabulary is incomplete in {vocabulary_path}: "
            + ", ".join(missing_vocab)
        )

    if require_ollama:
        try:
            models = _ollama_models(llm_url)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            failures.append(f"Ollama is unavailable at {llm_url}: {exc}")
        else:
            if MODEL_NAME not in models:
                failures.append(
                    f"Ollama model {MODEL_NAME!r} is not installed; available models: "
                    + (", ".join(sorted(model for model in models if model)) or "none")
                )

    if require_dqd and find_rscript() is None:
        failures.append("Rscript was not found; the official OHDSI DQD cannot run.")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-dqd",
        action="store_true",
        help="also require Rscript for the official OHDSI DataQualityDashboard",
    )
    args = parser.parse_args()
    failures = collect_failures(require_dqd=args.include_dqd)
    if failures:
        details = "\n".join(f" - {failure}" for failure in failures)
        raise SystemExit(f"Preflight failed with {len(failures)} blocking issue(s):\n{details}")
    print("Preflight passed: FHIR, Athena vocabulary and Ollama are ready.")


if __name__ == "__main__":
    main()
