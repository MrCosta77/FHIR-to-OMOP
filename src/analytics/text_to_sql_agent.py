import re
import sys
from pathlib import Path

import duckdb
import ollama

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH, MODEL_NAME

MAX_RESULT_ROWS = 1000
_FORBIDDEN_SQL = re.compile(
    r"\b(?:ALTER|ATTACH|CALL|COPY|CREATE|DELETE|DETACH|DROP|EXPORT|IMPORT|"
    r"INSERT|INSTALL|LOAD|PRAGMA|RESET|SET|TRUNCATE|UPDATE|VACUUM)\b|"
    r"\b(?:GLOB|PARQUET_SCAN|POSTGRES_SCAN|SQLITE_SCAN)\s*\(|"
    r"\bREAD_(?:CSV|JSON|PARQUET)(?:_AUTO)?\s*\(",
    re.IGNORECASE,
)

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

        return extract_sql_query(response['message']['content'])

    except Exception as e:
        print(f"❌ LLM Error: {e}")
        return None


def extract_sql_query(raw_output: str) -> str:
    """Extract one SQL payload from an optional Markdown code fence."""
    output = str(raw_output or "").strip()
    fenced = re.search(
        r"```(?:sql)?[ \t]*(?:\r?\n)?(.*?)(?:\r?\n)?```",
        output,
        re.DOTALL | re.IGNORECASE,
    )
    return fenced.group(1).strip() if fenced else output


def _sql_control_text(sql_query: str) -> str:
    """Mask literals/comments so control-token checks do not inspect their contents."""
    text = re.sub(r"/\*.*?\*/", " ", sql_query, flags=re.DOTALL)
    text = re.sub(r"--[^\r\n]*", " ", text)
    text = re.sub(r"'(?:''|[^'])*'", "''", text)
    text = re.sub(r'"(?:""|[^"])*"', '""', text)
    return text.strip()


def validate_read_only_sql(sql_query: str) -> str:
    """Accept one SELECT/CTE statement without DuckDB external-access functions."""
    query = str(sql_query or "").strip()
    if not query:
        raise ValueError("The generated SQL query is empty.")

    control = _sql_control_text(query)
    without_trailing_semicolon = control[:-1].rstrip() if control.endswith(";") else control
    if ";" in without_trailing_semicolon:
        raise ValueError("Only one SQL statement is allowed.")
    if not re.match(r"^(?:SELECT|WITH)\b", without_trailing_semicolon, re.IGNORECASE):
        raise ValueError("Only SELECT queries and read-only CTEs are allowed.")
    if _FORBIDDEN_SQL.search(without_trailing_semicolon):
        raise ValueError("The query contains a forbidden SQL operation or external data access.")
    return query

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
                sql_query = validate_read_only_sql(sql_query)
                result = con.execute(sql_query).fetchmany(MAX_RESULT_ROWS)
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

            except (duckdb.Error, ValueError) as e:
                print(f"❌ SQL Execution Error: The generated query had a syntax issue.\nDetails: {e}\n")

if __name__ == "__main__":
    run_agent()
