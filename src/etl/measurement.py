import glob
import hashlib
import json
import os
import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.adapters.fhir_coding import (
    LOINC_URI,
    SNOMED_URI,
    iter_observation_elements,
    replace_fhir_source_codings,
    select_source_coding,
)
from src.adapters.fhir_semantics import fhir_datetime, is_publishable_fhir_resource
from src.mapping.governance import current_run_id
from src.omop.cdm54 import create_table_sql
from src.utils.config import DB_PATH, FHIR_DIR
from src.utils.helpers import stable_person_id
from src.utils.quarantine import ensure_quarantine_table
from src.utils.unit_mapping import canonical_ucum_code


def generate_measurement_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_measurements(file_path):
    records = []
    with open(file_path, encoding='utf-8') as f:
        bundle = json.load(f)

        if bundle.get('resourceType') != 'Bundle':
            return records

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})

            if resource.get('resourceType') == 'Observation':
                if not is_publishable_fhir_resource(resource):
                    continue
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(patient_ref)

                if not person_id:
                    continue

                effective = fhir_datetime(
                    resource.get('effectiveDateTime')
                    or resource.get('effectivePeriod', {}).get('start')
                )
                if effective is None:
                    continue
                date, event_datetime = effective
                full_url = entry.get('fullUrl', '')
                if full_url:
                    source_event_key = full_url
                else:
                    resource_json = json.dumps(resource, sort_keys=True)
                    source_event_key = (
                        f"sha256:{hashlib.sha256(resource_json.encode('utf-8')).hexdigest()}"
                    )

                for component_path, codeable, value_holder in (
                    iter_observation_elements(resource)
                ):
                    coding = select_source_coding(
                        codeable.get('coding', []),
                        preferred_systems=(LOINC_URI,),
                    )
                    quantity = value_holder.get('valueQuantity')
                    value_coding = select_source_coding(
                        value_holder.get('valueCodeableConcept', {}).get(
                            'coding', []
                        ),
                        preferred_systems=(SNOMED_URI, LOINC_URI),
                    )
                    if coding is None or (
                        not isinstance(quantity, dict) and value_coding is None
                    ):
                        continue
                    value = quantity.get('value') if quantity else None
                    if value is None and value_coding is None:
                        continue
                    unit_system = quantity.get('system') if quantity else None
                    unit_code = quantity.get('code') if quantity else None
                    unit = (quantity.get('unit') or unit_code) if quantity else None
                    canonical_unit_code = canonical_ucum_code(
                        unit_system, unit_code
                    )
                    base_string = full_url or resource_json
                    if component_path:
                        base_string = f"{base_string}::{component_path}"
                    measurement_id = generate_measurement_id(base_string)
                    records.append((
                        measurement_id, person_id, coding.code,
                        coding.source_value,
                        float(value) if value is not None else None,
                        unit, unit_system, unit_code, canonical_unit_code, date,
                        event_datetime,
                        coding.system_uri, coding.athena_vocabulary_id,
                        coding.source_vocabulary_id, coding.version,
                        source_event_key, component_path,
                        value_coding.system_uri if value_coding else None,
                        value_coding.athena_vocabulary_id if value_coding else None,
                        value_coding.source_vocabulary_id if value_coding else None,
                        value_coding.code if value_coding else None,
                        value_coding.source_value if value_coding else None,
                        value_coding.version if value_coding else None,
                    ))
    return records

