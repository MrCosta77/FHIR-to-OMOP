import glob
import json
import os
import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.adapters.fhir_coding import (
    RXNORM_URI,
    replace_fhir_source_codings,
    select_source_coding,
)
from src.adapters.fhir_records import CodedFHIRPeriodRecord
from src.adapters.fhir_semantics import (
    extract_fhir_publication_exclusions,
    is_publishable_fhir_resource,
    medication_request_period,
    replace_fhir_publication_exclusions,
)
from src.mapping.governance import current_run_id
from src.omop.cdm54 import create_table_sql
from src.utils.config import DB_PATH, FHIR_DIR
from src.utils.helpers import (
    build_fhir_reference_index,
    normalise_fhir_reference,
    resolve_fhir_reference,
    stable_event_id,
    stable_payload_event_id,
    stable_person_id,
    stable_resource_fingerprint,
)


def extract_drugs(file_path):
    records = []
    with open(file_path, encoding='utf-8') as f:
        bundle = json.load(f)

        if bundle.get('resourceType') != 'Bundle':
            return records
        reference_index = build_fhir_reference_index(bundle)

        # Pass 1: Build a dictionary of Medication resources (UUID -> RxNorm details)
        medications = {}
        for entry in bundle.get('entry', []):
            res = entry.get('resource', {})
            if res.get('resourceType') == 'Medication':
                med_id = normalise_fhir_reference(res.get('id', ''))
                codings = res.get('code', {}).get('coding', [])
                coding = select_source_coding(
                    codings, preferred_systems=(RXNORM_URI,)
                )
                if med_id and coding:
                    medications[med_id] = coding

        # Pass 2: Process MedicationRequests
        for entry in bundle.get('entry', []):
            res = entry.get('resource', {})
            if res.get('resourceType') == 'MedicationRequest':
                if not is_publishable_fhir_resource(res):
                    continue
                patient_ref = res.get('subject', {}).get('reference', '')
                person_id = stable_person_id(
                    resolve_fhir_reference(patient_ref, reference_index)
                )
                if not person_id:
                    continue

                coding = None

                # Try inline coding first
                med_cc = res.get('medicationCodeableConcept', {})
                codings = med_cc.get('coding', [])
                if codings:
                    coding = select_source_coding(
                        codings, preferred_systems=(RXNORM_URI,)
                    )

                # If not inline, use two-pass medicationReference lookup
                if coding is None:
                    med_ref = res.get('medicationReference', {}).get('reference', '')
                    coding = medications.get(normalise_fhir_reference(med_ref))

                if coding is None:
                    continue

                period = medication_request_period(res)
                if period is None:
                    continue
                start_date, start_datetime, end_date, end_datetime = period

                # A forma mais segura de identificar um recurso num Bundle FHIR é o seu 'fullUrl'
                full_url = entry.get('fullUrl', '')
                if full_url:
                    base_string = full_url
                    source_event_key = full_url
                else:
                    # Fallback à prova de bala: transformar o recurso inteiro numa string e fazer o hash
                    base_string = json.dumps(res, sort_keys=True)
                    source_event_key = (
                        stable_resource_fingerprint(base_string)
                    )

                drug_id = (
                    stable_event_id(base_string)
                    if full_url
                    else stable_payload_event_id(base_string)
                )

                records.append(CodedFHIRPeriodRecord(
                    event_id=drug_id,
                    person_id=person_id,
                    coding=coding,
                    start_date=start_date,
                    start_datetime=start_datetime,
                    end_date=end_date,
                    end_datetime=end_datetime,
                    source_event_key=source_event_key,
                ))

    return records

