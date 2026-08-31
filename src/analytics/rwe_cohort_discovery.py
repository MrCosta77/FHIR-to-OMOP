import sys
from pathlib import Path

import duckdb

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))
from src.utils.config import DB_PATH


def generate_report():
    print("\n📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊")
    print("      REAL-WORLD EVIDENCE (RWE) - COHORT DISCOVERY")
    print("📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊📊\n")

    with duckdb.connect(DB_PATH) as con:
        # 1. POPULATION OVERVIEW
        print("1. POPULATION OVERVIEW\n" + "-"*50)
        pop_count = con.execute("SELECT COUNT(*) FROM person").fetchone()[0]
        avg_obs = con.execute("""
            SELECT AVG(DATEDIFF('year', observation_period_start_date, observation_period_end_date)) 
            FROM observation_period
        """).fetchone()[0]

        print(f"Total Patients in Cohort: {pop_count}")
        print(f"Average Observation Time per Patient: {avg_obs:.1f} years\n")

        # 2. TOP 5 CLINICAL CONDITIONS
        print("2. TOP 5 CLINICAL CONDITIONS (Exact Matches)\n" + "-"*50)
        top_conditions = con.execute("""
            SELECT c.concept_name, COUNT(co.condition_occurrence_id) as occurrences
            FROM condition_occurrence co
            JOIN concept c ON co.condition_concept_id = c.concept_id
            WHERE co.condition_concept_id != 0
            GROUP BY c.concept_name
            ORDER BY occurrences DESC
            LIMIT 5
        """).fetchall()

        for name, count in top_conditions:
            print(f" - {name[:40]:<40} | {count} occurrences")
        print("\n")

        # 3. PHENOTYPING: HYPERTENSION CASCADE
        print("3. PHENOTYPING: HYPERTENSION CASCADE (Specific Phenotype)\n" + "-"*50)
        ht_patients = con.execute("""
            SELECT COUNT(DISTINCT person_id) FROM condition_occurrence 
            WHERE condition_concept_id = 320128 -- Essential hypertension
        """).fetchone()[0]

        ht_meds = con.execute("""
            SELECT COUNT(DISTINCT de.person_id) 
            FROM drug_exposure de
            JOIN condition_occurrence co ON de.person_id = co.person_id
            WHERE co.condition_concept_id = 320128
            AND de.drug_concept_id IN (1308216, 1319998, 1341927) -- ACE Inhibitors / ARBs
        """).fetchone()[0]

        print(f"Patients Diagnosed with Hypertension: {ht_patients}")
        pct = (ht_meds / ht_patients * 100) if ht_patients > 0 else 0
        print(f"Patients on Target Medication:        {ht_meds} ({pct:.1f}%)\n")

        # 4. HIERARCHICAL PHENOTYPING (THE POWER OF CONCEPT_ANCESTOR)
        print("4. HIERARCHICAL PHENOTYPING (Using CONCEPT_ANCESTOR)\n" + "-"*50)

        def get_hierarchical_count(ancestor_id, group_name):
            count = con.execute(f"""
                SELECT COUNT(DISTINCT co.person_id)
                FROM condition_occurrence co
                JOIN concept_ancestor ca ON co.condition_concept_id = ca.descendant_concept_id
                WHERE ca.ancestor_concept_id = {ancestor_id}
            """).fetchone()[0]
            print(f" - {group_name:<38}: {count} distinct patients")

        # IDs validados ontologicamente na base de dados OMOP
        get_hierarchical_count(432250, "Any Infectious Disease")
        get_hierarchical_count(320136, "Any Respiratory System Disease")
        get_hierarchical_count(134057, "Any Cardiovascular Disease")       # ID validado hoje!
        get_hierarchical_count(201603, "Any Dental/Oral Disease")          # ID validado hoje!

if __name__ == "__main__":
    generate_report()
