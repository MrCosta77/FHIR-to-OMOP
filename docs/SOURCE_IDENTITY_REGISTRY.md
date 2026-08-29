# Governed hospital source identity and event binding

The source identity registry and event-binding contract form the 7D.4 boundary
between an opaque pre-ingestion record and an existing OMOP event. Registration
establishes which local code system a hospital feed represents. Binding then
verifies one concrete event before the proposal may enter the existing blinded
review and adjudication workflow.

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

## 7D.4B event-binding contract

A resolved identity remains pre-ingestion evidence until
`bind_pre_ingestion_decision` atomically verifies all of the following:

- the caller is an authorized `source_admin` and supplies a rationale;
- the registry entry is still active and unchanged;
- the decision belongs to the same adapter, hashed record key and target table;
- the pre-ingestion result is `SELECT`, not `ABSTAIN`;
- `target_id` identifies exactly one existing OMOP event in that table;
- its target concept is `0`, its source concept is `0`/`NULL`, and its source
  value exactly equals the registered local `source_code`;
- exactly one non-publishable provenance row exists for the decision.

On success, `source_event_binding` records the immutable correlation, the
decision and provenance receive the explicit source system/vocabulary/code and
the proposal becomes eligible for the normal review queue. A repeated identical
call is idempotent; any conflicting binding fails closed. Binding does not create
or modify a clinical event, publish STCM, or approve a mapping.

```python
from src.adapters.event_binding import bind_pre_ingestion_decision

binding = bind_pre_ingestion_decision(
    con,
    identity,
    mapping_decision_id,
    measurement_id,
    actor="Source Administrator",
    reason="Verified against the ingested LIS event",
)
```

## Publication and policy scope

Adjudication rechecks both the active registry entry and the bound event. An
approval writes the explicit local `source_code` and `source_vocabulary_id` to
`source_to_concept_map`; a rejection records policy under the same scoped
identity. `scoped_approved_mapping_set` and
`scoped_mapping_rejection_policy` are authoritative for this vocabulary-aware
path, preventing identical codes in different hospitals from colliding. The
legacy tables remain intact and are dual-written only for legacy Synthea
decisions.

`apply_stcm.py` joins hospital mappings through the active binding and approved
event provenance, so another event with the same local code is not changed
unless it has its own governed binding. The adapter still cannot publish, and
the required two blinded reviewers plus distinct adjudicator remain the sole
approval path.

Audit records contain binding metadata, not raw source values or local codes.

For batch operation, the [7D.4C ingestion handoff](INGESTION_HANDOFF.md)
validates an authorized upstream attestation against one successful,
manifest-linked ETL run before delegating to this binding contract. The run,
manifest digest and first batch are retained on the binding. Expected failures
remain isolated to their receipt and are surfaced through a metadata-only
report.