def run_drug_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP DRUG) [PRODUCTION]")
    print("-" * 50)

    print("🔍 Extracting medications from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))

    all_records = []
    all_exclusions = []
    for f in fhir_files:
        all_records.extend(extract_drugs(f))
        all_exclusions.extend(
            extract_fhir_publication_exclusions(f, {"MedicationRequest"})
        )

    print(f"📊 Extracted {len(all_records)} raw medication records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")

    with duckdb.connect(DB_PATH) as con:
        con.execute('BEGIN TRANSACTION')
        try:
            replace_fhir_publication_exclusions(
                con, "FHIR_R4_MedicationRequest", all_exclusions,
                run_id=current_run_id(),
            )
            con.execute("DROP TABLE IF EXISTS drug_exposure")

            con.execute(create_table_sql("drug_exposure"))

            con.execute("DROP TABLE IF EXISTS stg_drug")
            con.execute("""
                CREATE TEMPORARY TABLE stg_drug (
                    drug_exposure_id BIGINT,
                    person_id BIGINT,
                    rxnorm_code VARCHAR,
                    display_text VARCHAR,
                    start_date DATE,
                    start_datetime TIMESTAMP,
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
                "INSERT INTO stg_drug VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [record.as_staging_row() for record in all_records],
            )

            con.execute("""
                INSERT INTO drug_exposure (
                    drug_exposure_id, person_id, drug_concept_id,
                    drug_exposure_start_date, drug_exposure_start_datetime,
                    drug_exposure_end_date, drug_exposure_end_datetime,
                    drug_type_concept_id, drug_source_value, drug_source_concept_id
                )
                SELECT
                    stg.drug_exposure_id,
                    stg.person_id,
                    CASE
                        WHEN c_std.domain_id = 'Drug' THEN COALESCE(c_std.concept_id::INTEGER, 0)
                        ELSE 0
                    END AS drug_concept_id,
                    stg.start_date,
                    stg.start_datetime,
                    stg.end_date,
                    stg.end_datetime,
                    38000177 AS drug_type_concept_id,
                    stg.display_text AS drug_source_value,
                    COALESCE(c_src.concept_id::INTEGER, 0) AS drug_source_concept_id
                FROM stg_drug stg
                LEFT JOIN concept c_src
                    ON stg.rxnorm_code = c_src.concept_code
                    AND stg.athena_vocabulary_id = c_src.vocabulary_id
                    AND c_src.invalid_reason IS NULL -- Evita duplicados de conceitos descontinuados
                LEFT JOIN concept_relationship cr
                    ON c_src.concept_id = cr.concept_id_1
                    AND cr.relationship_id = 'Maps to'
                    AND cr.invalid_reason IS NULL
                LEFT JOIN concept c_std
                    ON cr.concept_id_2 = c_std.concept_id
                    AND c_std.standard_concept = 'S'
                    AND c_std.invalid_reason IS NULL
                -- QUALIFY garante que, mesmo que haja múltiplos mapeamentos, só levamos 1 linha por ID
                QUALIFY ROW_NUMBER() OVER (PARTITION BY stg.drug_exposure_id ORDER BY c_std.concept_id DESC) = 1
            """)

            replace_fhir_source_codings(
                con,
                "drug_exposure",
                [
                    (
                        "drug_exposure", record.event_id, record.source_event_key,
                        None, record.coding.system_uri,
                        record.coding.source_vocabulary_id, record.coding.code,
                        record.coding.source_value, record.coding.version,
                        current_run_id(),
                    )
                    for record in all_records
                ],
                source_adapter="FHIR_R4_MedicationRequest",
            )

            con.execute("""
                INSERT INTO mapping_provenance (
                    target_table, target_id, source_value, normalized_value,
                    assigned_concept_id, mapping_method, score, model_name,
                    vocabulary_version, reviewed_by, run_id, source_system,
                    source_code, source_vocabulary_id, source_record_key
                )
                SELECT
                    'drug_exposure',
                    drug_exposure_id,
                    drug_source_value,
                    drug_source_value,
                    drug_concept_id,
                    'deterministic_maps_to',
                    1.0,
                    'N/A',
                    'Athena_v5.4',
                    'System', ?, coding.source_system_uri, coding.source_code,
                    coding.source_vocabulary_id, coding.source_event_key
                FROM drug_exposure event
                JOIN fhir_event_source_coding coding
                  ON coding.target_table = 'drug_exposure'
                 AND coding.target_id = event.drug_exposure_id
                WHERE drug_concept_id != 0
                AND NOT EXISTS (
                    SELECT 1 FROM mapping_provenance p
                    WHERE p.target_table = 'drug_exposure'
                      AND p.target_id = event.drug_exposure_id
                      AND p.mapping_method = 'deterministic_maps_to'
                      AND COALESCE(p.run_id, '') = COALESCE(?, '')
                )
            """, [current_run_id(), current_run_id()])

            mapped_count = con.execute("SELECT COUNT(*) FROM drug_exposure WHERE drug_concept_id != 0").fetchone()[0]
            unmapped_count = con.execute("SELECT COUNT(*) FROM drug_exposure WHERE drug_concept_id = 0").fetchone()[0]

            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped (OMOP Standard): {mapped_count} medications")
    print(f" - Sent to AI Fallback Queue (ID 0): {unmapped_count} medications")

if __name__ == "__main__":
    run_drug_etl()