def run_measurement_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP MEASUREMENT) [PRODUCTION]")
    print("-" * 50)

    print("🔍 Extracting laboratory results from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))

    all_records = []
    for f in fhir_files:
        all_records.extend(extract_measurements(f))

    print(f"📊 Extracted {len(all_records)} raw measurement records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")

    with duckdb.connect(DB_PATH) as con:
        con.execute("BEGIN TRANSACTION")
        ensure_quarantine_table(con)
        con.execute("DROP TABLE IF EXISTS measurement")

        con.execute(create_table_sql("measurement"))

        con.execute("DROP TABLE IF EXISTS stg_measurement")
        con.execute("""
            CREATE TEMPORARY TABLE stg_measurement (
                measurement_id BIGINT,
                person_id BIGINT,
                loinc_code VARCHAR,
                display_text VARCHAR,
                value DOUBLE,
                unit VARCHAR,
                unit_system VARCHAR,
                unit_code VARCHAR,
                canonical_unit_code VARCHAR,
                date DATE,
                event_datetime TIMESTAMP,
                source_system_uri VARCHAR,
                athena_vocabulary_id VARCHAR,
                source_vocabulary_id VARCHAR,
                source_version VARCHAR,
                source_event_key VARCHAR,
                component_path VARCHAR,
                value_source_system_uri VARCHAR,
                value_athena_vocabulary_id VARCHAR,
                value_source_vocabulary_id VARCHAR,
                value_source_code VARCHAR,
                value_source_value VARCHAR,
                value_source_version VARCHAR
            )
        """)

        con.executemany(
            "INSERT INTO stg_measurement VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            all_records,
        )

        # LOINC 788-0 is RDW expressed as a ratio/percent. Synthea also emits
        # it with fL (RDW-SD semantics). Preserve those records in quarantine
        # instead of changing the unit or publishing a false OMOP combination.
        con.execute("""
            UPDATE etl_quarantine
            SET active = FALSE, last_seen_at = CURRENT_TIMESTAMP
            WHERE target_table = 'measurement'
              AND reason_code = 'LOINC_UNIT_SEMANTIC_MISMATCH'
        """)
        con.execute("""
            INSERT INTO etl_quarantine (
                target_table, target_id, source_event_key, source_code,
                source_value, unit_source_value, reason_code, reason_detail,
                active
            )
            SELECT 'measurement', measurement_id, source_event_key,
                   loinc_code, display_text, unit,
                   'LOINC_UNIT_SEMANTIC_MISMATCH',
                   'LOINC 788-0 represents RDW ratio/percent; source unit fL represents incompatible RDW-SD semantics.',
                   TRUE
            FROM stg_measurement
            WHERE loinc_code = '788-0'
              AND athena_vocabulary_id = 'LOINC'
              AND canonical_unit_code IS DISTINCT FROM '%'
            ON CONFLICT (target_table, target_id, reason_code) DO UPDATE SET
                source_event_key = EXCLUDED.source_event_key,
                source_code = EXCLUDED.source_code,
                source_value = EXCLUDED.source_value,
                unit_source_value = EXCLUDED.unit_source_value,
                reason_detail = EXCLUDED.reason_detail,
                active = TRUE,
                last_seen_at = now()
        """)

        ambiguous = con.execute("""
            SELECT COUNT(*)
            FROM (
                SELECT stg.measurement_id
                FROM stg_measurement stg
                JOIN concept c_src
                  ON stg.loinc_code = c_src.concept_code
                 AND stg.athena_vocabulary_id = c_src.vocabulary_id
                JOIN concept_relationship cr
                  ON c_src.concept_id = cr.concept_id_1
                 AND cr.relationship_id = 'Maps to'
                 AND cr.invalid_reason IS NULL
                JOIN concept c_std
                  ON cr.concept_id_2 = c_std.concept_id
                 AND c_std.standard_concept = 'S'
                 AND c_std.invalid_reason IS NULL
                GROUP BY stg.measurement_id
                HAVING COUNT(DISTINCT c_std.concept_id) > 1
            ) ambiguous_sources
        """).fetchone()[0]
        if ambiguous:
            raise ValueError(
                f"Measurement routing found {ambiguous} events with "
                "multiple Standard Maps to targets; explicit review is required."
            )

        con.execute("""
            INSERT INTO measurement (
                measurement_id, person_id, measurement_concept_id,
                measurement_date, measurement_datetime,
                measurement_type_concept_id, value_as_number,
                value_as_concept_id,
                measurement_source_value, measurement_source_concept_id,
                unit_concept_id, unit_source_value, unit_source_concept_id,
                value_source_value
            )
            SELECT 
                stg.measurement_id,
                stg.person_id,
                CASE 
                    WHEN c_std.domain_id = 'Measurement' THEN COALESCE(c_std.concept_id::INTEGER, 0)
                    ELSE 0 
                END AS measurement_concept_id,
                stg.date,
                stg.event_datetime,
                32817 AS measurement_type_concept_id,
                stg.value AS value_as_number,
                CASE
                    WHEN stg.value_source_code IS NULL THEN NULL
                    WHEN c_value_src.standard_concept = 'S'
                        THEN c_value_src.concept_id::INTEGER
                    WHEN c_value_std.standard_concept = 'S'
                        THEN c_value_std.concept_id::INTEGER
                    ELSE 0
                END AS value_as_concept_id,
                stg.display_text AS measurement_source_value,
                COALESCE(c_src.concept_id::INTEGER, 0) AS measurement_source_concept_id,
                CASE
                    WHEN COALESCE(stg.unit_code, stg.unit) IS NULL THEN NULL
                    ELSE COALESCE(c_unit_std.concept_id::INTEGER, 0)
                END AS unit_concept_id,
                stg.unit AS unit_source_value,
                CASE
                    WHEN COALESCE(stg.unit_code, stg.unit) IS NULL THEN NULL
                    ELSE COALESCE(c_unit_src.concept_id::INTEGER, 0)
                END AS unit_source_concept_id,
                COALESCE(stg.value_source_value, stg.value::VARCHAR)
                    AS value_source_value
            FROM stg_measurement stg
            LEFT JOIN concept c_src 
                ON stg.loinc_code = c_src.concept_code 
                AND stg.athena_vocabulary_id = c_src.vocabulary_id
            LEFT JOIN concept_relationship cr 
                ON c_src.concept_id = cr.concept_id_1 
                AND cr.relationship_id = 'Maps to'
                AND cr.invalid_reason IS NULL
            LEFT JOIN concept c_std 
                ON cr.concept_id_2 = c_std.concept_id 
                AND c_std.standard_concept = 'S'
                AND c_std.invalid_reason IS NULL
            LEFT JOIN concept c_unit_src
                ON stg.unit_system = 'http://unitsofmeasure.org'
                AND stg.unit_code = c_unit_src.concept_code
                AND c_unit_src.vocabulary_id = 'UCUM'
                AND c_unit_src.invalid_reason IS NULL
            LEFT JOIN concept c_unit_std
                ON stg.canonical_unit_code = c_unit_std.concept_code
                AND c_unit_std.vocabulary_id = 'UCUM'
                AND c_unit_std.domain_id = 'Unit'
                AND c_unit_std.standard_concept = 'S'
                AND c_unit_std.invalid_reason IS NULL
            LEFT JOIN concept c_value_src
                ON stg.value_source_code = c_value_src.concept_code
                AND stg.value_athena_vocabulary_id = c_value_src.vocabulary_id
                AND c_value_src.invalid_reason IS NULL
            LEFT JOIN concept_relationship cr_value
                ON c_value_src.concept_id = cr_value.concept_id_1
                AND cr_value.relationship_id = 'Maps to'
                AND cr_value.invalid_reason IS NULL
            LEFT JOIN concept c_value_std
                ON cr_value.concept_id_2 = c_value_std.concept_id
                AND c_value_std.standard_concept = 'S'
                AND c_value_std.invalid_reason IS NULL
            -- Numeric FHIR Observations with a Standard target in another
            -- OMOP domain are routed by that domain's ETL, not retained here
            -- as artificial concept_id 0 measurements. Truly unresolved
            -- numeric observations remain in MEASUREMENT for human review.
            WHERE (
                    c_std.domain_id = 'Measurement'
                    OR c_std.concept_id IS NULL
                  )
              AND NOT (
                    stg.loinc_code = '788-0'
                    AND stg.athena_vocabulary_id = 'LOINC'
                    AND stg.canonical_unit_code IS DISTINCT FROM '%'
                  )
            -- O escudo contra o 'merge-inflation' garantindo apenas 1 conceito por registo
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY stg.measurement_id
                ORDER BY c_std.concept_id DESC,
                         c_unit_std.concept_id DESC,
                         c_unit_src.concept_id DESC,
                         c_value_std.concept_id DESC
            ) = 1
        """)

        existing_measurement_ids = {
            row[0] for row in con.execute("SELECT measurement_id FROM measurement").fetchall()
        }
        replace_fhir_source_codings(
            con,
            "measurement",
            [
                (
                    "measurement", record[0], record[15], record[16], record[11],
                    record[13], record[2], record[3], record[14],
                    current_run_id(),
                )
                for record in all_records
                if record[0] in existing_measurement_ids
            ],
            source_adapter="FHIR_R4_Observation",
        )

        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by, run_id, source_system,
                source_code, source_vocabulary_id, source_record_key
            )
            SELECT 
                'measurement',
                measurement_id,
                measurement_source_value,
                measurement_source_value,
                measurement_concept_id,
                'deterministic_maps_to',
                1.0,
                'N/A',
                'Athena_v5.4',
                'System', ?, coding.source_system_uri, coding.source_code,
                coding.source_vocabulary_id, coding.source_event_key
            FROM measurement event
            JOIN fhir_event_source_coding coding
              ON coding.target_table = 'measurement'
             AND coding.target_id = event.measurement_id
            WHERE measurement_concept_id != 0
            AND NOT EXISTS (
                SELECT 1 FROM mapping_provenance p
                WHERE p.target_table = 'measurement'
                  AND p.target_id = event.measurement_id
                  AND p.mapping_method = 'deterministic_maps_to'
                  AND COALESCE(p.run_id, '') = COALESCE(?, '')
            )
        """, [current_run_id(), current_run_id()])

        mapped_count = con.execute("SELECT COUNT(*) FROM measurement WHERE measurement_concept_id != 0").fetchone()[0]
        unmapped_count = con.execute("SELECT COUNT(*) FROM measurement WHERE measurement_concept_id = 0").fetchone()[0]
        con.execute("COMMIT")

    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped (Clean Data): {mapped_count} records")
    print(f" - Sent to AI Fallback Queue (ID 0): {unmapped_count} records")

if __name__ == "__main__":
    run_measurement_etl()
