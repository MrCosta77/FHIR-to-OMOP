import os
import sys
import duckdb
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

# Aponta para a pasta onde extraíste o zip da Athena
VOCAB_DIR = os.path.join(PROJECT_ROOT, "data", "omop_vocab")

def load_vocabularies():
    print("⚙️ STARTING VOCABULARY LOAD (CONCEPT & CONCEPT_RELATIONSHIP)")
    print("-" * 50)
    
    concept_csv = os.path.join(VOCAB_DIR, "CONCEPT.csv")
    rel_csv = os.path.join(VOCAB_DIR, "CONCEPT_RELATIONSHIP.csv")
    
    if not os.path.exists(concept_csv) or not os.path.exists(rel_csv):
        print(f"❌ Error: Could not find vocabulary CSV files in {VOCAB_DIR}")
        print("Please ensure CONCEPT.csv and CONCEPT_RELATIONSHIP.csv are present.")
        return
        
    with duckdb.connect(DB_PATH) as con:
        print("⏳ Loading CONCEPT table (this may take a few seconds)...")
        con.execute("DROP TABLE IF EXISTS concept")
        
        # Leitura segura sugerida na review: delimitador tab, sem aspas e forçar strings
        con.execute(f"""
            CREATE TABLE concept AS 
            SELECT * FROM read_csv('{concept_csv}', 
                delim='\t', header=true, quote='', escape='', nullstr='', all_varchar=true)
        """)
        
        print("⏳ Loading CONCEPT_RELATIONSHIP table (the 'Maps to' bridge)...")
        con.execute("DROP TABLE IF EXISTS concept_relationship")
        con.execute(f"""
            CREATE TABLE concept_relationship AS 
            SELECT * FROM read_csv('{rel_csv}', 
                delim='\t', header=true, quote='', escape='', nullstr='', all_varchar=true)
        """)
        
        concept_count = con.execute("SELECT COUNT(*) FROM concept").fetchone()[0]
        rel_count = con.execute("SELECT COUNT(*) FROM concept_relationship").fetchone()[0]
        
    print("\n✅ Vocabularies successfully loaded into DuckDB!")
    print(f" - CONCEPT: {concept_count:,} rows")
    print(f" - CONCEPT_RELATIONSHIP: {rel_count:,} rows")

if __name__ == "__main__":
    load_vocabularies()