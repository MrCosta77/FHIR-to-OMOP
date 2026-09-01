import glob
import json
import os
import sys
from pathlib import Path

import duckdb

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.omop.cdm54 import create_table_sql
from src.utils.config import DB_PATH, FHIR_DIR
from src.adapters.fhir_semantics import fhir_datetime
from src.utils.helpers import (
    build_fhir_reference_index,
    resolve_fhir_reference,
    stable_event_id,
    stable_person_id,
)

# OMOP Standard Visit Concepts
# FHIR Encounter class codes mapped to OMOP Concept IDs
VISIT_MAPPING = {
    "AMB": 9202,    # Outpatient Visit
    "WELL": 9202,   # Outpatient Visit (Wellness is mapped to Outpatient)
    "IMP": 9201,    # Inpatient Visit
    "EMER": 9203,   # Emergency Room Visit
    "URGENT": 9203, # Emergency Room Visit
    "HH": 581476,   # Home Visit
    "VR": 722455,   # Telehealth
}

DEFAULT_VISIT_CONCEPT_ID = 0  # Unmapped visit

def run_visit_etl():
    """Extracts FHIR Encounters and loads them into OMOP VISIT_OCCURRENCE."""
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP VISIT_OCCURRENCE)")
    print("-" * 50)

    print("🔍 Extracting encounters from FHIR JSON files...")
    if not os.path.exists(FHIR_DIR):
        raise FileNotFoundError(f"FHIR data directory not found: {FHIR_DIR}")

    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))

    visit_records = []

    for file_path in fhir_files:
        with open(file_path, encoding='utf-8') as f:
            bundle = json.load(f)

            # Skip if not a Bundle
            if bundle.get('resourceType') != 'Bundle':
                continue
            reference_index = build_fhir_reference_index(bundle)

            for entry in bundle.get('entry', []):
                resource = entry.get('resource', {})

                if resource.get('resourceType') == 'Encounter':
                    # 1. Get Foreign Key (Patient ID)
                    patient_ref = resource.get('subject', {}).get('reference', '')
                    person_id = stable_person_id(
                        resolve_fhir_reference(patient_ref, reference_index)
                    )

                    if not person_id:
                        continue

                    # 2. Get Primary Key (Visit ID)
                    encounter_identity = (
                        entry.get('fullUrl') or resource.get('id', '')
                    )
                    visit_occurrence_id = stable_event_id(encounter_identity)

                    # 3. Get Dates
                    period = resource.get('period', {})
                    start = fhir_datetime(period.get('start'))
                    if start is None:
                        continue
                    end = fhir_datetime(period.get('end')) or start
                    start_date, start_datetime = start
                    end_date, end_datetime = end

                    # 4. Map Visit Type (Encounter Class)
                    fhir_class_code = resource.get('class', {}).get('code', '').upper()
                    visit_concept_id = VISIT_MAPPING.get(fhir_class_code, DEFAULT_VISIT_CONCEPT_ID)

                    # 32817 means "EHR" (Electronic Health Record) - standard for origin tracking
                    visit_type_concept_id = 32817

                    visit_records.append((
                        visit_occurrence_id,
                        person_id,
                        visit_concept_id,
                        start_date,
                        start_datetime,
                        end_date,
                        end_datetime,
                        visit_type_concept_id,
                        None,
                        None,
                        fhir_class_code,
                        0
                    ))

    print(f"📊 Extracted {len(visit_records)} raw visit records.")
    print("🔌 Connecting to DuckDB for standardized insertion...")

    with duckdb.connect(DB_PATH) as con:
        con.execute('BEGIN TRANSACTION')
        try:
            con.execute("DROP TABLE IF EXISTS visit_occurrence")
            con.execute(create_table_sql("visit_occurrence"))

            # Bulk Insert
            if visit_records:
                con.executemany("""
                    INSERT INTO visit_occurrence (
                        visit_occurrence_id, person_id, visit_concept_id,
                        visit_start_date, visit_start_datetime, visit_end_date,
                        visit_end_datetime, visit_type_concept_id, provider_id,
                        care_site_id, visit_source_value, visit_source_concept_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, visit_records)

            # Diagnostics
            mapped_count = con.execute("SELECT COUNT(*) FROM visit_occurrence WHERE visit_concept_id != 0").fetchone()[0]
            unmapped_count = con.execute("SELECT COUNT(*) FROM visit_occurrence WHERE visit_concept_id = 0").fetchone()[0]

            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
    print("\n✅ ETL Complete!")
    print(f" - Successfully mapped to OMOP Standards: {mapped_count} visits")
    print(f" - Failed to map (Unknown Type): {unmapped_count} visits")

if __name__ == "__main__":
    run_visit_etl()
