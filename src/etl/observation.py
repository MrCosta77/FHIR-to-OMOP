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
from src.adapters.fhir_records import FHIRObservationRecord
from src.adapters.fhir_semantics import (
    extract_fhir_publication_exclusions,
    fhir_datetime,
    is_publishable_fhir_resource,
    replace_fhir_publication_exclusions,
)
from src.mapping.governance import current_run_id
from src.omop.cdm54 import create_table_sql
from src.utils.config import DB_PATH, FHIR_DIR
from src.utils.helpers import (
    build_fhir_reference_index,
    resolve_fhir_reference,
    stable_person_id,
)
from src.utils.unit_mapping import canonical_ucum_code


def generate_observation_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_observation_candidates(file_path):
    records = []
    with open(file_path, encoding='utf-8') as f:
        bundle = json.load(f)

        if bundle.get('resourceType') != 'Bundle':
            return records
        reference_index = build_fhir_reference_index(bundle)

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})

            # 1. Caçar as "Condições" que são na verdade Observações Sociais (as nossas 1082 fugitivas)
            if resource.get('resourceType') == 'Condition':
                if not is_publishable_fhir_resource(resource):
                    continue
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(
                    resolve_fhir_reference(patient_ref, reference_index)
                )
                if not person_id: continue

                codings = resource.get('code', {}).get('coding', [])
                coding = select_source_coding(
                    codings, preferred_systems=(SNOMED_URI,)
                )
                if coding is None:
                    continue

                effective = fhir_datetime(
                    resource.get('onsetDateTime')
                    or resource.get('onsetPeriod', {}).get('start')
                )
                if effective is None:
                    continue
                date, event_datetime = effective

                full_url = entry.get('fullUrl', '')
                base_string = full_url if full_url else json.dumps(resource, sort_keys=True)
                source_event_key = full_url or (
                    f"sha256:{hashlib.sha256(base_string.encode('utf-8')).hexdigest()}"
                )
                obs_id = generate_observation_id(base_string)

                records.append(FHIRObservationRecord(
                    event_id=obs_id,
                    person_id=person_id,
                    coding=coding,
                    event_date=date,
                    event_datetime=event_datetime,
                    source_event_key=source_event_key,
                ))

            # 2. Route every coded FHIR Observation by its Standard OMOP
            # domain. Numeric questionnaire scores can legitimately belong to
            # OBSERVATION even though FHIR represents them as valueQuantity.
            elif resource.get('resourceType') == 'Observation':
                if not is_publishable_fhir_resource(resource):
                    continue
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(
                    resolve_fhir_reference(patient_ref, reference_index)
                )
                if not person_id: continue

                effective = fhir_datetime(
                    resource.get('effectiveDateTime')
                    or resource.get('effectivePeriod', {}).get('start')
                )
                if effective is None:
                    continue
                date, event_datetime = effective

                full_url = entry.get('fullUrl', '')
                resource_json = json.dumps(resource, sort_keys=True)
                source_event_key = full_url or (
                    f"sha256:{hashlib.sha256(resource_json.encode('utf-8')).hexdigest()}"
                )

                for component_path, codeable, value_holder in (
                    iter_observation_elements(resource)
                ):
                    coding = select_source_coding(
                        codeable.get('coding', []),
                        preferred_systems=(LOINC_URI, SNOMED_URI),
                    )
                    if coding is None:
                        continue
                    base_string = full_url or resource_json
                    if component_path:
                        base_string = f"{base_string}::{component_path}"
                    obs_id = generate_observation_id(base_string)

                    value_as_number = None
                    value_as_string = None
                    unit = None
                    unit_system = None
                    unit_code = None
                    canonical_unit_code = None
                    value_coding = None
                    if 'valueQuantity' in value_holder:
                        quantity = value_holder['valueQuantity']
                        value_as_number = quantity.get('value')
                        unit = quantity.get('unit') or quantity.get('code')
                        unit_system = quantity.get('system')
                        unit_code = quantity.get('code')
                        canonical_unit_code = canonical_ucum_code(
                            unit_system, unit_code
                        )
                    elif 'valueString' in value_holder:
                        value_as_string = value_holder.get('valueString')
                    elif 'valueBoolean' in value_holder:
                        value_as_string = str(
                            value_holder.get('valueBoolean')
                        ).lower()
                    elif 'valueCodeableConcept' in value_holder:
                        value_coding = select_source_coding(
                            value_holder['valueCodeableConcept'].get('coding', []),
                            preferred_systems=(SNOMED_URI, LOINC_URI),
                        )

                    records.append(FHIRObservationRecord(
                        event_id=obs_id,
                        person_id=person_id,
                        coding=coding,
                        event_date=date,
                        event_datetime=event_datetime,
                        source_event_key=source_event_key,
                        component_path=component_path,
                        value_as_number=value_as_number,
                        value_as_string=value_as_string,
                        unit=unit,
                        unit_system=unit_system,
                        unit_code=unit_code,
                        canonical_unit_code=canonical_unit_code,
                        value_coding=value_coding,
                    ))

    return records

