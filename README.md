# 🏥 Clinical Data Mapping Framework (CMF)

An enterprise-grade, AI-powered ETL pipeline designed to ingest, harmonize, and standardize legacy healthcare data into the **OMOP Common Data Model (v5.4)**. Built for modern Health Data Science and Real-World Evidence (RWE) applications, this framework ensures clinical semantic interoperability with robust data governance.

## 🚀 Key Features

* **Resilient ETL Engine:** Idempotent Python pipeline that safely extracts complex EHR/FHIR data (simulated via Synthea) and normalizes it into core OMOP clinical domains (`person`, `condition_occurrence`, `measurement`, `drug_exposure`, etc.).
* **AI Semantic Mapping (RAG):** Utilizes Local LLMs (Ollama) combined with Vector Databases (ChromaDB) for high-accuracy semantic normalization against official vocabularies (**SNOMED CT, LOINC, RxNorm**).
* **Dynamic Few-Shot Learning:** The AI engine dynamically learns from human curator decisions, injecting approved mappings directly into the context window for continuous accuracy improvement.
* **Human-in-the-Loop Governance:** Includes a fully interactive Web Portal (built with Streamlit) for clinical data managers to audit, approve, or reject AI-generated terminology mappings, ensuring 100% regulatory compliance.
* **Automated Data Quality Gates:** Integrated `pytest` suite enforcing OMOP referential integrity and medical logic validation before analytics generation.

## 🏗️ Architecture

1. **Extraction & Orchestration:** `main.py` orchestrates the deterministic ETL pipeline.
2. **Semantic Resolution:** Orphan legacy concepts trigger the Retrieval-Augmented Generation (RAG) module to find the closest standard medical concepts.
3. **Audit & Provenance:** Every AI decision is heavily audited (`mapping_provenance` table) with confidence scores and model versioning.
4. **Curation:** The Streamlit Web UI allows experts to review pending decisions.
5. **Analytics Ready:** Standardized data sits in a high-performance **DuckDB** instance, ready for large-scale RWE cohort discovery.

## 🛠️ Technology Stack

* **Core:** Python 3.12, SQL, DuckDB
* **Artificial Intelligence:** Ollama (Local LLMs), ChromaDB (Vector Store), Prompt Engineering
* **Data Governance & UI:** Streamlit, Pandas
* **Testing:** Pytest

## ⚙️ How to Run

### 1. Install Dependencies
```bash
python -m pip install -r requirements.txt