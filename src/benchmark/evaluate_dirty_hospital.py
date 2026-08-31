"""Evaluate the code-only deterministic baseline on the dirty-hospital fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from src.utils.config import SETTINGS

SYSTEM_TO_VOCABULARY = {
    "http://snomed.info/sct": "SNOMED",
    "http://loinc.org": "LOINC",
    "http://www.nlm.nih.gov/research/umls/rxnorm": "RxNorm",
}
VALID_DOMAINS = {"Condition", "Measurement", "Procedure", "Observation", "Drug"}
VALID_SPLITS = {"development", "held_out"}
VALID_DECISIONS = {"MAP", "ABSTAIN"}
EVALUATOR_VERSION = "1.0.0"


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            cases.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON at {path}:{line_number}: {exc}") from exc
    return cases


def validate_cases(cases: list[dict], *, enforce_release_shape: bool = True) -> None:
    if not cases:
        raise ValueError("Benchmark contains no cases.")
    ids = [case.get("case_id") for case in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("case_id values must be unique.")

    family_split: dict[str, set[str]] = defaultdict(set)
    concept_split: dict[int, set[str]] = defaultdict(set)
    family_variants: dict[str, set[str]] = defaultdict(set)
    for case in cases:
        missing = {"schema_version", "case_id", "family_id", "split", "domain", "variant", "source", "expected", "curation"} - case.keys()
        if missing:
            raise ValueError(f"{case.get('case_id', '<unknown>')}: missing {sorted(missing)}")
        if case["split"] not in VALID_SPLITS or case["domain"] not in VALID_DOMAINS:
            raise ValueError(f"{case['case_id']}: invalid split or domain.")
        expected = case["expected"]
        if expected.get("decision") not in VALID_DECISIONS:
            raise ValueError(f"{case['case_id']}: invalid expected decision.")
        if expected["decision"] == "MAP":
            if not isinstance(expected.get("concept_id"), int) or expected.get("domain") != case["domain"]:
                raise ValueError(f"{case['case_id']}: MAP requires an integer concept_id and matching domain.")
            concept_split[expected["concept_id"]].add(case["split"])
        elif any(expected.get(key) is not None for key in ("concept_id", "concept_name", "domain")):
            raise ValueError(f"{case['case_id']}: ABSTAIN must not encode a target concept.")
        family_split[case["family_id"]].add(case["split"])
        family_variants[case["family_id"]].add(case["variant"])

    leaked_families = sorted(key for key, splits in family_split.items() if len(splits) > 1)
    leaked_concepts = sorted(key for key, splits in concept_split.items() if len(splits) > 1)
    if leaked_families or leaked_concepts:
        raise ValueError(f"Split leakage detected: families={leaked_families}, concepts={leaked_concepts}")

    if enforce_release_shape:
        expected_variants = {"coded_exact", "dirty_text", "local_code", "ambiguous"}
        if len(cases) != 100 or Counter(c["split"] for c in cases) != {"development": 60, "held_out": 40}:
            raise ValueError("Release fixture must contain 100 cases split 60/40.")
        if Counter(c["domain"] for c in cases) != {domain: 20 for domain in VALID_DOMAINS}:
            raise ValueError("Release fixture must contain 20 cases per domain.")
        if any(variants != expected_variants for variants in family_variants.values()):
            raise ValueError("Each family must contain the four required variants.")


def validate_reference_concepts(connection: duckdb.DuckDBPyConnection, cases: Iterable[dict]) -> None:
    expected_by_id = {
        case["expected"]["concept_id"]: (case["expected"]["concept_name"], case["domain"])
        for case in cases if case["expected"]["decision"] == "MAP"
    }
    errors = []
    for concept_id, (expected_name, expected_domain) in expected_by_id.items():
        row = connection.execute(
            """SELECT concept_name, domain_id, standard_concept, invalid_reason
               FROM concept WHERE concept_id = ?""", [concept_id]
        ).fetchone()
        if row is None:
            errors.append(f"{concept_id}: absent")
        elif row != (expected_name, expected_domain, "S", None):
            errors.append(f"{concept_id}: expected {(expected_name, expected_domain, 'S', None)!r}, found {row!r}")
    if errors:
        raise ValueError("Invalid reference concepts:\n" + "\n".join(errors))


def deterministic_prediction(connection: duckdb.DuckDBPyConnection, case: dict) -> dict:
    """Predict from source coding only; labels and text are deliberately unused."""
    source = case["source"]
    code = source.get("code")
    vocabulary = SYSTEM_TO_VOCABULARY.get(source.get("system"))
    if not code:
        return {"decision": "ABSTAIN", "concept_id": None, "reason": "NO_SOURCE_CODE"}
    if vocabulary is None:
        return {"decision": "ABSTAIN", "concept_id": None, "reason": "UNSUPPORTED_CODE_SYSTEM"}

    rows = connection.execute(
        """
        WITH source_concepts AS (
          SELECT * FROM concept
          WHERE vocabulary_id = ? AND concept_code = ? AND invalid_reason IS NULL
        ), candidates AS (
          SELECT concept_id, concept_name, domain_id
          FROM source_concepts WHERE standard_concept = 'S'
          UNION
          SELECT target.concept_id, target.concept_name, target.domain_id
          FROM source_concepts source
          JOIN concept_relationship rel ON rel.concept_id_1 = source.concept_id
          JOIN concept target ON target.concept_id = rel.concept_id_2
          WHERE rel.relationship_id = 'Maps to'
            AND rel.invalid_reason IS NULL
            AND target.standard_concept = 'S'
            AND target.invalid_reason IS NULL
        )
        SELECT DISTINCT concept_id, concept_name, domain_id FROM candidates
        """, [vocabulary, code]
    ).fetchall()
    if not rows:
        return {"decision": "ABSTAIN", "concept_id": None, "reason": "NO_STANDARD_MAPPING"}
    if len(rows) > 1:
        return {"decision": "ABSTAIN", "concept_id": None, "reason": "AMBIGUOUS_STANDARD_MAPPING", "candidate_count": len(rows)}
    concept_id, concept_name, domain = rows[0]
    return {"decision": "MAP", "concept_id": concept_id, "concept_name": concept_name, "domain": domain, "reason": "EXACT_STANDARD_CODE"}


def score_predictions(cases: list[dict], predictions: list[dict]) -> dict:
    if len(cases) != len(predictions):
        raise ValueError("Prediction count does not match case count.")
    aggregate = Counter()
    by_domain: dict[str, Counter] = defaultdict(Counter)
    by_split: dict[str, Counter] = defaultdict(Counter)
    case_results = []
    for case, prediction in zip(cases, predictions, strict=True):
        expected = case["expected"]
        accepted = prediction["decision"] == "MAP"
        correct_map = expected["decision"] == "MAP" and accepted and prediction.get("concept_id") == expected["concept_id"]
        wrong_map = expected["decision"] == "MAP" and accepted and not correct_map
        false_map = expected["decision"] == "ABSTAIN" and accepted
        correct_abstain = expected["decision"] == "ABSTAIN" and not accepted
        missed_map = expected["decision"] == "MAP" and not accepted
        correct = correct_map or correct_abstain
        flags = {
            "expected_map": expected["decision"] == "MAP", "expected_abstain": expected["decision"] == "ABSTAIN",
            "accepted": accepted, "correct_map": correct_map, "wrong_map": wrong_map,
            "false_map": false_map, "correct_abstain": correct_abstain, "missed_map": missed_map, "correct": correct,
        }
        for name, value in flags.items():
            aggregate[name] += int(value)
            by_domain[case["domain"]][name] += int(value)
            by_split[case["split"]][name] += int(value)
        aggregate["total"] += 1
        by_domain[case["domain"]]["total"] += 1
        by_split[case["split"]]["total"] += 1
        case_results.append({"case_id": case["case_id"], "split": case["split"], "domain": case["domain"], "prediction": prediction, "expected": expected, "correct": correct})

    def metrics(counts: Counter) -> dict:
        accepted = counts["accepted"]
        expected_map = counts["expected_map"]
        expected_abstain = counts["expected_abstain"]
        return {
            **dict(counts),
            "coverage": accepted / counts["total"] if counts["total"] else 0.0,
            "accepted_precision": counts["correct_map"] / accepted if accepted else None,
            "mappable_recall": counts["correct_map"] / expected_map if expected_map else None,
            "abstain_accuracy": counts["correct_abstain"] / expected_abstain if expected_abstain else None,
            "overall_accuracy": counts["correct"] / counts["total"] if counts["total"] else 0.0,
        }
    return {
        "metrics": metrics(aggregate),
        "by_domain": {domain: metrics(by_domain[domain]) for domain in sorted(by_domain)},
        "by_split": {split: metrics(by_split[split]) for split in sorted(by_split)},
        "cases": case_results,
    }


def _git_commit(project_root: Path) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=project_root, check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def evaluate(fixture_path: Path, database_path: Path, split: str = "all") -> dict:
    all_cases = load_cases(fixture_path)
    validate_cases(all_cases)
    cases = all_cases if split == "all" else [case for case in all_cases if case["split"] == split]
    with duckdb.connect(str(database_path), read_only=True) as connection:
        validate_reference_concepts(connection, cases)
        # Strip labels before prediction so the predictor cannot observe ground truth.
        blind_inputs = [
            {key: case[key] for key in ("case_id", "domain", "source", "context")}
            for case in cases
        ]
        predictions = [deterministic_prediction(connection, case) for case in blind_inputs]
        vocabulary_version = connection.execute("SELECT vocabulary_version FROM cdm_source LIMIT 1").fetchone()
        etl_run = connection.execute("SELECT run_id FROM etl_run WHERE status = 'SUCCESS' ORDER BY completed_at DESC LIMIT 1").fetchone()
    scored = score_predictions(cases, predictions)
    project_root = Path(__file__).resolve().parents[2]
    return {
        "benchmark": "dirty-hospital-to-omop",
        "evaluator": {"name": "deterministic-code-only", "version": EVALUATOR_VERSION},
        "generated_at": datetime.now(UTC).isoformat(),
        "selection": {"split": split, "case_count": len(cases)},
        "provenance": {
            "fixture": str(fixture_path.resolve()),
            "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            "database": str(database_path.resolve()),
            "vocabulary_version": vocabulary_version[0] if vocabulary_version else None,
            "etl_run_id": etl_run[0] if etl_run else None,
            "git_commit": _git_commit(project_root),
            "python": platform.python_version(),
            "duckdb": duckdb.__version__,
        },
        **scored,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=Path("benchmarks/dirty_hospital/cases.jsonl"))
    parser.add_argument("--database", type=Path, default=SETTINGS.db_path)
    parser.add_argument("--split", choices=["all", "development", "held_out"], default="all")
    parser.add_argument("--output", type=Path, default=Path("benchmark_results/deterministic_baseline.json"))
    args = parser.parse_args()
    report = evaluate(args.fixture, args.database, args.split)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    metrics = report["metrics"]
    print(f"Wrote {args.output}: accuracy={metrics['overall_accuracy']:.3f}, coverage={metrics['coverage']:.3f}, accepted_precision={metrics['accepted_precision']:.3f}")


if __name__ == "__main__":
    main()
