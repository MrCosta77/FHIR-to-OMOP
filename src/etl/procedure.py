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
    SNOMED_URI,
    replace_fhir_source_codings,
    select_source_coding,
)
from src.adapters.fhir_semantics import fhir_datetime, is_publishable_fhir_resource
from src.mapping.governance import current_run_id
from src.omop.cdm54 import create_table_sql, ensure_table_columns
from src.utils.config import DB_PATH, FHIR_DIR
from src.utils.helpers import stable_person_id


def generate_procedure_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_procedures(file_path):
    records = []
    with open(file_path, encoding='utf-8') as f:
        bundle = json.load(f)

        if bundle.get('resourceType') != 'Bundle':
            return records

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})

            # Capturar Procedimentos
            if resource.get('resourceType') == 'Procedure':
                if not is_publishable_fhir_resource(resource):
                    continue
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(patient_ref)

                if not person_id: continue

                codings = resource.get('code', {}).get('coding', [])
                coding = select_source_coding(
                    codings, preferred_systems=(SNOMED_URI,)
                )
                if coding is None:
                    continue

                # FHIR procedures can have performedDateTime or performedPeriod.
                period = resource.get('performedPeriod', {})
                start = fhir_datetime(
                    resource.get('performedDateTime') or period.get('start')
                )
                if start is None:
                    continue
                end = fhir_datetime(period.get('end')) or start
                date, event_datetime = start
                end_date, end_datetime = end

                full_url = entry.get('fullUrl', '')
                base_string = full_url if full_url else json.dumps(resource, sort_keys=True)
                source_event_key = full_url or (
                    f"sha256:{hashlib.sha256(base_string.encode('utf-8')).hexdigest()}"
                )
                procedure_id = generate_procedure_id(base_string)

                records.append((
                    procedure_id, person_id, coding.code, coding.source_value,
                    date, event_datetime, end_date, end_datetime,
                    coding.system_uri, coding.athena_vocabulary_id,
                    coding.source_vocabulary_id, coding.version,
                    source_event_key,
                ))

    return records

