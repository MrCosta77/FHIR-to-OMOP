import os
import sys
import duckdb
import ollama
import re
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, MODEL_NAME

def get_unmapped_drugs(con):
    """Fetch unique unmapped drug terms from DuckDB."""
    query = """
        SELECT DISTINCT drug_source_value
        FROM drug_exposure
        WHERE drug_concept_id = 0
        AND drug_source_concept_id = 0
        AND drug_source_value IS NOT NULL
    """
    return [row[0] for row in con.execute(query).fetchall()]

def clean_llm_output(raw_text):
    """Extracts only the clinical term from the LLM output."""
    clean = re.sub(r'```.*?```', '', raw_text, flags=re.DOTALL)
    return clean.replace('"', '').replace("'", '').strip()

def run_drug_ai_mapping():
    print("⚙️ STARTING AI-ASSISTED SEMANTIC MAPPING (DRUGS) ")
    print("-" * 50)
    
    with duckdb.connect(DB_PATH) as con:
        unmapped_drugs = get_unmapped_drugs(con)
        
        if not unmapped_drugs:
            print("✅ No unmapped drugs found. Skipping AI mapping.")
            return
            
        print(f"⚠️ Found {len(unmapped_drugs)} UNIQUE unmapped terms. Starting AI pipeline...\n")
        
        updates = []
        provenance = []
        
        for idx, raw_term in enumerate(unmapped_drugs, 1):
            prompt = (
                f"You are a clinical NLP entity extractor. "
                f"Extract the core active ingredient or medication name from this raw text: '{raw_term}'. "
                f"Return ONLY the standardized ingredient name. Do not include dosages or explanations."
            )
            
            try:
                response = ollama.chat(
                    model=MODEL_NAME,
                    messages=[{'role': 'user', 'content': prompt}],
                    options={'temperature': 0.0}
                )
                ai_term = clean_llm_output(response['message']['content'])
                
                # Jaro-Winkler match contra vocabulário RxNorm (Standard)
                # O revisor sugeriu usar tie-break (concept_id ASC) e limite 0.95
                match_query = """
                    SELECT concept_id, concept_name, jaro_winkler_similarity(LOWER(concept_name), LOWER(?)) AS score
                    FROM concept
                    WHERE vocabulary_id = 'RxNorm' 
                      AND standard_concept = 'S'
                      AND invalid_reason IS NULL
                    HAVING score >= 0.95
                    ORDER BY score DESC, concept_id ASC
                    LIMIT 1
                """
                match = con.execute(match_query, (ai_term,)).fetchone()
                
                if match:
                    concept_id, concept_name, score = match
                    print(f"[{idx}/{len(unmapped_drugs)}] Raw: '{raw_term}'\n   ✨ AI:  '{ai_term}'\n   🎯 DB:  '{concept_name}' (ID: {concept_id}) | Score: {score:.2f}")
                    updates.append((concept_id, raw_term))
                    provenance.append((
                        'drug_exposure', 0, raw_term, ai_term, concept_id,
                        'llm_jaro_winkler', score, MODEL_NAME, 'Athena_v5.4', 'Pending_Human_Review'
                    ))
                else:
                    print(f"[{idx}/{len(unmapped_drugs)}] Raw: '{raw_term}'\n   ✨ AI:  '{ai_term}'\n   ❌ DB:  No match found above 95.0% confidence.")
                    
            except Exception as e:
                print(f"[{idx}/{len(unmapped_drugs)}] ❌ LLM Error on '{raw_term}': {e}")
                
        if updates:
            print(f"\n💾 Writing {len(updates)} standardized concepts back to the database...")
            
            # Atualizar Tabela Clínica
            con.executemany("""
                UPDATE drug_exposure 
                SET drug_concept_id = ? 
                WHERE drug_source_value = ? 
                  AND drug_concept_id = 0
            """, updates)
            
            # Precisamos do ID original para a proveniência
            for p in provenance:
                target_table, _, src_val, norm_val, concept_id, method, score, model, vocab, review = p
                
                # Inserir um registo de auditoria por cada ocorrência desta droga
                con.execute("""
                    INSERT INTO mapping_provenance (
                        target_table, target_id, source_value, normalized_value,
                        assigned_concept_id, mapping_method, score, model_name,
                        vocabulary_version, reviewed_by
                    )
                    SELECT ?, drug_exposure_id, ?, ?, ?, ?, ?, ?, ?, ?
                    FROM drug_exposure
                    WHERE drug_source_value = ? 
                      AND drug_concept_id = ?
                """, (target_table, src_val, norm_val, concept_id, method, score, model, vocab, review, src_val, concept_id))
            
            print("✅ Database and Provenance Audit successfully updated!")
        
        print(f"\n📊 SUMMARY: Successfully mapped {len(updates)} out of {len(unmapped_drugs)} unique terms.")

if __name__ == "__main__":
    run_drug_ai_mapping()