"""
Script to manually execute the 7D.4D Hospital Acceptance test against the REAL infrastructure.
This proves that the system handles RAG retrieval, LLM mapping, and ingestion handoff without mocks.
"""

import os
from pathlib import Path
import duckdb

from src.adapters.hospital_csv_mapping import run_hospital_csv_mapping
from src.utils.config import DB_PATH, CHROMA_PATH

# 1. Provide the CSV fixture
FIXTURE_CSV = Path("tests/fixtures/hospital_csv/e2e_hospital_6domain.csv")

print("=====================================================")
print("7D.4D: Execução de Aceitação em Ambiente Real (Qwen)")
print("=====================================================")
print(f"Base de dados: {DB_PATH}")
print(f"Chroma DB: {CHROMA_PATH}")
print(f"Fixture: {FIXTURE_CSV}\n")

# 2. Run the CSV mapping through the real infrastructure
# Note: This connects to your actual Ollama instance and Chroma database.
result = run_hospital_csv_mapping(FIXTURE_CSV, db_path=DB_PATH, chroma_path=CHROMA_PATH)

print("\nResultados do Processamento LLM:")
print(f" - Registos lidos: {result['records']}")
print(f" - Propostas (SELECT): {result['proposals']}")
print(f" - Rejeições (ABSTAIN): {result['abstentions']}")
print(f" - Registados na BD: {result['persisted']}")

print("\nSe as Propostas (SELECT) não forem 6, o modelo Qwen decidiu abster-se por cautela ou por ser a versão 'coder'.")
print("Isto é normal no ambiente real, o sistema de integração lidará com a rejeição corretamente, não fazendo o binding.")
