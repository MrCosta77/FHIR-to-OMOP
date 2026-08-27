import os
import sys
import json
import glob
import duckdb
import hashlib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, FHIR_DIR
from src.utils.helpers import stable_person_id
from src.utils.unit_mapping import canonical_ucum_code
from src.omop.cdm54 import create_table_sql
from src.mapping.governance import current_run_id

def generate_observation_id(unique_string):
    """Generates a stable, deterministic ID from a unique string."""
    clean_string = unique_string.replace('urn:uuid:', '')
    return int(hashlib.sha256(clean_string.encode('utf-8')).hexdigest()[:15], 16)

def extract_observation_candidates(file_path):
    records = []
    with open(file_path, 'r', encoding='utf-8') as f:
        bundle = json.load(f)
        
        if bundle.get('resourceType') != 'Bundle':
            return records
            
        for entry in bundle.get('entry', []):
            resource = entry.get('resource', {})
            
            # 1. Caçar as "Condições" que são na verdade Observações Sociais (as nossas 1082 fugitivas)
            if resource.get('resourceType') == 'Condition':
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(patient_ref)
                if not person_id: continue
                    
                code = None
                display = "Unknown"
                codings = resource.get('code', {}).get('coding', [])
                for c in codings:
                    if c.get('system') == 'http://snomed.info/sct':
                        code = c.get('code')
                        display = c.get('display', '')
                        break
                if not code and codings:
                    code = codings[0].get('code')
                    display = codings[0].get('display', '')
                if not code: continue
                    
                date = resource.get('onsetDateTime', '')[:10]
                if not date: 
                    continue
                    
                full_url = entry.get('fullUrl', '')
                base_string = full_url if full_url else json.dumps(resource, sort_keys=True)
                obs_id = generate_observation_id(base_string)
                    
                records.append((
                    obs_id, person_id, code, display, date,
                    None, None, None, None, None, None,
                ))
                
            # 2. Route every coded FHIR Observation by its Standard OMOP
            # domain. Numeric questionnaire scores can legitimately belong to
            # OBSERVATION even though FHIR represents them as valueQuantity.
            elif resource.get('resourceType') == 'Observation':
                patient_ref = resource.get('subject', {}).get('reference', '')
                person_id = stable_person_id(patient_ref)
                if not person_id: continue
                
                code = None
                display = "Unknown"
                codings = resource.get('code', {}).get('coding', [])
                for c in codings:
                    if c.get('system') in ['http://loinc.org', 'http://snomed.info/sct']:
                        code = c.get('code')
                        display = c.get('display', '')
                        break
                if not code: continue
                
                date = resource.get('effectiveDateTime', '')[:10]
                if not date: continue
                
                full_url = entry.get('fullUrl', '')
                base_string = full_url if full_url else json.dumps(resource, sort_keys=True)
                obs_id = generate_observation_id(base_string)

                value_as_number = None
                value_as_string = None
                unit = None
                unit_system = None
                unit_code = None
                canonical_unit_code = None
                if 'valueQuantity' in resource:
                    quantity = resource['valueQuantity']
                    value_as_number = quantity.get('value')
                    unit = quantity.get('unit') or quantity.get('code')
                    unit_system = quantity.get('system')
                    unit_code = quantity.get('code')
                    canonical_unit_code = canonical_ucum_code(
                        unit_system, unit_code
                    )
                elif 'valueString' in resource:
                    value_as_string = resource.get('valueString')
                elif 'valueBoolean' in resource:
                    value_as_string = str(resource.get('valueBoolean')).lower()
                elif 'valueCodeableConcept' in resource:
                    value_codings = resource['valueCodeableConcept'].get('coding', [])
                    if value_codings:
                        value_as_string = (
                            value_codings[0].get('display')
                            or value_codings[0].get('code')
                        )

                records.append((
                    obs_id, person_id, code, display, date,
                    value_as_number, value_as_string, unit,
                    unit_system, unit_code, canonical_unit_code,
                ))
                
    return records

