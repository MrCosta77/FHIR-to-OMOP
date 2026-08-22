"""Shared, reproducible retrieval and proposal persistence for OMOP mappings."""

import hashlib
import json
import re

import chromadb

from src.utils.config import MODEL_NAME, SIMILARITY_THRESHOLD


INDEX_SCHEMA_VERSION = "omop-rag-index-v1"
TARGETS = {
    "condition_occurrence": {
        "collection": "snomed_conditions",
        "vocabulary": "SNOMED",
        "domain": "Condition",
        "source_vocabulary": "CMF_SYNTHEA_CONDITION",
        "id_column": "condition_occurrence_id",
        "concept_column": "condition_concept_id",
        "source_column": "condition_source_value",
    },
    "drug_exposure": {
        "collection": "rxnorm_drugs",
        "vocabulary": "RxNorm",
        "domain": "Drug",
        "source_vocabulary": "CMF_SYNTHEA_DRUG",
        "id_column": "drug_exposure_id",
        "concept_column": "drug_concept_id",
        "source_column": "drug_source_value",
    },
    "measurement": {
        "collection": "loinc_measurements",
        "vocabulary": "LOINC",
        "domain": "Measurement",
        "source_vocabulary": "CMF_SYNTHEA_MEASUREMENT",
        "id_column": "measurement_id",
        "concept_column": "measurement_concept_id",
        "source_column": "measurement_source_value",
    },
}


def _valid_date(field):
    return (
        f"COALESCE(TRY_STRPTIME({field}, '%Y%m%d')::DATE, "
        f"TRY_CAST({field} AS DATE))"
    )


def _vocabulary_stats(con, target_table):
    config = TARGETS[target_table]
    start = _valid_date("valid_start_date")
    end = _valid_date("valid_end_date")
    return con.execute(f"""
        SELECT COUNT(*), MIN(CAST(concept_id AS BIGINT)),
               MAX(CAST(concept_id AS BIGINT)),
               BIT_XOR(HASH(concept_id, concept_name, valid_start_date,
                            valid_end_date, invalid_reason))
        FROM concept
        WHERE vocabulary_id = ? AND domain_id = ?
          AND standard_concept = 'S'
          AND (invalid_reason IS NULL OR invalid_reason = '')
          AND CURRENT_DATE BETWEEN {start} AND {end}
    """, [config["vocabulary"], config["domain"]]).fetchone()


