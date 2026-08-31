from __future__ import annotations

import uuid
from difflib import SequenceMatcher

from src.security.privacy import (
    audit_security_event,
    authorize_actor,
    canonical_actor_key,
)

from .schema import ensure_governance_tables


GOVERNED_ROLES = {"reviewer", "adjudicator", "source_admin"}


def _identity_administrator(con, claimed_name):
    claimed_name = authorize_actor(claimed_name, "source_admin")
    key = canonical_actor_key(claimed_name)
    row = con.execute("""
        SELECT a.actor_id, a.display_name
        FROM governed_actor_alias al
        JOIN governed_actor a USING (actor_id)
        JOIN governed_actor_role ar USING (actor_id)
        WHERE al.alias_key = ? AND al.active AND a.active
          AND ar.role = 'source_admin' AND ar.active
        LIMIT 1
    """, [key]).fetchone()
    if not row:
        raise ValueError(
            "Identity administration requires a registered active source_admin. "
            "Use the one-time bootstrap only when no identity administrator exists."
        )
    return {"actor_id": row[0], "display_name": row[1]}


def bootstrap_identity_administrator(con, display_name, reason):
    """Create or grant the sole initial identity administrator explicitly."""
    ensure_governance_tables(con)
    display_name = authorize_actor(display_name, "source_admin")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("A bootstrap authorization reason is required.")
    existing_admins = con.execute("""
        SELECT COUNT(*) FROM governed_actor_role ar
        JOIN governed_actor a USING (actor_id)
        WHERE ar.role = 'source_admin' AND ar.active AND a.active
    """).fetchone()[0]
    if existing_admins:
        raise ValueError("An active identity administrator already exists.")
    key = canonical_actor_key(display_name)
    actor = con.execute("""
        SELECT a.actor_id, a.display_name
        FROM governed_actor_alias al
        JOIN governed_actor a USING (actor_id)
        WHERE al.alias_key = ? AND al.active AND a.active
    """, [key]).fetchone()
    con.execute("BEGIN TRANSACTION")
    try:
        if actor:
            actor_id, canonical_name = actor
        else:
            actor_id = str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"cmf:governed-actor:{key}")
            )
            canonical_name = display_name
            con.execute("""
                INSERT INTO governed_actor (
                    actor_id, display_name, canonical_name, registered_by,
                    registration_reason
                ) VALUES (?, ?, ?, ?, ?)
            """, [actor_id, display_name, key, display_name, reason])
            con.execute("""
                INSERT INTO governed_actor_alias (
                    alias_key, actor_id, alias_name, source, approved_by,
                    approval_reason
                ) VALUES (?, ?, ?, 'bootstrap', ?, ?)
            """, [key, actor_id, display_name, display_name, reason])
        con.execute("""
            INSERT INTO governed_actor_role (
                actor_id, role, granted_by, grant_reason
            ) VALUES (?, 'source_admin', ?, ?)
        """, [actor_id, display_name, reason])
        audit_security_event(
            con, "IDENTITY_ADMIN_BOOTSTRAPPED", display_name, "RECORDED",
            {"actor_id": actor_id},
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"actor_id": actor_id, "display_name": canonical_name}


def _similarity(left: str, right: str) -> float:
    left_tokens = left.split()
    right_tokens = right.split()
    sequence = SequenceMatcher(None, left, right).ratio()
    same_edges = (
        len(left_tokens) >= 2
        and len(right_tokens) >= 2
        and left_tokens[0] == right_tokens[0]
        and left_tokens[-1] == right_tokens[-1]
    )
    subset = (
        len(left_tokens) >= 2
        and set(left_tokens).issubset(set(right_tokens))
    ) or (
        len(right_tokens) >= 2
        and set(right_tokens).issubset(set(left_tokens))
    )
    return max(sequence, 0.96 if same_edges else 0.0, 0.92 if subset else 0.0)


def suggest_actor_matches(con, identity, *, threshold=0.84):
    """Return possible identities for human resolution; never auto-merge them."""
    ensure_governance_tables(con)
    candidate_key = canonical_actor_key(identity)
    if not candidate_key:
        return []
    rows = con.execute("""
        SELECT DISTINCT a.actor_id, a.display_name, al.alias_key
        FROM governed_actor a
        JOIN governed_actor_alias al USING (actor_id)
        WHERE a.active AND al.active
    """).fetchall()
    best = {}
    for actor_id, display_name, alias_key in rows:
        score = _similarity(candidate_key, alias_key)
        if score >= threshold:
            current = best.get(actor_id)
            if not current or score > current["similarity"]:
                best[actor_id] = {
                    "actor_id": actor_id,
                    "display_name": display_name,
                    "similarity": score,
                }
    return sorted(
        best.values(), key=lambda item: (-item["similarity"], item["display_name"])
    )


def resolve_governed_actor(con, identity, role):
    """Resolve an exact approved alias to an active actor with the required role."""
    if role not in GOVERNED_ROLES:
        raise ValueError(f"Unsupported governed role: {role}")
    ensure_governance_tables(con)
    alias_key = canonical_actor_key(identity)
    if not alias_key:
        raise ValueError("A registered governed identity is required.")
    row = con.execute("""
        SELECT a.actor_id, a.display_name
        FROM governed_actor_alias al
        JOIN governed_actor a USING (actor_id)
        JOIN governed_actor_role ar USING (actor_id)
        WHERE al.alias_key = ? AND al.active AND a.active
          AND ar.role = ? AND ar.active
        LIMIT 1
    """, [alias_key, role]).fetchone()
    if row:
        return {"actor_id": row[0], "display_name": row[1]}

    exact_actor = con.execute("""
        SELECT a.display_name
        FROM governed_actor_alias al
        JOIN governed_actor a USING (actor_id)
        WHERE al.alias_key = ? AND al.active AND a.active
        LIMIT 1
    """, [alias_key]).fetchone()
    if exact_actor:
        raise ValueError(
            f"Identity '{exact_actor[0]}' is not authorized for role '{role}'."
        )
    matches = suggest_actor_matches(con, identity)
    if matches:
        names = ", ".join(match["display_name"] for match in matches[:3])
        raise ValueError(
            f"Unregistered identity resembles an existing actor: {names}. "
            "Select an approved alias or ask an identity administrator to add one."
        )
    raise ValueError(
        "Identity is not registered. An identity administrator must register it "
        "before any governed action."
    )