def run_procedure_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP PROCEDURE) [PRODUCTION]")
    print("-" * 50)

    print("🔍 Extracting procedures from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))

    all_records = []
    for f in fhir_files:
        all_records.extend(extract_procedures(f))

    print(f"📊 Extracted {len(all_records)} raw procedure records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")

    with duckdb.connect(DB_PATH) as con:
        # Every native and cross-domain publication below is atomic. Closing
        # the connection after an exception rolls the active transaction back.
        con.execute("BEGIN TRANSACTION")
        con.execute("DROP TABLE IF EXISTS procedure_occurrence")

        con.execute(create_table_sql("procedure_occurrence"))

        con.execute("DROP TABLE IF EXISTS stg_procedure")
        con.execute("""
            CREATE TEMPORARY TABLE stg_procedure (
                procedure_occurrence_id BIGINT,
                person_id BIGINT,
                code VARCHAR,
                display_text VARCHAR,
                date DATE,
                event_datetime TIMESTAMP,
                end_date DATE,
                end_datetime TIMESTAMP,
                source_system_uri VARCHAR,
                athena_vocabulary_id VARCHAR,
                source_vocabulary_id VARCHAR,
                source_version VARCHAR,
                source_event_key VARCHAR
            )
        """)

        con.executemany(
            "INSERT INTO stg_procedure VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            all_records,
        )

        ambiguous = con.execute("""
            SELECT COUNT(*)
            FROM (
                SELECT stg.procedure_occurrence_id
                FROM stg_procedure stg
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
                GROUP BY stg.procedure_occurrence_id
                HAVING COUNT(DISTINCT c_std.concept_id) > 1
            ) ambiguous_sources
        """).fetchone()[0]
        if ambiguous:
            raise ValueError(
                f"Procedure routing found {ambiguous} events with multiple "
                "Standard Maps to targets; explicit review is required."
            )

        con.execute("DROP TABLE IF EXISTS stg_procedure_routed")
        con.execute("""
            CREATE TEMPORARY TABLE stg_procedure_routed AS
            SELECT
                stg.*,
                COALESCE(c_src.concept_id::INTEGER, 0) AS source_concept_id,
                c_std.concept_id::INTEGER AS target_concept_id,
                c_std.domain_id AS target_domain
            FROM stg_procedure stg
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
        """)

        # DOMAIN ROUTING ESTRITO
        con.execute("""
            INSERT INTO procedure_occurrence (
                procedure_occurrence_id, person_id, procedure_concept_id,
                procedure_date, procedure_datetime,
                procedure_end_date, procedure_end_datetime,
                procedure_type_concept_id,
                procedure_source_value, procedure_source_concept_id
            )
            SELECT 
                stg.procedure_occurrence_id,
                stg.person_id,
                COALESCE(stg.target_concept_id, 0) AS procedure_concept_id,
                stg.date,
                stg.event_datetime,
                stg.end_date,
                stg.end_datetime,
                32817 AS procedure_type_concept_id,
                stg.display_text AS procedure_source_value,
                stg.source_concept_id AS procedure_source_concept_id
            FROM stg_procedure_routed stg
            WHERE stg.target_domain = 'Procedure'
               OR stg.target_concept_id IS NULL
        """)

        source_rows = {
            record[0]: (
                record[12], record[8], record[10], record[2], record[3], record[11]
            )
            for record in all_records
        }
        for target_table, _id_column, domain in (
            ('procedure_occurrence', 'procedure_occurrence_id', 'Procedure'),
            ('measurement', 'measurement_id', 'Measurement'),
            ('observation', 'observation_id', 'Observation'),
            ('device_exposure', 'device_exposure_id', 'Device'),
        ):
            target_ids = {
                row[0]
                for row in con.execute(
                    "SELECT procedure_occurrence_id FROM stg_procedure_routed "
                    "WHERE target_domain = ? OR (? = 'Procedure' AND target_domain IS NULL)",
                    [domain, domain],
                ).fetchall()
            }
            replace_fhir_source_codings(
                con,
                target_table,
                [
                    (
                        target_table, target_id, source_rows[target_id][0], None,
                        source_rows[target_id][1], source_rows[target_id][2],
                        source_rows[target_id][3], source_rows[target_id][4],
                        source_rows[target_id][5], current_run_id(),
                    )
                    for target_id in target_ids
                ],
                source_adapter="FHIR_R4_Procedure",
            )

        ensure_table_columns(con, "measurement")
        ensure_table_columns(con, "observation")
        ensure_table_columns(con, "device_exposure")

        route_targets = (
            ('measurement', 'measurement_id', 'Measurement'),
            ('observation', 'observation_id', 'Observation'),
            ('device_exposure', 'device_exposure_id', 'Device'),
        )
        for table, id_column, domain in route_targets:
            collisions = con.execute(f"""
                SELECT COUNT(*)
                FROM {table} event
                JOIN stg_procedure_routed stg
                  ON event.{id_column} = stg.procedure_occurrence_id
                WHERE stg.target_domain = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM mapping_provenance p
                      WHERE p.target_table = ?
                        AND p.target_id = event.{id_column}
                        AND p.mapping_method = 'deterministic_maps_to_domain_routed'
                  )
            """, [domain, table]).fetchone()[0]
            if collisions:
                raise ValueError(
                    f"Refusing to overwrite {collisions} unrelated {table} "
                    "rows whose IDs collide with routed Procedure resources."
                )

        con.execute("""
            INSERT INTO measurement (
                measurement_id, person_id, measurement_concept_id,
                measurement_date, measurement_datetime,
                measurement_type_concept_id, measurement_source_value,
                measurement_source_concept_id
            )
            SELECT procedure_occurrence_id, person_id, target_concept_id,
                   date, event_datetime, 32817, display_text,
                   source_concept_id
            FROM stg_procedure_routed
            WHERE target_domain = 'Measurement'
            ON CONFLICT (measurement_id) DO UPDATE SET
                person_id = EXCLUDED.person_id,
                measurement_concept_id = EXCLUDED.measurement_concept_id,
                measurement_date = EXCLUDED.measurement_date,
                measurement_datetime = EXCLUDED.measurement_datetime,
                measurement_type_concept_id = EXCLUDED.measurement_type_concept_id,
                measurement_source_value = EXCLUDED.measurement_source_value,
                measurement_source_concept_id = EXCLUDED.measurement_source_concept_id
        """)

        con.execute("""
            INSERT INTO observation (
                observation_id, person_id, observation_concept_id,
                observation_date, observation_datetime,
                observation_type_concept_id, observation_source_value,
                observation_source_concept_id
            )
            SELECT procedure_occurrence_id, person_id, target_concept_id,
                   date, event_datetime, 32817, display_text,
                   source_concept_id
            FROM stg_procedure_routed
            WHERE target_domain = 'Observation'
            ON CONFLICT (observation_id) DO UPDATE SET
                person_id = EXCLUDED.person_id,
                observation_concept_id = EXCLUDED.observation_concept_id,
                observation_date = EXCLUDED.observation_date,
                observation_datetime = EXCLUDED.observation_datetime,
                observation_type_concept_id = EXCLUDED.observation_type_concept_id,
                observation_source_value = EXCLUDED.observation_source_value,
                observation_source_concept_id = EXCLUDED.observation_source_concept_id
        """)

        con.execute("""
            INSERT INTO device_exposure (
                device_exposure_id, person_id, device_concept_id,
                device_exposure_start_date, device_exposure_start_datetime,
                device_exposure_end_date, device_exposure_end_datetime,
                device_type_concept_id, device_source_value,
                device_source_concept_id
            )
            SELECT procedure_occurrence_id, person_id, target_concept_id,
                   date, event_datetime, end_date, end_datetime,
                   32817, display_text, source_concept_id
            FROM stg_procedure_routed
            WHERE target_domain = 'Device'
            ON CONFLICT (device_exposure_id) DO UPDATE SET
                person_id = EXCLUDED.person_id,
                device_concept_id = EXCLUDED.device_concept_id,
                device_exposure_start_date = EXCLUDED.device_exposure_start_date,
                device_exposure_start_datetime = EXCLUDED.device_exposure_start_datetime,
                device_exposure_end_date = EXCLUDED.device_exposure_end_date,
                device_exposure_end_datetime = EXCLUDED.device_exposure_end_datetime,
                device_type_concept_id = EXCLUDED.device_type_concept_id,
                device_source_value = EXCLUDED.device_source_value,
                device_source_concept_id = EXCLUDED.device_source_concept_id
        """)

        # Registar na Auditoria
        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by, run_id, source_system,
                source_code, source_vocabulary_id, source_record_key
            )
            SELECT 
                'procedure_occurrence',
                procedure_occurrence_id,
                procedure_source_value,
                procedure_source_value,
                procedure_concept_id,
                'deterministic_maps_to',
                1.0,
                'N/A',
                'Athena_v5.4',
                'System', ?, coding.source_system_uri, coding.source_code,
                coding.source_vocabulary_id, coding.source_event_key
            FROM procedure_occurrence event
            JOIN fhir_event_source_coding coding
              ON coding.target_table = 'procedure_occurrence'
             AND coding.target_id = event.procedure_occurrence_id
            WHERE procedure_concept_id != 0
            AND NOT EXISTS (
                SELECT 1 FROM mapping_provenance p
                WHERE p.target_table = 'procedure_occurrence'
                  AND p.target_id = event.procedure_occurrence_id
                  AND p.mapping_method = 'deterministic_maps_to'
                  AND COALESCE(p.run_id, '') = COALESCE(?, '')
            )
        """, [current_run_id(), current_run_id()])

        routed_provenance = (
            ('measurement', 'measurement_id', 'measurement_concept_id', 'Measurement'),
            ('observation', 'observation_id', 'observation_concept_id', 'Observation'),
            ('device_exposure', 'device_exposure_id', 'device_concept_id', 'Device'),
        )
        for target_table, id_column, concept_column, domain in routed_provenance:
            con.execute(f"""
                INSERT INTO mapping_provenance (
                    target_table, target_id, source_value, normalized_value,
                    assigned_concept_id, mapping_method, score, model_name,
                    vocabulary_version, reviewed_by, run_id, source_system,
                    source_code, source_vocabulary_id, source_record_key
                )
                SELECT ?, event.{id_column}, stg.display_text,
                       stg.display_text, event.{concept_column},
                       'deterministic_maps_to_domain_routed', 1.0, 'N/A',
                       'Athena_v5.4', 'System', ?, coding.source_system_uri,
                       coding.source_code, coding.source_vocabulary_id,
                       coding.source_event_key
                FROM {target_table} event
                JOIN stg_procedure_routed stg
                  ON event.{id_column} = stg.procedure_occurrence_id
                JOIN fhir_event_source_coding coding
                  ON coding.target_table = ?
                 AND coding.target_id = event.{id_column}
                WHERE stg.target_domain = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM mapping_provenance p
                      WHERE p.target_table = ?
                        AND p.target_id = event.{id_column}
                        AND p.assigned_concept_id = event.{concept_column}
                        AND p.mapping_method = 'deterministic_maps_to_domain_routed'
                        AND COALESCE(p.run_id, '') = COALESCE(?, '')
                  )
            """, [
                target_table, current_run_id(), target_table, domain,
                target_table, current_run_id(),
            ])

        mapped_count = con.execute("SELECT COUNT(*) FROM procedure_occurrence WHERE procedure_concept_id != 0").fetchone()[0]
        unmapped_count = con.execute("SELECT COUNT(*) FROM procedure_occurrence WHERE procedure_concept_id = 0").fetchone()[0]
        routed_measurements = con.execute("SELECT COUNT(*) FROM stg_procedure_routed WHERE target_domain = 'Measurement'").fetchone()[0]
        routed_observations = con.execute("SELECT COUNT(*) FROM stg_procedure_routed WHERE target_domain = 'Observation'").fetchone()[0]
        routed_devices = con.execute("SELECT COUNT(*) FROM stg_procedure_routed WHERE target_domain = 'Device'").fetchone()[0]
        con.execute("COMMIT")

    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped (OMOP Standard): {mapped_count} procedures")
    print(f" - Routed to OMOP Measurement: {routed_measurements}")
    print(f" - Routed to OMOP Observation: {routed_observations}")
    print(f" - Routed to OMOP Device Exposure: {routed_devices}")
    print(f" - Sent to Human Mapping Queue (ID 0): {unmapped_count} procedures")

if __name__ == "__main__":
    run_procedure_etl()
