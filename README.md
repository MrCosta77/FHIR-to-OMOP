# Clinical Mapping Framework: FHIR to OMOP CDM

This repository contains a local, privacy-first clinical data engineering pipeline that transforms raw synthetic healthcare data (FHIR) into the OMOP Common Data Model (CDM). 

The primary goal of this project is to address a major challenge in Health Data Science and Real-World Evidence (RWE): handling messy, unstructured clinical text that fails standard deterministic ETL mapping, without compromising patient data privacy.

## 🏗️ Architecture & Tech Stack

- **Data Generation:** Synthea (FHIR JSON Bundles)
- **Database / SQL Engine:** DuckDB (Chosen for in-process, memory-efficient analytical queries over 6.4M+ OMOP vocabulary concepts)
- **Orchestration & ETL:** Python 3.12 (Modularized under `src/`)
- **Semantic Engine:** Local Ollama (`qwen2.5-coder:7b`) + ChromaDB (`all-MiniLM-L6-v2` embeddings)

## 🧠 Core Engineering Philosophy

During development, I prioritized data integrity, clinical accuracy, and privacy:

1. **Deterministic Relational Integrity:** Built using stable cryptographic hashing (`hashlib.sha256`) on original FHIR UUIDs. This ensures `person_id` remains strictly consistent across different ETL runs, maintaining perfect referential integrity between clinical event tables.
2. **Idempotency & Data Quality:** The pipeline is designed to be safely re-run without record duplication. Automated Pytest suites validate unique IDs, foreign keys, and temporal constraints (e.g., no future dates).
3. **Local-Only Processing:** Clinical data processing is designed to remain local; the AI inference layer and vector databases run entirely on-machine, avoiding external API calls to safeguard potential PHI.

## 🧬 The Hybrid Mapping Strategy (Rules + AI)

A purely deterministic pipeline leaves noisy data behind, while a purely LLM-based pipeline risks clinical hallucinations. This framework uses a **3-Tiered Hybrid Approach**:

### 1. Deterministic ETL (The Foundation)
Extracts and loads clean FHIR resources using structured coding (e.g., SNOMED, RxNorm). Complex HL7 FHIR structures, such as resolving `medicationReference` UUIDs to standard RxNorm concepts, are handled natively in SQL/Python.

### 2. Orthographic Fallback for Conditions
For unmapped diseases and conditions (`CONDITION_OCCURRENCE`), the pipeline utilizes the local LLM to normalize raw text, validated against the OMOP dictionary using **Jaro-Winkler similarity**. This solves superficial noise (e.g., typos, shorthand) effectively.

### 3. Vector RAG for Laboratory Data (The LOINC Challenge)
Clinical chemistry and laboratory data (`MEASUREMENT`) cannot be mapped using string similarity. An orthographic algorithm might erroneously map "Bilirubin in Blood" to "Bilirubin in Urine", ignoring the biological sample axis.
To solve this, I implemented a **Retrieval-Augmented Generation (RAG) pipeline**:
- The LLM first acts as a clinical normalizer, expanding jargon and abbreviations.
- The normalized text is converted into vector embeddings.
- A local **ChromaDB** vector store searches the LOINC vocabulary by spatial semantic proximity, correctly isolating the clinical context (analyte, specimen, and method) before applying a strict cosine-distance confidence threshold for database write-back.

## 🚀 The Pipeline

The pipeline has transitioned from exploratory notebooks into a modular Python architecture.

- `01_explore_fhir.ipynb` / `02_load_omop_vocabularies.ipynb`: Data ingestion and dictionary setup.
- `99_data_detective.ipynb`: A methodology notebook demonstrating raw JSON debugging and root-cause analysis for missing clinical references.
- `src/main.py`: Central orchestrator running the ETL and AI mapping sequentially.
- `src/etl/`: Deterministic extraction modules (`person.py`, `condition.py`, `drug.py`, `measurement.py`).
- `src/mapping/`: AI and semantic search engines, including the Vector Store initialization.
- `tests/test_data_quality.py`: Automated Pytest framework ensuring OMOP compliance.

## ⚙️ Setup & Execution

1. Clone the repository and set up a virtual environment.
2. Install dependencies: `pip install -r requirements.txt` *(Note: requires `chromadb`, `duckdb`, and `sentence-transformers`)*.
3. Download the OMOP vocabularies from Athena and place them in the `data/` folder.
4. Ensure Ollama is running locally with the `qwen2.5-coder:7b` model.
5. Execute the pipeline: `python src/main.py`