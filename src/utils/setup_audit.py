import sys
from datetime import datetime
from pathlib import Path

import duckdb

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.mapping.governance import ensure_governance_tables
from src.utils.config import DB_PATH, SETTINGS


def setup_audit_tables():
    print("⚙️ STARTING AUDIT & METADATA SETUP")
    print("-" * 50)

    with duckdb.connect(DB_PATH) as con:
        # 1. Provenance, executions, and human decisions
        ensure_governance_tables(con)
        con.execute("""
            UPDATE mapping_provenance
            SET reviewed_by = 'Superseded_Legacy_Placeholder'
            WHERE target_id = 0
              AND reviewed_by = 'Pending_Human_Review'
        """)
        print("✅ 'mapping_provenance' table verified/created successfully!")

        # 3. CDM_SOURCE TABLE (Required for the OHDSI Data Quality Dashboard)
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

        # Try to read the vocabulary version dynamically from the vocabulary table
        vocab_version = "Unknown_Vocab_Version"
        try:
            res = con.execute("SELECT vocabulary_version FROM vocabulary WHERE vocabulary_id = 'None'").fetchone()
            if res and res[0]:
                vocab_version = res[0]
        except duckdb.CatalogException:
            pass # Ignore if the vocabulary table does not exist yet

        current_date = datetime.now().strftime('%Y-%m-%d')
        source_release_date = SETTINGS.cdm_source_release_date or current_date
        con.execute("DELETE FROM cdm_source")

        con.execute("""
            INSERT INTO cdm_source (
                cdm_source_name, cdm_source_abbreviation, cdm_holder,
                source_description, source_documentation_reference, cdm_etl_reference,
                source_release_date, cdm_release_date, cdm_version,
                cdm_version_concept_id, vocabulary_version
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, '5.4', 756265, ?
            )
        """, (
            SETTINGS.cdm_source_name,
            SETTINGS.cdm_source_abbreviation,
            SETTINGS.cdm_holder,
            SETTINGS.cdm_source_description,
            SETTINGS.cdm_source_documentation_reference or None,
            SETTINGS.cdm_etl_reference or None,
            source_release_date,
            current_date,
            vocab_version,
        ))

        print("✅ 'cdm_source' table verified/created successfully!")

if __name__ == "__main__":
    setup_audit_tables()
