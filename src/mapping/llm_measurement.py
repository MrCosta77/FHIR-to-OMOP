import os
import sys
import json
import re
import duckdb
import chromadb
import ollama
from pathlib import Path

# Setup paths
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
    """Fetches real laboratory examples already approved by a human in the Streamlit interface."""
    return get_few_shot_prompt(con, "measurement", "LOINC measurement", limit)

def setup_vector_store(con):
    return get_versioned_collection(con, CHROMA_PATH, "measurement")

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
    print("⚙️ STARTING AI-ASSISTED SEMANTIC MAPPING (MEASUREMENTS / RAG + FEW-SHOT)")
    print("-" * 50)
    
    with duckdb.connect(DB_PATH) as con:
        retired = reconcile_resolved_proposals(con, "measurement")
        if retired:
            print(f"♻️ Retired {retired} proposals resolved deterministically.")
        collection = setup_vector_store(con)
        if collection.count() == 0:
            print("❌ Vector store is empty. Skipping RAG mapping.")
            return

        unmapped = get_unmapped_measurements(con)
        if not unmapped:
            print("✅ No unmapped measurements found. Skipping AI mapping.")
            return
            
        print(f"⚠️ Found {len(unmapped)} UNIQUE legacy laboratory terms. Starting RAG pipeline...\n")
        
        # DYNAMIC FEW-SHOT
        few_shot_prompt = get_few_shot_examples(con, limit=3)
        if few_shot_prompt:
            print("🧠 Dynamic Few-Shot ACTIVE: Injecting previously approved examples into AI context...\n")
        
        proposed_count = 0
        below_threshold_count = 0
        
        for idx, raw_term in enumerate(unmapped, 1):
            try:
                search_results = collection.query(
                    query_texts=[raw_term],
                    n_results=5
                )
            except Exception as e:
                print(f"[{idx}/{len(unmapped)}] ❌ Vector Search Error on '{raw_term}': {e}")
                continue
            
            if not search_results['ids'] or not search_results['ids'][0]:
                print(f"[{idx}/{len(unmapped)}] ❌ No vector matches found for '{raw_term}'.")
                continue
                
            retrieved_loincs = []
            distances = search_results.get('distances', [[0]*5])[0]
            
            for i in range(len(search_results['ids'][0])):
                retrieved_loincs.append({
                    "concept_id": search_results['ids'][0][i],
                    "concept_name": search_results['documents'][0][i],
                    "distance": distances[i]
                })
            
            # Removemos a distância do prompt visual para não confundir o LLM, enviamos apenas ID e Nome
            prompt_candidates = [{"concept_id": c["concept_id"], "concept_name": c["concept_name"]} for c in retrieved_loincs]
            
            prompt = (
                f"You are an expert Clinical Data Manager mapping legacy lab tests to LOINC.\n"
                f"{few_shot_prompt}"
                f"Now, map the following Source Legacy Term: '{raw_term}'\n\n"
                f"Here are the top 5 closest standard LOINC candidates retrieved from our database:\n"
                f"{json.dumps(prompt_candidates, indent=2)}\n\n"
                f"Analyze the semantic meaning (e.g., Blood vs Urine, Mass vs Count). "
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
                        con, "measurement", raw_term, match
                    )
                    print(
                        f"[{idx}/{len(unmapped)}] Raw: '{raw_term}'\n"
                        f"   🎯 AI selected: '{concept_name}' (ID: {concept_id}) "
                        f"| score={score:.4f} | {status} | events={event_count}"
                    )
                    if status == "Pending_Human_Review":
                        proposed_count += 1
                    else:
                        below_threshold_count += 1
                else:
                    print(f"[{idx}/{len(unmapped)}] Raw: '{raw_term}'\n   ❌ AI rejected all candidates (Returned 0).")
                    
            except Exception as e:
                print(f"[{idx}/{len(unmapped)}] ❌ LLM Error on '{raw_term}': {e}")
                
        print(
            f"\n📊 SUMMARY: {proposed_count} proposals awaiting human review; "
            f"{below_threshold_count} candidates below the configured threshold; "
            f"{len(unmapped)} terms evaluated."
        )

if __name__ == "__main__":
    run_measurement_ai_mapping()
