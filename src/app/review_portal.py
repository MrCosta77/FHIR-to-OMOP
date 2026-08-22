import streamlit as st
import duckdb
import pandas as pd
import sys
from pathlib import Path

# Setup paths dynamically
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.append(str(PROJECT_ROOT))

from src.utils.config import DB_PATH

# Web page configuration
st.set_page_config(page_title="CMF - Human-in-the-Loop", page_icon="👩‍⚕️", layout="wide")

def get_pending_reviews():
    """Fetches all pending AI mappings from the database."""
    with duckdb.connect(DB_PATH) as con:
        query = """
            SELECT MIN(provenance_id) AS provenance_id, target_table,
                   source_value, normalized_value, assigned_concept_id,
                   mapping_method, score, model_name,
                   COUNT(DISTINCT target_id) AS affected_events
            FROM mapping_provenance
            WHERE reviewed_by = 'Pending_Human_Review'
            GROUP BY target_table, source_value, normalized_value,
                     assigned_concept_id, mapping_method, score, model_name
            ORDER BY provenance_id DESC
        """
        return con.execute(query).df()

def get_metrics():
    """Quick metrics for the Dashboard."""
    with duckdb.connect(DB_PATH) as con:
        def mapping_count(status):
            return con.execute("""
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM mapping_provenance
                    WHERE reviewed_by = ?
                    GROUP BY target_table, source_value, assigned_concept_id
                )
            """, [status]).fetchone()[0]

        pending = mapping_count("Pending_Human_Review")
        approved = mapping_count("Approved_by_Human")
        rejected = mapping_count("Rejected_by_Human")
        return pending, approved, rejected

def process_review(target_table, source_value, concept_id, action):
    """Updates the database based on the human curator's decision."""
    with duckdb.connect(DB_PATH) as con:
        if action == "approve":
            # UPDATE seguro sem f-strings
            con.execute(
                """UPDATE mapping_provenance
                   SET reviewed_by = 'Approved_by_Human'
                   WHERE target_table = ? AND source_value = ?
                     AND assigned_concept_id = ?
                     AND reviewed_by = 'Pending_Human_Review'""",
                (target_table, source_value, int(concept_id))
            )
            st.toast(f"✅ Mapping '{source_value}' approved!")
            
        elif action == "reject":
            # UPDATE seguro sem f-strings
            con.execute(
                """UPDATE mapping_provenance
                   SET reviewed_by = 'Rejected_by_Human'
                   WHERE target_table = ? AND source_value = ?
                     AND assigned_concept_id = ?
                     AND reviewed_by = 'Pending_Human_Review'""",
                (target_table, source_value, int(concept_id))
            )
            st.toast(f"❌ Mapping '{source_value}' rejected.")

# --- USER INTERFACE (UI) ---

st.title("👩‍⚕️ Clinical Mapping - Human-in-the-Loop")
st.markdown("Clinical Data Governance Platform. Review decisions made by the Artificial Intelligence Engine.")

# Metrics Dashboard
pending, approved, rejected = get_metrics()
col1, col2, col3 = st.columns(3)
col1.metric("⏳ Pending Review", pending)
col2.metric("✅ Approved", approved)
col3.metric("❌ Rejected", rejected)

st.divider()

# Validation Table
df_pending = get_pending_reviews()

if df_pending.empty:
    st.success("🎉 No pending mappings! The database is fully governed.")
else:
    # --- OTIMIZAÇÃO AQUI ---
    # Limita a vista a 50 linhas para o browser não bloquear
    display_limit = 50
    st.subheader(f"Mappings Awaiting Approval (Showing {min(display_limit, len(df_pending))} of {len(df_pending)})")
    
    df_display = df_pending.head(display_limit)
    
    # Table Header
    hcol1, hcol2, hcol3, hcol4, hcol5, hcol6 = st.columns([1, 2, 2, 1.5, 1, 2])
    hcol1.markdown("**Domain**")
    hcol2.markdown("**Raw Term (Legacy)**")
    hcol3.markdown("**AI Translation (OMOP)**")
    hcol4.markdown("**Concept ID**")
    hcol5.markdown("**Confidence**")
    hcol6.markdown("**Action**")
    
    st.markdown("---")

    # Interactive Rows (agora usa o df_display em vez do df_pending)
    for index, row in df_display.iterrows():
        col1, col2, col3, col4, col5, col6 = st.columns([1, 2, 2, 1.5, 1, 2])
        
        # Format table visually
        domain = row['target_table'].replace('_occurrence', '').replace('source_to_concept_map', 'STCM Dictionary').capitalize()
        
        col1.write(f"`{domain}`")
        col2.write(row['source_value'])
        col3.write(f"✨ {row['normalized_value']}")
        col4.write(row['assigned_concept_id'])
        
        # Color code confidence score
        score = float(row['score'])
        color = "green" if score > 0.9 else "orange"
        col5.markdown(f":{color}[{score*100:.1f}%]")
        
        # Action Buttons
        btn_col1, btn_col2 = col6.columns(2)
        if btn_col1.button("✅ Approve", key=f"app_{row['provenance_id']}", use_container_width=True):
            process_review(
                row['target_table'], row['source_value'],
                row['assigned_concept_id'], "approve"
            )
            st.rerun() # Refresh page automatically
            
        if btn_col2.button("❌ Reject", key=f"rej_{row['provenance_id']}", type="primary", use_container_width=True):
            process_review(
                row['target_table'], row['source_value'],
                row['assigned_concept_id'], "reject"
            )
            st.rerun() # Refresh page automatically

    st.markdown("---")
    st.caption(f"🤖 Mapping Engine: {df_pending.iloc[0]['model_name'] if not df_pending.empty else ''}")
