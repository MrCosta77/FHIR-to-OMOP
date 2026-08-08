import duckdb
import os
import time
import sys
from pathlib import Path

# Setup dynamic paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
VOCAB_DIR = os.path.join(PROJECT_ROOT, "data", "omop_vocab")
DB_PATH = os.path.join(PROJECT_ROOT, "data", "omop_clinical.duckdb")

def load_concept_table():
    """Loads the OMOP CONCEPT table from the Athena CSV. (Run once per environment)"""
    concept_csv = os.path.join(VOCAB_DIR, "CONCEPT.csv")
    
    if not os.path.exists(concept_csv):
        print(f"❌ Error: The file {concept_csv} does not exist.")
        return

    print("🔌 Connecting to DuckDB...")
    try:
        con = duckdb.connect(DB_PATH)
        print("⏳ Loading CONCEPT table... (This takes a few seconds)")
        start_time = time.time()
        
        con.execute("DROP TABLE IF EXISTS concept")
        con.execute(f"""
            CREATE TABLE concept AS 
            SELECT * FROM read_csv_auto('{concept_csv}', header=True, delim='\t', nullstr='', sample_size=100000)
        """)
        
        elapsed_time = time.time() - start_time
        count = con.execute("SELECT COUNT(*) FROM concept").fetchone()[0]
        
        print(f"✅ Success! Loaded {count:,} concepts in {elapsed_time:.2f} seconds.")
    except Exception as e:
        print(f"❌ Critical error during load: {e}")
    finally:
        con.close()
        print("🔒 DuckDB connection closed.")

if __name__ == "__main__":
    load_concept_table()