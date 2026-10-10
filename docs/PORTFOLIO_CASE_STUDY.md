# Portfolio case study — governed FHIR-to-OMOP clinical mapping

## Executive summary

FHIR-to-OMOP is a portfolio/reference implementation for converting structured
FHIR R4 clinical data into OMOP CDM 5.4 while handling local, legacy or dirty
clinical terms through terminology retrieval, a local LLM and governed human
review. It is designed to demonstrate clinical data engineering, semantic
interoperability, reproducible evaluation and fail-closed AI governance.

The project does **not** treat an LLM response as an approved clinical mapping.
The model may select only one of the Standard OMOP candidates supplied by the
retrieval layer or abstain. A proposal can reach the active mapping set only
after two independent blinded reviews and adjudication by a third distinct
professional.

The current evidence uses synthetic data and technically curated benchmark
labels. It demonstrates software behaviour under stated conditions, not
clinical validation, regulatory conformity or production readiness.

## The problem

Hospital data rarely arrives as a clean set of standard terminology codes. A
single laboratory concept may appear as a valid LOINC code, a local LIS code, an
abbreviation, a legacy name or free text with partial context. Direct string
matching loses clinical meaning; unrestricted LLM generation can invent concept
IDs or make plausible but unsafe mappings; and an ETL that hides uncertainty
produces untraceable OMOP data.

The engineering question is therefore not merely “Can a model guess the right
concept?” It is:

> Can heterogeneous source events be normalized, compared with valid OMOP
> candidates, safely proposed for review and published with complete provenance
> without allowing model output to bypass clinical governance?

## Solution architecture

```text
FHIR R4 / governed hospital CSV
                |
                v
      validation and normalization
                |
                v
     OMOP vocabulary and Maps to rules
                |
       unresolved local term
                v
   Chroma retrieval + clinical reranking
                |
          top-5 valid candidates
                v
   local LLM: SELECT one candidate / ABSTAIN
                |
                v
      event-level proposal + provenance
                |
                v
  two blinded reviews + separate adjudication
                |
                v
        approved STCM mapping
                |
                v
       OMOP publication + DQD evidence
```

The portable `clinical_mapping_core` owns typed requests, candidates, decisions,
model provenance, deterministic prompt rendering and fail-closed parsing. FHIR,
CSV, Athena, Chroma, Ollama, DuckDB, privacy and the review portal remain explicit
adapters or product-policy boundaries.

## Important design decisions

### 1. Deterministic mapping precedes AI

FHIR codes and official OMOP `Maps to` relationships are resolved before
retrieval or model inference. The LLM is reserved for unresolved terms rather
than placed in the critical path for already-standard data.

### 2. Candidate-bound model decisions

The model receives a bounded candidate set and must return schema-valid JSON.
Invented IDs, extra fields, malformed JSON and inconsistent `SELECT`/`ABSTAIN`
combinations fail closed. Invalid output is recorded as abstention instead of
terminating the full ETL run.

### 3. Clinical context without unnecessary disclosure

Measurement retrieval can use explicit unit, specimen, property, method and
value-kind signals. Individual result values are not sent to the model. In PHI
mode, the runtime requires local inference, direct-identifier redaction,
institution-managed pseudonymization-key continuity and metadata-only audit
evidence.

### 4. No automatic publication

Confidence and retrieval thresholds determine whether a proposal is eligible
for a review queue; they do not authorize publication. Reviewer identity uses a
stable governed actor ID. Two independent reviews and a separate adjudicator are
required even when both reviewers agree.

### 5. Atomic publication and traceability

Each run records configuration, input manifest, Git state, step results and
quality evidence. Work occurs in an isolated staging database. The published
DuckDB is replaced only after mandatory gates pass; failed staging databases are
retained for forensic inspection.

## Evaluation evidence

### Dirty-hospital benchmark

The versioned benchmark contains 100 technically curated cases across Condition,
Measurement, Procedure, Observation and Drug. Development and held-out concepts
are separated to reduce leakage. Cases include multilingual terms, local codes,
abbreviations, spelling noise, invalid mappings and explicit abstentions.

The frozen `phase5-v1` held-out evaluation reported:

- deterministic baseline: 50% overall accuracy, 25% coverage and 100% accepted
  precision;
- embedding retrieval, Qwen and Llama: no improvement over that baseline at the
  governed 0.90 threshold;
- fuzzy matching: 52.5% accuracy and 30% coverage, but one false mapping;
- embedding top-5 recall: 56.7% overall and 35% among mappable fallback cases.

The result is deliberately reported as a limitation: retrieval recall, not a
claim of LLM superiority, was the main measured bottleneck. The later Phase 5 v2
protocol aligns the runner with the current `mapping-json-v5` contract, but its
results must be reported separately from the historical v1 evidence.

### Scale and operational checks

A reproducible synthetic scale run generated 282 Patient resources and processed
13,404 visits plus 151,888 clinical events. The complete pipeline finished in
647.3 seconds, excluding synthetic generation, and produced a 1.39 GB DuckDB.
These numbers characterize one recorded synthetic run; they are not throughput
guarantees for another machine or hospital dataset.

CI checks contracts, mapping, privacy, governance, publication, DQD integration,
synthetic hospital acceptance and isolated-wheel installation. The OHDSI Data
Quality Dashboard remains a mandatory run-linked gate in the hospital profile.

## What the project demonstrates

- FHIR R4 parsing and OMOP CDM 5.4 ETL;
- official Athena vocabulary use and domain-aware routing;
- local retrieval and LLM integration without unrestricted concept generation;
- clinical-review workflow and separation of duties;
- PHI-aware boundaries, provenance and fail-closed configuration;
- reproducible benchmarks that preserve negative findings;
- RWE-oriented data quality and cohort analytics;
- packaging, CI, versioning and immutable run evidence.

## Known limitations

- benchmark labels are `PROVISIONAL_TECHNICAL`, not a clinical gold standard;
- the clinical workflow still requires execution by two qualified independent
  reviewers and a distinct adjudicator;
- synthetic E2E acceptance does not validate real hospital infrastructure;
- the standalone Streamlit portal is not an institutional identity provider;
- the Phase 5 results show limited retrieval recall and no demonstrated LLM gain
  over the deterministic baseline;
- PHI operation requires institutional security, privacy, retention and incident
  controls beyond this repository.

## Next evidence milestone

The highest-value next phase is not automatic mapping or a larger model. It is a
versioned clinical gold-standard study:

1. prespecify sampling, roles, metrics and adjudication;
2. collect two blinded reviews per case;
3. adjudicate disagreement independently;
4. measure agreement, retrieval recall, proposal precision, mapping recall and
   abstention correctness by domain;
5. freeze a new untouched held-out set before any further calibration.

After that evidence exists, an HL7 v2 `ORU^R01` laboratory adapter is the most
useful architectural extension because it tests whether the governed mapping
core generalizes beyond FHIR.

## Reproduction entry points

```powershell
# Full synthetic pipeline
python main.py

# Blinded review portal
python -m streamlit run src/app/review_portal.py

# Deterministic dirty-hospital baseline
python -m src.benchmark.evaluate_dirty_hospital `
  --database data/omop_clinical.duckdb

# Frozen current Phase 5 protocol
python -m src.benchmark.evaluate_phase5 `
  --database data/omop_clinical.duckdb `
  --output benchmark_results/phase5_held_out.json
```

Local Athena exports, databases and benchmark result files remain outside Git
because of licensing, size, privacy and reproducibility boundaries documented in
the repository.
