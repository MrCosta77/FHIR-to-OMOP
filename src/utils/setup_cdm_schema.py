import os
import sys
import duckdb
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH
from src.omop.cdm54 import (
    CDM_RELEASE,
    CDM_VERSION,
    ensure_complete_cdm_schema,
    record_schema_manifest,
)

def create_omop_skeleton():
    print(f"⚙️ INSTALLING OMOP CDM {CDM_VERSION} SCHEMA ({CDM_RELEASE})")
    print("-" * 50)
    
    with duckdb.connect(DB_PATH) as con:
        ensure_complete_cdm_schema(con)
        record_schema_manifest(con)
        # 1. Tabelas Clínicas Adicionais (vazias, mas obrigatórias para o DQD)
        con.execute("""
            CREATE TABLE IF NOT EXISTS death (
                person_id BIGINT NOT NULL,
                death_date DATE NOT NULL,
                death_datetime TIMESTAMP,
                death_type_concept_id INTEGER NOT NULL,
                cause_concept_id INTEGER,
                cause_source_value VARCHAR(50),
                cause_source_concept_id INTEGER
            );
            
            CREATE TABLE IF NOT EXISTS device_exposure (
                device_exposure_id BIGINT PRIMARY KEY,
                person_id BIGINT NOT NULL,
                device_concept_id INTEGER NOT NULL,
                device_exposure_start_date DATE NOT NULL,
                device_exposure_start_datetime TIMESTAMP,
                device_exposure_end_date DATE,
                device_exposure_end_datetime TIMESTAMP,
                device_type_concept_id INTEGER NOT NULL,
                unique_device_id VARCHAR(255),
                production_id VARCHAR(255),
                quantity INTEGER,
                provider_id BIGINT,
                visit_occurrence_id BIGINT,
                visit_detail_id BIGINT,
                device_source_value VARCHAR(50),
                device_source_concept_id INTEGER,
                unit_concept_id INTEGER,
                unit_source_value VARCHAR(50),
                unit_source_concept_id INTEGER
            );
            
            CREATE TABLE IF NOT EXISTS note (
                note_id BIGINT PRIMARY KEY,
                person_id BIGINT NOT NULL,
                note_date DATE NOT NULL,
                note_datetime TIMESTAMP,
                note_type_concept_id INTEGER NOT NULL,
                note_class_concept_id INTEGER NOT NULL,
                note_title VARCHAR(250),
                note_text VARCHAR NOT NULL,
                encoding_concept_id INTEGER NOT NULL,
                language_concept_id INTEGER NOT NULL,
                provider_id BIGINT,
                visit_occurrence_id BIGINT,
                visit_detail_id BIGINT,
                note_source_value VARCHAR(50)
            );
            
            CREATE TABLE IF NOT EXISTS specimen (
                specimen_id BIGINT PRIMARY KEY,
                person_id BIGINT NOT NULL,
                specimen_concept_id INTEGER NOT NULL,
                specimen_type_concept_id INTEGER NOT NULL,
                specimen_date DATE NOT NULL,
                specimen_datetime TIMESTAMP,
                quantity DOUBLE,
                unit_concept_id INTEGER,
                anatomic_site_concept_id INTEGER,
                disease_status_concept_id INTEGER,
                specimen_source_id VARCHAR(50),
                specimen_source_value VARCHAR(50),
                unit_source_value VARCHAR(50),
                anatomic_site_source_value VARCHAR(50),
                disease_status_source_value VARCHAR(50)
            );
            
            CREATE TABLE IF NOT EXISTS cost (
                cost_id BIGINT PRIMARY KEY,
                cost_event_id BIGINT NOT NULL,
                cost_domain_id VARCHAR(20) NOT NULL,
                cost_type_concept_id INTEGER NOT NULL,
                currency_concept_id INTEGER,
                total_charge DOUBLE,
                total_cost DOUBLE,
                total_paid DOUBLE,
                paid_by_payer DOUBLE,
                paid_by_patient DOUBLE,
                paid_patient_copay DOUBLE,
                paid_patient_coinsurance DOUBLE,
                paid_patient_deductible DOUBLE,
                paid_by_primary DOUBLE,
                paid_ingredient_cost DOUBLE,
                paid_dispensing_fee DOUBLE,
                payer_plan_period_id BIGINT,
                amount_allowed DOUBLE,
                revenue_code_concept_id INTEGER,
                revenue_code_source_value VARCHAR(50),
                drg_concept_id INTEGER,
                drg_source_value VARCHAR(3)
            );
            
            CREATE TABLE IF NOT EXISTS location (
                location_id BIGINT PRIMARY KEY,
                address_1 VARCHAR(50),
                address_2 VARCHAR(50),
                city VARCHAR(50),
                state VARCHAR(2),
                zip VARCHAR(9),
                county VARCHAR(20),
                location_source_value VARCHAR(50),
                country_concept_id INTEGER,
                country_source_value VARCHAR(80),
                latitude DOUBLE,
                longitude DOUBLE
            );
            
            CREATE TABLE IF NOT EXISTS provider (
                provider_id BIGINT PRIMARY KEY,
                provider_name VARCHAR(255),
                npi VARCHAR(20),
                dea VARCHAR(20),
                specialty_concept_id INTEGER,
                care_site_id BIGINT,
                year_of_birth INTEGER,
                gender_concept_id INTEGER,
                provider_source_value VARCHAR(50),
                specialty_source_value VARCHAR(50),
                specialty_source_concept_id INTEGER,
                gender_source_value VARCHAR(50),
                gender_source_concept_id INTEGER
            );
            
            CREATE TABLE IF NOT EXISTS source_to_concept_map (
                source_code VARCHAR(50) NOT NULL,
                source_concept_id INTEGER NOT NULL,
                source_vocabulary_id VARCHAR(20) NOT NULL,
                source_code_description VARCHAR(255),
                target_concept_id INTEGER NOT NULL,
                target_vocabulary_id VARCHAR(20) NOT NULL,
                valid_start_date DATE NOT NULL,
                valid_end_date DATE NOT NULL,
                invalid_reason VARCHAR(1)
            );
        """)
        
        # 2. Garantir os Tipos Obrigatórios nas tabelas que o nosso ETL já cria
        # O DQD exige que as chaves estrangeiras estejam estritamente definidas
        con.execute("CREATE SEQUENCE IF NOT EXISTS seq_location_id START 1")
        
        print("✅ The non-destructive 39-table OMOP CDM contract is installed.")

if __name__ == "__main__":
    create_omop_skeleton()
