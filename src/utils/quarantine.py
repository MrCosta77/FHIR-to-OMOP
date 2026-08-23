"""Persistent, auditable quarantine for source records rejected by ETL contracts."""


def ensure_quarantine_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS etl_quarantine (
            target_table VARCHAR NOT NULL,
            target_id BIGINT NOT NULL,
            source_event_key VARCHAR NOT NULL,
            source_code VARCHAR,
            source_value VARCHAR,
            unit_source_value VARCHAR,
            reason_code VARCHAR NOT NULL,
            reason_detail VARCHAR NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (target_table, target_id, reason_code)
        )
    """)
