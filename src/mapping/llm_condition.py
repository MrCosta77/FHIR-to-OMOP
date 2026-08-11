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

# Configuration
from src.utils.config import DB_PATH, MODEL_NAME

# Usar a mesma diretoria ChromaDB que criámos para os laboratórios
CHROMA_PATH = os.path.join(PROJECT_ROOT, "data", "chroma_db")

def setup_vector_store(con):
    """Initializes ChromaDB and populates it with valid SNOMED Condition concepts."""
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(name="snomed_conditions")
    
    # Se já tiver dados, salta a reconstrução
    if collection.count() > 0:
        return collection
        
    print("⏳ Building SNOMED Vector Store for RAG (this will take a few minutes)...")
    
    # Extrair os conceitos válidos da SNOMED (apenas doenças)
    conditions = con.execute("""
        SELECT concept_id, concept_name 
        FROM concept 
        WHERE vocabulary_id = 'SNOMED' 
          AND domain_id = 'Condition' 
          AND standard_concept = 'S' 
          AND invalid_reason IS NULL
    """).fetchall()
    
    if not conditions:
        print("⚠️ No SNOMED concepts found in database. Vector store will be empty.")
        return collection

    ids = [str(row[0]) for row in conditions]
    documents = [row[1] for row in conditions]
    
    # Inserção em lotes (são milhares de doenças, vamos imprimir o progresso)
    batch_size = 5000
    for i in range(0, len(ids), batch_size):
        collection.add(
            ids=ids[i:i+batch_size],
            documents=documents[i:i+batch_size]
        )
        print(f"   Indexed {min(i+batch_size, len(ids))} / {len(ids)} conditions...")
        
    print(f"✅ Indexed {len(ids)} SNOMED concepts into Vector Store.")
    return collection

def get_unique_unmapped_conditions(con):
    """Fetch ALL unique unmapped clinical conditions from DuckDB."""
    query = """
        SELECT DISTINCT condition_source_value
        FROM condition_occurrence
        WHERE condition_concept_id = 0
        AND condition_source_concept_id = 0
        AND condition_source_value IS NOT NULL
    """
    return [row[0] for row in con.execute(query).fetchall()]

def run_semantic_mapping():
    print("⚙️ STARTING AI-ASSISTED SEMANTIC MAPPING (CONDITIONS / RAG) \n" + "-"*50)

    with duckdb.connect(DB_PATH) as con:
        print("🔌 Connecting to DuckDB...")
        
        # 1. Setup Vector Store
        collection = setup_vector_store(con)
        if collection.count() == 0:
            print("❌ Vector store is empty. Skipping RAG mapping.")
            return

        # 2. Obter termos órfãos
        unique_terms = get_unique_unmapped_conditions(con)
        if not unique_terms:
            print("✅ No unmapped conditions found! Database is fully normalized.")
            return
            
        total_terms = len(unique_terms)
        print(f"⚠️ Found {total_terms} UNIQUE unmapped terms. Starting RAG pipeline...\n")
        
        updates = []
        
        for idx, raw_term in enumerate(unique_terms, 1):
            try:
                # 3. RAG Retrieval: Procurar as 5 doenças mais semelhantes vetorialmente
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
                
            retrieved_snomed = []
            for i in range(len(search_results['ids'][0])):
                retrieved_snomed.append({
                    "concept_id": search_results['ids'][0][i],
                    "concept_name": search_results['documents'][0][i]
                })
            
            # 4. Prompt Híbrido RAG
            prompt = (
                f"You are an expert Clinical Data Manager mapping raw EHR diagnosis texts to SNOMED CT.\n"
                f"Source Raw Term: '{raw_term}'\n\n"
                f"Here are the top 5 closest standard SNOMED candidates retrieved from our database:\n"
                f"{json.dumps(retrieved_snomed, indent=2)}\n\n"
                f"Analyze the clinical meaning carefully. 'Disorder' and 'Finding' are common suffixes. "
                f"Reply ONLY with the exact 'concept_id' of the best match. "
                f"If none of the candidates are a clinically safe match, reply with '0'."
            )
            
            try:
                response = ollama.chat(
                    model=MODEL_NAME,
                    messages=[{'role': 'user', 'content': prompt}],
                    options={'temperature': 0.0} 
                )
                ai_answer = response['message']['content'].strip()
                
                # Validação estrita
                match = next((c for c in retrieved_snomed if str(c['concept_id']) in ai_answer), None)
                
                if match and ai_answer != '0':
                    concept_id = int(match['concept_id'])
                    concept_name = match['concept_name']
                    print(f"[{idx}/{total_terms}] Raw: '{raw_term}'\n   🎯 AI selected SNOMED: '{concept_name}' (ID: {concept_id})")
                    
                    updates.append((concept_id, concept_name, 1.0, raw_term))
                else:
                    print(f"[{idx}/{total_terms}] Raw: '{raw_term}'\n   ❌ AI rejected all candidates (Returned 0).")
                    
            except Exception as e:
                print(f"[{idx}/{total_terms}] ❌ LLM Error on '{raw_term}': {e}")
                
        # 5. Escrita no Dicionário (STCM)
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
                        ?, 'SNOMED', CURRENT_DATE, '2099-12-31', NULL
                    )
                """, (raw_term, raw_term, concept_id))
                
                con.execute("""
                    INSERT INTO mapping_provenance (
                        target_table, target_id, source_value, normalized_value,
                        assigned_concept_id, mapping_method, score, model_name,
                        vocabulary_version, reviewed_by
                    ) VALUES (
                        'source_to_concept_map', 0, ?, ?,
                        ?, 'llm_rag', ?, ?, 
                        'Athena_v5.4', 'Pending_Human_Review'
                    )
                """, (raw_term, llm_term, concept_id, score, MODEL_NAME))
                
            print("✅ STCM Dictionary and Provenance Audit successfully updated!")
            
        else:
            print("\n⚠️ No matches met the confidence threshold. Database was not updated.")
            
        print(f"\n📊 SUMMARY: Successfully mapped {len(updates)} out of {total_terms} unique terms.")

if __name__ == "__main__":
    run_semantic_mapping()