def vocabulary_signature(con, target_table):
    """Fingerprint the exact concept slice used to build a vector index."""
    config = TARGETS[target_table]
    row = _vocabulary_stats(con, target_table)
    payload = json.dumps(
        [INDEX_SCHEMA_VERSION, config["vocabulary"], config["domain"], *row],
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_versioned_collection(con, chroma_path, target_table):
    """Return a current Chroma index, rebuilding stale or unversioned indexes."""
    config = TARGETS[target_table]
    signature = vocabulary_signature(con, target_table)
    expected_count = int(_vocabulary_stats(con, target_table)[0])
    client = chromadb.PersistentClient(path=chroma_path)
    try:
        collection = client.get_collection(config["collection"])
    except Exception:
        collection = None

    if collection is not None:
        metadata = collection.metadata or {}
        actual_count = collection.count()
        if metadata.get("index_signature") == signature and actual_count == expected_count:
            if not metadata.get("build_complete"):
                metadata["build_complete"] = True
                metadata.setdefault("distance_metric", metadata.get("hnsw:space", "cosine"))
                collection.modify(
                    metadata={k: v for k, v in metadata.items() if not k.startswith("hnsw:")}
                )
            return collection
        if metadata.get("index_signature") == signature and actual_count < expected_count:
            # Resume an interrupted build instead of discarding completed batches.
            pass
        if not metadata.get("index_signature") and actual_count == expected_count:
            # One-time migration for legacy indexes. Chroma's historical
            # default was L2, so retain that metric explicitly.
            metadata.update({
                "index_signature": signature,
                "index_schema_version": INDEX_SCHEMA_VERSION,
                "vocabulary_id": config["vocabulary"],
                "domain_id": config["domain"],
                "distance_metric": metadata.get("hnsw:space", "l2"),
                "build_complete": True,
            })
            collection.modify(
                metadata={k: v for k, v in metadata.items() if not k.startswith("hnsw:")}
            )
            return collection
        elif metadata.get("index_signature") != signature or actual_count > expected_count:
            client.delete_collection(config["collection"])
            collection = None
    if collection is None:
        collection = client.create_collection(
            name=config["collection"],
            metadata={
                "hnsw:space": "cosine",
                "index_signature": signature,
                "index_schema_version": INDEX_SCHEMA_VERSION,
                "vocabulary_id": config["vocabulary"],
                "domain_id": config["domain"],
                "distance_metric": "cosine",
                "build_complete": False,
            },
        )

    if collection.count() < expected_count:
        start = _valid_date("valid_start_date")
        end = _valid_date("valid_end_date")
        concepts = con.execute(f"""
            SELECT concept_id, concept_name
            FROM concept
            WHERE vocabulary_id = ? AND domain_id = ?
              AND standard_concept = 'S'
              AND (invalid_reason IS NULL OR invalid_reason = '')
              AND CURRENT_DATE BETWEEN {start} AND {end}
            ORDER BY CAST(concept_id AS BIGINT)
        """, [config["vocabulary"], config["domain"]]).fetchall()
        existing_ids = set()
        if collection.count():
            existing_ids = set(collection.get(include=[])["ids"])
        missing = [row for row in concepts if str(row[0]) not in existing_ids]
        for offset in range(0, len(missing), 5000):
            batch = missing[offset:offset + 5000]
            collection.add(
                ids=[str(row[0]) for row in batch],
                documents=[row[1] for row in batch],
            )
            print(
                f"   Indexed {collection.count():,} / {len(concepts):,} "
                f"{config['collection']} concepts..."
            )
        metadata = dict(collection.metadata or {})
        metadata["build_complete"] = True
        collection.modify(
            metadata={k: v for k, v in metadata.items() if not k.startswith("hnsw:")}
        )
    return collection


def get_few_shot_prompt(con, target_table, label, limit=3):
    """Build stable few-shot context from distinct human-approved mappings."""
    rows = con.execute("""
        SELECT source_value, assigned_concept_id, normalized_value
        FROM mapping_provenance
        WHERE reviewed_by = 'Approved_by_Human' AND target_table = ?
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY LOWER(TRIM(source_value)), assigned_concept_id
            ORDER BY created_at, provenance_id
        ) = 1
        ORDER BY LOWER(TRIM(source_value)), assigned_concept_id
        LIMIT ?
    """, [target_table, int(limit)]).fetchall()
    if not rows:
        return ""
    lines = [f"Human-approved {label} examples:"]
    lines.extend(
        f"- '{source}' -> {concept_id} ({name})"
        for source, concept_id, name in rows
    )
    return "\n".join(lines) + "\n"


def selected_candidate(search_results, answer, distance_metric="cosine"):
    """Parse one exact candidate ID and return its name, distance and score."""
    numbers = re.findall(r"\d+", str(answer))
    selected_id = numbers[0] if numbers else "0"
    ids = search_results.get("ids", [[]])[0]
    documents = search_results.get("documents", [[]])[0]
    distances = search_results.get("distances", [[]])[0]
    for index, concept_id in enumerate(ids):
        if str(concept_id) == selected_id and selected_id != "0":
            distance = float(distances[index]) if index < len(distances) else 1.0
            if distance_metric == "l2":
                score = 1.0 - (distance / 2.0)
            else:
                score = 1.0 - distance
            score = round(max(0.0, min(1.0, score)), 4)
            return int(concept_id), documents[index], distance, score
    return None


def record_mapping_proposal(con, target_table, source_value, match):
    """Persist a candidate by event; publish to STCM only above the threshold."""
    if not match:
        return "UNRESOLVED", 0
    concept_id, concept_name, _distance, score = match
    config = TARGETS[target_table]
    target_ids = [
        int(row[0])
        for row in con.execute(f"""
            SELECT {config['id_column']}
            FROM {target_table}
            WHERE {config['concept_column']} = 0
              AND {config['source_column']} = ?
            ORDER BY {config['id_column']}
        """, [source_value]).fetchall()
    ]
    review_status = (
        "Pending_Human_Review"
        if score >= SIMILARITY_THRESHOLD
        else "Below_Confidence_Threshold"
    )
    vocabulary_version = con.execute("""
        SELECT vocabulary_version FROM vocabulary
        WHERE vocabulary_id = 'None' LIMIT 1
    """).fetchone()
    vocabulary_version = vocabulary_version[0] if vocabulary_version else "Unknown"

    # Remove the legacy term-level placeholder now that every affected event
    # receives its own provenance row.
    con.execute("""
        UPDATE mapping_provenance
        SET reviewed_by = 'Superseded_Legacy_Placeholder'
        WHERE target_table = ? AND source_value = ? AND target_id = 0
          AND mapping_method = 'llm_rag_few_shot'
          AND reviewed_by = 'Pending_Human_Review'
    """, [target_table, source_value])

    if review_status == "Pending_Human_Review":
        con.execute("""
            DELETE FROM source_to_concept_map
            WHERE source_code = ? AND source_vocabulary_id = ?
        """, [source_value, config["source_vocabulary"]])
        con.execute("""
            INSERT INTO source_to_concept_map (
                source_code, source_concept_id, source_vocabulary_id,
                source_code_description, target_concept_id,
                target_vocabulary_id, valid_start_date, valid_end_date,
                invalid_reason
            ) VALUES (?, 0, ?, ?, ?, ?, CURRENT_DATE, '2099-12-31', NULL)
        """, [
            source_value,
            config["source_vocabulary"],
            source_value,
            concept_id,
            config["vocabulary"],
        ])

    for target_id in target_ids:
        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by
            ) SELECT ?, ?, ?, ?, ?, 'llm_rag_few_shot', ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM mapping_provenance
                WHERE target_table = ? AND target_id = ?
                  AND assigned_concept_id = ?
                  AND mapping_method = 'llm_rag_few_shot'
            )
        """, [
            target_table, target_id, source_value, concept_name, concept_id,
            score, MODEL_NAME, vocabulary_version, review_status,
            target_table, target_id, concept_id,
        ])
    return review_status, len(target_ids)
