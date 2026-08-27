# 🏥 Clinical Mapping Framework (FHIR to OMOP CDM v5.4)

An end-to-end Health Data Engineering and Real-World Evidence (RWE) pipeline. This framework extracts raw clinical data from FHIR JSON bundles, standardizes it into the **OMOP Common Data Model (v5.4)**, and maps messy/legacy clinical text using a **Retrieval-Augmented Generation (RAG) + Human-in-the-Loop Architecture**.

## 🚀 Core Engineering Philosophy

This project was built with strict adherence to clinical data management standards, focusing on determinism, auditability, and OHDSI conventions:

1. **OMOP-Canonical Mapping (STCM):** AI candidates are stored as proposals, not mappings. Only a named human approval publishes a candidate to `approved_mapping_set` and `source_to_concept_map`; pending and rejected candidates never enter the active mapping set.
2. **Deterministic Tie-Breaking:** Avoids SQL *fan-out* issues (`QUALIFY ROW_NUMBER() = 1`) and guarantees high-fidelity mappings using the official `Maps To` relationship and Domain routing.
3. **Hierarchical Phenotyping:** RWE analytics leverage the `CONCEPT_ANCESTOR` table for accurate disease-group phenotyping rather than relying on brittle string matching.
4. **FHIR Unit Provenance:** `valueQuantity.unit`, `system`, and `code` are retained through staging. UCUM codes are matched case-sensitively to Standard OMOP `Unit` Concepts, while the original unit text and source concept are preserved.
5. **Standards-Based Era Derivation:** After approved STCM mappings are applied, mapped conditions are collapsed into `CONDITION_ERA` with a 30-day persistence window. Drug products are expanded through `CONCEPT_ANCESTOR` to every current Standard Ingredient before `DRUG_ERA` is derived. Both tables use deterministic IDs and are published together only after coverage and integrity checks pass.

## 🧠 The AI Mapping Engine & Governance Loop

Standard OMOP vocabularies handle the majority of clinical data, but real-world data (like legacy LIS lab results) is messy. This framework uses a progressive retrieval-adjudication system:

1. **RAG Retrieval:** Unmapped text triggers a vector search (ChromaDB) against standard OMOP vocabularies (e.g., LOINC) to retrieve the top 5 clinically valid candidates.
2. **LLM Adjudication:** A local LLM evaluates the 5 candidates through a strict JSON schema and returns either `SELECT` with one retrieved ID or `ABSTAIN` with a null ID. Invalid JSON, extra fields, and invented IDs fail closed.
3. **Human-in-the-Loop (Streamlit):** Every proposal receives a stable `mapping_decision_id`, `run_id`, affected-event provenance, model digest, prompt/vocabulary/index version, generation parameters, confidence, rationale, clinical signals and status. Curators record their name and optional rationale when approving or rejecting. Rejections become active policy and suppress identical future proposals.
4. **Active Learning (Few-Shot):** Human-approved mappings are dynamically injected back into the LLM's prompt in subsequent runs, creating a continuous feedback loop where the audit trail becomes the training data.

The retrieval layer is reproducible: the configured Chroma collections for
Condition, Drug, Measurement, and Procedure carry
a fingerprint of the valid vocabulary/domain slice, index schema version,
distance metric, and `build_complete` marker. Changed vocabularies trigger a
resumable rebuild; matching legacy collections are adopted without recomputing
embeddings. Few-shot examples use a stable order, candidate IDs are parsed by
exact membership, and `SIMILARITY_THRESHOLD` is enforced before a proposal can
enter the review queue.

The four domain adapters share one governed semantic-mapping engine. Procedure
fallback is restricted to current Standard SNOMED Procedure concepts; its LLM
output is recorded only as an event-level proposal or abstention and never
changes `PROCEDURE_OCCURRENCE` before named human approval.

LLM candidates are recorded once per affected clinical event. Approval validates
that the target is a current Standard Concept in the required OMOP domain and
then publishes the term to STCM. `apply_stcm.py` independently requires matching
human-approved provenance, the correct domain-specific source vocabulary, a
current Standard Concept, and valid STCM dates. Legacy proposals are migrated to
decision records; pending legacy STCM rows are withdrawn, while historical
`target_id=0` placeholders remain preserved as superseded audit data.

Each orchestrated execution receives a `RUN-...` identifier and records the Git
commit, SHA-256 input manifest, configuration, step status and error details in
both `etl_run` and a local JSON manifest. Work happens against an isolated
staging DuckDB. The published database is replaced only after every mandatory
step and quality gate succeeds; failed staging databases are retained for
forensic inspection and do not alter the last successful publication.

## 📊 Results & Validation