def run_observation_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP OBSERVATION) [PRODUCTION]")
    print("-" * 50)

    print("🔍 Extracting potential observation candidates from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))

    all_records = []
    all_exclusions = []
    for f in fhir_files:
        all_records.extend(extract_observation_candidates(f))
        all_exclusions.extend(
            extract_fhir_publication_exclusions(f, {"Condition", "Observation"})
        )

    print(f"📊 Extracted {len(all_records)} raw candidates (mixed domains).")
    print("🔌 Connecting to DuckDB for strict domain-routed insertion...")

    with duckdb.connect(DB_PATH) as con:
        con.execute("BEGIN TRANSACTION")
        replace_fhir_publication_exclusions(
            con, "FHIR_R4_Observation", all_exclusions,
            run_id=current_run_id(),
        )
        con.execute("DROP TABLE IF EXISTS observation")

        con.execute(create_table_sql("observation"))

        con.execute("DROP TABLE IF EXISTS stg_observation")
        con.execute("""
            CREATE TEMPORARY TABLE stg_observation (
                observation_id BIGINT,
                person_id BIGINT,
                code VARCHAR,
                display_text VARCHAR,
                date DATE,
                event_datetime TIMESTAMP,
                value_as_number DOUBLE,
                value_as_string VARCHAR,
                unit VARCHAR,
                unit_system VARCHAR,
                unit_code VARCHAR,
                canonical_unit_code VARCHAR,
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
            "INSERT INTO stg_observation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [record.as_staging_row() for record in all_records],
        )

        ambiguous = con.execute("""
            SELECT COUNT(*)
            FROM (
                SELECT stg.observation_id
                FROM stg_observation stg
                JOIN concept c_src
                  ON stg.code = c_src.concept_code
                 AND stg.athena_vocabulary_id = c_src.vocabulary_id
                JOIN concept_relationship cr
                  ON c_src.concept_id = cr.concept_id_1
                 AND cr.relationship_id = 'Maps to'
                 AND cr.invalid_reason IS NULL
                JOIN concept c_std
                  ON cr.concept_id_2 = c_std.concept_id
                 AND c_std.standard_concept = 'S'
                 AND c_std.invalid_reason IS NULL
                GROUP BY stg.observation_id
                HAVING COUNT(DISTINCT c_std.concept_id) > 1
            ) ambiguous_sources
        """).fetchone()[0]
        if ambiguous:
            raise ValueError(
                f"Observation routing found {ambiguous} events with "
                "multiple Standard Maps to targets; explicit review is required."
            )

        # DOMAIN ROUTING ESTRITO: O INNER JOIN garante que apenas os conceitos de Observação entram
        con.execute("""
            INSERT INTO observation (
                observation_id, person_id, observation_concept_id,
                observation_date, observation_datetime,
                observation_type_concept_id,
                value_as_number, value_as_string, value_as_concept_id,
                unit_concept_id, observation_source_value,
                observation_source_concept_id, unit_source_value,
                value_source_value
            )
            SELECT
                stg.observation_id,
                stg.person_id,
                COALESCE(c_std.concept_id::INTEGER, 0) AS observation_concept_id,
                stg.date,
                stg.event_datetime,
                32817 AS observation_type_concept_id,
                stg.value_as_number,
                stg.value_as_string,
                CASE
                    WHEN stg.value_source_code IS NULL THEN NULL
                    WHEN c_value_src.standard_concept = 'S'
                        THEN c_value_src.concept_id::INTEGER
                    WHEN c_value_std.standard_concept = 'S'
                        THEN c_value_std.concept_id::INTEGER
                    ELSE 0
                END AS value_as_concept_id,
                CASE
                    WHEN stg.unit IS NULL THEN NULL
                    ELSE COALESCE(c_unit_std.concept_id::INTEGER, 0)
                END AS unit_concept_id,
                stg.display_text AS observation_source_value,
                COALESCE(c_src.concept_id::INTEGER, 0) AS observation_source_concept_id,
                stg.unit AS unit_source_value,
                stg.value_source_value
            FROM stg_observation stg
            LEFT JOIN concept c_src
                ON stg.code = c_src.concept_code
                AND stg.athena_vocabulary_id = c_src.vocabulary_id
            LEFT JOIN concept_relationship cr
                ON c_src.concept_id = cr.concept_id_1
                AND cr.relationship_id = 'Maps to'
                AND cr.invalid_reason IS NULL
            LEFT JOIN concept c_std
                ON cr.concept_id_2 = c_std.concept_id
                AND c_std.standard_concept = 'S'
                AND c_std.invalid_reason IS NULL
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
            WHERE c_std.domain_id = 'Observation' OR c_std.concept_id IS NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY stg.observation_id
                ORDER BY c_std.concept_id DESC, c_unit_std.concept_id DESC,
                         c_value_std.concept_id DESC
            ) = 1
        """)

        existing_observation_ids = {
            row[0] for row in con.execute("SELECT observation_id FROM observation").fetchall()
        }
        replace_fhir_source_codings(
            con,
            "observation",
            [
                (
                    "observation", record.event_id, record.source_event_key,
                    record.component_path, record.coding.system_uri,
                    record.coding.source_vocabulary_id, record.coding.code,
                    record.coding.source_value, record.coding.version,
                    current_run_id(),
                )
                for record in all_records
                if record.event_id in existing_observation_ids
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
                'observation',
                observation_id,
                observation_source_value,
                observation_source_value,
                observation_concept_id,
                'deterministic_maps_to',
                1.0,
                'N/A',
                'Athena_v5.4',
                'System', ?, coding.source_system_uri, coding.source_code,
                coding.source_vocabulary_id, coding.source_event_key
            FROM observation event
            JOIN fhir_event_source_coding coding
              ON coding.target_table = 'observation'
             AND coding.target_id = event.observation_id
            WHERE observation_concept_id != 0
            AND NOT EXISTS (
                SELECT 1 FROM mapping_provenance p
                WHERE p.target_table = 'observation'
                  AND p.target_id = event.observation_id
                  AND p.mapping_method = 'deterministic_maps_to'
                  AND COALESCE(p.run_id, '') = COALESCE(?, '')
            )
        """, [current_run_id(), current_run_id()])

        mapped_count = con.execute("SELECT COUNT(*) FROM observation").fetchone()[0]
        con.execute("COMMIT")

    print("\n✅ ETL Complete!")
    print(f" - Successfully routed & mapped to OMOP Observation: {mapped_count} records")

if __name__ == "__main__":
    run_observation_etl()
