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


def generate_condition_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_conditions(file_path):
    records = []
    with open(file_path, encoding='utf-8') as f:
        bundle = json.load(f)

        if bundle.get('resourceType') != 'Bundle':
            return records
        reference_index = build_fhir_reference_index(bundle)

        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})

            if resource.get('resourceType') == 'Condition':
                if not is_publishable_fhir_resource(resource):
                    continue
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(
                    resolve_fhir_reference(patient_ref, reference_index)
                )

                if not person_id:
                    continue

                codings = resource.get('code', {}).get('coding', [])
                coding = select_source_coding(
                    codings, preferred_systems=(SNOMED_URI,)
                )
                if coding is None:
                    continue

                start = fhir_datetime(
                    resource.get('onsetDateTime')
                    or resource.get('onsetPeriod', {}).get('start')
                )
                if start is None:
                    continue
                start_date, start_datetime = start

                end = fhir_datetime(
                    resource.get('abatementDateTime')
                    or resource.get('abatementPeriod', {}).get('end')
                ) or start
                end_date, end_datetime = end

                # A forma mais segura de identificar um recurso num Bundle FHIR
                full_url = entry.get('fullUrl', '')
                if full_url:
                    base_string = full_url
                    source_event_key = full_url
                else:
                    base_string = json.dumps(resource, sort_keys=True)
                    source_event_key = (
                        f"sha256:{hashlib.sha256(base_string.encode('utf-8')).hexdigest()}"
                    )

                condition_id = generate_condition_id(base_string)

                records.append((
                    condition_id,
                    person_id,
                    coding.code,
                    coding.source_value,
                    start_date,
                    start_datetime,
                    end_date,
                    end_datetime,
                    coding.system_uri,
                    coding.athena_vocabulary_id,
                    coding.source_vocabulary_id,
                    coding.version,
                    source_event_key,
                ))
    return records

def run_condition_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP CONDITION) [PRODUCTION]")
    print("-" * 50)

    print("🔍 Extracting conditions from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))

    all_records = []
    all_exclusions = []
    for f in fhir_files:
        all_records.extend(extract_conditions(f))
        all_exclusions.extend(extract_fhir_publication_exclusions(f, {"Condition"}))

    print(f"📊 Extracted {len(all_records)} raw condition records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")

    with duckdb.connect(DB_PATH) as con:
        con.execute('BEGIN TRANSACTION')
        try:
            replace_fhir_publication_exclusions(
                con, "FHIR_R4_Condition", all_exclusions,
                run_id=current_run_id(),
            )
            # FORÇA A ELIMINAÇÃO DA TABELA ANTIGA PARA ATUALIZAR O SCHEMA
            con.execute("DROP TABLE IF EXISTS condition_occurrence")

            con.execute(create_table_sql("condition_occurrence"))

            # Temporary staging table
            con.execute("DROP TABLE IF EXISTS stg_condition")
            con.execute("""
                CREATE TEMPORARY TABLE stg_condition (
                    condition_occurrence_id BIGINT,
                    person_id BIGINT,
                    snomed_code VARCHAR,
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
                "INSERT INTO stg_condition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                all_records,
            )

            con.execute("DELETE FROM condition_occurrence")

            # THE OMOP TRIPLE-JOIN: Source -> Relationship -> Standard
            con.execute("""
                INSERT INTO condition_occurrence (
                    condition_occurrence_id, person_id, condition_concept_id,
                    condition_start_date, condition_start_datetime,
                    condition_end_date, condition_end_datetime,
                    condition_type_concept_id, condition_source_value,
                    condition_source_concept_id
                )
                SELECT 
                    stg.condition_occurrence_id,
                    stg.person_id,
                    CASE 
                        WHEN c_std.domain_id = 'Condition' THEN COALESCE(c_std.concept_id::INTEGER, 0)
                        ELSE 0 
                    END AS condition_concept_id,
                    stg.start_date,
                    stg.start_datetime,
                    stg.end_date,
                    stg.end_datetime,
                    32817 AS condition_type_concept_id, 
                    stg.display_text AS condition_source_value, 
                    COALESCE(c_src.concept_id::INTEGER, 0) AS condition_source_concept_id
                FROM stg_condition stg
                LEFT JOIN concept c_src 
                    ON stg.snomed_code = c_src.concept_code 
                    AND stg.athena_vocabulary_id = c_src.vocabulary_id
                    AND c_src.invalid_reason IS NULL
                LEFT JOIN concept_relationship cr 
                    ON c_src.concept_id = cr.concept_id_1 
                    AND cr.relationship_id = 'Maps to'
                    AND cr.invalid_reason IS NULL
                LEFT JOIN concept c_std 
                    ON cr.concept_id_2 = c_std.concept_id 
                    AND c_std.standard_concept = 'S'
                    AND c_std.invalid_reason IS NULL
                -- Só insere se for estritamente uma condição, ou se não teve mapeamento nenhum (IS NULL)
                WHERE (c_std.domain_id = 'Condition' OR c_std.domain_id IS NULL)
                -- Em caso de empate, dá prioridade ao conceito cujo domínio é 'Condition'
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY stg.condition_occurrence_id 
                    ORDER BY 
                        CASE WHEN c_std.domain_id = 'Condition' THEN 0 ELSE 1 END ASC,
                        c_std.concept_id ASC
                ) = 1
            """)

            replace_fhir_source_codings(
                con,
                "condition_occurrence",
                [
                    (
                        "condition_occurrence", record[0], record[12], None,
                        record[8], record[10], record[2], record[3], record[11],
                        current_run_id(),
                    )
                    for record in all_records
                ],
                source_adapter="FHIR_R4_Condition",
            )

            con.execute("""
                INSERT INTO mapping_provenance (
                    target_table, target_id, source_value, normalized_value,
                    assigned_concept_id, mapping_method, score, model_name,
                    vocabulary_version, reviewed_by, run_id, source_system,
                    source_code, source_vocabulary_id, source_record_key
                )
                SELECT 
                    'condition_occurrence',
                    condition_occurrence_id,
                    condition_source_value,
                    condition_source_value, -- Sem normalização nesta fase
                    condition_concept_id,
                    'deterministic_maps_to',
                    1.0, -- Confiança total
                    'N/A',
                    'Athena_v5.4',
                    'System', ?, coding.source_system_uri, coding.source_code,
                    coding.source_vocabulary_id, coding.source_event_key
                FROM condition_occurrence event
                JOIN fhir_event_source_coding coding
                  ON coding.target_table = 'condition_occurrence'
                 AND coding.target_id = event.condition_occurrence_id
                WHERE condition_concept_id != 0
                -- Evita duplicar registos se correres o script várias vezes
                AND NOT EXISTS (
                    SELECT 1 FROM mapping_provenance p
                    WHERE p.target_table = 'condition_occurrence'
                      AND p.target_id = event.condition_occurrence_id
                      AND p.mapping_method = 'deterministic_maps_to'
                      AND COALESCE(p.run_id, '') = COALESCE(?, '')
                )
            """, [current_run_id(), current_run_id()])

            mapped_count = con.execute("SELECT COUNT(*) FROM condition_occurrence WHERE condition_concept_id != 0").fetchone()[0]
            unmapped_count = con.execute("SELECT COUNT(*) FROM condition_occurrence WHERE condition_concept_id = 0").fetchone()[0]

            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped (OMOP Standard): {mapped_count} conditions")
    print(f" - Sent to AI Fallback / Observation Queue (ID 0): {unmapped_count} conditions")

if __name__ == "__main__":
    run_condition_etl()
