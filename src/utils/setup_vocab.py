import os
import sys
import duckdb
import time
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, VOCAB_DIR

def load_vocabularies():
    print("⚙️ STARTING VOCABULARY LOAD (OMOP STANDARD)")
    print("-" * 50)
    
    start_time = time.time()
    
    # Caminhos para os ficheiros
    concept_csv = os.path.join(VOCAB_DIR, "CONCEPT.csv")
    concept_rel_csv = os.path.join(VOCAB_DIR, "CONCEPT_RELATIONSHIP.csv")
    vocabulary_csv = os.path.join(VOCAB_DIR, "VOCABULARY.csv")
    domain_csv = os.path.join(VOCAB_DIR, "DOMAIN.csv")
    concept_class_csv = os.path.join(VOCAB_DIR, "CONCEPT_CLASS.csv")
    concept_ancestor_csv = os.path.join(VOCAB_DIR, "CONCEPT_ANCESTOR.csv") # <-- NOVO
    
    if not os.path.exists(concept_csv) or not os.path.exists(concept_rel_csv):
        print("❌ ERROR: CONCEPT.csv or CONCEPT_RELATIONSHIP.csv not found in vocabulary folder.")
        sys.exit(1)
        
    with duckdb.connect(DB_PATH) as con:
        # 1. CONCEPT
        print("⏳ Loading CONCEPT table (this may take a few seconds)...")
        con.execute("DROP TABLE IF EXISTS concept")
        con.execute(f"""
            CREATE TABLE concept AS 
            SELECT * FROM read_csv_auto('{concept_csv}', delim='\t', quote='', escape='', nullstr='', all_varchar=true)
        """)
        
        # 2. CONCEPT_RELATIONSHIP
        print("⏳ Loading CONCEPT_RELATIONSHIP table (the 'Maps to' bridge)...")
        con.execute("DROP TABLE IF EXISTS concept_relationship")
        con.execute(f"""
            CREATE TABLE concept_relationship AS 
            SELECT * FROM read_csv_auto('{concept_rel_csv}', delim='\t', quote='', escape='', nullstr='', all_varchar=true)
        """)

        # 3. VOCABULARY
        print("⏳ Loading VOCABULARY metadata...")
        con.execute("DROP TABLE IF EXISTS vocabulary")
        if os.path.exists(vocabulary_csv):
            con.execute(f"""
                CREATE TABLE vocabulary AS 
                SELECT * FROM read_csv_auto('{vocabulary_csv}', delim='\t', quote='', escape='', nullstr='', all_varchar=true)
            """)
        
        # 4. DOMAIN
        print("⏳ Loading DOMAIN metadata...")
        con.execute("DROP TABLE IF EXISTS domain")
        if os.path.exists(domain_csv):
            con.execute(f"""
                CREATE TABLE domain AS 
                SELECT * FROM read_csv_auto('{domain_csv}', delim='\t', quote='', escape='', nullstr='', all_varchar=true)
            """)

        # 5. CONCEPT_CLASS
        print("⏳ Loading CONCEPT_CLASS metadata...")
        con.execute("DROP TABLE IF EXISTS concept_class")
        if os.path.exists(concept_class_csv):
            con.execute(f"""
                CREATE TABLE concept_class AS 
                SELECT * FROM read_csv_auto('{concept_class_csv}', delim='\t', quote='', escape='', nullstr='', all_varchar=true)
            """)
            
        # 6. CONCEPT_ANCESTOR (NOVO)
        print("⏳ Loading CONCEPT_ANCESTOR table (the hierarchy tree)...")
        con.execute("DROP TABLE IF EXISTS concept_ancestor")
        ca_count = 0
        if os.path.exists(concept_ancestor_csv):
            con.execute(f"""
                CREATE TABLE concept_ancestor AS 
                SELECT * FROM read_csv_auto('{concept_ancestor_csv}', delim='\t', quote='', escape='', nullstr='', all_varchar=true)
            """)
            ca_count = con.execute("SELECT COUNT(*) FROM concept_ancestor").fetchone()[0]
        else:
            print("⚠️ WARNING: CONCEPT_ANCESTOR.csv not found. Skipping hierarchy load.")
            
        # Contagens finais
        c_count = con.execute("SELECT COUNT(*) FROM concept").fetchone()[0]
        cr_count = con.execute("SELECT COUNT(*) FROM concept_relationship").fetchone()[0]
        
    elapsed = time.time() - start_time
    print(f"\n✅ Vocabularies successfully loaded into DuckDB in {elapsed:.1f} seconds!")
    print(f" - CONCEPT: {c_count:,} rows")
    print(f" - CONCEPT_RELATIONSHIP: {cr_count:,} rows")
    if ca_count > 0:
        print(f" - CONCEPT_ANCESTOR: {ca_count:,} rows")

if __name__ == "__main__":
    load_vocabularies()