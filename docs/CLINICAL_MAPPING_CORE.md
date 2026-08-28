# Clinical mapping core boundary

`src/clinical_mapping_core/` is the in-repository extraction boundary for a
future standalone `clinical-mapping-core`. It contains only portable value
contracts, fail-closed decision parsing, deterministic prompt rendering and
model provenance. The package uses the Python standard library and does not
open databases, call models, retrieve concepts or read runtime configuration.

```text
FHIR / future HL7 v2 / future CSV adapters
                    |
                    v
       OMOP retrieval + hospital policy
                    |
                    v
         clinical_mapping_core contracts
                    |
                    v
        local-model adapter (Ollama)
                    |
                    v
     governance + DuckDB publication gate
```

The dependency direction is one-way: project adapters may import the core, but
the core must never import project adapters or runtime frameworks. This rule is
enforced by `tests/test_clinical_mapping_core.py`.

## Stable contracts

- `Candidate`: a concept ID and display name supplied by terminology retrieval.
- `MappingRequest`: source text, target domain/vocabulary and the complete
  candidate set exposed to the model.
- `MappingDecision`: exactly `SELECT` with one supplied concept ID or normal
  `ABSTAIN`, plus bounded confidence, reason and clinical signals.
- `ModelProvenance`: model, prompt, generation and index identity attached to a
  validated decision without source-system or storage details.

The JSON schema remains `mapping-json-v2`. `semantic_mapper.py` keeps its public
compatibility functions, but delegates parsing and prompt rendering to the core.
Existing adapters therefore preserve their inputs, prompts, persistence status,
redaction, audit events and human-publication gate.

## Deliberately outside the core

- FHIR parsing and future HL7 v2 or CSV normalization;
- OMOP table/column configuration and Athena vocabulary queries;
- Chroma index construction and retrieval implementation;
- Ollama transport, model discovery and timeout policy;
- PHI activation, redaction implementation, authentication and audit storage;
- DuckDB governance, blinded review, adjudication and STCM publication;
- Streamlit, DQD, runtime paths and orchestration.

These are adapters or product policy. Moving them into the portable package
would couple a reusable decision contract to one source format, database or UI.

## Extension path

A future source adapter must normalize its input into a source value and context,
obtain valid Standard OMOP candidates through the governed retrieval adapter and
then construct the same `MappingRequest`. The core does not allow a source
adapter to bypass candidate binding, invent IDs or publish a result.

Do not split this directory into another repository or publish it to PyPI until
its API has survived at least one additional source adapter, its versioning and
compatibility policy are explicit, and all boundary/behavior tests remain green.
