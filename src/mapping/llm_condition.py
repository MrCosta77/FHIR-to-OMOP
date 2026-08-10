import os
import sys
import re
import time
import json
import duckdb
import ollama
from pathlib import Path

# Setup paths dynamically
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

# Configuration
from src.utils.config import DB_PATH, MODEL_NAME

# Stricter threshold to avoid false positives in short clinical strings
SIMILARITY_THRESHOLD = 0.95 

def get_unique_unmapped_conditions(con):
    """Fetch ALL unique unmapped clinical conditions from DuckDB."""
    query = """
        SELECT DISTINCT condition_source_value
        FROM condition_occurrence
        WHERE condition_concept_id = 0
        AND condition_source_value IS NOT NULL
    """
    return [row[0] for row in con.execute(query).fetchall()]

def ai_semantic_normalization(raw_term):
    """Uses local LLM to normalize a messy clinical term into a core standard name."""
    system_prompt = """
    You are an expert Clinical Data Informatician.
    Normalize raw, messy clinical text into a clean, core medical term.
    RULES:
    1. Respond ONLY with the core medical term.
    2. Do NOT include any numeric IDs.
    3. Do NOT include any tags in parentheses like '(disorder)', '(finding)', or '(person)'.
    4. Keep it as short and precise as possible.
    """
    try:
        # Added temperature=0.0 for strict determinism
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': f"Raw clinical text: '{raw_term}'"}
            ],
            options={'temperature': 0.0} 
        )
        clean_text = response['message']['content'].strip().strip("'").strip('"')
        
        # Hardcode fallback to ensure tags are removed even if AI forgets
        clean_text = re.sub(r'\([^)]*\)', '', clean_text).strip()
        return clean_text
    except Exception as e:
        print(f"LLM Error: {e}")
        return None

def find_best_match(con, normalized_term):
    """Finds the best OMOP concept using Jaro-Winkler similarity."""
    search_query = """
        SELECT concept_id, concept_name, domain_id, 
               jaro_winkler_similarity(LOWER(concept_name), LOWER(?)) AS score
        FROM concept 
        WHERE vocabulary_id = 'SNOMED'
        AND domain_id = 'Condition'
        AND standard_concept = 'S'
        AND invalid_reason IS NULL
        ORDER BY score DESC, concept_id ASC -- Deterministic tie-breaking
        LIMIT 1
    """
    match = con.execute(search_query, [normalized_term]).fetchone()
    
    if match and match[3] >= SIMILARITY_THRESHOLD:
        return match
    return None

def run_semantic_mapping():
    """Main execution block for AI-Assisted Mapping with Audit Trail."""
    print("⚙️ STARTING AI-ASSISTED SEMANTIC MAPPING (CONDITIONS) \n" + "-"*50)

    with duckdb.connect(DB_PATH) as con:
        print("🔌 Connecting to DuckDB to fetch unmapped conditions...")
        unique_terms = get_unique_unmapped_conditions(con)

        if not unique_terms:
            print("✅ No unmapped conditions found! Database is fully normalized.")
            return
            
        total_terms = len(unique_terms)
        print(f"⚠️ Found {total_terms} UNIQUE unmapped terms. Starting AI pipeline...\n")
        
        updates = []
        
        for i, term in enumerate(unique_terms, 1):
            print(f"[{i}/{total_terms}] Raw: '{term}'")
            
            # 1. AI Normalization
            ai_term = ai_semantic_normalization(term)
            if not ai_term:
                print("   ❌ AI Failed to respond.")
                print("-" * 40)
                continue
            
            print(f"   ✨ AI:  '{ai_term}'")
            
            # 2. Database Fuzzy Match
            start_time = time.time()
            match = find_best_match(con, ai_term)
            db_time = time.time() - start_time
            
            # 3. Queue for Validation
            if match:
                concept_id, concept_name, domain, score = match
                print(f"   🎯 DB:  '{concept_name}' (ID: {concept_id}) | Score: {score:.2f} | Time: {db_time:.1f}s")
                updates.append((concept_id, ai_term, score, term))
            else:
                print(f"   ❌ DB:  No match found above {SIMILARITY_THRESHOLD*100}% confidence.")
            print("-" * 40)
            
        # 4. The Write-Back & Provenance Registration
        if updates:
            print(f"\n💾 Writing {len(updates)} standardized concepts back to the database...")
            
            for concept_id, llm_term, score, raw_term in updates:
                # 4a. Update the main clinical table
                con.execute("""
                    UPDATE condition_occurrence 
                    SET condition_concept_id = ? 
                    WHERE condition_source_value = ? 
                      AND condition_concept_id = 0
                """, (concept_id, raw_term))
                
                # 4b. Insert the Audit Trail for the newly mapped rows
                con.execute("""
                    INSERT INTO mapping_provenance (
                        target_table, target_id, source_value, normalized_value,
                        assigned_concept_id, mapping_method, score, model_name,
                        vocabulary_version, reviewed_by
                    )
                    SELECT 
                        'condition_occurrence',
                        condition_occurrence_id,
                        condition_source_value,
                        ?, 
                        ?, 
                        'llm_jaro_winkler',
                        ?,
                        ?, 
                        'Athena_v5.4',
                        'Pending_Human_Review'
                    FROM condition_occurrence
                    WHERE condition_source_value = ? AND condition_concept_id = ?
                    AND condition_occurrence_id NOT IN (
                        SELECT target_id FROM mapping_provenance WHERE target_table = 'condition_occurrence'
                    )
                """, (llm_term, concept_id, score, MODEL_NAME, raw_term, concept_id))
                
            print("✅ Database and Provenance Audit successfully updated!")
            
        else:
            print("\n⚠️ No matches met the confidence threshold. Database was not updated.")
            
        print(f"\n📊 SUMMARY: Successfully mapped {len(updates)} out of {total_terms} unique terms.")

if __name__ == "__main__":
    run_semantic_mapping()