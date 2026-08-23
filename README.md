# 🏥 Clinical Mapping Framework (FHIR to OMOP CDM v5.4)

An end-to-end Health Data Engineering and Real-World Evidence (RWE) pipeline. This framework extracts raw clinical data from FHIR JSON bundles, standardizes it into the **OMOP Common Data Model (v5.4)**, and maps messy/legacy clinical text using a **Retrieval-Augmented Generation (RAG) + Human-in-the-Loop Architecture**.

## 🚀 Core Engineering Philosophy

This project was built with strict adherence to clinical data management standards, focusing on determinism, auditability, and OHDSI conventions:

1. **OMOP-Canonical Mapping (STCM):** AI-derived mappings are not forced directly into clinical event tables. Instead, they are written to the `source_to_concept_map` (STCM) dictionary. This isolates mapping decisions from clinical data, allowing for versioning, retraction, and human review.
2. **Deterministic Tie-Breaking:** Avoids SQL *fan-out* issues (`QUALIFY ROW_NUMBER() = 1`) and guarantees high-fidelity mappings using the official `Maps To` relationship and Domain routing.
3. **Hierarchical Phenotyping:** RWE analytics leverage the `CONCEPT_ANCESTOR` table for accurate disease-group phenotyping rather than relying on brittle string matching.
4. **FHIR Unit Provenance:** `valueQuantity.unit`, `system`, and `code` are retained through staging. UCUM codes are matched case-sensitively to Standard OMOP `Unit` Concepts, while the original unit text and source concept are preserved.
5. **Standards-Based Era Derivation:** After approved STCM mappings are applied, mapped conditions are collapsed into `CONDITION_ERA` with a 30-day persistence window. Drug products are expanded through `CONCEPT_ANCESTOR` to every current Standard Ingredient before `DRUG_ERA` is derived. Both tables use deterministic IDs and are published together only after coverage and integrity checks pass.

## 🧠 The AI Mapping Engine & Governance Loop

Standard OMOP vocabularies handle the majority of clinical data, but real-world data (like legacy LIS lab results) is messy. This framework uses a progressive retrieval-adjudication system:

1. **RAG Retrieval:** Unmapped text triggers a vector search (ChromaDB) against standard OMOP vocabularies (e.g., LOINC) to retrieve the top 5 clinically valid candidates.
2. **LLM Adjudication:** A local LLM evaluates the 5 candidates and selects the exact match or explicitly refuses (Returns 0), eliminating free-text hallucination.
3. **Human-in-the-Loop (Streamlit):** Mappings are flagged as `Pending_Human_Review` in a `mapping_provenance` audit table. Curators use a Streamlit portal to Approve or Reject mappings.
4. **Active Learning (Few-Shot):** Human-approved mappings are dynamically injected back into the LLM's prompt in subsequent runs, creating a continuous feedback loop where the audit trail becomes the training data.

The retrieval layer is reproducible: the three active Chroma collections carry
a fingerprint of the valid vocabulary/domain slice, index schema version,
distance metric, and `build_complete` marker. Changed vocabularies trigger a
resumable rebuild; matching legacy collections are adopted without recomputing
embeddings. Few-shot examples use a stable order, candidate IDs are parsed by
exact membership, and `SIMILARITY_THRESHOLD` is enforced before a proposal can
enter the review queue.

LLM candidates are recorded once per affected clinical event. They are never
applied merely because they exist in STCM: `apply_stcm.py` requires matching
human-approved provenance, the correct domain-specific source vocabulary, a
current Standard Concept, and valid STCM dates. Legacy `target_id=0` proposals
are retained as superseded history rather than treated as active decisions.

## 📊 Results & Validation

### 1. Mapping Accuracy (against seeded synthetic LIS noise)
The architecture is explicitly designed to handle unstructured, legacy clinical text. To prove the efficacy of the RAG tier, the pipeline deliberately corrupts 10% of standard lab measurements into "Legacy LIS" formats (e.g., converting "Glucose [Mass/volume] in Blood" to "GLUCOSE RANDOM (LEGACY)"), maps them via AI, and evaluates against a strictly held ground-truth table.

