# 🏥 Clinical Mapping Framework (FHIR to OMOP CDM v5.4)

An end-to-end Health Data Engineering and Real-World Evidence (RWE) pipeline. This framework extracts raw clinical data from FHIR JSON bundles, standardizes it into the **OMOP Common Data Model (v5.4)**, and enriches unmapped concepts using a **Three-Tier Hybrid Mapping Architecture** (Deterministic, Heuristic, and Vector RAG).

The integrity of the resulting database is actively validated against OHDSI community standards using the native R `DataQualityDashboard`.

## 🚀 Core Engineering Philosophy

This project was built with strict adherence to clinical data management standards, focusing on determinism, idempotency, and auditability:

1. **Strict Domain Routing:** Actively separates Medical Conditions from Social/Categorical Observations based on official Athena vocabulary domains.
2. **Deterministic Tie-Breaking:** Avoids SQL *fan-out* issues (`QUALIFY ROW_NUMBER() = 1`) and guarantees high-fidelity mappings using the `Maps To` relationship.
3. **Stable Natural Keys:** Surrogate IDs (e.g., `person_id`, `condition_occurrence_id`) are generated via SHA-256 hashing of the original FHIR identifiers/URLs, guaranteeing consistent IDs across multiple runs.
4. **Regulatory-Grade Provenance:** Maintains a dedicated `mapping_provenance` table to audit AI vs. Deterministic decisions.

## 🧠 The Three-Tier Mapping Engine

Standard OMOP vocabularies handle the majority of clinical data, but real-world data is messy. This framework uses a progressively complex three-tier system:

* **Tier 1: Deterministic (DuckDB + Athena)**
  Resolves exact SNOMED/RxNorm matches via the `CONCEPT_RELATIONSHIP` table.
* **Tier 2: Heuristic AI (Ollama + Jaro-Winkler)**
  Orphan terms (e.g., messy condition text) are semantically normalized by a local LLM, then mapped to OMOP using Jaro-Winkler similarity thresholds (≥0.95).
* **Tier 3: Vector RAG (ChromaDB + Ollama)**
  Legacy LIS noise is embedded and queried against a vector store of 68,000+ LOINC standard concepts. The LLM acts as the final judge to select the clinically safe match. *(Includes a noise injection simulator to test RWE scenarios).*

## 📊 OHDSI Data Quality Validation (R Integration)

To ensure analytical readiness, this framework seamlessly integrates with the **OHDSI DataQualityDashboard (R package)**. 
The pipeline automatically generates the necessary metadata (`cdm_source`) and subjects the DuckDB database to over **1,600 clinical plausibility, conformance, and completeness checks**, successfully mapping over 99% of synthetic clinical events to standard terminologies on its baseline run.

## 🛠️ Setup & Execution

**1. Environment Setup (Python)**
```bash
python -m venv .venv
source .venv/bin/activate  # Or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

**2. Local LLM Setup**
Ensure [Ollama](https://ollama.ai/) is installed and running locally:
```bash
ollama run qwen2.5-coder:7b
```

**3. Run the Full Orchestrator**
Executes a 16-step pipeline (Vocabularies ➔ Base ETL ➔ Linkage ➔ Noise Simulation ➔ AI Mapping ➔ Data Quality Tests):
```bash
python main.py
```

**4. Run OHDSI Clinical Validation (RStudio)**
Open the R script to launch the interactive validation dashboard:
```bash
# Open in RStudio or run via terminal:
Rscript src/analytics/view_dqd_dashboard.R
```