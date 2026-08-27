"""Atomically load an Athena vocabulary into typed OMOP CDM 5.4 tables."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import duckdb


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT))

from src.omop.cdm54 import create_table_sql, duckdb_type, load_table_specs
from src.utils.config import DB_PATH, VOCAB_DIR


VOCABULARY_FILES = {
    "concept": "CONCEPT.csv",
    "concept_relationship": "CONCEPT_RELATIONSHIP.csv",
    "vocabulary": "VOCABULARY.csv",
    "domain": "DOMAIN.csv",
    "concept_class": "CONCEPT_CLASS.csv",
    "concept_ancestor": "CONCEPT_ANCESTOR.csv",
}


def _source_expression(field):
    name = f'"{field.name}"'
    datatype = duckdb_type(field.datatype)
    if datatype == "BIGINT":
        return f"CAST({name} AS BIGINT)"
    if datatype == "DOUBLE":
        return f"CAST({name} AS DOUBLE)"
    if datatype == "DATE":
        return (
            f"COALESCE(TRY_STRPTIME({name}, '%Y%m%d')::DATE, "
            f"TRY_CAST({name} AS DATE))"
        )
    return name


def _load_next_table(con, table, source_path):
    next_table = f"next_{table}"
    con.execute(f'DROP TABLE IF EXISTS "{next_table}"')
    con.execute(create_table_sql(table, physical_name=next_table))
    fields = load_table_specs()[table]
    columns = ", ".join(f'"{field.name}"' for field in fields)
    expressions = ", ".join(_source_expression(field) for field in fields)
    escaped_path = str(source_path).replace("'", "''")
    con.execute(f"""
        INSERT INTO "{next_table}" ({columns})
        SELECT {expressions}
        FROM read_csv_auto(
            '{escaped_path}', delim='\t', header=true, quote='', escape='',
            nullstr='', all_varchar=true, strict_mode=true
        )
    """)
    count = con.execute(f'SELECT COUNT(*) FROM "{next_table}"').fetchone()[0]
    if count == 0:
        raise ValueError(f"Athena table {table} is empty: {source_path}")
    return count


def load_vocabularies():
    print("⚙️ STARTING TYPED ATHENA VOCABULARY LOAD (OMOP CDM 5.4)")
    print("-" * 50)
    start_time = time.time()
    paths = {
        table: Path(VOCAB_DIR) / filename
        for table, filename in VOCABULARY_FILES.items()
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Required OMOP vocabulary files are missing: " + ", ".join(missing)
        )

    counts = {}
    with duckdb.connect(DB_PATH) as con:
        con.execute("BEGIN TRANSACTION")
        try:
            for table, source_path in paths.items():
                print(f"⏳ Loading typed {table.upper()} from {source_path.name}...")
                counts[table] = _load_next_table(con, table, source_path)
            for table in paths:
                con.execute(f'DROP TABLE IF EXISTS "{table}"')
                con.execute(f'ALTER TABLE "next_{table}" RENAME TO "{table}"')
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

    elapsed = time.time() - start_time
    print(f"\n✅ Typed vocabularies loaded atomically in {elapsed:.1f} seconds!")
    for table, count in counts.items():
        print(f" - {table.upper()}: {count:,} rows")


if __name__ == "__main__":
    load_vocabularies()
