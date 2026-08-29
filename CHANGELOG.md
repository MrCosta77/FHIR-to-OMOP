# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-08-29

### Added

- Dependency-free `clinical_mapping_core` boundary with typed candidate,
  request, decision and model-provenance contracts, preserving the existing
  six governed adapters and preparing future HL7 v2 and hospital CSV inputs.
- Versioned `hospital-csv-v1` adapter with comma/semicolon/tab support,
  allowlisted clinical context, fail-closed routing and dates, direct-identifier
  redaction and a metadata-only validation CLI.
- Governed hospital CSV runner reusing Athena/Chroma retrieval and the local
  structured LLM, with hashed record correlation, idempotent proposal/abstention
  persistence and a fail-closed pre-ingestion publication gate.
- Historical hospital source-identity registry with OMOP-length local
  vocabulary IDs, explicit deactivation, unambiguous record resolution,
  metadata-only audit and a PHI-aware source-administrator role.
- Atomic hospital source-event binding that revalidates active source identity,
  exact local code and one existing unmapped OMOP event before enabling the
  established two-review adjudication path. Vocabulary-scoped approval and
  rejection policy prevents local-code collisions, while approved STCM
  application remains restricted to the bound event.
- Strict ingestion receipts anchored to a successful `etl_run` and its exact
  input-manifest digest, with source-admin authorization, per-record atomic and
  idempotent handoff, durable binding lineage, explicit partial-failure reporting
  and a metadata-only CLI.
- Comprehensive synthetic End-to-End Hospital Acceptance gate spanning 
  all six domains, proving isolated mapping, ingestion handoff, manual review,
  adjudication, and final STCM logic. Includes a real-environment validation
  script bridging automated tests and operational RAG infrastructure.

## [0.1.0] - 2026-08-28

### Added

- Reproducible FHIR-to-OMOP pipeline with official Athena vocabulary loading,
  OMOP CDM 5.4 contracts, deterministic provenance and atomic publication.
- Governed local-LLM mapping for Condition, Drug, Measurement, Procedure,
  Observation and Device, including constrained retrieval, validated JSON,
  normal abstention and blinded human review with independent adjudication.
- Versioned dirty-hospital benchmark, held-out evaluation, development-only
  calibration, scale evidence and privacy controls for future PHI processing.
- Portable runtime profiles and immutable, content-addressed run evidence with
  pytest, DQD, mapping coverage, governance counts and database/input hashes.
- Universal hashed Python dependency lock and an R `renv.lock` containing the
  official OHDSI DataQualityDashboard stack.
- Apache License 2.0 with preserved project attribution in `NOTICE`.

### Safety

- The release is labelled `PROVISIONAL_TECHNICAL`; it is not a medical device,
  clinical recommendation system or authorization for clinical deployment.
- Documented clinical review and institutional governance remain mandatory
  before any hospital pilot.

[Unreleased]: https://github.com/MrCosta77/FHIR-to-OMOP/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/MrCosta77/FHIR-to-OMOP/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MrCosta77/FHIR-to-OMOP/releases/tag/v0.1.0
