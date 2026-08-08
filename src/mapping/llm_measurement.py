import sys
import requests
import duckdb
from pathlib import Path

# Setup paths to import our centralized configurations
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, OLLAMA_URL, MODEL_NAME

def get_unmapped_measurements():
    """Fetches unique unmapped laboratory descriptions from the database."""
    with duckdb.connect(DB_PATH) as con:
        return con.execute("""
            SELECT DISTINCT measurement_source_value 
            FROM measurement 
            WHERE measurement_concept_id = 0 
              AND measurement_source_value IS NOT NULL 
              AND measurement_source_value != 'Unknown'
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

def normalize_lab_test(raw_text):
    """Uses LLM to extract the core analyte from noisy laboratory text."""
    prompt = f"""
    You are an expert clinical biochemist. 
    Extract the core biomarker, analyte, or laboratory test name from the following noisy text. 
    Remove administrative tags, units, or sample types. Return ONLY the clean analyte name (e.g., 'Glucose', 'Cholesterol', 'Calcium', 'Hemoglobin').
    
    Raw Text: {raw_text}
    Clean Analyte Name:"""
    return ask_llm(prompt)

def find_loinc_match_by_substring(con, clean_text):
    """
    Finds a standard LOINC concept by checking if the official concept name 
    contains the clean analyte extracted by the LLM.
    """
    # Clean the text for SQL safety
    clean_text_lower = clean_text.lower().strip()
    
    query = """
        SELECT concept_id, concept_name
        FROM concept
        WHERE vocabulary_id = 'LOINC' 
          AND domain_id = 'Measurement'
          AND standard_concept = 'S'
          AND LOWER(concept_name) LIKE ?
        ORDER BY LENGTH(concept_name) ASC
        LIMIT 1
    """
    # Use wildcards to search for the substring
    search_pattern = f"%{clean_text_lower}%"
    result = con.execute(query, (search_pattern,)).fetchone()
    
    if result:
        return result[0], result[1]
    return None, None

# EXECUTION BLOCK
if __name__ == "__main__":
    print("🤖 STARTING REVISED AI SEMANTIC MAPPING (LOINC MEASUREMENTS)\n" + "-"*50)
    
    unmapped = get_unmapped_measurements()
    print(f"🔍 Found {len(unmapped)} unique unmapped laboratory tests.")
    
    if not unmapped:
        print("✅ No unmapped tests found. The database is fully standardized!")
    else:
        successful_mappings = []
        
        with duckdb.connect(DB_PATH) as con:
            for row in unmapped:
                raw_text = row[0]
                print(f"\n⚙️ Processing: '{raw_text}'")
                
                # 1. AI Normalization (Only text cleaning, no guessing codes)
                clean_text = normalize_lab_test(raw_text)
                if not clean_text:
                    continue
                print(f"   🧠 LLM Analyte Extracted: '{clean_text}'")
                
                # 2. Robust SQL Substring Matching
                concept_id, concept_name = find_loinc_match_by_substring(con, clean_text)
                
                if concept_id:
                    print(f"   ✅ LOINC Match Found: {concept_name} (ID: {concept_id})")
                    successful_mappings.append((concept_id, raw_text))
                else:
                    print(f"   ❌ No matching LOINC concept found for '{clean_text}'.")
            
            # 3. Write-back
            if successful_mappings:
                print(f"\n💾 Writing {len(successful_mappings)} mapped LOINC concepts back to the database...")
                con.executemany("""
                    UPDATE measurement
                    SET measurement_concept_id = ?
                    WHERE measurement_source_value = ? 
                      AND measurement_concept_id = 0
                """, successful_mappings)
                print("✅ Database successfully updated!")
            else:
                print("\n⚠️ No new mappings met the criteria to write back.")