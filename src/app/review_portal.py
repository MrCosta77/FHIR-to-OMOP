"""Blinded two-review and adjudication portal for governed OMOP mappings."""

import os
import sys
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from src.mapping.governance import (
    adjudicate_mapping_decision,
    blinded_adjudication_queue,
    blinded_review_queue,
    clinical_review_agreement,
    counterproposal_source_queue,
    ensure_governance_tables,
    submit_blinded_review,
    submit_counterproposal,
)
from src.utils.config import DB_PATH


st.set_page_config(
    page_title="CMF - Blinded Clinical Review", page_icon="👩‍⚕️", layout="wide"
)


def get_review_queue(reviewer):
    with duckdb.connect(DB_PATH) as con:
        return pd.DataFrame(blinded_review_queue(con, reviewer))


def get_adjudication_queue(adjudicator):
    with duckdb.connect(DB_PATH) as con:
        return pd.DataFrame(blinded_adjudication_queue(con, adjudicator))


def get_counterproposal_queue(proposer):
    with duckdb.connect(DB_PATH) as con:
        return pd.DataFrame(counterproposal_source_queue(con, proposer))


def get_dashboard_metrics():
    with duckdb.connect(DB_PATH) as con:
        ensure_governance_tables(con)
        pending, ready = con.execute("""
            WITH review_counts AS (
                SELECT mapping_decision_id,
                       COUNT(DISTINCT review_id) AS review_count
                FROM clinical_mapping_review
                GROUP BY mapping_decision_id
            ), ranked AS (
                SELECT d.mapping_decision_id,
                       COALESCE(r.review_count, 0) AS review_count,
                       ROW_NUMBER() OVER (
                           PARTITION BY d.target_table,
                                        COALESCE(d.source_adapter, ''),
                                        COALESCE(d.source_vocabulary_id, ''),
                                        COALESCE(d.source_code, ''),
                                        LOWER(TRIM(d.source_value)),
                                        d.assigned_concept_id
                           ORDER BY COALESCE(r.review_count, 0) DESC,
                                    d.proposed_at, d.mapping_decision_id
                       ) AS canonical_rank
                FROM mapping_decision d
                LEFT JOIN review_counts r USING (mapping_decision_id)
                WHERE d.status IN ('PENDING', 'LOW_CONFIDENCE')
                  AND COALESCE(d.publication_eligible, TRUE)
            )
            SELECT COUNT(*) FILTER (WHERE canonical_rank = 1),
                   COUNT(*) FILTER (
                       WHERE canonical_rank = 1 AND review_count >= 2
                   )
            FROM ranked
        """).fetchone()
        approved = con.execute(
            "SELECT COUNT(*) FROM mapping_decision WHERE status = 'APPROVED'"
        ).fetchone()[0]
        rejected = con.execute(
            "SELECT COUNT(*) FROM mapping_decision WHERE status = 'REJECTED'"
        ).fetchone()[0]
        agreement = clinical_review_agreement(con)
    return pending, ready, approved, rejected, agreement


def submit_review(decision_id, action, reviewer, rationale):
    with duckdb.connect(DB_PATH) as con:
        result = submit_blinded_review(
            con, decision_id, action, reviewer, rationale
        )
    state = "ready for adjudication" if result["ready_for_adjudication"] else "review 1 of 2"
    st.toast(f"Independent review recorded: {state}")


def submit_adjudication(decision_id, action, adjudicator, rationale):
    with duckdb.connect(DB_PATH) as con:
        status = adjudicate_mapping_decision(
            con, decision_id, action, adjudicator, rationale
        )
    st.toast(f"Adjudication recorded: {status}")


def submit_candidate_correction(
    decision_id, candidate_concept_id, proposer, rationale
):
    with duckdb.connect(DB_PATH) as con:
        result = submit_counterproposal(
            con, decision_id, candidate_concept_id, proposer, rationale
        )
    state = "created" if result["created"] else "already recorded"
    st.toast(
        f"Counterproposal {state}: {result['candidate_concept_id']} "
        f"{result['candidate_name']}"
    )


def render_mapping(row, identity, mode):
    columns = st.columns([1, 2, 2, 1.2, 1, 2.2])
    domain = row["target_table"].replace("_occurrence", "").capitalize()
    columns[0].write(f"`{domain}`")
    columns[1].write(row["source_value"])
    columns[2].write(f"✨ {row['normalized_value']}")
    columns[3].write(row["assigned_concept_id"])
    score = row["score"]
    columns[4].write("—" if pd.isna(score) else f"{float(score) * 100:.1f}%")
    rationale = columns[5].text_input(
        "Required clinical rationale",
        key=f"{mode}_reason_{row['mapping_decision_id']}",
        label_visibility="collapsed",
        placeholder="Required clinical rationale",
    )
    approve, reject = columns[5].columns(2)
    if approve.button(
        "✅ Approve", key=f"{mode}_approve_{row['mapping_decision_id']}",
        width="stretch",
    ):
        try:
            if mode == "review":
                submit_review(row["mapping_decision_id"], "APPROVE", identity, rationale)
            else:
                submit_adjudication(
                    row["mapping_decision_id"], "APPROVE", identity, rationale
                )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if reject.button(
        "❌ Reject", key=f"{mode}_reject_{row['mapping_decision_id']}",
        width="stretch",
    ):
        try:
            if mode == "review":
                submit_review(row["mapping_decision_id"], "REJECT", identity, rationale)
            else:
                submit_adjudication(
                    row["mapping_decision_id"], "REJECT", identity, rationale
                )
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


