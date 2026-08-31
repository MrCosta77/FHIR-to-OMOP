import sys
from datetime import datetime
from pathlib import Path

import duckdb

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.mapping.governance import ensure_governance_tables
from src.utils.config import DB_PATH


def setup_audit_tables():
    print("⚙️ STARTING AUDIT & METADATA SETUP")
    print("-" * 50)

    with duckdb.connect(DB_PATH) as con:
        # 1. Proveniência, execuções e decisões humanas
        ensure_governance_tables(con)
        con.execute("""
            UPDATE mapping_provenance
            SET reviewed_by = 'Superseded_Legacy_Placeholder'
            WHERE target_id = 0
              AND reviewed_by = 'Pending_Human_Review'
        """)
        print("✅ 'mapping_provenance' table verified/created successfully!")

        # 3. TABELA CDM_SOURCE (Obrigatória para o OHDSI Data Quality Dashboard)
        con.execute("""
            CREATE TABLE IF NOT EXISTS cdm_source (
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
        con.execute("DELETE FROM cdm_source")

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
                'https://github.com/MrCosta77/FHIR-to-OMOP',
                ?, ?, '5.4', 756265, ?
            )
        """, (current_date, current_date, vocab_version))

        print("✅ 'cdm_source' table verified/created successfully!")

if __name__ == "__main__":
    setup_audit_tables()
