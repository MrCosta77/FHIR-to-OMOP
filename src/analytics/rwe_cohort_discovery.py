import os
import sys
import duckdb
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

def run_analytics():
    print("\n" + "📊"*30)
    print("      REAL-WORLD EVIDENCE (RWE) - COHORT DISCOVERY")
    print("📊"*30 + "\n")

    with duckdb.connect(DB_PATH) as con:
        
        # 1. VISÃO GERAL DA POPULAÇÃO
        print("1. POPULATION OVERVIEW")
        print("-" * 50)
        pop_query = """
            SELECT 
                COUNT(DISTINCT person_id) as total_patients,
                ROUND(AVG(DATE_DIFF('year', observation_period_start_date, observation_period_end_date)), 1) as avg_years_observed
            FROM observation_period
        """
        pop_res = con.execute(pop_query).fetchone()
        print(f"Total Patients in Cohort: {pop_res[0]}")
        print(f"Average Observation Time per Patient: {pop_res[1]} years\n")

        # 2. TOP 5 CONDIÇÕES CLÍNICAS (Usando o dicionário para traduzir o ID para texto)
        print("2. TOP 5 CLINICAL CONDITIONS")
        print("-" * 50)
        cond_query = """
            SELECT c.concept_name, COUNT(co.condition_occurrence_id) as occurrences
            FROM condition_occurrence co
            JOIN concept c ON co.condition_concept_id = c.concept_id
            WHERE co.condition_concept_id != 0
            GROUP BY c.concept_name
            ORDER BY occurrences DESC
            LIMIT 5
        """
        for row in con.execute(cond_query).fetchall():
            print(f" - {row[0]:<40} | {row[1]} occurrences")
        print("\n")

        # 3. FENOTIPAGEM COMPLEXA: Doentes com Hipertensão QUE TOMARAM medicação
        print("3. PHENOTYPING: HYPERTENSION CASCADE")
        print("-" * 50)
        pheno_query = """
            WITH HTN_Patients AS (
                -- Encontrar doentes com códigos relacionados com Hipertensão
                SELECT DISTINCT co.person_id
                FROM condition_occurrence co
                JOIN concept c ON co.condition_concept_id = c.concept_id
                WHERE LOWER(c.concept_name) LIKE '%hypertension%'
            ),
            Treated_HTN AS (
                -- Destes doentes, quais têm registo na tabela de medicamentos?
                SELECT DISTINCT h.person_id
                FROM HTN_Patients h
                JOIN drug_exposure de ON h.person_id = de.person_id
            )
            SELECT 
                (SELECT COUNT(*) FROM HTN_Patients) as diagnosed_htn,
                (SELECT COUNT(*) FROM Treated_HTN) as treated_htn
        """
        pheno_res = con.execute(pheno_query).fetchone()
        print(f"Patients Diagnosed with Hypertension: {pheno_res[0]}")
        if pheno_res[0] > 0:
            treatment_rate = (pheno_res[1] / pheno_res[0]) * 100
            print(f"Patients on Medication:               {pheno_res[1]} ({treatment_rate:.1f}%)")
        else:
            print("Patients on Medication:               0 (0.0%)")
        print("\n")

if __name__ == "__main__":
    run_analytics()