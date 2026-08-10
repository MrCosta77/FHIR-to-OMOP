# 🏥 Clinical Mapping Framework (FHIR to OMOP CDM)

An end-to-end Health Data Engineering and Real-World Evidence (RWE) pipeline. This framework extracts raw clinical data from FHIR JSON bundles, standardizes it into the **OMOP Common Data Model (v5.4)** using deterministic vocabulary mapping, and enriches unmapped concepts using **Local Large Language Models (LLMs)** with full audit provenance.

## 🚀 Key Features

* **Strict Domain Routing:** Clinically robust ETL that actively separates Medical Conditions from Social/Categorical Observations based on the official Athena vocabulary domains.
* **Deterministic Mapping Pipeline:** Avoids SQL *fan-out* issues (`QUALIFY ROW_NUMBER() = 1`) and guarantees high-fidelity mappings using the `Maps To` relationship.
* **Longitudinal Patient Context:** Autonomously computes `observation_period` and links all independent clinical events to their respective hospital encounters (`visit_occurrence_id`).
* **AI Semantic Enrichment:** Uses a local LLM (Qwen via Ollama) and Jaro-Winkler similarity to map orphan clinical terms, keeping a strict confidence threshold (0.95).
* **Regulatory-Grade Provenance:** Maintains a dedicated `mapping_provenance` table to audit AI vs. Deterministic decisions (ready for human review).
* **Autonomous Text-to-SQL Agent:** An integrated AI assistant capable of answering clinical questions in natural language by autonomously generating and executing DuckDB queries.

## 🛠️ Tech Stack

* **Database Engine:** DuckDB (Columnar, high-performance analytical engine)
* **Language:** Python 3.x
* **AI / LLM:** Ollama (qwen2.5-coder:7b) for local, privacy-preserving inference
* **Standardization:** OHDSI / OMOP CDM v5.4 Vocabularies

## 📁 Pipeline Orchestration

The entire infrastructure can be instantiated from scratch using the unified orchestrator:

```bash
python main.py
```
*This executes a 10-step pipeline spanning vocabulary setup, base extraction (Visits, Conditions, Drugs, Measurements, Observations, Procedures), longitudinal linkage, and final AI semantic mapping.*

## 📊 Analytics & Text-to-SQL

To test the database's Real-World Evidence capabilities:

```bash
# Run predefined cohort discovery scripts
python src/analytics/rwe_cohort_discovery.py

# Launch the interactive Text-to-SQL AI Agent
python src/analytics/text_to_sql_agent.py
```