def run_observation_etl():
    print("⚙️ STARTING ETL PIPELINE (FHIR -> OMOP OBSERVATION) [PRODUCTION]")
    print("-" * 50)
    
    print("🔍 Extracting potential observation candidates from FHIR JSON files...")
    fhir_files = glob.glob(os.path.join(FHIR_DIR, "*.json"))
    
    all_records = []
    for f in fhir_files:
        all_records.extend(extract_observation_candidates(f))
        
    print(f"📊 Extracted {len(all_records)} raw candidates (mixed domains).")
    print("🔌 Connecting to DuckDB for strict domain-routed insertion...")
    
    with duckdb.connect(DB_PATH) as con:
        con.execute("BEGIN TRANSACTION")
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
                value_as_number DOUBLE,
                value_as_string VARCHAR,
                unit VARCHAR,
                unit_system VARCHAR,
                unit_code VARCHAR,
                canonical_unit_code VARCHAR
            )
        """)
        
        con.executemany(
            "INSERT INTO stg_observation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            all_records,
        )

        ambiguous = con.execute("""
            SELECT COUNT(*)
            FROM (
                SELECT stg.observation_id
                FROM stg_observation stg
                JOIN concept c_src
                  ON stg.code = c_src.concept_code
                 AND c_src.vocabulary_id IN ('SNOMED', 'LOINC')
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
                value_as_number, value_as_string,
                unit_concept_id, observation_source_value,
                observation_source_concept_id, unit_source_value
            )
            SELECT 
                stg.observation_id,
                stg.person_id,
                c_std.concept_id::INTEGER AS observation_concept_id,
                stg.date,
                stg.date::TIMESTAMP,
                32817 AS observation_type_concept_id,
                stg.value_as_number,
                stg.value_as_string,
                CASE
                    WHEN stg.unit IS NULL THEN NULL
                    ELSE COALESCE(c_unit_std.concept_id::INTEGER, 0)
                END AS unit_concept_id,
                stg.display_text AS observation_source_value,
                COALESCE(c_src.concept_id::INTEGER, 0) AS observation_source_concept_id,
                stg.unit AS unit_source_value
            FROM stg_observation stg
            LEFT JOIN concept c_src 
                ON stg.code = c_src.concept_code 
                AND c_src.vocabulary_id IN ('SNOMED', 'LOINC')
            LEFT JOIN concept_relationship cr 
                ON c_src.concept_id = cr.concept_id_1 
                AND cr.relationship_id = 'Maps to'
                AND cr.invalid_reason IS NULL
            INNER JOIN concept c_std 
                ON cr.concept_id_2 = c_std.concept_id 
                AND c_std.standard_concept = 'S'
                AND c_std.invalid_reason IS NULL
                AND c_std.domain_id = 'Observation' -- 🚨 O FILTRO MÁGICO 🚨
            LEFT JOIN concept c_unit_std
                ON stg.canonical_unit_code = c_unit_std.concept_code
                AND c_unit_std.vocabulary_id = 'UCUM'
                AND c_unit_std.domain_id = 'Unit'
                AND c_unit_std.standard_concept = 'S'
                AND c_unit_std.invalid_reason IS NULL
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY stg.observation_id
                ORDER BY c_std.concept_id DESC, c_unit_std.concept_id DESC
            ) = 1
        """)

        con.execute("""
            INSERT INTO mapping_provenance (
                target_table, target_id, source_value, normalized_value,
                assigned_concept_id, mapping_method, score, model_name,
                vocabulary_version, reviewed_by, run_id
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
                'System',
                ?
            FROM observation
            WHERE observation_concept_id != 0
            AND NOT EXISTS (
                SELECT 1 FROM mapping_provenance p
                WHERE p.target_table = 'observation'
                  AND p.target_id = observation.observation_id
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
