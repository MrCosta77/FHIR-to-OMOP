import sys
import os
import requests
import duckdb
from pathlib import Path

# 1. Setup paths so Python can find the 'src' folder
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.mapping.vector_store import ClinicalVectorStore

# 2. Centralized configurations
try:
    from src.utils.config import DB_PATH, OLLAMA_URL, MODEL_NAME
except ImportError:
    DB_PATH = os.path.join(PROJECT_ROOT, "data", "omop_clinical.duckdb")
    OLLAMA_URL = "http://localhost:11434/api/generate"
    MODEL_NAME = "qwen2.5-coder:7b"

CONFIDENCE_THRESHOLD = 0.35  # In cosine distance, closer to 0 is better. < 0.35 is high confidence.

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

def normalize_clinical_text(dirty_text):
    """
    Step 1 (RAG): Uses the LLM to clean laboratory jargon and fix spelling 
    before sending it to the vector search.
    """
    prompt = f"""You are an expert clinical biochemist.
    Your only task is to fix typos and expand abbreviations in this dirty laboratory test name 
    into standard English medical terminology.
    
    Rules:
    - DO NOT add explanations or conversational text.
    - Output ONLY the clean, normalized name of the test.
    
    Dirty text: '{dirty_text}'
    Clean text:"""
    
    clean_text = ask_llm(prompt)
    if clean_text:
        return clean_text.strip("'\"")
    return dirty_text # Fallback if it fails

def run_measurement_rag_mapping():
    print("🚀 Starting RAG Pipeline for MEASUREMENT (LOINC)...")
    
    # Initialize the vector database (vectors are already saved on disk)
    vector_store = ClinicalVectorStore(collection_name="loinc_concepts")
    
    with duckdb.connect(DB_PATH) as con:
        # Extract unmapped measurements
        query_unmapped = """
            SELECT DISTINCT measurement_source_value
            FROM measurement
            WHERE measurement_concept_id = 0
              AND measurement_source_value IS NOT NULL
        """
        unmapped_records = con.execute(query_unmapped).fetchall()
        
        if not unmapped_records:
            print("✅ All laboratory records are already mapped!")
            return

        print(f"🔍 Found {len(unmapped_records)} unique terms to normalize and map.")
        
        successful_mappings = []

        for row in unmapped_records:
            dirty_source_text = row[0]
            
            # 1. LLM Normalization
            clean_text = normalize_clinical_text(dirty_source_text)
            
            # 2. Semantic Search in ChromaDB
            search_results = vector_store.search(clean_text, top_k=1)
            
            best_concept_id = search_results['ids'][0][0]
            best_concept_name = search_results['metadatas'][0][0]['concept_name']
            best_concept_code = search_results['metadatas'][0][0]['concept_code']
            distance = search_results['distances'][0][0]
            
            print(f"\n🧪 Original: '{dirty_source_text}'")
            print(f"✨ LLM Clean: '{clean_text}'")
            
            # 3. Strict Validation
            if distance <= CONFIDENCE_THRESHOLD:
                print(f"✅ MATCH (Distance {distance:.4f}): {best_concept_name} (LOINC: {best_concept_code})")
                successful_mappings.append((int(best_concept_id), dirty_source_text))
            else:
                print(f"❌ REJECTED (Distance {distance:.4f} > {CONFIDENCE_THRESHOLD}): Best guess was {best_concept_name}")
        
        # 4. Secure Bulk Write-back
        if successful_mappings:
            print(f"\n💾 Writing {len(successful_mappings)} mappings to the database...")
            con.executemany("""
                UPDATE measurement
                SET measurement_concept_id = ?
                WHERE measurement_source_value = ? 
                  AND measurement_concept_id = 0
            """, successful_mappings)
            print("✅ Database successfully updated!")
        else:
            print("\n⚠️ No new mappings met the confidence threshold to write back.")

if __name__ == "__main__":
    run_measurement_rag_mapping()