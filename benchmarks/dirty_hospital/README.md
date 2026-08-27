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

Index preparation records the ONNX Runtime version and available execution
providers. On Windows, the optional `onnxruntime-directml` package can
accelerate the same Chroma default embedding model through DirectML without a
separate CUDA toolkit and without changing the frozen semantic protocol.

## Phase 5 held-out result

The first `phase5-v1` held-out execution was made from Git commit `b59e7c7` with
Athena vocabulary `v5.0 27-FEB-26`, DirectML, and the frozen 0.90 threshold.

| Arm | Accuracy | Coverage | Accepted precision | Mappable recall | Wrong maps |
|---|---:|---:|---:|---:|---:|
| deterministic code | 50.0% | 25.0% | 100.0% | 33.3% | 0 |
| fuzzy lexical | 52.5% | 30.0% | 91.7% | 36.7% | 1 |
| embedding retrieval | 50.0% | 25.0% | 100.0% | 33.3% | 0 |
| retrieval + Qwen | 50.0% | 25.0% | 100.0% | 33.3% | 0 |
| retrieval + Llama | 50.0% | 25.0% | 100.0% | 33.3% | 0 |

Embedding top-5 recall was 56.7% across all mappable cases and 35% among cases
that required fallback. Neither LLM improved the frozen operating point and
both had zero JSON-contract failures. Qwen completed 30 calls in 97.1 seconds;
Llama completed them in 105.9 seconds. Llama's diagnostic curve was more useful,
but changing the threshold from this held-out result is forbidden. Production
therefore retains 0.90, while Llama is only a candidate for future calibration
on development data after retrieval is improved.

The case-free public result is `phase5_held_out_summary.json`. The ignored local
detailed report has SHA-256
`737852f34c64bd6121d16d5ee9029e1e5f94fa9b62514877bbc2f7934ab3b797`;
the generated public summary has SHA-256
`e30082e546506ec826a831482e6182e645b794448a68f5444cffca686b4120de`.
