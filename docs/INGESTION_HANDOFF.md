# Governed ingestion handoff (`cmf-ingestion-receipt-v1`)

The 7D.4C handoff connects an upstream hospital ingestion run to the governed
mapping workflow without making the mapping adapter responsible for creating
clinical events. The upstream ETL writes the OMOP event first and emits one
authorized attestation containing only the correlation and source identity
required to verify that event against the governed database state.

This is not a generic CSV-to-OMOP loader. The current hospital CSV mapping
contract does not contain the person, visit or institutional context required to
create clinically valid OMOP rows.

## Receipt contract

Each JSON object uses `schema_version=cmf-ingestion-receipt-v1` and contains
exactly these fields:

| Field | Contract |
|---|---|
| `ingestion_run_id` | Canonical `RUN-*` identifier present in `etl_run` with status `SUCCESS` |
| `input_manifest_sha256` | Lowercase SHA-256 of the exact UTF-8 `etl_run.input_manifest` value |
| `source_adapter` | Canonical registered adapter ID |
| `source_system` | Canonical uppercase local system |
| `source_code` | Exact local code written to the OMOP event source-value column |
| `source_record_key` | Existing lowercase SHA-256 correlation key |
| `target_table` | One of the six governed OMOP event tables |
| `target_id` | Positive identifier of the event created by the successful run |

Unknown or missing fields, duplicate source records, duplicate target events,
mixed runs/manifests and batches above 10,000 receipts fail before any binding.
The input file is a UTF-8 JSON array of receipt objects.

## Processing guarantees

`process_ingestion_handoff` first requires an authenticated, allowlisted
`source_admin` in PHI mode. It then verifies the successful ETL run and exact
input-manifest digest before accessing any receipt. Batch authorization is
recorded in the metadata-only security audit.

Each receipt is processed in its own transaction:

1. reconstruct and resolve the governed source identity;
2. reject a conflicting existing binding;
3. require exactly one non-publishable pre-ingestion `SELECT` decision;
4. delegate exact event/code/provenance checks to the 7D.4B atomic binding;
5. persist the ingestion run, manifest digest and first handoff batch on the
   binding;
6. commit the binding, lineage and security audits together.

Expected record-level failures roll back only that receipt and return a stable
failure category. Unexpected database or infrastructure failures abort loudly.
Repeating an identical successful batch returns `ALREADY_BOUND` without creating
duplicate rows.

The receipt is an authenticated source-administrator attestation, not a
standalone digital signature proving who created the clinical row. Institutional
upstream ETL must emit it inside the controlled ingestion process. The handoff
adds durable, revalidated lineage; it does not replace upstream access controls
or event-level audit.

The report contains receipt digests, binding UUIDs, counts and stable failure
categories. It never contains source values, source codes or source record keys.
It does not approve a mapping, write STCM or change the mapped concept of a
clinical event.

## Local CLI

```bash
python -m src.adapters.ingestion_handoff receipts.json \
  --database data/omop_clinical.duckdb \
  --actor "Source Administrator" \
  --reason "Verified hospital ingestion receipts"
```

The CLI prints only the metadata-safe JSON report. Exit code `0` means every
receipt was bound or already bound; exit code `2` means at least one expected
record-level failure was reported. Contract, authorization and infrastructure
errors fail with a non-zero exception exit.
