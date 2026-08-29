import os
import sys
import re
import duckdb
import ollama
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, MODEL_NAME

# 1. Define the Agent's "Brain": Database Context
SCHEMA_CONTEXT = """
You are an expert Health Data Scientist and SQL developer working with an OMOP CDM v5.4 database in DuckDB.
Your task is to translate natural language clinical questions into exact, executable DuckDB SQL queries.

Here are the main tables and their relevant columns in our database:
- condition_occurrence: condition_occurrence_id, person_id, condition_concept_id, condition_start_date
- drug_exposure: drug_exposure_id, person_id, drug_concept_id, drug_exposure_start_date
- measurement: measurement_id, person_id, measurement_concept_id, measurement_date, value_as_number
- observation: observation_id, person_id, observation_concept_id, observation_date
- procedure_occurrence: procedure_occurrence_id, person_id, procedure_concept_id, procedure_date
- concept: concept_id, concept_name, domain_id, vocabulary_id
- observation_period: observation_period_id, person_id, observation_period_start_date, observation_period_end_date

CRITICAL RULES:
1. Always JOIN clinical tables with the 'concept' table to filter by disease/drug names.
   Example: JOIN concept c ON condition_occurrence.condition_concept_id = c.concept_id
2. ALWAYS exclude unmapped concepts by ensuring the concept_id is not 0 (e.g., WHERE condition_concept_id != 0).
3. Use ILIKE for case-insensitive text matching on c.concept_name (e.g., c.concept_name ILIKE '%covid%').
4. To count unique patients, use COUNT(DISTINCT person_id).
5. Output ONLY valid DuckDB SQL code. Do not include markdown formatting, explanations, or any other text.
"""

def generate_sql_query(question):
    """Requests Ollama to translate English text into SQL using the provided schema context."""
    try:
        response = ollama.chat(
            model=MODEL_NAME,
            messages=[
                {'role': 'system', 'content': SCHEMA_CONTEXT},
                {'role': 'user', 'content': f"Write a SQL query to answer this clinical question: {question}"}
            ],
            options={'temperature': 0.0} # We want exact and deterministic responses
        )
        
        raw_output = response['message']['content'].strip()
        
        # Regex to strip markdown formatting if the LLM includes it (e.g., ```sql ... ```)
        sql_match = re.search(r'```(?:sql)?\n(.*?)\n```', raw_output, re.DOTALL | re.IGNORECASE)
        if sql_match:
            return sql_match.group(1).strip()
            
        return raw_output.replace('```', '').strip()
        
    except Exception as e:
        print(f"❌ LLM Error: {e}")
        return None

def run_agent():
    print("\n" + "🤖"*25)
    print("      CLINICAL AI AGENT (TEXT-TO-SQL)")
    print("🤖"*25 + "\n")
    print("Welcome to your autonomous Real-World Evidence assistant.")
    print("Type your clinical question in English (or 'exit' to quit).")
    
    # Open the database in read_only mode for safety (prevents the LLM from accidentally deleting data)
    with duckdb.connect(DB_PATH, read_only=True) as con:
        while True:
            print("-" * 50)
            question = input("🩺 Ask a question: ")
            
            if question.lower() in ['exit', 'quit', 'sair', 'q']:
                print("\nShutting down AI agent. Goodbye! 👋")
                break
                
            if not question.strip():
                continue
                
            print("\n🧠 Thinking (translating natural language to SQL)...")
            sql_query = generate_sql_query(question)
            
            if not sql_query:
                continue
                
            print(f"📝 Generated SQL:\n\033[94m{sql_query}\033[0m\n") # \033[94m adds blue color to the terminal
            
            try:
                print("⚙️ Executing query in DuckDB...")
                result = con.execute(sql_query).fetchall()
                columns = [desc[0] for desc in con.description]
                
                print("\n📊 RESULT:")
                if not result:
                    print("No data found.")
                else:
                    # Simple table formatting in the terminal
                    print(" | ".join(columns))
                    print("-" * (len(" | ".join(columns))))
                    for row in result:
                        print(" | ".join(str(val) for val in row))
                print("\n")
                
            except duckdb.Error as e:
                print(f"❌ SQL Execution Error: The generated query had a syntax issue.\nDetails: {e}\n")

if __name__ == "__main__":
    run_agent()