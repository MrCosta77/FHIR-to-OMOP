"""Link OMOP clinical events to visits using governed FHIR semantics."""

from __future__ import annotations

import glob
import json
import os
import sys
from collections import Counter
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.mapping.governance import current_run_id
from src.utils.config import DB_PATH, FHIR_DIR
from src.utils.helpers import (
    build_fhir_reference_index,
    resolve_fhir_reference,
    stable_event_id,
    stable_resource_fingerprint,
)
from src.utils.quarantine import ensure_quarantine_table

EVENT_TABLES = {
    "condition_occurrence": ("condition_occurrence_id", "condition_start_date"),
    "drug_exposure": ("drug_exposure_id", "drug_exposure_start_date"),
    "measurement": ("measurement_id", "measurement_date"),
    "observation": ("observation_id", "observation_date"),
    "procedure_occurrence": ("procedure_occurrence_id", "procedure_date"),
    "device_exposure": ("device_exposure_id", "device_exposure_start_date"),
}

RESOURCE_TARGETS = {
    "Condition": ("condition_occurrence", "observation"),
    "MedicationRequest": ("drug_exposure",),
    "Observation": ("measurement", "observation"),
    "Procedure": ("procedure_occurrence", "measurement", "observation", "device_exposure"),
}

VISIT_QUARANTINE_REASONS = (
    "VISIT_REFERENCE_NOT_FOUND",
    "VISIT_REFERENCE_PERSON_MISMATCH",
    "VISIT_REFERENCE_DATE_OUTSIDE",
    "VISIT_TEMPORAL_AMBIGUOUS",
    "VISIT_TEMPORAL_UNRESOLVED",
)


def _table_exists(con, table: str) -> bool:
    return bool(con.execute(
        """SELECT COUNT(*) FROM information_schema.tables
           WHERE table_schema = 'main' AND table_name = ?""", [table]
    ).fetchone()[0])