def register_governed_actor(
    con, display_name, roles, registered_by, reason, *, confirm_distinct=False
):
    """Register a new person; similar identities require explicit alias handling."""
    display_name = (display_name or "").strip()
    reason = (reason or "").strip()
    if not display_name:
        raise ValueError("A full professional display name is required.")
    if not reason:
        raise ValueError("An identity registration reason is required.")
    roles = {str(role).strip() for role in roles if str(role).strip()}
    if not roles or not roles.issubset(GOVERNED_ROLES):
        raise ValueError("At least one supported governed role is required.")
    ensure_governance_tables(con)
    administrator = _identity_administrator(con, registered_by)["display_name"]
    key = canonical_actor_key(display_name)
    exact = con.execute("""
        SELECT a.actor_id, a.display_name
        FROM governed_actor_alias al
        JOIN governed_actor a USING (actor_id)
        WHERE al.alias_key = ? AND al.active AND a.active
    """, [key]).fetchone()
    if exact:
        raise ValueError(
            f"This identity already belongs to registered actor '{exact[1]}'."
        )
    matches = suggest_actor_matches(con, display_name)
    if matches and not confirm_distinct:
        names = ", ".join(match["display_name"] for match in matches[:3])
        raise ValueError(
            f"Possible existing identity detected: {names}. Add an approved alias "
            "instead of creating another actor."
        )

    actor_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cmf:governed-actor:{key}"))
    con.execute("BEGIN TRANSACTION")
    try:
        con.execute("""
            INSERT INTO governed_actor (
                actor_id, display_name, canonical_name, registered_by,
                registration_reason
            ) VALUES (?, ?, ?, ?, ?)
        """, [actor_id, display_name, key, administrator, reason])
        con.execute("""
            INSERT INTO governed_actor_alias (
                alias_key, actor_id, alias_name, source, approved_by,
                approval_reason
            ) VALUES (?, ?, ?, 'registration', ?, ?)
        """, [key, actor_id, display_name, administrator, reason])
        for role in sorted(roles):
            con.execute("""
                INSERT INTO governed_actor_role (
                    actor_id, role, granted_by, grant_reason
                ) VALUES (?, ?, ?, ?)
            """, [actor_id, role, administrator, reason])
        audit_security_event(
            con, "GOVERNED_ACTOR_REGISTERED", administrator, "RECORDED",
            {"actor_id": actor_id, "roles": sorted(roles)},
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return {"actor_id": actor_id, "display_name": display_name, "roles": roles}


def add_governed_actor_alias(
    con, actor_id, alias_name, approved_by, reason, *, confirm_owner=False
):
    """Attach a reviewed name variant to one existing actor."""
    alias_name = (alias_name or "").strip()
    reason = (reason or "").strip()
    if not alias_name or not reason:
        raise ValueError("Alias and approval reason are required.")
    ensure_governance_tables(con)
    administrator = _identity_administrator(con, approved_by)["display_name"]
    actor = con.execute("""
        SELECT display_name FROM governed_actor
        WHERE actor_id = ? AND active
    """, [actor_id]).fetchone()
    if not actor:
        raise ValueError("Unknown or inactive governed actor.")
    alias_key = canonical_actor_key(alias_name)
    existing = con.execute("""
        SELECT actor_id FROM governed_actor_alias
        WHERE alias_key = ? AND active
    """, [alias_key]).fetchone()
    if existing:
        if existing[0] == actor_id:
            return {"actor_id": actor_id, "alias_name": alias_name, "created": False}
        raise ValueError("This alias already belongs to another governed actor.")
    conflicting = [
        match for match in suggest_actor_matches(con, alias_name)
        if match["actor_id"] != actor_id
    ]
    if conflicting and not confirm_owner:
        names = ", ".join(match["display_name"] for match in conflicting[:3])
        raise ValueError(
            f"Alias also resembles another actor: {names}. Resolve manually first."
        )
    con.execute("""
        INSERT INTO governed_actor_alias (
            alias_key, actor_id, alias_name, source, approved_by, approval_reason
        ) VALUES (?, ?, ?, 'manual_alias', ?, ?)
    """, [alias_key, actor_id, alias_name, administrator, reason])
    audit_security_event(
        con, "GOVERNED_ACTOR_ALIAS_ADDED", administrator, "RECORDED",
        {"actor_id": actor_id},
    )
    return {"actor_id": actor_id, "alias_name": alias_name, "created": True}


def list_governed_actors(con):
    ensure_governance_tables(con)
    rows = con.execute("""
        SELECT a.actor_id, a.display_name,
               STRING_AGG(ar.role, ', ' ORDER BY ar.role) AS roles
        FROM governed_actor a
        LEFT JOIN governed_actor_role ar
          ON ar.actor_id = a.actor_id AND ar.active
        WHERE a.active
        GROUP BY a.actor_id, a.display_name
        ORDER BY a.display_name
    """).fetchall()
    return [
        {"actor_id": row[0], "display_name": row[1], "roles": row[2] or ""}
        for row in rows
    ]
