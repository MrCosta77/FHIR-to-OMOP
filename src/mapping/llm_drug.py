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
from src.mapping.mapping_service import (
    get_few_shot_prompt,
    get_versioned_collection,
    reconcile_resolved_proposals,
    record_mapping_proposal,
    selected_candidate,
)

CHROMA_PATH = os.path.join(PROJECT_ROOT, "data", "chroma_db")

def get_few_shot_examples(con, limit=3):
    """Fetches real medication examples already approved by a human in the Streamlit interface."""
    return get_few_shot_prompt(con, "drug_exposure", "RxNorm drug", limit)

def setup_vector_store(con):
    return get_versioned_collection(con, CHROMA_PATH, "drug_exposure")

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
        retired = reconcile_resolved_proposals(con, "drug_exposure")
        if retired:
            print(f"♻️ Retired {retired} proposals resolved deterministically.")
        
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
        
        # DYNAMIC FEW-SHOT
        few_shot_prompt = get_few_shot_examples(con, limit=3)
        if few_shot_prompt:
            print("🧠 Dynamic Few-Shot ACTIVE: Injecting previously approved examples into AI context...\n")
        
        proposed_count = 0
        below_threshold_count = 0
        
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
                
                match = selected_candidate(
                    search_results, ai_answer,
                    (collection.metadata or {}).get("distance_metric", "cosine"),
                )
                if match:
                    concept_id, concept_name, _distance, score = match
                    status, event_count = record_mapping_proposal(
                        con, "drug_exposure", raw_term, match
                    )
                    print(
                        f"[{idx}/{total_terms}] Raw: '{raw_term}'\n"
                        f"   🎯 AI selected RxNorm: '{concept_name}' "
                        f"(ID: {concept_id}) | score={score:.4f} | {status} | "
                        f"events={event_count}"
                    )
                    if status == "Pending_Human_Review":
                        proposed_count += 1
                    else:
                        below_threshold_count += 1
                else:
                    print(f"[{idx}/{total_terms}] Raw: '{raw_term}'\n   ❌ AI rejected all candidates (Returned 0).")
                    
            except Exception as e:
                print(f"[{idx}/{total_terms}] ❌ LLM Error on '{raw_term}': {e}")
                
        print(
            f"\n📊 SUMMARY: {proposed_count} proposals awaiting human review; "
            f"{below_threshold_count} candidates below the configured threshold; "
            f"{total_terms} terms evaluated."
        )

if __name__ == "__main__":
    run_drug_ai_mapping()
