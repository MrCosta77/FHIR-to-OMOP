import os
import sys
import json
import duckdb
import chromadb
import ollama
from pathlib import Path

# Setup paths dynamically
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, MODEL_NAME

CHROMA_PATH = os.path.join(PROJECT_ROOT, "data", "chroma_db")

def get_few_shot_examples(con, limit=3):
    """Busca exemplos de medicamentos já aprovados pelo humano na interface Streamlit."""
    query = f"""
        SELECT source_value, assigned_concept_id, normalized_value
        FROM mapping_provenance
        WHERE reviewed_by = 'Approved_by_Human'
          AND target_table = 'drug_exposure'
        ORDER BY RANDOM()
        LIMIT {limit}
    """
    examples = con.execute(query).fetchall()
    
    if not examples:
        return ""
        
    fs_text = "Here are examples of correct RxNorm mappings previously approved by a human expert:\n"
    for raw, concept_id, norm in examples:
        fs_text += f" - Raw Term: '{raw}' -> Concept ID: {concept_id} (Reasoning: Matches '{norm}')\n"
    return fs_text + "\n"

def setup_vector_store(con):
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(name="rxnorm_drugs")
    
    if collection.count() > 0:
        return collection
        
    print("⏳ Building RxNorm Vector Store for RAG (this will take a minute or two)...")
    
    drugs = con.execute("""
        SELECT concept_id, concept_name 
        FROM concept 
        WHERE vocabulary_id = 'RxNorm' 
          AND standard_concept = 'S' 
          AND invalid_reason IS NULL
    """).fetchall()
    
    if not drugs:
        print("⚠️ No RxNorm concepts found in database. Vector store will be empty.")
        return collection

    ids = [str(row[0]) for row in drugs]
    documents = [row[1] for row in drugs]
    
    batch_size = 5000
    for i in range(0, len(ids), batch_size):
        collection.add(
            ids=ids[i:i+batch_size],
            documents=documents[i:i+batch_size]
        )
        print(f"   Indexed {min(i+batch_size, len(ids))} / {len(ids)} medications...")
        
    print(f"✅ Indexed {len(ids)} RxNorm concepts into Vector Store.")
    return collection

def get_unmapped_drugs(con):
    query = """
        SELECT DISTINCT drug_source_value
        FROM drug_exposure
        WHERE drug_concept_id = 0
        AND drug_source_concept_id = 0
        AND drug_source_value IS NOT NULL
    """
    return [row[0] for row in con.execute(query).fetchall()]

def run_drug_ai_mapping():
    print("⚙️ STARTING AI-ASSISTED SEMANTIC MAPPING (DRUGS / RAG + FEW-SHOT) \n" + "-"*50)

    with duckdb.connect(DB_PATH) as con:
        print("🔌 Connecting to DuckDB...")
        
        collection = setup_vector_store(con)
        if collection.count() == 0:
            print("❌ Vector store is empty. Skipping RAG mapping.")
            return

        unique_terms = get_unmapped_drugs(con)
        if not unique_terms:
            print("✅ No unmapped drugs found! Database is fully normalized.")
            return
            
        total_terms = len(unique_terms)
        print(f"⚠️ Found {total_terms} UNIQUE unmapped terms. Starting RAG pipeline...\n")
        
        # FEW-SHOT DINÂMICO
        few_shot_prompt = get_few_shot_examples(con, limit=3)
        if few_shot_prompt:
            print("🧠 Dynamic Few-Shot ATIVO: A injetar exemplos previamente aprovados no cérebro da IA...\n")
        
        updates = []
        
        for idx, raw_term in enumerate(unique_terms, 1):
            try:
                search_results = collection.query(
                    query_texts=[raw_term],
                    n_results=5
                )
            except Exception as e:
                print(f"[{idx}/{total_terms}] ❌ Vector Search Error on '{raw_term}': {e}")
                continue
            
            if not search_results['ids'] or not search_results['ids'][0]:
                print(f"[{idx}/{total_terms}] ❌ No vector matches found for '{raw_term}'.")
                continue
                
            retrieved_rxnorm = []
            for i in range(len(search_results['ids'][0])):
                retrieved_rxnorm.append({
                    "concept_id": search_results['ids'][0][i],
                    "concept_name": search_results['documents'][0][i]
                })
            
            prompt = (
                f"You are an expert Clinical Pharmacist mapping raw EHR medication texts to RxNorm.\n"
                f"{few_shot_prompt}"
                f"Now, map the following Source Raw Term: '{raw_term}'\n\n"
                f"Here are the top 5 closest standard RxNorm candidates retrieved from our database:\n"
                f"{json.dumps(retrieved_rxnorm, indent=2)}\n\n"
                f"Analyze the active ingredient, dosage, and form carefully. "
                f"Reply ONLY with the exact numeric 'concept_id' of the best match. "
                f"If none of the candidates are a clinically safe match, reply with '0'."
            )
            
            try:
                response = ollama.chat(
                    model=MODEL_NAME,
                    messages=[{'role': 'user', 'content': prompt}],
                    options={'temperature': 0.0} 
                )
                ai_answer = response['message']['content'].strip()
                
                match = next((c for c in retrieved_rxnorm if str(c['concept_id']) in ai_answer), None)
                
                if match and ai_answer != '0':
                    concept_id = int(match['concept_id'])
                    concept_name = match['concept_name']
                    print(f"[{idx}/{total_terms}] Raw: '{raw_term}'\n   🎯 AI selected RxNorm: '{concept_name}' (ID: {concept_id})")
                    updates.append((concept_id, concept_name, 1.0, raw_term))
                else:
                    print(f"[{idx}/{total_terms}] Raw: '{raw_term}'\n   ❌ AI rejected all candidates (Returned 0).")
                    
            except Exception as e:
                print(f"[{idx}/{total_terms}] ❌ LLM Error on '{raw_term}': {e}")
                
        if updates:
            print(f"\n💾 Writing {len(updates)} standardized concepts to the STCM Dictionary...")
            
            for concept_id, llm_term, score, raw_term in updates:
                con.execute("DELETE FROM source_to_concept_map WHERE source_code = ?", (raw_term,))
                
                con.execute("""
                    INSERT INTO source_to_concept_map (
                        source_code, source_concept_id, source_vocabulary_id, source_code_description,
                        target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason
                    ) VALUES (
                        ?, 0, 'CMF_SYNTHEA', ?,
                        ?, 'RxNorm', CURRENT_DATE, '2099-12-31', NULL
                    )
                """, (raw_term, raw_term, concept_id))
                
                con.execute("""
                    INSERT INTO mapping_provenance (
                        target_table, target_id, source_value, normalized_value,
                        assigned_concept_id, mapping_method, score, model_name,
                        vocabulary_version, reviewed_by
                    ) VALUES (
                        'drug_exposure', 0, ?, ?,
                        ?, 'llm_rag_few_shot', ?, ?, 
                        'Athena_v5.4', 'Pending_Human_Review'
                    )
                """, (raw_term, llm_term, concept_id, score, MODEL_NAME))
                
            print("✅ STCM Dictionary and Provenance Audit successfully updated!")
            
        else:
            print("\n⚠️ No matches met the confidence threshold. Database was not updated.")
            
        print(f"\n📊 SUMMARY: Successfully mapped {len(updates)} out of {total_terms} unique terms.")

if __name__ == "__main__":
    run_drug_ai_mapping()