### 1. Mapping Accuracy (against seeded synthetic LIS noise)
The optional benchmark corrupts a deterministic 10% slice of mapped laboratory
measurements and stores the expected concepts in `lis_noise_ground_truth`.
Coverage, precision and recall must be generated for a specific `run_id` with
`src/analytics/evaluate_mapping_accuracy.py`; the repository does not present
mutable, run-independent percentages as current evidence.

### 2. OHDSI Data Quality Dashboard (DQD)
The resulting DuckDB instance is validated with the native R
`DataQualityDashboard` package against OMOP v5.4. DQD JSON files remain local;
the repository versions the executable check configuration and a human-reviewed
acceptance policy instead of committing one favorable report. The Python gate
requires zero DQD execution errors, rejects unknown or stale allowances, and
enforces per-check row and percentage caps. The runner preserves all DQD
future-date evaluations while normalizing their threshold expression to native
DuckDB SQL. It executes `plausibleValueHigh` and `measureValueCompleteness` in
per-table isolated R processes to avoid driver memory accumulation, verifies
that every shard is disjoint, and merges them without excluding or weakening
any check. The repository does not commit local result JSON, so a fresh clone
must generate a run-linked DQD report before making a quality claim about its
published database.

### 3. Unit Mapping Contract

FHIR `valueQuantity` units use three explicit outcomes: a known UCUM unit maps
to its Standard OMOP `Unit` Concept; a supplied but unsupported annotation maps
to concept ID `0`; and a genuinely absent unit remains `NULL`. Reviewed UCUM
aliases are centralized in `src/utils/unit_mapping.py`; for example, FHIR
`{score}` maps deterministically to Standard OMOP `[score]` (44777566).
Semantically incompatible code/unit pairs are retained in a traceable ETL
quarantine rather than converted or silently published.

### 4. Dirty Hospital Data Benchmark

`benchmarks/dirty_hospital/` contains a versioned 100-case benchmark spanning
Condition, Measurement, Procedure, Observation, and Drug. Its 60 development
and 40 held-out cases have disjoint target concepts, and include coded input,
Portuguese/mixed-language dirty text, local hospital codes, and explicit safe
`ABSTAIN` cases. Labels are currently technical provisional curation and must
not be represented as clinically validated until human review is recorded.

Run the deterministic code-only baseline after building the Athena-backed DB:
```bash
python -m src.benchmark.evaluate_dirty_hospital --database data/omop_clinical.duckdb
```
The report records full provenance and separates accepted precision, mappable
recall, false mappings, abstain accuracy, coverage, and overall accuracy. Local
reports are written under ignored `benchmark_results/`.

### 5. FHIR Encounter and Observation-Period Contract

Clinical events use an explicit FHIR `Encounter/{id}` reference before any
date-based matching. The reference must resolve to a visit for the same person
and the event date must fall inside that visit. Temporal fallback is allowed
only when no reference was supplied and exactly one visit covers the event;
zero or multiple candidates remain unlinked and are written to
`etl_quarantine`. Every outcome is recorded in `event_visit_linkage` as
`FHIR_REFERENCE`, `TEMPORAL_FALLBACK`, or `UNRESOLVED` with its run ID.

`OBSERVATION_PERIOD` uses encounter coverage when available and extends it only
when clinical-event evidence falls outside that range. If no encounter exists,
the event envelope is an explicit fallback. `observation_period_provenance`
records the evidence bounds and derivation method for every run.

## 🛠️ Setup & Execution

**1. Environment Setup**
```bash
# Clone the repository
git clone https://github.com/MrCosta77/FHIR-to-OMOP.git
cd FHIR-to-OMOP

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
* Download the **OMOP Vocabularies** from [Athena](https://athena.ohdsi.org/) and place `CONCEPT.csv`, `CONCEPT_RELATIONSHIP.csv`, `VOCABULARY.csv`, `DOMAIN.csv`, `CONCEPT_CLASS.csv` and `CONCEPT_ANCESTOR.csv` in `data/omop_vocab/`. Missing required files fail the run.
* Place synthetic **FHIR JSON bundles** (e.g., from Synthea) in `synthea/output/fhir/`. Every bundle is contract-validated before clinical tables are rebuilt.
* Ensure [Ollama](https://ollama.ai/) is installed and running locally with the target model: `ollama pull qwen2.5-coder:7b`

Check all mandatory pipeline inputs and services before starting a run:
```bash
python -m src.quality.preflight
```
Add `--include-dqd` to also require `Rscript` for the official OHDSI DQD.

**3. Run the Full Orchestrator**
Executes the full pipeline (Vocabularies ➔ Base ETL ➔ STCM Application ➔ Era Derivation ➔ Tests ➔ Analytics).
*(To test AI accuracy, inject LIS noise by setting `SIMULATE_LIS_NOISE="true"`).*
```bash
python main.py
```

Successful publications are written to `data/omop_clinical.duckdb`. Run
manifests are retained under `data/run_manifests/`; failed working databases are
kept under `data/runs/` and can be removed after investigation.

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
