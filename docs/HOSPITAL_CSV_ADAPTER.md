# Hospital CSV adapter (`hospital-csv-v1`)

The hospital CSV adapter is the first non-FHIR source boundary. It validates
row-oriented dirty clinical terms, routes them to one of the six governed OMOP
domains and constructs the same `MappingRequest` used by the existing mapping
engine. Validation alone does not retrieve concepts, call an LLM or write
DuckDB. The separate governed runner can retrieve and persist pre-ingestion
proposals, but it cannot publish them.

Files must be UTF-8 and may use comma, semicolon or tab delimiters. Every row
must declare `schema_version=hospital-csv-v1`.

## Columns

| Column | Required | Model prompt | Contract |
|---|---:|---:|---|
| `schema_version` | yes | no | Exactly `hospital-csv-v1` |
| `record_id` | yes | no | Unique opaque row ID; never a patient identifier |
| `domain` | yes | routing only | Condition, Drug/Medication, Measurement/Lab, Observation, Procedure or Device |
| `source_value` | yes | yes, redacted | Dirty clinical term, maximum 500 characters |
| `source_system` | no | yes, redacted | Local system/vocabulary label |
| `source_code` | no | yes, redacted | Local clinical code |
| `unit` | no | yes, redacted | Original unit text/code |
| `specimen` | no | yes, redacted | Specimen context |
| `route` | no | yes, redacted | Medication route |
| `dose` | no | yes, redacted | Dose/strength context |
| `event_date` | no | yes | ISO `YYYY-MM-DD` |

Unknown columns fail closed. This prevents an accidental `patient_name`, MRN or
other unreviewed field from silently entering a prompt. Direct identifier
patterns in all prompt-bound values are redacted before the core request is
created. `record_id` is retained only as adapter routing metadata.

Validate a file without printing source values or local codes:

```bash
python -m src.adapters.hospital_csv path/to/input.csv
```

The output contains only schema version, row counts, target-table counts and
the names of context fields present. A versioned synthetic example is available
at `tests/fixtures/hospital_csv/golden_hospital.csv`.

Run the local governed mapping path after validation:

```bash
python -m src.adapters.hospital_csv_mapping path/to/input.csv
```

The runner reuses the domain-specific Athena/Chroma indexes, the structured
`SELECT`/`ABSTAIN` contract, model provenance, confidence threshold and
metadata-only security audit. Retrieval text, prompt content and returned LLM
text are redacted before use or persistence. The opaque `record_id` is never
stored; a SHA-256 `source_record_key`, scoped by adapter, source system and
target table, provides stable, idempotent correlation.

Rows intended for later OMOP ingestion can additionally be converted into a
fail-closed source identity claim and resolved against the governed
[`source_identity_registry`](SOURCE_IDENTITY_REGISTRY.md). Resolution requires
both `source_system` and `source_code`; it never guesses a vocabulary.

## Governed mapping flow

```text
hospital-csv-v1
      |
      v
allowlist + schema/domain/date validation
      |
      v
HospitalCSVRecord (in memory; no publication)
      |
      +--> governed OMOP retrieval supplies Candidate objects
      |
      v
redacted MappingRequest
      |
      v
clinical_mapping_core SELECT/ABSTAIN contract
      |
      v
pre-ingestion proposal/abstention + provenance
      |
      v
publication_eligible = false (review, adjudication and STCM blocked)
      |
      v
active source identity + exact existing OMOP event binding
      |
      v
existing two-review + independent adjudication workflow
```

The adapter does not make CSV clinically authorized. Real hospital files remain
subject to the PHI activation, institutional approval, access, retention and
local-LLM controls in `docs/PHI_CONTROL_POLICY.md`. Pattern redaction is a
defence in depth and is not a replacement for upstream minimization or DLP.

## Deliberate publication boundary

CSV decisions start as `publication_eligible=false`; they are excluded from
clinical review, adjudication, STCM and clinical-table publication. After a
separate ingestion step has created the OMOP event, the governed
[identity/event-binding contract](SOURCE_IDENTITY_REGISTRY.md) may promote only
an exact `SELECT` proposal into the existing blinded workflow. It does not
provide CSV ingestion, create events, approve mappings or bypass review.

Operational upstream ETLs can submit the resulting event correlations through
the strict [governed ingestion handoff](INGESTION_HANDOFF.md). A receipt must
reference a successful `etl_run` and the exact input-manifest digest; the handoff
does not infer missing person, visit or event data from this CSV contract.
