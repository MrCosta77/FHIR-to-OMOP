import duckdb
import requests
import sys
import os
from pathlib import Path

# Setup paths dynamically
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

# Centralized configurations (Fallback to local if config is missing)
try:
    from src.utils.config import DB_PATH, OLLAMA_URL, MODEL_NAME, SIMILARITY_THRESHOLD
except ImportError:
    DB_PATH = os.path.join(PROJECT_ROOT, "data", "omop_clinical.duckdb")
    OLLAMA_URL = "http://localhost:11434/api/generate"
    MODEL_NAME = "qwen2.5-coder:7b"
    SIMILARITY_THRESHOLD = 0.90

def get_unmapped_drugs(con):
    """Fetches unique unmapped drug descriptions from the database."""
    return con.execute("""
        SELECT DISTINCT drug_source_value 
        FROM drug_exposure 
        WHERE drug_concept_id = 0 
          AND drug_source_value IS NOT NULL 
          AND drug_source_value != 'Unknown'
    """).fetchall()

def ask_llm(prompt):
    """Sends a deterministic prompt to the local LLM."""
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0} 
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        return response.json().get("response", "").strip()
    except requests.exceptions.RequestException as e:
        print(f"LLM API Error: {e}")
        return None

def normalize_drug(raw_text):
    """Uses LLM to extract the core active ingredient from messy text."""
    prompt = f"""
    You are an expert clinical data encoder. 
    Extract the core medication or active ingredient name from the following raw text. 
    Remove any dosages, grammatical noise, or administrative tags.
    Return ONLY the clean medication name, nothing else.
    
    Raw Text: {raw_text}
    Clean Medication Name:"""
    return ask_llm(prompt)

def find_best_rxnorm_match(con, clean_text):
    """Uses DuckDB's Jaro-Winkler similarity to find the best RxNorm match."""
    query = """
        SELECT concept_id, concept_name, jaro_winkler_similarity(LOWER(concept_name), LOWER(?)) as score
        FROM concept
        WHERE vocabulary_id = 'RxNorm' 
          AND domain_id = 'Drug'
          AND standard_concept = 'S'
        ORDER BY score DESC
        LIMIT 1
    """
    result = con.execute(query, (clean_text,)).fetchone()
    
    if result and result[2] >= SIMILARITY_THRESHOLD:
        return result[0], result[1], result[2]
    return None, None, None

def run_semantic_mapping_drugs():
    """Main execution block for AI Semantic Mapping (Drugs)."""
    print("🤖 STARTING AI SEMANTIC MAPPING (DRUGS)\n" + "-"*50)

    with duckdb.connect(DB_PATH) as con:
        unmapped = get_unmapped_drugs(con)
        print(f"🔍 Found {len(unmapped)} unique unmapped drug descriptions.")

        if not unmapped:
            print("✅ No unmapped valid drugs found. The database is already fully standardized!")
            return
            
        successful_mappings = []

        for row in unmapped:
            raw_text = row[0]
            print(f"\n⚙️ Processing: '{raw_text}'")
            
            # 1. Ask LLM to normalize
            clean_text = normalize_drug(raw_text)
            if not clean_text:
                continue
            print(f"   🧠 LLM Normalized: '{clean_text}'")
            
            # 2. Check DuckDB for Jaro-Winkler match
            concept_id, concept_name, score = find_best_rxnorm_match(con, clean_text)
            
            if concept_id:
                print(f"   ✅ Match Found: {concept_name} (ID: {concept_id}) | Confidence: {score:.2f}")
                successful_mappings.append((concept_id, raw_text))
            else:
                print(f"   ❌ No match met the {SIMILARITY_THRESHOLD} threshold.")
        
        # 3. Bulk Update the Database
        if successful_mappings:
            print(f"\n💾 Writing {len(successful_mappings)} mapped concepts back to the database...")
            con.executemany("""
                UPDATE drug_exposure
                SET drug_concept_id = ?
                WHERE drug_source_value = ? 
                  AND drug_concept_id = 0
            """, successful_mappings)
            print("✅ Database successfully updated!")
            
            # 4. Integrated Audit
            print("\n🔍 AUDIT: Verifying a sample of AI-mapped drugs...")
            audit_query = """
                SELECT drug_source_value, drug_concept_id
                FROM drug_exposure
                WHERE drug_concept_id != 0
                LIMIT 5
            """
            results = con.execute(audit_query).fetchall()
            for r in results:
                print(f"   - Raw: '{r[0]:<30}' ➡️ ID: {r[1]}")
        else:
            print("\n⚠️ No new mappings met the confidence criteria to write back.")

if __name__ == "__main__":
    run_semantic_mapping_drugs()