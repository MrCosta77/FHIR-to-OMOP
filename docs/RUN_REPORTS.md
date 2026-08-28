# Immutable run reports

Every successfully published ETL run produces one content-addressed JSON report
under the active profile's `reports_dir`. The v1 schema is versioned at
`quality/run_report_schema.json`.

Local report directories are ignored by Git. Operational deployments must copy
sealed reports to their approved immutable retention/archive system.

The report aggregates:

- run ID, commit and timestamps;
- a privacy-safe effective configuration subset;
- count, total size and aggregate SHA-256 of the input manifest without paths;
- pipeline step status and timings;
- pytest totals and hash of the run-scoped JUnit evidence;
- DQD result and policy hashes plus the accepted summary, when required;
- per-domain OMOP mapping coverage and metadata-only governance counts;
- exact DuckDB size and SHA-256;
- an explicit `deployment_authorized=false` readiness statement.

Development and benchmark profiles record DQD as `NOT_RUN` because it is not a
required gate in those profiles. The hospital profile cannot disable DQD: a
missing, erroneous or policy-rejected run-linked result prevents publication.

Reports contain no FHIR filenames, source values, reviewer identities, raw
prompts or raw model responses. Dataset classification can be PHI while the
report itself remains metadata-only and declares `report_contains_phi=false`.

## Immutability and verification

The SHA-256 is calculated over compact sorted-key JSON before the `integrity`
object is added. Its first 16 characters are included in the filename. Reports
are created through an exclusive hard link, so an existing content-addressed
file is never overwritten by the application.

Verify a report independently:

```bash
python -m src.quality.run_report data/run_reports/RUN-...-<hash>.json
```

Editing any sealed field causes verification to fail. The original JUnit/DQD
hashes remain in the report even if operational retention later removes those
larger supporting artefacts.

Failed runs retain their mutable forensic manifest and staging database but do
not receive a success report. This prevents partial execution from being
mistaken for publication evidence.
