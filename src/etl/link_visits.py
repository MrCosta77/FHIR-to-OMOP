import os
import sys
import duckdb
from pathlib import Path

# Setup paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

def link_events_to_visits():
    print("⚙️ STARTING ETL PIPELINE (LINKING EVENTS TO VISITS)")
    print("-" * 50)
    
    with duckdb.connect(DB_PATH) as con:
        print("⏳ Linking Conditions to Visits...")
        con.execute("""
            UPDATE condition_occurrence
            SET visit_occurrence_id = v.visit_occurrence_id
            FROM visit_occurrence v
            WHERE condition_occurrence.person_id = v.person_id
              AND condition_occurrence.condition_start_date >= v.visit_start_date
              AND condition_occurrence.condition_start_date <= v.visit_end_date
        """)
        
        print("⏳ Linking Medications to Visits...")
        con.execute("""
            UPDATE drug_exposure
            SET visit_occurrence_id = v.visit_occurrence_id
            FROM visit_occurrence v
            WHERE drug_exposure.person_id = v.person_id
              AND drug_exposure.drug_exposure_start_date >= v.visit_start_date
              AND drug_exposure.drug_exposure_start_date <= v.visit_end_date
        """)
        
        print("⏳ Linking Measurements to Visits...")
        con.execute("""
            UPDATE measurement
            SET visit_occurrence_id = v.visit_occurrence_id
            FROM visit_occurrence v
            WHERE measurement.person_id = v.person_id
              AND measurement.measurement_date >= v.visit_start_date
              AND measurement.measurement_date <= v.visit_end_date
        """)
        
        print("⏳ Linking Observations to Visits...")
        con.execute("""
            UPDATE observation
            SET visit_occurrence_id = v.visit_occurrence_id
            FROM visit_occurrence v
            WHERE observation.person_id = v.person_id
              AND observation.observation_date >= v.visit_start_date
              AND observation.observation_date <= v.visit_end_date
        """)
        
        print("⏳ Linking Procedures to Visits...")
        con.execute("""
            UPDATE procedure_occurrence
            SET visit_occurrence_id = v.visit_occurrence_id
            FROM visit_occurrence v
            WHERE procedure_occurrence.person_id = v.person_id
              AND procedure_occurrence.procedure_date >= v.visit_start_date
              AND procedure_occurrence.procedure_date <= v.visit_end_date
        """)

        print("⏳ Linking Device Exposures to Visits...")
        con.execute("""
            UPDATE device_exposure
            SET visit_occurrence_id = v.visit_occurrence_id
            FROM visit_occurrence v
            WHERE device_exposure.person_id = v.person_id
              AND device_exposure.device_exposure_start_date >= v.visit_start_date
              AND device_exposure.device_exposure_start_date <= v.visit_end_date
        """)

        # Fazemos TODAS as contagens dentro do bloco `with`, enquanto a ligação está aberta
        cond_linked = con.execute("SELECT COUNT(*) FROM condition_occurrence WHERE visit_occurrence_id IS NOT NULL").fetchone()[0]
        drug_linked = con.execute("SELECT COUNT(*) FROM drug_exposure WHERE visit_occurrence_id IS NOT NULL").fetchone()[0]
        meas_linked = con.execute("SELECT COUNT(*) FROM measurement WHERE visit_occurrence_id IS NOT NULL").fetchone()[0]
        obs_linked  = con.execute("SELECT COUNT(*) FROM observation WHERE visit_occurrence_id IS NOT NULL").fetchone()[0]
        proc_linked = con.execute("SELECT COUNT(*) FROM procedure_occurrence WHERE visit_occurrence_id IS NOT NULL").fetchone()[0]
        device_linked = con.execute("SELECT COUNT(*) FROM device_exposure WHERE visit_occurrence_id IS NOT NULL").fetchone()[0]
        
    # Quando o bloco `with` acaba, o Python fecha a base de dados em segurança.
    # Agora podemos imprimir as variáveis livremente.
    print("\n✅ All Clinical Events Successfully Linked to Visits!")
    print(f" - Conditions Linked:   {cond_linked}")
    print(f" - Medications Linked:  {drug_linked}")
    print(f" - Measurements Linked: {meas_linked}")
    print(f" - Observations Linked: {obs_linked}")
    print(f" - Procedures Linked:   {proc_linked}")
    print(f" - Devices Linked:      {device_linked}")

if __name__ == "__main__":
    link_events_to_visits()
