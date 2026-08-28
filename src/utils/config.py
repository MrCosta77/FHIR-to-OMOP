import os
from pathlib import Path

# Get the absolute path of the project root (two levels up from this file)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Centralized Paths
FHIR_DIR = os.environ.get(
    "CMF_FHIR_DIR",
    os.path.join(PROJECT_ROOT, "synthea", "output", "fhir"),
)
DB_PATH = os.environ.get(
    "CMF_DB_PATH",
    os.path.join(PROJECT_ROOT, "data", "omop_clinical.duckdb"),
)

# A PASTA DOS DICIONÁRIOS (Corrigida)
VOCAB_DIR = os.path.join(PROJECT_ROOT, "data", "omop_vocab") 

# LLM Configurations
OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "qwen2.5-coder:7b"
SIMILARITY_THRESHOLD = float(os.environ.get("CMF_SIMILARITY_THRESHOLD", "0.90"))