st.title("👩‍⚕️ Blinded Clinical Mapping Review")
st.caption(
    "Two independent reviews are required. Reviewers never see peer votes; "
    "a distinct adjudicator is the only person who can publish or reject a mapping."
)
authenticated_identity = os.environ.get("CMF_AUTHENTICATED_USER", "").strip()
identity = st.text_input(
    "Named reviewer/adjudicator",
    value=authenticated_identity,
    disabled=bool(authenticated_identity),
    placeholder="Full professional name",
)

pending, ready, approved, rejected, agreement = get_dashboard_metrics()
metric_columns = st.columns(5)
metric_columns[0].metric("Pending decisions", pending)
metric_columns[1].metric("Ready to adjudicate", ready)
metric_columns[2].metric("Approved", approved)
metric_columns[3].metric("Rejected", rejected)
kappa = agreement["overall"]["cohens_kappa"]
metric_columns[4].metric("Cohen's κ", "—" if kappa is None else f"{kappa:.3f}")

review_tab, adjudication_tab, correction_tab, agreement_tab = st.tabs(
    [
        "Independent review", "Blinded adjudication",
        "Candidate correction", "Agreement",
    ]
)

with review_tab:
    if not identity.strip():
        st.info("Enter your full professional name to receive a blinded queue.")
    else:
        queue = get_review_queue(identity)
        if queue.empty:
            st.success("No independently reviewable mappings remain for this reviewer.")
        else:
            st.subheader(f"Independent queue ({len(queue)} decisions)")
            st.caption("Peer identities, votes, and rationales are intentionally hidden.")
            for _, mapping in queue.head(50).iterrows():
                render_mapping(mapping, identity, "review")
                st.divider()

with adjudication_tab:
    if not identity.strip():
        st.info("Enter your full professional name to receive an adjudication queue.")
    else:
        queue = get_adjudication_queue(identity)
        if queue.empty:
            st.success("No decisions are ready for this adjudicator.")
        else:
            st.subheader(f"Adjudication queue ({len(queue)} decisions)")
            st.caption(
                "Two reviews are complete. Their identities, votes, and rationales "
                "remain hidden while you make an independent final decision."
            )
            for _, mapping in queue.head(50).iterrows():
                render_mapping(mapping, identity, "adjudicate")
                st.divider()

with correction_tab:
    if not identity.strip():
        st.info("Enter your full professional name to propose a correction.")
    else:
        queue = get_counterproposal_queue(identity)
        if queue.empty:
            st.info(
                "No eligible corrections. The original candidate must first be "
                "finally rejected after two independent reviews and adjudication."
            )
        else:
            st.subheader(f"Rejected candidates eligible for correction ({len(queue)})")
            st.caption(
                "The Athena candidate is validated before a new governed decision "
                "is created. The author cannot review or adjudicate it."
            )
            for _, mapping in queue.head(50).iterrows():
                domain = mapping["target_table"].replace(
                    "_occurrence", ""
                ).capitalize()
                st.write(
                    f"`{domain}`  {mapping['source_value']} → rejected: "
                    f"{mapping['rejected_candidate']} "
                    f"(`{mapping['rejected_concept_id']}`)"
                )
                candidate = st.text_input(
                    "Correct Standard Athena concept_id",
                    key=f"candidate_{mapping['mapping_decision_id']}",
                    placeholder="e.g. 37165431",
                )
                rationale = st.text_area(
                    "Clinical rationale for the counterproposal",
                    key=f"counter_reason_{mapping['mapping_decision_id']}",
                    placeholder=(
                        "Explain why this concept is clinically and semantically "
                        "more precise than the rejected candidate."
                    ),
                )
                if st.button(
                    "Submit governed counterproposal",
                    key=f"counter_submit_{mapping['mapping_decision_id']}",
                ):
                    try:
                        submit_candidate_correction(
                            mapping["mapping_decision_id"], candidate,
                            identity, rationale,
                        )
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))
                st.divider()

with agreement_tab:
    overall = agreement["overall"]
    st.write({
        "completed_pairs": overall["pair_count"],
        "raw_agreement": overall["raw_agreement"],
        "cohens_kappa": overall["cohens_kappa"],
    })
    if agreement["by_domain"]:
        st.dataframe(
            pd.DataFrame.from_dict(agreement["by_domain"], orient="index"),
            width="stretch",
        )
    st.caption(
        "Agreement is descriptive until enough clinically reviewed pairs exist; "
        "no minimum κ is claimed from synthetic or technically curated labels."
    )
