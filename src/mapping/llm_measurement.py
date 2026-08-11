import os
import sys
import json
import duckdb
import chromadb
import ollama
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, MODEL_NAME

# Corrigido: Usar caminho absoluto em vez de relativo para o ChromaDB (recomendação da revisão)
CHROMA_PATH = os.path.join(PROJECT_ROOT, "data", "chroma_db")

def setup_vector_store(con):
    """Initializes ChromaDB and populates it with valid LOINC concepts if it's empty."""
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    collection = client.get_or_create_collection(name="loinc_measurements")
    
    # Se já tiver dados, não precisamos de o reconstruir
    if collection.count() > 0:
        return collection
        
    print("⏳ Building LOINC Vector Store for RAG (this might take a minute)...")
    
    # Extrair os conceitos válidos da tabela OMOP
    loincs = con.execute("""
        SELECT concept_id, concept_name 
        FROM concept 
        WHERE vocabulary_id = 'LOINC' 
          AND domain_id = 'Measurement' 
          AND standard_concept = 'S' 
          AND invalid_reason IS NULL
    """).fetchall()
    
    if not loincs:
        print("⚠️ No LOINC concepts found in database. Vector store will be empty.")
        return collection

    ids = [str(row[0]) for row in loincs]
    documents = [row[1] for row in loincs]
    
    # Inserção em lotes para não sobrecarregar a memória do ChromaDB
    batch_size = 5000
    for i in range(0, len(ids), batch_size):
        collection.add(
            ids=ids[i:i+batch_size],
            documents=documents[i:i+batch_size]
        )
        
    print(f"✅ Indexed {len(ids)} LOINC concepts into Vector Store.")
    return collection

def get_unmapped_measurements(con):
    query = """
        SELECT DISTINCT measurement_source_value
        FROM measurement
        WHERE measurement_concept_id = 0
        AND measurement_source_concept_id = 0
        AND measurement_source_value IS NOT NULL
    """
    return [row[0] for row in con.execute(query).fetchall()]

def run_measurement_ai_mapping():
    print("⚙️ STARTING AI-ASSISTED SEMANTIC MAPPING (MEASUREMENTS / RAG)")
    print("-" * 50)
    
    with duckdb.connect(DB_PATH) as con:
        # 1. Preparar o cérebro vetorial (ChromaDB)
        collection = setup_vector_store(con)
        if collection.count() == 0:
            print("❌ Vector store is empty. Skipping RAG mapping.")
            return

        # 2. Procurar lixo laboratorial injetado pelo nosso simulador
        unmapped = get_unmapped_measurements(con)
        if not unmapped:
            print("✅ No unmapped measurements found. Skipping AI mapping.")
            return
            
        print(f"⚠️ Found {len(unmapped)} UNIQUE legacy laboratory terms. Starting RAG pipeline...\n")
        
        updates = []
        provenance = []
        
        for idx, raw_term in enumerate(unmapped, 1):
            try:
                # 3. RAG Retrieval: Procurar os 5 testes LOINC mais semelhantes semanticamente
                search_results = collection.query(
                    query_texts=[raw_term],
                    n_results=5
                )
            except Exception as e:
                print(f"[{idx}/{len(unmapped)}] ❌ Vector Search Error on '{raw_term}': {e}")
                continue
            
            # Corrigido: Proteção contra pesquisas vazias (recomendação P0 da revisão)
            if not search_results['ids'] or not search_results['ids'][0]:
                print(f"[{idx}/{len(unmapped)}] ❌ No vector matches found for '{raw_term}'.")
                continue
                
            retrieved_loincs = []
            for i in range(len(search_results['ids'][0])):
                retrieved_loincs.append({
                    "concept_id": search_results['ids'][0][i],
                    "concept_name": search_results['documents'][0][i]
                })
            
            # 4. Prompt para o LLM tomar a decisão final
            prompt = (
                f"You are an expert Clinical Data Manager mapping legacy lab tests to LOINC.\n"
                f"Source Legacy Term: '{raw_term}'\n\n"
                f"Here are the top 5 closest standard LOINC candidates retrieved from our database:\n"
                f"{json.dumps(retrieved_loincs, indent=2)}\n\n"
                f"Analyze the semantic meaning (e.g., Blood vs Urine, Mass vs Count). "
                f"Reply ONLY with the exact 'concept_id' of the best match. "
                f"If none of the candidates are a clinically safe match, reply with '0'."
            )
            
            try:
                # Temperatura a 0.0 para garantir que a IA não inventa respostas
                response = ollama.chat(
                    model=MODEL_NAME,
                    messages=[{'role': 'user', 'content': prompt}],
                    options={'temperature': 0.0} 
                )
                ai_answer = response['message']['content'].strip()
                
                # Encontrar a correspondência exata para garantir que a IA escolheu um ID válido
                match = next((c for c in retrieved_loincs if str(c['concept_id']) in ai_answer), None)
                
                if match and ai_answer != '0':
                    concept_id = int(match['concept_id'])
                    concept_name = match['concept_name']
                    print(f"[{idx}/{len(unmapped)}] Raw: '{raw_term}'\n   🎯 AI selected LOINC: '{concept_name}' (ID: {concept_id})")
                    
                    updates.append((concept_id, raw_term))
                    provenance.append((
                        'measurement', 0, raw_term, concept_name, concept_id,
                        'llm_rag', 1.0, MODEL_NAME, 'Athena_v5.4', 'Pending_Human_Review'
                    ))
                else:
                    print(f"[{idx}/{len(unmapped)}] Raw: '{raw_term}'\n   ❌ AI rejected all candidates (Returned 0).")
                    
            except Exception as e:
                print(f"[{idx}/{len(unmapped)}] ❌ LLM Error on '{raw_term}': {e}")
                
        # 5. Escrita no Dicionário (STCM)
        if updates:
            print(f"\n💾 Writing {len(updates)} RAG-mapped concepts to the STCM Dictionary...")
            
            for p in provenance:
                target_table, _, src_val, norm_val, concept_id, method, score, model, vocab, review = p
                
                # Remover duplicados antigos
                con.execute("DELETE FROM source_to_concept_map WHERE source_code = ?", (src_val,))
                
                # Inserir no dicionário oficial
                con.execute("""
                    INSERT INTO source_to_concept_map (
                        source_code, source_concept_id, source_vocabulary_id, source_code_description,
                        target_concept_id, target_vocabulary_id, valid_start_date, valid_end_date, invalid_reason
                    ) VALUES (
                        ?, 0, 'CMF_SYNTHEA', ?,
                        ?, 'LOINC', CURRENT_DATE, '2099-12-31', NULL
                    )
                """, (src_val, src_val, concept_id))
                
                # Auditoria
                con.execute("""
                    INSERT INTO mapping_provenance (
                        target_table, target_id, source_value, normalized_value,
                        assigned_concept_id, mapping_method, score, model_name,
                        vocabulary_version, reviewed_by
                    ) VALUES (
                        'source_to_concept_map', 0, ?, ?,
                        ?, ?, ?, ?, ?, ?
                    )
                """, (src_val, norm_val, concept_id, method, score, model, vocab, review))
            
            print("✅ STCM Dictionary and Provenance Audit successfully updated!")
        
        print(f"\n📊 SUMMARY: Successfully mapped {len(updates)} out of {len(unmapped)} legacy lab terms.")

if __name__ == "__main__":
    run_measurement_ai_mapping()