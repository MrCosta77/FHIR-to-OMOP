# Governed hospital source identity

The source identity registry is the 7D.4A boundary between an opaque
pre-ingestion record and a future publishable OMOP event. It establishes which
local code system a hospital feed represents without enabling mapping review,
STCM publication or clinical-table writes.

## Identity contract

Every record that will later be bound to OMOP must provide:

- `source_adapter`: canonical adapter ID, currently `hospital-csv-v1`;
- `source_system`: uppercase local system code, such as `LIS_LOCAL`;
- `source_code`: non-empty local clinical code, within the OMOP 50-character
  `source_code` limit;
- `target_table`: the already validated OMOP domain route;
- `source_record_key`: the existing non-reversible SHA-256 correlation key.

`source_system` and `source_code` fail closed when they match a direct-identifier
pattern. Missing source identity is allowed for exploratory pre-ingestion LLM
mapping, but it cannot be resolved or promoted towards clinical review.

## Registry contract

`source_identity_registry` maps one `(source_adapter, source_system)` pair to
exactly one active `source_vocabulary_id`. Vocabulary IDs:

- start with `CMF_`;
- contain only uppercase letters, digits and underscores;
- fit the OMOP `VARCHAR(20)` limit;
- cannot use the reserved `CMF_SYNTHEA*` namespace.

Registrations require a named source administrator and rationale. Repeating the
same registration is idempotent. Changing a vocabulary while another is active
fails closed: the administrator must explicitly deactivate the old entry first.
Deactivation retains the full historical row and audit event. Resolution fails
when no entry or more than one entry is active.

The programmatic API is in `src.adapters.source_identity`:

```python
claim = claim_hospital_csv_identity(record)
register_source_system(
    con,
    "hospital-csv-v1",
    "LIS_LOCAL",
    "CMF_HOSP_LIS",
    actor="Source Administrator",
    reason="Approved local terminology registration",
)
identity = resolve_source_identity(con, claim)
```

In PHI mode, the actor must match `CMF_AUTHENTICATED_USER` and appear in
`CMF_SOURCE_ADMIN_ALLOWLIST`. The current environment-variable identity remains
a technical hook, not an institutional identity provider.

## Deliberate 7D.4A limit

A resolved identity remains pre-ingestion evidence only. This phase does not:

- change `publication_eligible=false`;
- place a proposal in clinical review or adjudication queues;
- insert or update `source_to_concept_map`;
- create or update an OMOP clinical event;
- infer a vocabulary ID from free text or from a target table.

7D.4B must bind the resolved identity to a concrete, domain-correct OMOP event
and verify the local code before any proposal can become reviewable.
