# 🏥 Clinical Mapping Framework (FHIR to OMOP CDM v5.4)

An end-to-end Health Data Engineering and Real-World Evidence (RWE) pipeline. This framework extracts raw clinical data from FHIR JSON bundles, standardizes it into the **OMOP Common Data Model (v5.4)**, and enriches unmapped concepts using a **Three-Tier Hybrid Mapping Architecture** (Deterministic, Heuristic, and Vector RAG) with full governance and audit provenance.

## 🚀 Core Engineering Philosophy & Architecture

This project was built with strict adherence to clinical data management standards, focusing on determinism, idempotency, and auditability:

1. **Strict Domain Routing:** Actively separates Medical Conditions from Social/Categorical Observations based on official Athena vocabulary domains.
2. **Deterministic Tie-Breaking:** Avoids SQL *fan-out* issues (`QUALIFY ROW_NUMBER() = 1`) and guarantees high-fidelity mappings using the `Maps To` relationship.
3. **Stable Natural Keys:** Surrogate IDs (e.g., `person_id`, `condition_occurrence_id`) are generated via SHA-256 hashing of the original FHIR identifiers/URLs, guaranteeing consistent IDs across multiple runs.
4. **Regulatory-Grade Provenance:** Maintains a dedicated `mapping_provenance` table to audit AI vs. Deterministic decisions, pushing LLM outputs to a `Pending_Human_Review` state.

## 🧠 The Three-Tier Mapping Engine

Standard OMOP vocabularies handle the majority of clinical data, but real-world data is messy. This framework uses a progressively complex three-tier system:

* **Tier 1: Deterministic (DuckDB + Athena)**
  Resolves exact SNOMED/RxNorm matches via the `CONCEPT_RELATIONSHIP` table.
* **Tier 2: Heuristic AI (Ollama + Jaro-Winkler)**
  Orphan terms (e.g., messy condition text) are semantically normalized by a local LLM, then mapped to OMOP using Jaro-Winkler similarity thresholds (≥0.95).
* **Tier 3: Vector RAG (ChromaDB + Ollama)**
  Legacy LIS noise (e.g., "GLUCOSE RANDOM") is embedded and queried against a vector store of 68,000+ LOINC standard concepts. The LLM acts as the final judge to select the clinically safe match. *(Includes a noise injection simulator and ground-truth table for accuracy validation).*

## 🛠️ Setup & Execution

**1. Environment Setup**
```bash
python -m venv .venv
source .venv/bin/activate  # Or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

**2. Local LLM Setup**
Ensure [Ollama](https://ollama.ai/) is installed and running locally, then pull the required model:
```bash
ollama run qwen2.5-coder:7b
```

**3. Run the Full Orchestrator**
This single command executes a 16-step pipeline (Vocabularies ➔ Base ETL ➔ Linkage ➔ Noise Simulation ➔ AI Mapping ➔ Data Quality Tests ➔ Analytics):
```bash
python main.py
```

## 📊 Analytics & Text-to-SQL

To test the database's Real-World Evidence capabilities:

```bash
# Run predefined cohort discovery scripts (e.g., Hypertension Cascade)
python src/analytics/rwe_cohort_discovery.py

# Launch the interactive Text-to-SQL AI Agent
python src/analytics/text_to_sql_agent.py
```