| Metric | Score | Description |
| :--- | :--- | :--- |
| **Coverage** | 97.11% | Proportion of dirty terms the AI successfully found a candidate for. |
| **Precision** | 77.01% | Proportion of AI mappings that were exactly correct. |
| **Recall** | 74.78% | Overall recovery rate of the corrupted dataset. |

### 2. OHDSI Data Quality Dashboard (DQD)
The resulting DuckDB instance is validated with the native R
`DataQualityDashboard` package against OMOP v5.4. DQD JSON files remain local;
the repository versions the executable check configuration and a human-reviewed
acceptance policy instead of committing one favorable report. The Python gate
requires zero DQD execution errors, rejects unknown or stale allowances, and
enforces per-check row and percentage caps. The runner preserves all 55 DQD
future-date evaluations while normalizing their threshold expression to native
DuckDB SQL. It executes `plausibleValueHigh` in an isolated R process to avoid
driver memory accumulation, verifies that the two check sets are disjoint, and
merges them into one standard 1,983-check JSON without excluding or weakening
any check. The final approved run completed 1,983 unique checks: 1,981 passed,
2 reviewed exceptions, 0 execution errors (99.9% without failure). Conformance
passed 1,060/1,060 checks, Completeness 110/110, and Plausibility 811/813.

### 3. Unit Mapping Contract

FHIR `valueQuantity` units use three explicit outcomes: a known UCUM unit maps
to its Standard OMOP `Unit` Concept; a supplied but unsupported annotation maps
to concept ID `0`; and a genuinely absent unit remains `NULL`. Reviewed UCUM
aliases are centralized in `src/utils/unit_mapping.py`; for example, FHIR
`{score}` maps deterministically to Standard OMOP `[score]` (44777566).
Semantically incompatible code/unit pairs are retained in a traceable ETL
quarantine rather than converted or silently published.

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

The repository includes a small versioned FHIR golden bundle. Contract and
unit tests run in GitHub Actions without Athena, Ollama, or a local database:
```bash
python -m pytest -m "not integration" -v
```
After building the local DuckDB, run the complete suite (including OMOP
integration checks):
```bash
python -m pytest -v
```
Any external FHIR bundle can be checked before ETL with
`python -m src.quality.validate_fhir <bundle-or-directory>`.

**2. Prerequisites**
* Download the **OMOP Vocabularies** (v5.4) from [Athena](https://athena.ohdsi.org/) and place the CSV files in `data/omop_vocab/`.
* Place synthetic **FHIR JSON bundles** (e.g., from Synthea) in `data/fhir_raw/`.
* Ensure [Ollama](https://ollama.ai/) is installed and running locally with the target model: `ollama pull qwen2.5-coder:7b`

**3. Run the Full Orchestrator**
Executes the full pipeline (Vocabularies ➔ Base ETL ➔ STCM Application ➔ Era Derivation ➔ Tests ➔ Analytics).
*(To test AI accuracy, inject LIS noise by setting `SIMULATE_LIS_NOISE="true"`).*
```bash
python main.py
```

The first run after a vocabulary change can take considerably longer while
active vector indexes are rebuilt. Progress is printed per 5,000 concepts and
an interrupted build resumes from the IDs already indexed.

**4. Run the Human-in-the-Loop Portal**
Launch the Streamlit app to curate pending AI mappings:
```bash
streamlit run src/app/review_portal.py
```

**5. Run OHDSI Clinical Validation (RStudio)**
Launch `src/analytics/view_dqd_dashboard.R` to view the interactive quality report.
After generating a new DQD JSON report, apply the versioned acceptance budget:
```bash
python -m src.quality.validate_dqd dqd_results/<new-report>.json
```
The gate permits no DQD execution errors. Failed checks are capped and accepted
only when their check type has an explicit reason in `quality/dqd_policy.json`.
Historical reports predate this gate and are not evidence of current acceptance.
