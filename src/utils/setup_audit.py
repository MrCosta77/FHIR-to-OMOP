import os
import sys
import duckdb
from pathlib import Path
from datetime import datetime

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

def setup_audit_tables():
    print("⚙️ STARTING AUDIT & METADATA SETUP")
    print("-" * 50)
    
    with duckdb.connect(DB_PATH) as con:
        # 1. CRIAR A SEQUÊNCIA PRIMEIRO (A Correção!)
        con.execute("CREATE SEQUENCE IF NOT EXISTS seq_provenance_id START 1")
        
        # 2. TABELA DE PROVENIÊNCIA (Mapeamentos de IA)
        con.execute("""
            CREATE TABLE IF NOT EXISTS mapping_provenance (
                provenance_id BIGINT PRIMARY KEY DEFAULT nextval('seq_provenance_id'),
                target_table VARCHAR NOT NULL,
                target_id BIGINT NOT NULL,
                source_value VARCHAR,
                normalized_value VARCHAR,
                assigned_concept_id INTEGER NOT NULL,
                mapping_method VARCHAR NOT NULL,
                score DOUBLE NOT NULL,
                model_name VARCHAR,
                vocabulary_version VARCHAR,
                reviewed_by VARCHAR DEFAULT 'Pending_Human_Review',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        print("✅ 'mapping_provenance' table verified/created successfully!")

        # 3. TABELA CDM_SOURCE (Obrigatória para o OHDSI Data Quality Dashboard)
        con.execute("DROP TABLE IF EXISTS cdm_source")
        con.execute("""
            CREATE TABLE cdm_source (
                cdm_source_name VARCHAR(255) NOT NULL,
                cdm_source_abbreviation VARCHAR(25) NOT NULL,
                cdm_holder VARCHAR(255) NOT NULL,
                source_description VARCHAR,
                source_documentation_reference VARCHAR,
                cdm_etl_reference VARCHAR,
                source_release_date DATE NOT NULL,
                cdm_release_date DATE NOT NULL,
                cdm_version VARCHAR(10),
                cdm_version_concept_id INTEGER NOT NULL,
                vocabulary_version VARCHAR(20) NOT NULL
            )
        """)
        
        # Tentar ler a versão do vocabulário dinamicamente da tabela 'vocabulary'
        vocab_version = "Unknown_Vocab_Version"
        try:
            res = con.execute("SELECT vocabulary_version FROM vocabulary WHERE vocabulary_id = 'None'").fetchone()
            if res and res[0]:
                vocab_version = res[0]
        except duckdb.CatalogException:
            pass # Ignora se a tabela vocabulary ainda não existir

        current_date = datetime.now().strftime('%Y-%m-%d')
        
        con.execute("""
            INSERT INTO cdm_source (
                cdm_source_name, cdm_source_abbreviation, cdm_holder,
                source_description, source_documentation_reference, cdm_etl_reference,
                source_release_date, cdm_release_date, cdm_version,
                cdm_version_concept_id, vocabulary_version
            ) VALUES (
                'Clinical Mapping Framework (Synthea RWE)',
                'CMF-Synthea',
                'Mario Costa',
                'Synthetic patient records generated via Synthea and transformed into OMOP CDM.',
                'https://github.com/synthetichealth/synthea',
                'https://github.com/MarioCosta/Clinical-Mapping-Framework',
                ?, ?, '5.4', 756265, ?
            )
        """, (current_date, current_date, vocab_version))
        
        print("✅ 'cdm_source' table verified/created successfully!")

if __name__ == "__main__":
    setup_audit_tables()