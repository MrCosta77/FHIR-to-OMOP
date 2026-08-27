# Dirty hospital data benchmark

This versioned fixture measures mapping from coded and dirty hospital inputs to
Standard OMOP concepts. It contains 100 cases across Condition, Measurement,
Procedure, Observation, and Drug. Each concept family has an exact standard
code, noisy text, a local-code record, and a case where safe behavior is to
`ABSTAIN`.

The 60 development cases and 40 held-out cases have disjoint `family_id` and
target `concept_id` values. The evaluator checks this invariant before scoring.
The labels are technical curation (`PROVISIONAL_TECHNICAL`), not clinical gold;
promotion to `CLINICALLY_VALIDATED` requires documented human clinical review.

Rebuild the deterministic fixture:

```bash
python -m benchmarks.dirty_hospital.build_fixture
```

Run the code-only baseline against a local Athena-backed database:

```bash
python -m src.benchmark.evaluate_dirty_hospital \
  --database data/omop_clinical.duckdb \
  --output benchmark_results/deterministic_baseline.json
```

Generated reports are intentionally ignored because they contain local paths
and run-specific provenance. Every report records the fixture SHA-256, Athena
vocabulary version, ETL run, Git commit, and runtime versions.

The primary safety metrics are accepted precision, recall on mappable cases,
false maps on expected-abstain cases, abstain accuracy, coverage, and overall
accuracy. A later LLM runner must produce predictions without access to the
`expected` object and use the same scorer.

## Frozen Phase 5 comparison

`phase5_protocol.json` fixes the held-out comparison before execution. The five
arms share the exact-code baseline first and compare fuzzy lexical fallback,
top-5 embedding retrieval, and structured retrieval adjudication by
`qwen2.5-coder:7b` and `llama3.1:latest`. Benchmark cases are stripped to
`case_id`, domain, source and context before any predictor is called. The
benchmark never writes predictions to STCM or an OMOP clinical table.

Run the held-out split only after committing the frozen protocol and runner:

```bash
python -m src.benchmark.evaluate_phase5 \
  --database data/omop_clinical.duckdb \
  --output benchmark_results/phase5_held_out.json
```

The detailed local report retains per-case results for audit. A public summary
may be generated with `--summary-output`; it excludes labels and local database
paths. Held-out results must not be used to tune this protocol.
