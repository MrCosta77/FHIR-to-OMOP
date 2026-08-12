# 🏥 Clinical Mapping Framework (FHIR to OMOP CDM v5.4)

An end-to-end Health Data Engineering and Real-World Evidence (RWE) pipeline. This framework extracts raw clinical data from FHIR JSON bundles, standardizes it into the **OMOP Common Data Model (v5.4)**, and maps messy/legacy clinical text using a **Retrieval-Augmented Generation (RAG) + Human-in-the-Loop Architecture**.

## 🚀 Core Engineering Philosophy

This project was built with strict adherence to clinical data management standards, focusing on determinism, auditability, and OHDSI conventions:

1. **OMOP-Canonical Mapping (STCM):** AI-derived mappings are not forced directly into clinical event tables. Instead, they are written to the `source_to_concept_map` (STCM) dictionary. This isolates mapping decisions from clinical data, allowing for versioning, retraction, and human review.
2. **Deterministic Tie-Breaking:** Avoids SQL *fan-out* issues (`QUALIFY ROW_NUMBER() = 1`) and guarantees high-fidelity mappings using the official `Maps To` relationship and Domain routing.
3. **Hierarchical Phenotyping:** RWE analytics leverage the `CONCEPT_ANCESTOR` table for accurate disease-group phenotyping rather than relying on brittle string matching.

## 🧠 The AI Mapping Engine & Governance Loop

Standard OMOP vocabularies handle the majority of clinical data, but real-world data (like legacy LIS lab results) is messy. This framework uses a progressive retrieval-adjudication system:

1. **RAG Retrieval:** Unmapped text triggers a vector search (ChromaDB) against standard OMOP vocabularies (e.g., LOINC) to retrieve the top 5 clinically valid candidates.
2. **LLM Adjudication:** A local LLM evaluates the 5 candidates and selects the exact match or explicitly refuses (Returns 0), eliminating free-text hallucination.
3. **Human-in-the-Loop (Streamlit):** Mappings are flagged as `Pending_Human_Review` in a `mapping_provenance` audit table. Curators use a Streamlit portal to Approve or Reject mappings.
4. **Active Learning (Few-Shot):** Human-approved mappings are dynamically injected back into the LLM's prompt in subsequent runs, creating a continuous feedback loop where the audit trail becomes the training data.

## 📊 Results & Validation

### 1. Mapping Accuracy (against seeded synthetic LIS noise)
The architecture is explicitly designed to handle unstructured, legacy clinical text. To prove the efficacy of the RAG tier, the pipeline deliberately corrupts 10% of standard lab measurements into "Legacy LIS" formats (e.g., converting "Glucose [Mass/volume] in Blood" to "GLUCOSE RANDOM (LEGACY)"), maps them via AI, and evaluates against a strictly held ground-truth table.

| Metric | Score | Description |
| :--- | :--- | :--- |
| **Coverage** | 97.11% | Proportion of dirty terms the AI successfully found a candidate for. |
| **Precision** | 77.01% | Proportion of AI mappings that were exactly correct. |
| **Recall** | 74.78% | Overall recovery rate of the corrupted dataset. |

### 2. OHDSI Data Quality Dashboard (DQD)
The resulting DuckDB instance is successfully validated using the native R `DataQualityDashboard` package against OMOP v5.4 rules:
* **Overall Pass Rate:** 77% (Achieved by deploying the full OMOP v5.4 schema and enforcing strict STCM domain routing)
* **Plausibility (Validation):** 100% Pass Rate (Flawless temporal and clinical logic integrity)
* **Conformance (Total):** 69% Pass Rate (Up from baseline due to proper vocabulary metadata and DDL adherence)
* **Completeness (Total):** 65% Pass Rate

## 🛠️ Setup & Execution

**1. Environment Setup**
```bash
# Clone the repository
git clone [https://github.com/your-username/Clinical-Mapping-Framework.git](https://github.com/your-username/Clinical-Mapping-Framework.git)
cd Clinical-Mapping-Framework

# Set up Python virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .\.venv\Scripts\activate
pip install -r requirements.txt
```

**2. Prerequisites**
* Download the **OMOP Vocabularies** (v5.4) from [Athena](https://athena.ohdsi.org/) and place the CSV files in `data/omop_vocab/`.
* Place synthetic **FHIR JSON bundles** (e.g., from Synthea) in `data/fhir_raw/`.
* Ensure [Ollama](https://ollama.ai/) is installed and running locally with the target model: `ollama pull qwen2.5-coder:7b`

**3. Run the Full Orchestrator**
Executes the 16-step pipeline (Vocabularies ➔ Base ETL ➔ STCM Application ➔ Tests ➔ Analytics). 
*(To test AI accuracy, inject LIS noise by setting `SIMULATE_LIS_NOISE="true"`).*
```bash
python main.py
```

**4. Run the Human-in-the-Loop Portal**
Launch the Streamlit app to curate pending AI mappings:
```bash
streamlit run src/app/review_portal.py
```

**5. Run OHDSI Clinical Validation (RStudio)**
Launch `src/analytics/view_dqd_dashboard.R` to view the interactive quality report.