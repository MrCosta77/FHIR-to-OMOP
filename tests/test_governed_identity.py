import duckdb
import pytest

from src.mapping.governance import (
    add_governed_actor_alias,
    bootstrap_identity_administrator,
    ensure_governance_tables,
    register_governed_actor,
    resolve_governed_actor,
    suggest_actor_matches,
)


def _register_mario(con):
    if not con.execute("""
        SELECT COUNT(*) FROM governed_actor_role
        WHERE role = 'source_admin' AND active
    """).fetchone()[0]:
        bootstrap_identity_administrator(
            con, "Identity Administrator", "Authorized test bootstrap."
        )
    return register_governed_actor(
        con,
        "Mário Luís Gonçalves da Costa",
        {"reviewer"},
        "Identity Administrator",
        "Verified professional identity for governance testing.",
    )


def test_actor_id_survives_accents_case_aliases_and_typo_detection():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        actor = _register_mario(con)

        normalized = resolve_governed_actor(
            con, "  MARIO  LUIS GONCALVES DA COSTA ", "reviewer"
        )
        assert normalized["actor_id"] == actor["actor_id"]

        with pytest.raises(ValueError, match="resembles an existing actor"):
            resolve_governed_actor(con, "Mario Costa", "reviewer")
        short_matches = suggest_actor_matches(con, "Mario Costa")
        assert short_matches[0]["actor_id"] == actor["actor_id"]

        add_governed_actor_alias(
            con, actor["actor_id"], "Mario Costa", "Identity Administrator",
            "Approved first-and-last-name alias.",
        )
        assert resolve_governed_actor(
            con, "Mario Costa", "reviewer"
        )["actor_id"] == actor["actor_id"]

        with pytest.raises(ValueError, match="resembles an existing actor"):
            resolve_governed_actor(con, "Mairo Costa", "reviewer")
        add_governed_actor_alias(
            con, actor["actor_id"], "Mairo Costa", "Identity Administrator",
            "Verified historical spelling error.",
        )
        assert resolve_governed_actor(
            con, "Mairo Costa", "reviewer"
        )["actor_id"] == actor["actor_id"]


def test_similar_person_requires_explicit_distinct_confirmation_and_role():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        _register_mario(con)

        with pytest.raises(ValueError, match="Possible existing identity"):
            register_governed_actor(
                con, "Maria Luis Goncalves da Costa", {"reviewer"},
                "Identity Administrator", "A different verified professional.",
            )
        maria = register_governed_actor(
            con, "Maria Luis Goncalves da Costa", {"reviewer"},
            "Identity Administrator", "A different verified professional.",
            confirm_distinct=True,
        )
        assert maria["actor_id"]
        with pytest.raises(ValueError, match="not authorized for role 'adjudicator'"):
            resolve_governed_actor(
                con, "Maria Luis Goncalves da Costa", "adjudicator"
            )


def test_unrelated_unregistered_identity_fails_closed():
    with duckdb.connect(":memory:") as con:
        ensure_governance_tables(con)
        _register_mario(con)
        with pytest.raises(ValueError, match="Identity is not registered"):
            resolve_governed_actor(con, "Completely Different Person", "reviewer")