def ensure_visit_linkage_tables(con) -> None:
    ensure_quarantine_table(con)
    con.execute("""
        CREATE TABLE IF NOT EXISTS fhir_event_context (
            target_table VARCHAR NOT NULL,
            target_id BIGINT NOT NULL,
            source_event_key VARCHAR NOT NULL,
            encounter_reference VARCHAR,
            referenced_visit_occurrence_id BIGINT,
            PRIMARY KEY (target_table, target_id)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS event_visit_linkage (
            target_table VARCHAR NOT NULL,
            target_id BIGINT NOT NULL,
            run_id VARCHAR NOT NULL,
            source_event_key VARCHAR NOT NULL,
            encounter_reference VARCHAR,
            visit_occurrence_id BIGINT,
            link_method VARCHAR NOT NULL,
            link_status VARCHAR NOT NULL,
            reason_code VARCHAR,
            candidate_count INTEGER NOT NULL,
            reason_detail VARCHAR NOT NULL,
            linked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (target_table, target_id, run_id)
        )
    """)
    linkage_columns = {
        row[0] for row in con.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'main' AND table_name = 'event_visit_linkage'
        """).fetchall()
    }
    if "reason_code" not in linkage_columns:
        con.execute("ALTER TABLE event_visit_linkage ADD COLUMN reason_code VARCHAR")


def extract_fhir_event_contexts(fhir_dir=FHIR_DIR) -> list[tuple]:
    """Extract event IDs and Encounter references without using clinical labels."""
    contexts: dict[tuple[str, int], tuple] = {}
    for file_path in sorted(glob.glob(os.path.join(str(fhir_dir), "*.json"))):
        with open(file_path, encoding="utf-8") as handle:
            bundle = json.load(handle)
        if bundle.get("resourceType") != "Bundle":
            continue
        reference_index = build_fhir_reference_index(bundle)
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            targets = RESOURCE_TARGETS.get(resource.get("resourceType"), ())
            if not targets:
                continue
            full_url = entry.get("fullUrl")
            if full_url:
                identity = full_url
                source_event_key = full_url
            else:
                canonical = json.dumps(resource, sort_keys=True)
                identity = canonical
                source_event_key = stable_resource_fingerprint(canonical)
            target_id = stable_event_id(identity)
            encounter_reference = resource.get("encounter", {}).get("reference") or None
            referenced_visit_id = (
                stable_event_id(
                    resolve_fhir_reference(encounter_reference, reference_index)
                )
                if encounter_reference else None
            )
            for target_table in targets:
                key = (target_table, target_id)
                value = (target_table, target_id, source_event_key, encounter_reference, referenced_visit_id)
                if key in contexts and contexts[key] != value:
                    raise ValueError(f"Conflicting FHIR encounter context for {target_table}/{target_id}")
                contexts[key] = value
    return list(contexts.values())


def replace_fhir_event_contexts(con, contexts: list[tuple]) -> None:
    ensure_visit_linkage_tables(con)
    con.execute("DELETE FROM fhir_event_context")
    if contexts:
        con.executemany("INSERT INTO fhir_event_context VALUES (?, ?, ?, ?, ?)", contexts)


def _link_table(con, table: str, id_column: str, date_column: str, run_id: str) -> Counter:
    if not _table_exists(con, table):
        return Counter()
    con.execute(f'UPDATE "{table}" SET visit_occurrence_id = NULL')
    con.execute("DROP TABLE IF EXISTS temp_visit_link_decision")
    con.execute(f"""
        CREATE TEMPORARY TABLE temp_visit_link_decision AS
        WITH base AS (
            SELECT event.{id_column} AS target_id,
                   event.person_id,
                   event.{date_column} AS event_date,
                   context.source_event_key,
                   context.encounter_reference,
                   context.referenced_visit_occurrence_id
            FROM "{table}" event
            LEFT JOIN fhir_event_context context
              ON context.target_table = '{table}'
             AND context.target_id = event.{id_column}
        ), temporal AS (
            SELECT base.target_id,
                   COUNT(visit.visit_occurrence_id) AS candidate_count,
                   MIN(visit.visit_occurrence_id) AS sole_candidate_id
            FROM base
            LEFT JOIN visit_occurrence visit
              ON base.encounter_reference IS NULL
             AND visit.person_id = base.person_id
             AND base.event_date BETWEEN visit.visit_start_date AND visit.visit_end_date
            GROUP BY base.target_id
        )
        SELECT base.*,
               referenced.visit_occurrence_id AS referenced_visit_id,
               referenced.person_id AS referenced_person_id,
               referenced.visit_start_date AS referenced_start_date,
               referenced.visit_end_date AS referenced_end_date,
               temporal.candidate_count,
               temporal.sole_candidate_id,
               CASE
                 WHEN base.encounter_reference IS NOT NULL AND referenced.visit_occurrence_id IS NULL THEN 'UNRESOLVED'
                 WHEN base.encounter_reference IS NOT NULL AND referenced.person_id <> base.person_id THEN 'UNRESOLVED'
                 WHEN base.encounter_reference IS NOT NULL
                      AND base.event_date NOT BETWEEN referenced.visit_start_date AND referenced.visit_end_date THEN 'UNRESOLVED'
                 WHEN base.encounter_reference IS NOT NULL THEN 'FHIR_REFERENCE'
                 WHEN temporal.candidate_count = 1 THEN 'TEMPORAL_FALLBACK'
                 ELSE 'UNRESOLVED'
               END AS link_method,
               CASE
                 WHEN base.encounter_reference IS NOT NULL AND referenced.visit_occurrence_id IS NULL THEN 'VISIT_REFERENCE_NOT_FOUND'
                 WHEN base.encounter_reference IS NOT NULL AND referenced.person_id <> base.person_id THEN 'VISIT_REFERENCE_PERSON_MISMATCH'
                 WHEN base.encounter_reference IS NOT NULL
                      AND base.event_date NOT BETWEEN referenced.visit_start_date AND referenced.visit_end_date THEN 'VISIT_REFERENCE_DATE_OUTSIDE'
                 WHEN base.encounter_reference IS NULL AND temporal.candidate_count > 1 THEN 'VISIT_TEMPORAL_AMBIGUOUS'
                 WHEN base.encounter_reference IS NULL AND temporal.candidate_count = 0 THEN 'VISIT_TEMPORAL_UNRESOLVED'
                 ELSE NULL
               END AS reason_code
        FROM base
        JOIN temporal USING (target_id)
        LEFT JOIN visit_occurrence referenced
          ON referenced.visit_occurrence_id = base.referenced_visit_occurrence_id
    """)
    con.execute(f"""
        UPDATE "{table}" event
        SET visit_occurrence_id = CASE
            WHEN decision.link_method = 'FHIR_REFERENCE' THEN decision.referenced_visit_id
            WHEN decision.link_method = 'TEMPORAL_FALLBACK' THEN decision.sole_candidate_id
        END
        FROM temp_visit_link_decision decision
        WHERE event.{id_column} = decision.target_id
          AND decision.link_method IN ('FHIR_REFERENCE', 'TEMPORAL_FALLBACK')
    """)

    con.execute("DELETE FROM event_visit_linkage WHERE target_table = ? AND run_id = ?", [table, run_id])
    con.execute(f"""
        INSERT INTO event_visit_linkage (
            target_table, target_id, run_id, source_event_key,
            encounter_reference, visit_occurrence_id, link_method,
            link_status, reason_code, candidate_count, reason_detail
        )
        SELECT '{table}', target_id, ?,
               COALESCE(source_event_key, '{table}/' || target_id::VARCHAR),
               encounter_reference,
               CASE WHEN link_method = 'FHIR_REFERENCE' THEN referenced_visit_id
                    WHEN link_method = 'TEMPORAL_FALLBACK' THEN sole_candidate_id END,
               link_method,
               CASE WHEN reason_code IS NULL THEN 'LINKED' ELSE 'QUARANTINED' END,
               reason_code,
               CASE WHEN encounter_reference IS NOT NULL
                    THEN CASE WHEN referenced_visit_id IS NULL THEN 0 ELSE 1 END
                    ELSE candidate_count END::INTEGER,
               CASE
                 WHEN link_method = 'FHIR_REFERENCE' THEN 'Matched the explicit FHIR Encounter reference and validated person/date consistency.'
                 WHEN link_method = 'TEMPORAL_FALLBACK' THEN 'No Encounter reference was supplied; exactly one visit covered the event date.'
                 ELSE reason_code
               END
        FROM temp_visit_link_decision
    """, [run_id])

    placeholders = ", ".join("?" for _ in VISIT_QUARANTINE_REASONS)
    con.execute(f"""
        UPDATE etl_quarantine SET active = FALSE, last_seen_at = CURRENT_TIMESTAMP
        WHERE target_table = ? AND reason_code IN ({placeholders})
    """, [table, *VISIT_QUARANTINE_REASONS])
    con.execute(f"""
        INSERT INTO etl_quarantine (
            target_table, target_id, source_event_key, source_value,
            reason_code, reason_detail, active
        )
        SELECT '{table}', target_id,
               COALESCE(source_event_key, '{table}/' || target_id::VARCHAR),
               encounter_reference, reason_code,
               CASE reason_code
                 WHEN 'VISIT_REFERENCE_NOT_FOUND' THEN 'Explicit FHIR Encounter reference does not resolve to a published visit.'
                 WHEN 'VISIT_REFERENCE_PERSON_MISMATCH' THEN 'Explicit Encounter belongs to a different person.'
                 WHEN 'VISIT_REFERENCE_DATE_OUTSIDE' THEN 'Event date is outside the explicitly referenced Encounter period.'
                 WHEN 'VISIT_TEMPORAL_AMBIGUOUS' THEN 'Multiple visits cover the event date; temporal fallback is unsafe.'
                 ELSE 'No visit covers the event date and no explicit Encounter reference was supplied.'
               END,
               TRUE
        FROM temp_visit_link_decision
        WHERE reason_code IS NOT NULL
        ON CONFLICT (target_table, target_id, reason_code) DO UPDATE SET
            source_event_key = EXCLUDED.source_event_key,
            source_value = EXCLUDED.source_value,
            reason_detail = EXCLUDED.reason_detail,
            active = TRUE,
            last_seen_at = now()
    """)
    rows = con.execute(
        """SELECT link_method, link_status, COUNT(*) FROM event_visit_linkage
           WHERE target_table = ? AND run_id = ? GROUP BY 1, 2""", [table, run_id]
    ).fetchall()
    counts = Counter()
    for method, status, count in rows:
        counts[method] += count
        counts[status] += count
    return counts


def link_events_in_connection(con, run_id: str | None = None) -> dict[str, Counter]:
    ensure_visit_linkage_tables(con)
    effective_run_id = run_id or current_run_id() or "UNTRACKED"
    return {
        table: _link_table(con, table, id_column, date_column, effective_run_id)
        for table, (id_column, date_column) in EVENT_TABLES.items()
    }


def link_events_to_visits():
    print("⚙️ STARTING GOVERNED EVENT → VISIT LINKAGE")
    print("-" * 50)
    contexts = extract_fhir_event_contexts(FHIR_DIR)
    with duckdb.connect(DB_PATH) as con:
        con.execute("BEGIN TRANSACTION")
        replace_fhir_event_contexts(con, contexts)
        results = link_events_in_connection(con)
        con.execute("COMMIT")
    print("\n✅ Visit linkage complete")
    for table, counts in results.items():
        print(
            f" - {table}: reference={counts['FHIR_REFERENCE']}, "
            f"temporal={counts['TEMPORAL_FALLBACK']}, quarantined={counts['QUARANTINED']}"
        )


if __name__ == "__main__":
    link_events_to_visits()
